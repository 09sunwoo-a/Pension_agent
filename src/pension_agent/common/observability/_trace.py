"""트레이스 컨텍스트(턴·브리핑 단위 묶음) · 단계 로그 · span · score · generation 기록."""

from __future__ import annotations

from typing import Any, Iterator
import contextlib
import contextvars
import logging
from datetime import datetime
import time
import uuid

from pension_agent.common.observability._conf import conf, enabled
from pension_agent.common.observability._payload import _iso, _now, _payload, _put_if
from pension_agent.common.observability._transport import _debug, _emit

#: 현재 트레이스 id. 스레드 자동 전파 없음 — 스레드 생성 시 `contextvars.copy_context()` 필요.
_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "langfuse_trace_id", default=None)

#: 현재 열린 span id. 있으면 하위 관측이 그 아래에 붙음.
_PARENT_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "langfuse_parent_id", default=None)

#: 처리 중인 HTTP 요청의 짧은 id(main.py). 단계 로그 묶음용, Langfuse 꺼져도 유지.
_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "observability_request_id", default=None)

#: 턴(브리핑) 시작 시각(`time.monotonic()`). 단계 줄 경과초 기준. 없으면 경과초 비움.
_STARTED: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "observability_started", default=None)

#: 이번 턴 LLM 호출 수·입력 글자 수 합계(`turn` 끝 줄에 출력).
_LLM_TALLY: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "observability_llm_tally", default=None)


#: 이번 턴 단계 기록(`step()` 1회 = 1항목, `detail` 포함 구조 그대로). 응답 절차 기록용, 로그 미출력.
_JOURNAL: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "observability_journal", default=None)


@contextlib.contextmanager
def request_id(rid: str | None) -> Iterator[None]:
    """블록 안 단계 로그에 요청 id 부착, 경과초 시계·LLM 집계·단계 기록 시작."""
    tokens = (_REQUEST_ID.set(rid), _STARTED.set(time.monotonic()),
              _LLM_TALLY.set({"calls": 0, "chars": 0}), _JOURNAL.set([]))
    try:
        yield
    finally:
        _REQUEST_ID.reset(tokens[0])
        _STARTED.reset(tokens[1])
        _LLM_TALLY.reset(tokens[2])
        _JOURNAL.reset(tokens[3])


def journal() -> list[dict]:
    """이번 턴 단계 기록 사본(시간순). 턴 밖이면 빈 목록."""
    return list(_JOURNAL.get() or [])


def wall_clock() -> str:
    """현재 시각 — ISO 8601, 밀리초·시간대 포함."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _plain_value(value: Any) -> Any:
    """단계 기록용 JSON 가능 값으로 변환."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_plain_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain_value(v) for k, v in value.items()}
    return str(value)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def tally_llm(chars: int) -> None:
    """LLM 호출 한 건을 이번 턴 집계에 가산. 턴 밖이면 무시."""
    tally = _LLM_TALLY.get()
    if tally is not None:
        tally["calls"] += 1
        tally["chars"] += chars


def llm_tally() -> dict[str, int]:
    """이번 턴 LLM 호출 수·입력 글자 수. 턴 밖이면 0."""
    return dict(_LLM_TALLY.get() or {"calls": 0, "chars": 0})


# 단계 로그 꼴: `경과초 단계  키=값 …` (예: `3.4s tool  fact 결과=성공 후보=3`)
# - 키·판정 값은 아래 두 표의 말만 사용. 자유 텍스트는 query·reason 뿐(STEP_TEXT_MAX 상한)
# - 단계 이름·도구·카드 id 는 영문 그대로(코드 검색용)

#: 단계 로거(루트 → stdout). HTTP 경계 줄은 `api` 로거(main.py).
_step_log = logging.getLogger("agent")

#: 자유 텍스트 값(질의·사유) 상한. 초과 시 … 로 자름.
STEP_TEXT_MAX = 120

#: 키 표 — 영문 인자 이름 → 로그 한글 키. 표 밖 키는 미출력.
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
    "masked": "가린룰",   # 룰 이름만. 걸린 값 미포함
    # briefing
    "tier": "등급", "skipped": "건너뜀", "narrative": "서술",
    # branch(메인 브리핑)
    "rows": "행수", "truncated": "잘림",
}

#: 값 표 — 판정 토큰 → 로그 한글 값. 표에 없는 문자열(식별자)은 그대로 출력.
STEP_VALUES: dict[Any, str] = {
    True: "예", False: "아니오",
    # 도구 결과 · 턴 결과
    "found": "성공", "miss": "없음", "failed": "실패",
    "answer": "답변", "clarify": "되묻기", "llm_down": "LLM다운", "tool_failed": "도구실패",
    # 판정 등급
    "ask": "되묻기", "assume": "전제", "none": "대상없음", "n/a": "없음",
    # 연계 상태
    "sent": "발송", "stubbed": "스텁", "ok": "성공", "blocked": "차단",
    "not_connected": "미연결", "unknown": "미상",
    # 승낙 응답
    "accept": "승낙", "reject": "거절", "unclear": "불명확",
    # 쪽지 초안 턴
    "edited": "초안수정", "not_edit": "수정아님", "pasted": "붙여넣기", "screened": "검사걸림",
    "echo": "지시문옮김", "down": "LLM실패",
    # 폴백
    "raw_evidence": "근거원문",
}


def _show(value: Any) -> str:
    """값 → 로그 문자열. 판정 토큰은 표 변환, 공백 포함 문자열은 따옴표."""
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
         detail: dict[str, Any] | None = None, **facts: Any) -> None:
    """단계 한 줄 기록. None 값 키 미출력. `detail` 은 로그 제외, `journal()` 에만 기록.

        observability.step("tool", "fact", result="found", candidates=3, picked=1)
    """
    try:
        started = _STARTED.get()
        clock = f"{time.monotonic() - started:5.1f}s" if started is not None else "     -"
        pairs = " ".join(f"{STEP_KEYS[k]}={_show(v)}" for k, v in facts.items()
                         if v is not None and k in STEP_KEYS)
        body = f"{ident} {pairs}".strip() if ident else pairs
        _step_log.log(level, "[%s] %s %-11s %s",
                      _REQUEST_ID.get() or "-", clock, stage, body)
        record = _JOURNAL.get()
        if record is not None:
            record.append({
                "at": wall_clock(),
                "elapsed_ms": round((time.monotonic() - started) * 1000) if started is not None else None,
                "stage": stage, "ident": ident, "level": logging.getLevelName(level),
                "text": body,
                "facts": {k: _plain_value(v) for k, v in {**facts, **(detail or {})}.items()
                          if v is not None},
            })
    except Exception:                                     # noqa: BLE001 — 기록은 흐름을 막지 않는다
        pass


class Trace:
    """트레이스 핸들. `trace()` 로만 생성."""

    __slots__ = ("id", "name", "_fields", "_live")

    def __init__(self, trace_id: str, name: str, live: bool) -> None:
        self.id = trace_id
        self.name = name
        self._fields: dict[str, Any] = {}
        self._live = live

    def update(self, **fields: Any) -> None:
        """트레이스에 얹을 값(output·metadata·tags·user_id …). 닫힐 때 전송."""
        if self._live:
            self._fields.update(fields)


#: 관측 꺼짐 시 핸들(update() no-op).
_NULL_TRACE = Trace("", "", live=False)


def _ensure_trace_id(name: str) -> str:
    """열린 트레이스 id. 없으면 새로 생성(스크립트 직접 호출 대비)."""
    trace_id = _TRACE_ID.get()
    if trace_id is None:
        trace_id = uuid.uuid4().hex
        _emit("trace-create", {"id": trace_id, "name": name, "timestamp": _now()})
    return trace_id


@contextlib.contextmanager
def trace(name: str, *, input: Any = None, user_id: str | None = None,
          session_id: str | None = None, metadata: dict | None = None,
          tags: list[str] | None = None) -> Iterator[Trace]:
    """트레이스 열기. 블록 안 LLM 호출 전부 여기에 묶임. 예외는 ERROR 기록 후 재전파."""
    if not enabled():
        yield _NULL_TRACE
        return

    # environment 는 메타데이터로 — 수집 API 최상위 필드는 서버 판마다 다름
    metadata = {"environment": conf().environment, **(metadata or {})}
    handle = Trace(uuid.uuid4().hex, name, live=True)
    body: dict[str, Any] = {"id": handle.id, "name": name, "timestamp": _now()}
    _put_if(body, "release", conf().release or None)
    _put_if(body, "input", _payload(input))
    _put_if(body, "userId", user_id)
    _put_if(body, "sessionId", session_id)
    _put_if(body, "metadata", metadata)
    _put_if(body, "tags", tags)
    _emit("trace-create", body)   # 시작 시점 전송 — 도중 프로세스 종료 대비

    # 새 트레이스는 최상위(바깥 span 무시). 경과초 시계도 시작(요청 컨텍스트 없는 브리핑용)
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
        # 여는 이벤트 값을 닫는 이벤트에도 재전송 — 누락 필드 병합 방식이 서버 판마다 다름
        _put_if(closing, "userId", fields.pop("user_id", None) or user_id)
        _put_if(closing, "sessionId", fields.pop("session_id", None) or session_id)
        _put_if(closing, "tags", fields.pop("tags", None) or tags)
        meta.update(fields)
        closing["metadata"] = meta
        _emit("trace-create", closing)                   # 같은 id — Langfuse 가 병합


def current_trace_id() -> str | None:
    """열린 트레이스 id. 없으면 None."""
    return _TRACE_ID.get()


def tag(kind: str, value: Any) -> list[str]:
    """«분류:값» 태그 목록(대시보드 필터용). 값 없으면 빈 목록."""
    return [f"{kind}:{value}"] if value else []


def customer_ref(customer_id: str | None, name: str | None,
                 conditions: list[str] | None = None) -> dict:
    """관측용 고객 식별(user_id·태그·메타데이터) 한 벌. 브리핑·대화 턴 공용 꼴.

    - user_id 는 «이름(id)» — Users 목록이 userId 문자열만 표시
    - `conditions` 는 호출부가 넘긴 타겟 요건(여기서 판정 금지)
    - `LANGFUSE_CAPTURE_CONTENT=0` 이면 이름·요건 제외, id 만 유지
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


@contextlib.contextmanager
def span(name: str, *, input: Any = None, metadata: dict | None = None) -> Iterator[Trace]:
    """LLM 외 실행 단계 span 열기. 하위 호출은 이 밑에 붙음. update() 의 기타 키는 메타데이터."""
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
    """핸들 값을 전용 필드·메타데이터로 분리, latency_ms 추가."""
    fields = dict(handle._fields)
    meta.update(fields.pop("metadata", None) or {})
    meta["latency_ms"] = int((time.time() - started) * 1000)
    return fields, meta


def score(name: str, value: bool | int | float | str, *, comment: str | None = None) -> None:
    """열린 트레이스에 점수 한 건(bool→BOOLEAN, 숫자→NUMERIC, 그 외 CATEGORICAL). 로그 미출력."""
    if not enabled():
        return
    try:
        trace_id = _TRACE_ID.get()
        if trace_id is None:
            return
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


def record_generation(name: str, *, model: str | None = None, input: Any = None,
                      output: Any = None, usage: dict | None = None,
                      start: float | None = None, end: float | None = None,
                      parameters: dict | None = None, metadata: dict | None = None,
                      error: str | None = None) -> None:
    """LLM 호출 한 건 기록(성공·실패 공통). start·end 는 `time.time()`. 트레이스 없으면 새로 생성."""
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
