"""트레이스 컨텍스트 — 한 턴/한 브리핑 아래 호출들을 묶는다

`observability.py` 에서 갈라낸 모듈 — 공개 표면은 패키지 `__init__` 이다.
"""

from __future__ import annotations

from typing import Any, Iterator
import contextlib
import contextvars
import logging
import time
import uuid

from pension_agent.observability._conf import conf, enabled
from pension_agent.observability._payload import _iso, _now, _payload, _put_if
from pension_agent.observability._transport import _debug, _emit


# ─────────────────────────────────────────────────────────────
# 트레이스 컨텍스트 — 한 턴/한 브리핑 아래 호출들을 묶는다
# ─────────────────────────────────────────────────────────────

#: 현재 트레이스 id. ContextVar 라 스레드에 자동으로 따라가지 않는다 — 스레드를 띄우는
#: 자리(answer.py 의 compose 병렬 작성)는 이미 `contextvars.copy_context()` 로 넘기고
#: 있어서 그대로 따라간다. asyncio.to_thread(llm.agenerate)도 컨텍스트를 복사한다.
_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "langfuse_trace_id", default=None)

#: 현재 열려 있는 span 의 id. 이것이 있으면 그 아래에 붙는다 — 트레이스가 평면이 아니라
#: 실행 구조(계획 루프 → 도구 호출 → 작성 → 재작성)를 그대로 닮은 트리가 된다.
_PARENT_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "langfuse_parent_id", default=None)

#: 지금 처리 중인 HTTP 요청의 짧은 id(main.py 가 붙인다). «단계» 로그 줄을 그 요청의 다른
#: 줄(요청·완료)과 묶는 열쇠다. 트레이스 id 와 달리 Langfuse 가 꺼져 있어도 있다.
_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "observability_request_id", default=None)

#: 지금 턴(또는 브리핑 한 건)이 시작된 시각(`time.monotonic()`). 단계 줄의 경과초가 여기서
#: 나온다 — `request_id()`·`trace()` 가 연다. 없으면 경과초 칸은 비운다.
_STARTED: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "observability_started", default=None)

#: 지금 턴의 LLM 호출 집계 — 호출 수와 입력 글자 수 합계. `turn` 끝 줄이 싣는다. 호출 한 건
#: 한 건은 DEBUG 줄이라(llm._observe) INFO 로 남는 것은 이 합계뿐이다.
_LLM_TALLY: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "observability_llm_tally", default=None)


@contextlib.contextmanager
def request_id(rid: str | None) -> Iterator[None]:
    """이 블록 안의 단계 로그에 요청 id 를 붙이고 경과초 시계·LLM 집계를 연다.

    main.py 가 ask() 를 부를 때 열고, graph.ask 가 같은 id 로 한 번 더 연다 — 그래서
    경과초는 «턴이 시작된 뒤»다(HTTP 파싱 시간은 안 들어간다).
    """
    tokens = (_REQUEST_ID.set(rid), _STARTED.set(time.monotonic()),
              _LLM_TALLY.set({"calls": 0, "chars": 0}))
    try:
        yield
    finally:
        _REQUEST_ID.reset(tokens[0])
        _STARTED.reset(tokens[1])
        _LLM_TALLY.reset(tokens[2])


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def tally_llm(chars: int) -> None:
    """LLM 호출 한 건을 이번 턴 집계에 더한다(llm._observe 가 부른다). 턴 밖이면 아무것도 안 한다.

    스레드로 갈라진 호출(answer.py 의 compose 병렬 작성)도 같은 dict 객체를 보므로 — 컨텍스트
    복사는 참조를 복사한다 — 합계에 들어온다.
    """
    tally = _LLM_TALLY.get()
    if tally is not None:
        tally["calls"] += 1
        tally["chars"] += chars


def llm_tally() -> dict[str, int]:
    """이번 턴의 LLM 호출 수·입력 글자 수 합계. 턴 밖이면 0."""
    return dict(_LLM_TALLY.get() or {"calls": 0, "chars": 0})


# ─────────────────────────────────────────────────────────────
# «단계» 로그 — 에이전트가 이 턴에서 무엇을 했나, 한 단계에 한 줄
#
# 줄의 꼴은 하나다:   경과초 단계  키=값 키=값 …
#
#   INFO:     [agent] [ab12cd34]  2.0s plan        회차=1/4 도구=fact 질의='세액공제 한도'
#   INFO:     [agent] [ab12cd34]  3.4s tool        fact 결과=성공 후보=3 채택=1 카드=fact.k04.f2(0.37)
#   WARNING:  [agent] [ab12cd34]  7.0s verify      통과=아니오 시도=1/2 사유="수치 '1,485,000' 이 근거에 없음"
#
# 규칙 — 로그가 «LLM 이 쓴 글»로 읽히면 안 된다. 그래서 문장이 아니라 키=값이고, 동사·조사가
# 없고, 키와 판정 값은 아래 두 표 안의 말만 쓴다(표 밖의 키는 찍히지 않는다 — 테스트가
# 잡는다). 한글이 자유롭게 들어가는 자리는 둘뿐이다: LLM 이 만든 질의(`query`)와 실패
# 사유(`reason`). 둘 다 상한이 있다(STEP_TEXT_MAX) — 로그는 상담 내용의 저장소가 아니다.
#
# 단계 이름(왼쪽)은 영문 그대로다 — 그래프 노드·LLM 호출 이름과 같은 말이라 코드에서 찾을
# 수 있어야 한다. 도구·의도·카드 id·화면번호도 같은 이유로 번역하지 않는다.
#
# Langfuse 로 나가는 점수(`score()`)는 이 로그와 별개다 — 점수 이름은 대시보드 집계용이라
# 영문 그대로 두고, 로그는 사람이 읽는 자리라 한글로 간다.
# ─────────────────────────────────────────────────────────────

#: 단계 로거. 루트 로거로 흘러 stdout(→ Grafana)에 찍힌다. HTTP 경계 줄은 `api` 로거다(main.py).
_step_log = logging.getLogger("agent")

#: 자유 텍스트 값(질의·사유)의 상한. 넘으면 자르고 … 을 붙인다.
STEP_TEXT_MAX = 120

#: 키 표 — 호출부가 쓰는 영문 인자 이름 → 로그의 한글 키. 여기 없는 키는 찍히지 않는다.
STEP_KEYS: dict[str, str] = {
    # turn
    "customer": "고객", "history": "맥락", "result": "결과", "evidence": "근거",
    "failed": "고장", "llm": "LLM",
    # understand · plan
    "intent": "의도", "error": "실패", "step": "회차", "tool": "도구", "query": "질의",
    "done": "종료", "retry": "재계획", "unused": "미사용도구",
    # tool
    "candidates": "후보", "picked": "채택", "cards": "카드", "branches": "분기",
    "requeried": "재검색", "reason": "사유",
    # compose · clarify · verify
    "verdict": "판정", "axis": "축", "options": "선택지", "draft": "초안", "answer": "답",
    "searched": "검색", "discarded": "폐기", "passed": "통과", "attempt": "시도",
    "fallback": "폴백",
    # confirm · offer · action
    "pending": "제안", "reply": "응답", "status": "상태", "screen": "화면",
    "recipients": "수신", "mode": "모드",
    # llm
    "retries": "재시도", "wait": "대기", "source": "출처", "chars": "입력", "elapsed": "소요",
    "masked": "가린룰",   # 개인정보 필터에 걸린 **룰 이름**. 걸린 값은 싣지 않는다(privacy.py)
    # briefing
    "tier": "등급", "skipped": "건너뜀",
}

#: 값 표 — 코드가 쓰는 판정 토큰 → 로그의 한글 값. 표에 없는 문자열은 그대로 찍힌다
#: (도구 이름·의도·카드 id 같은 식별자가 그렇다).
STEP_VALUES: dict[Any, str] = {
    True: "예", False: "아니오",
    # 도구 결과 · 턴 결과
    "found": "성공", "miss": "없음", "failed": "실패",
    "answer": "답변", "clarify": "되묻기", "llm_down": "LLM다운", "tool_failed": "도구실패",
    # 판정 등급(§5)
    "ask": "되묻기", "assume": "전제", "none": "대상없음", "n/a": "없음",
    # 연계 상태
    "sent": "발송", "stubbed": "스텁", "ok": "성공", "blocked": "차단",
    "not_connected": "미연결", "unknown": "미상",
    # 승낙 응답
    "accept": "승낙", "reject": "거절", "unclear": "불명확",
    # 쪽지 초안이 걸린 턴(consult_agent/nodes/act._memo_reply)
    "edited": "초안수정", "not_edit": "수정아님", "pasted": "붙여넣기", "screened": "검사걸림",
    "down": "LLM실패",
    # 폴백
    "raw_evidence": "근거원문",
}


def _show(value: Any) -> str:
    """값 하나를 로그 글자로. 판정 토큰은 표로 옮기고, 공백이 든 문자열은 따옴표로 감싼다."""
    if isinstance(value, bool) or (isinstance(value, str) and value in STEP_VALUES):
        return STEP_VALUES[value]
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (list, tuple)):
        return ",".join(_show(v) for v in value) or "없음"
    text = " ".join(str(value).split())
    if len(text) > STEP_TEXT_MAX:
        text = text[:STEP_TEXT_MAX] + "…"
    if " " in text or "=" in text:
        quote = "'" if "'" not in text else '"'
        return f"{quote}{text}{quote}"
    return text


def step(stage: str, ident: str | None = None, *, level: int = logging.INFO,
         **facts: Any) -> None:
    """단계 한 줄을 남긴다. 값이 None 인 키는 찍지 않는다.

        observability.step("tool", "fact", result="found", candidates=3, picked=1)
        → INFO: [agent] [ab12cd34]  3.4s tool        fact 결과=성공 후보=3 채택=1

    `level` 은 호출부가 정한다 — 직원이 받는 답이 실패·축소로 바뀐 사실(검증 폐기·도구
    실패·연계 실패·LLM 다운·원문 폴백)만 WARNING 이다. 기록은 흐름을 막지 않는다.
    """
    try:
        started = _STARTED.get()
        clock = f"{time.monotonic() - started:5.1f}s" if started is not None else "     -"
        pairs = " ".join(f"{STEP_KEYS[k]}={_show(v)}" for k, v in facts.items()
                         if v is not None and k in STEP_KEYS)
        body = f"{ident} {pairs}".strip() if ident else pairs
        _step_log.log(level, "[%s] %s %-11s %s",
                      _REQUEST_ID.get() or "-", clock, stage, body)
    except Exception:                                     # noqa: BLE001 — 기록은 흐름을 막지 않는다
        pass


class Trace:
    """트레이스 하나. `trace()` 가 만들어 준다 — 직접 만들지 않는다."""

    __slots__ = ("id", "name", "_fields", "_live")

    def __init__(self, trace_id: str, name: str, live: bool) -> None:
        self.id = trace_id
        self.name = name
        self._fields: dict[str, Any] = {}
        self._live = live

    def update(self, **fields: Any) -> None:
        """트레이스에 얹을 값(output·metadata·tags·user_id …). 닫힐 때 함께 보낸다."""
        if self._live:
            self._fields.update(fields)


#: 관측이 꺼져 있을 때 돌려주는 핸들. update() 가 아무것도 하지 않는다.
_NULL_TRACE = Trace("", "", live=False)


def _ensure_trace_id(name: str) -> str:
    """열려 있는 트레이스 id. 없으면 하나 만들어 붙인다.

    스크립트에서 도구·LLM 을 직접 부른 경우에도 관측을 잃지 않기 위한 자리다 —
    트레이스 없는 관측은 대시보드에서 찾을 방법이 없다.
    """
    trace_id = _TRACE_ID.get()
    if trace_id is None:
        trace_id = uuid.uuid4().hex
        _emit("trace-create", {"id": trace_id, "name": name, "timestamp": _now()})
    return trace_id


@contextlib.contextmanager
def trace(name: str, *, input: Any = None, user_id: str | None = None,
          session_id: str | None = None, metadata: dict | None = None,
          tags: list[str] | None = None) -> Iterator[Trace]:
    """트레이스 하나를 연다. 이 블록 안의 LLM 호출은 전부 여기에 묶인다.

    블록에서 예외가 나면 트레이스에 level=ERROR 와 예외 문구를 남기고 예외는 그대로
    올려보낸다 — 관측은 흐름을 바꾸지 않는다.
    """
    if not enabled():
        yield _NULL_TRACE
        return

    # 환경은 메타데이터로 싣는다 — 대시보드가 필터로 쓰는 자리이고, 서버 판이 달라도
    # 거부되지 않는다(수집 API 의 최상위 필드는 판마다 늘고 줄었다).
    metadata = {"environment": conf().environment, **(metadata or {})}
    handle = Trace(uuid.uuid4().hex, name, live=True)
    body: dict[str, Any] = {"id": handle.id, "name": name, "timestamp": _now()}
    _put_if(body, "release", conf().release or None)
    _put_if(body, "input", _payload(input))
    _put_if(body, "userId", user_id)
    _put_if(body, "sessionId", session_id)
    _put_if(body, "metadata", metadata)
    _put_if(body, "tags", tags)
    _emit("trace-create", body)   # 시작 시점에 한 번 — 도중에 프로세스가 죽어도 흔적이 남는다

    # 새 트레이스는 최상위에서 시작한다 — 바깥에 열려 있던 span 밑으로 들어가지 않는다.
    # 단계 로그의 경과초 시계도 여기서 연다 — 요청 컨텍스트 없이 도는 브리핑 생성이 그렇다.
    tokens = (_TRACE_ID.set(handle.id), _PARENT_ID.set(None), _STARTED.set(time.monotonic()),
              _LLM_TALLY.set({"calls": 0, "chars": 0}))
    started = time.time()
    try:
        yield handle
    except BaseException as exc:                        # noqa: BLE001 — 기록만 하고 되던진다
        handle.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        _TRACE_ID.reset(tokens[0])
        _PARENT_ID.reset(tokens[1])
        _STARTED.reset(tokens[2])
        _LLM_TALLY.reset(tokens[3])
        closing: dict[str, Any] = {"id": handle.id, "name": name, "timestamp": _now()}
        fields, meta = _split_fields(handle, dict(metadata or {}), started)
        for key in ("level", "status_message"):
            if key in fields:
                meta[key] = fields.pop(key)
        _put_if(closing, "output", _payload(fields.pop("output", None)))
        # 여는 이벤트에서 준 값을 닫는 이벤트에도 그대로 싣는다. 같은 id 의 두 이벤트를
        # Langfuse 가 병합하므로 생략해도 남을 값이지만, 빠진 필드를 어떻게 다루는지는
        # 서버 판에 달렸다 — 「대시보드에서 user 가 비어 보인다」는 원인을 짚기 어려운
        # 실패라 기대지 않는다. 호출부가 update() 로 덮었으면 그쪽이 이긴다.
        _put_if(closing, "userId", fields.pop("user_id", None) or user_id)
        _put_if(closing, "sessionId", fields.pop("session_id", None) or session_id)
        _put_if(closing, "tags", fields.pop("tags", None) or tags)
        meta.update(fields)                              # 남은 것은 전부 메타데이터로
        closing["metadata"] = meta
        _emit("trace-create", closing)                   # 같은 id — Langfuse 가 병합한다


def current_trace_id() -> str | None:
    """지금 열려 있는 트레이스 id. 없으면 None."""
    return _TRACE_ID.get()


def tag(kind: str, value: Any) -> list[str]:
    """«분류:값» 태그 한 장. 값이 없으면 빈 목록이라 아무것도 붙지 않는다.

    태그로 만드는 이유는 대시보드가 **거를 수 있는 축**이 태그이기 때문이다 —
    메타데이터에만 두면 한 건씩 열어봐야 보인다.
    """
    return [f"{kind}:{value}"] if value else []


def customer_ref(customer_id: str | None, name: str | None,
                 conditions: list[str] | None = None) -> dict:
    """관측에 실을 «누구인가» — user_id·태그·메타데이터를 한 벌로 만든다.

        who = observability.customer_ref(p.id, p.nm)
        with observability.trace("briefing.generate", user_id=who["user_id"],
                                 metadata=who["metadata"],
                                 tags=["briefing", *who["tags"]]) as tr:

    **user_id 에 이름을 함께 넣는 이유**는 대시보드의 Users 목록이 `userId` 문자열
    하나만 보여주기 때문이다 — 이름을 담을 칸이 따로 없다. id 만 두면 목록이
    `171203-4815062` 열두 줄이 되고, 누가 누구인지 매번 원장을 뒤져야 한다. id 를
    떼지 않는 것은 그것이 안정된 식별자이고 동명이인이 갈려야 하기 때문이다.

    **꼴을 여기서 정하는 이유는 진입점이 둘이기 때문**이다. 브리핑과 대화 턴이 같은
    고객을 다른 꼴로 적으면 대시보드에서 두 사람으로 갈리는데, 그 어긋남은 화면에
    «줄이 두 개 보인다»로만 나타나 알아채기 어렵다.

    **`conditions` 는 그 고객에게 성립한 타겟 선정 요건**(`idl`·`hlt`·`mat`…)이다. 이것을
    태그로 다는 이유는 「어떤 상태의 고객에게 무슨 일이 생기나」가 이 저장소에서 가장
    쓸모 있는 축이기 때문이다 — 「`요건:hlt` 인데 답이 게이트에 걸린 턴」처럼 거를 수 있다.
    **판정을 여기서 만들지 않는다**: 호출부가 strategy_agent 산출을 그대로 넘긴다(§3 —
    같은 판정을 두 번 구현하지 않는다).

    **`LANGFUSE_CAPTURE_CONTENT=0` 이면 이름과 요건을 뺀다.** 그 스위치는 «개인정보를
    외부로 내보내지 않는다»는 약속이다. 본문만 가리고 이름·상태를 user_id·태그로
    내보내면 그 약속이 거짓이 되고, 그건 이 저장소가 가장 경계하는 실패다(루트
    CLAUDE.md 절대규칙 1 — 표시가 거짓말하는 상태). 요건 코드는 이름이 아니지만 **그
    고객의 상태**라 같이 가린다. id 는 남는다 — 그것 없이는 실행을 묶을 방법이 없다.
    """
    if not conf().capture_content:
        name, conditions = None, None
    label = f"{name}({customer_id})" if name and customer_id else (name or customer_id)
    meta: dict[str, Any] = {}
    _put_if(meta, "customer_id", customer_id)
    _put_if(meta, "customer", name)
    _put_if(meta, "conditions", list(conditions) if conditions else None)
    tags = tag("고객", name) + [t for c in (conditions or []) for t in tag("요건", c)]
    return {"user_id": label, "metadata": meta, "tags": tags}


# ─────────────────────────────────────────────────────────────
# span — LLM 호출이 아닌 단계
#
# 트레이스가 generation 만 담으면 «LLM 을 다섯 번 불렀다»까지만 보인다. 정작 답이
# 갈리는 자리는 그 사이다 — 어떤 도구를 어떤 질의로 불러 몇 건을 얻었나, 작성이 게이트에
# 걸려 다시 썼나. span 은 그 단계를 트레이스 트리에 세워, 대시보드 한 화면에서 실행
# 구조를 읽게 한다.
# ─────────────────────────────────────────────────────────────

@contextlib.contextmanager
def span(name: str, *, input: Any = None, metadata: dict | None = None) -> Iterator[Trace]:
    """실행 단계 하나를 연다. 이 블록 안의 LLM 호출·하위 span 은 이 밑에 붙는다.

    핸들은 `trace()` 와 같은 모양이라 `update(output=…, 아무거나=…)` 로 결과를 얹는다 —
    output·level·status_message 를 뺀 나머지는 메타데이터로 실린다.
    """
    if not enabled():
        yield _NULL_TRACE
        return

    trace_id = _ensure_trace_id(name)
    handle = Trace(uuid.uuid4().hex, name, live=True)
    parent = _PARENT_ID.get()
    started = time.time()
    tokens = (_TRACE_ID.set(trace_id), _PARENT_ID.set(handle.id))
    try:
        yield handle
    except BaseException as exc:                        # noqa: BLE001 — 기록만 하고 되던진다
        handle.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        _TRACE_ID.reset(tokens[0])
        _PARENT_ID.reset(tokens[1])
        body: dict[str, Any] = {
            "id": handle.id, "traceId": trace_id, "name": name,
            "startTime": _iso(started), "endTime": _now(),
        }
        _put_if(body, "parentObservationId", parent)
        _put_if(body, "input", _payload(input))
        fields, meta = _split_fields(handle, dict(metadata or {}), started)
        _put_if(body, "output", _payload(fields.pop("output", None)))
        _put_if(body, "level", fields.pop("level", None))
        _put_if(body, "statusMessage", fields.pop("status_message", None))
        meta.update(fields)
        body["metadata"] = meta
        _emit("span-create", body)


def _split_fields(handle: Trace, meta: dict, started: float) -> tuple[dict, dict]:
    """핸들에 얹힌 값을 «전용 필드»와 «메타데이터»로 가른다. 소요시간을 함께 넣는다."""
    fields = dict(handle._fields)
    meta.update(fields.pop("metadata", None) or {})
    meta["latency_ms"] = int((time.time() - started) * 1000)
    return fields, meta


# ─────────────────────────────────────────────────────────────
# score — 이 실행이 어땠는지
#
# 트레이스는 «무슨 일이 있었나»를 한 건씩 보여주지만, «지난 30번 중 몇 번이 게이트에
# 걸렸나»는 답하지 못한다. 점수는 그 집계를 대시보드에 맡기는 자리다. 값은 전부 코드가
# 아는 사실이다 — LLM 이 자기 답을 채점하지 않는다.
# ─────────────────────────────────────────────────────────────

def score(name: str, value: bool | int | float | str, *, comment: str | None = None) -> None:
    """열려 있는 트레이스에 점수 한 건을 붙인다. 트레이스가 없으면 아무것도 하지 않는다.

    bool 은 BOOLEAN(1/0), 숫자는 NUMERIC, 문자열은 CATEGORICAL 로 나간다.

    **로그에는 남지 않는다** — 로그는 `step()` 이 단계 단위로 찍는다. 점수는 대시보드
    집계용이라 이름을 영문 그대로 두고, 로그는 사람이 읽는 자리라 한글 키=값이다. 같은
    사실을 두 곳에 남기는 호출부는 둘을 나란히 부른다(plan.compose 의 검증 결과가 그렇다).
    """
    if not enabled():
        return
    try:
        trace_id = _TRACE_ID.get()
        if trace_id is None:
            return          # 트레이스 없는 점수는 대시보드에서 찾을 방법이 없다
        body_value: Any
        if isinstance(value, bool):
            body_value, data_type = (1 if value else 0), "BOOLEAN"
        elif isinstance(value, (int, float)):
            body_value, data_type = value, "NUMERIC"
        else:
            body_value, data_type = str(value), "CATEGORICAL"
        body: dict[str, Any] = {
            "id": uuid.uuid4().hex, "traceId": trace_id,
            "name": name, "value": body_value, "dataType": data_type,
        }
        _put_if(body, "observationId", _PARENT_ID.get())
        _put_if(body, "comment", comment)
        _emit("score-create", body)
    except Exception as exc:                              # noqa: BLE001 — 관측은 흐름을 막지 않는다
        _debug(f"score 실패: {type(exc).__name__}: {exc}")


# ─────────────────────────────────────────────────────────────
# 관측 기록 — LLM 호출 한 건
# ─────────────────────────────────────────────────────────────

def record_generation(name: str, *, model: str | None = None, input: Any = None,
                      output: Any = None, usage: dict | None = None,
                      start: float | None = None, end: float | None = None,
                      parameters: dict | None = None, metadata: dict | None = None,
                      error: str | None = None) -> None:
    """LLM 호출 한 건을 남긴다. `llm.generate()` 가 성공·실패 양쪽에서 부른다.

    start·end 는 `time.time()` 값이다. 트레이스가 열려 있지 않으면 이 호출만 담은
    트레이스를 하나 만들어 붙인다 — 스크립트에서 직접 부른 호출도 잃지 않는다.
    """
    if not enabled():
        return
    try:
        body: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "traceId": _ensure_trace_id(name),
            "name": name,
            "startTime": _iso(start),
            "endTime": _iso(end),
        }
        # 열려 있는 span 이 있으면 그 밑에 붙는다 — 「어느 도구를 부르다가 난 호출인가」가
        # 트레이스 트리에 그대로 보인다.
        _put_if(body, "parentObservationId", _PARENT_ID.get())
        _put_if(body, "model", model)
        _put_if(body, "modelParameters", parameters)
        _put_if(body, "input", _payload(input))
        _put_if(body, "output", _payload(output))
        _put_if(body, "usage", usage)
        _put_if(body, "metadata", metadata)
        if error:
            body["level"] = "ERROR"
            body["statusMessage"] = error
        _emit("generation-create", body)
    except Exception as exc:                              # noqa: BLE001 — 관측은 흐름을 막지 않는다
        _debug(f"record_generation 실패: {type(exc).__name__}: {exc}")
