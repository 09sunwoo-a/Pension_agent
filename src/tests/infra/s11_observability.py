"""observability — Langfuse 관측

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

import json
import os

from tests.infra._common import check
from tests.infra.s10_llm_retry import _FakeResp, _http_error, _llm   # 앞 구간의 것을 이어받는다


# ─────────────────────────────────────────────────────────────
# observability — Langfuse 관측
#
# 고정하는 것은 셋이다.
#   ① 키가 없으면 통째로 꺼진다 — 테스트·시연이 키 없이 그대로 돈다.
#   ② 켜지면 LLM 호출 한 건이 이벤트 한 건으로 나가고, 트레이스 안에서 부른 호출은
#      같은 traceId 로 묶인다(브리핑 11연쇄·대화 4~7회를 되짚는 근거가 이 묶음이다).
#   ③ 전송이 깨져도 LLM 호출은 성공한다 — 관측은 부산물이지 기능이 아니다.
# ─────────────────────────────────────────────────────────────

from pension_agent import observability as _obs  # noqa: E402

_obs.reset()
check(not _obs.enabled(), "observability: 키가 없으면 꺼져 있다")
with _obs.trace("noop") as _null:
    _null.update(output="버려진다")
check(_obs.current_trace_id() is None, "observability: 꺼져 있으면 트레이스 id 도 없다")

# «상태» 로그 — score() 는 Langfuse 가 꺼져 있어도 로그 한 줄을 남긴다. 행내 컨테이너에는
# 키가 없어 대시보드가 꺼져 있고, 그때 «에이전트가 무엇을 했나»를 보는 자리가 Grafana 다.
import logging as _logging  # noqa: E402
_state_records: list = []


class _StateCapture(_logging.Handler):
    def emit(self, record):
        _state_records.append(record)


_agent_log = _logging.getLogger("agent")
_agent_log.addHandler(_StateCapture())
_agent_log.setLevel(_logging.INFO)
try:
    _obs.score("evidence_count", 7)
    _obs.score("compose_passed", True)
    _obs.score("compose_passed", False, comment="수치 '1,485,000' 이 근거에 없음")
    _obs.score("tool_outcome", "miss", comment="pitch · 질의 '수수료 반론'")
    _obs.score("tool_outcome", "failed", comment="fact · 사유 FileNotFoundError")
    _obs.score("action_outcome", "not_connected",
               comment="send_memo · 받는 사람 1명 · WorkB 클라이언트가 주입되지 않았습니다 — 본문만 생성했습니다")
    _obs.score("action_outcome", "sent", comment="send_memo · 받는 사람 1명 · 쪽지를 발송했습니다")
    _obs.score("action_outcome", "blocked", comment="open_lms_screen · 더미")
    _obs.score("turn_outcome", "answer")
    _obs.score("turn_outcome", "tool_failed", comment="x")
    with _obs.request_id("ab12cd34"):
        _obs.score("judge_verdict", "n/a")
    _obs.score("compose_passed", False, comment="가" * 300)
    _msgs = [(r.levelno, r.getMessage()) for r in _state_records]
    check(len(_msgs) == 12, "observability: score 는 꺼져 있어도 한 건마다 로그 한 줄", str(len(_msgs)))
    check(_msgs[0] == (_logging.INFO, "[-] 상태 evidence_count=7"),
          "observability: 상태 줄 형식 — [요청id] 상태 이름=값", str(_msgs[0]))
    check(_msgs[1][1].endswith("compose_passed=true") and _msgs[1][0] == _logging.INFO,
          "observability: bool 은 true/false 로 찍힌다", str(_msgs[1]))
    check(_msgs[2] == (_logging.WARNING, "[-] 상태 compose_passed=false · 수치 '1,485,000' 이 근거에 없음"),
          "observability: 검증 폐기는 WARNING · comment 가 뒤에 붙는다", str(_msgs[2]))
    check(_msgs[3][0] == _logging.INFO and _msgs[4][0] == _logging.WARNING,
          "observability: 도구 miss 는 INFO, failed 는 WARNING", str(_msgs[3:5]))
    check(_msgs[5][0] == _logging.WARNING and "WorkB 클라이언트가 주입되지 않았습니다" in _msgs[5][1],
          "observability: 연계 not_connected 는 WARNING 에 detail 이 실린다", str(_msgs[5]))
    check(_msgs[6][0] == _logging.INFO and _msgs[7][0] == _logging.WARNING,
          "observability: 연계 sent 는 INFO, blocked 는 WARNING", str(_msgs[6:8]))
    check(_msgs[8][0] == _logging.INFO and _msgs[9][0] == _logging.WARNING,
          "observability: turn_outcome answer 는 INFO, tool_failed 는 WARNING", str(_msgs[8:10]))
    check(_msgs[10][1].startswith("[ab12cd34] 상태 judge_verdict=n/a"),
          "observability: request_id 컨텍스트 안에서는 요청 id 가 붙는다", str(_msgs[10]))
    check(_obs.current_request_id() is None, "observability: 컨텍스트를 나오면 요청 id 가 지워진다")
    check(len(_msgs[11][1]) < 200 and _msgs[11][1].endswith("…"),
          "observability: comment 는 STATE_COMMENT_MAX 에서 자른다", str(len(_msgs[11][1])))
finally:
    _agent_log.handlers.clear()

_saved_env = {k: os.environ.get(k) for k in
              ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST",
               "LANGFUSE_CAPTURE_CONTENT")}
_saved_llm2 = (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.urllib.request.urlopen)
_sent: list[dict] = []


def _fake_urlopen(req, timeout=None):
    """LLM 게이트웨이와 Langfuse 수집 API 를 URL 로 갈라 받는다.

    둘 다 같은 `urllib.request.urlopen` 을 쓰므로(같은 모듈 객체) 한 자리에서 갈라야 한다.
    """
    if "langfuse" in req.full_url:
        _sent.append(json.loads(req.data.decode("utf-8")))
        return _FakeResp()
    return _FakeResp()


try:
    os.environ.update({"LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test",
                       "LANGFUSE_HOST": "https://langfuse.invalid"})
    os.environ.pop("LANGFUSE_CAPTURE_CONTENT", None)
    _obs.reset()
    _llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY = "genai", "http://fake", "k"
    _llm.urllib.request.urlopen = _fake_urlopen

    check(_obs.enabled(), "observability: 키가 있으면 켜진다")

    with _obs.trace("test.turn", input="질문", session_id="sess-x") as _tr:
        _inside = _obs.current_trace_id()
        _llm.generate("q", name="test.call")
        _tr.update(output="답")
    check(_obs.flush(timeout=5.0), "observability: flush 가 큐를 비운다")

    _events = [e for batch in _sent for e in batch["batch"]]
    _gens = [e for e in _events if e["type"] == "generation-create"]
    _traces = [e for e in _events if e["type"] == "trace-create"]
    check(len(_gens) == 1, "observability: LLM 호출 한 건이 generation 한 건으로 나간다",
          str(len(_gens)))
    check(bool(_gens) and _gens[0]["body"]["traceId"] == _inside,
          "observability: 트레이스 안의 호출은 그 트레이스에 묶인다")
    check(bool(_gens) and _gens[0]["body"]["name"] == "test.call",
          "observability: 호출부가 준 이름이 그대로 실린다")
    check(bool(_gens) and _gens[0]["body"]["input"] == "q"
          and _gens[0]["body"]["output"] == "답",
          "observability: 프롬프트와 응답이 실린다")
    check(bool(_traces) and all(t["body"]["id"] == _inside for t in _traces)
          and any(t["body"].get("output") == "답" for t in _traces),
          "observability: 트레이스가 열릴 때와 닫힐 때 같은 id 로 나간다")
    check(all(t["body"].get("sessionId") == "sess-x" for t in _traces),
          "observability: 상담 세션 id 가 여는·닫는 이벤트 양쪽에 실린다",
          str([t["body"].get("sessionId") for t in _traces]))

    # span 중첩 — 트레이스가 평면이 아니라 실행 구조를 닮은 트리가 된다
    _sent.clear()
    with _obs.trace("test.turn2") as _tr2:
        with _obs.span("tool:fact", input="세액공제") as _sp:
            _llm.generate("q", name="test.in_span")
            _sp.update(output="카드 1건", found=True)
        _obs.score("compose_passed", True)
        _obs.score("retries", 2)
        _obs.score("outcome", "answer")
    _obs.flush(timeout=5.0)
    _ev = [e for batch in _sent for e in batch["batch"]]
    _span = next((e["body"] for e in _ev if e["type"] == "span-create"), None)
    _gen2 = next((e["body"] for e in _ev if e["type"] == "generation-create"), None)
    _scores = [e["body"] for e in _ev if e["type"] == "score-create"]
    check(bool(_span) and _span["name"] == "tool:fact" and _span["output"] == "카드 1건",
          "observability: span 이 이름과 결과를 싣는다", str(_span))
    check(bool(_span) and _span["metadata"].get("found") is True,
          "observability: span 에 얹은 값은 메타데이터로 실린다")
    check(bool(_gen2) and _gen2.get("parentObservationId") == (_span or {}).get("id"),
          "observability: span 안의 LLM 호출은 그 span 밑에 붙는다")
    check(bool(_span) and _span["traceId"] == _tr2.id,
          "observability: span 이 열려 있는 트레이스에 묶인다")
    check({(s["name"], s["value"], s["dataType"]) for s in _scores} >=
          {("compose_passed", 1, "BOOLEAN"), ("retries", 2, "NUMERIC"),
           ("outcome", "answer", "CATEGORICAL")},
          "observability: 점수가 bool·숫자·문자열별로 형을 갈라 나간다", str(_scores))
    # LLM 호출마다 입력 크기가 점수로 남고(llm._observe — gemma 부담이 어느 호출에서 오는지
    # 볼 자리), 그 점수는 호출이 일어난 span 밑에 붙는다.
    _size = next((s for s in _scores if s["name"] == "prompt_chars"), None)
    check(bool(_size) and _size["value"] == len("q") and _size["dataType"] == "NUMERIC"
          and _size.get("observationId") == (_span or {}).get("id")
          and _size.get("comment") == "test.in_span",
          "observability: LLM 호출마다 prompt_chars 점수가 그 span 밑에 남는다", str(_size))
    check(all(s["traceId"] == _tr2.id for s in _scores),
          "observability: 점수가 그 트레이스에 붙는다")

    # 고객 표기 — Users 목록이 user_id 문자열 하나만 보여주므로 이름을 거기 넣는다
    _who = _obs.customer_ref("171203-4815062", "김서연", ["isa", "tax"])
    check(_who["user_id"] == "김서연(171203-4815062)",
          "observability: user_id 에 이름과 id 가 함께 실린다", str(_who["user_id"]))
    check(_who["tags"] == ["고객:김서연", "요건:isa", "요건:tax"],
          "observability: 고객 이름·성립 요건이 태그로 붙는다", str(_who["tags"]))
    check(_who["metadata"] == {"customer_id": "171203-4815062", "customer": "김서연",
                               "conditions": ["isa", "tax"]},
          "observability: 같은 값이 메타데이터에도 실린다", str(_who["metadata"]))
    check(_obs.customer_ref(None, None) == {"user_id": None, "metadata": {}, "tags": []},
          "observability: 고객이 없으면 아무것도 붙지 않는다")

    # 트레이스가 없으면 점수는 나가지 않는다 — 붙을 데가 없는 점수는 찾을 방법이 없다
    _sent.clear()
    _obs.score("orphan", 1)
    _obs.flush(timeout=5.0)
    check(not [e for batch in _sent for e in batch["batch"]],
          "observability: 트레이스 밖 점수는 보내지 않는다")

    # 본문 차단 — 개인정보를 외부로 내보내지 않는 스위치
    _sent.clear()
    os.environ["LANGFUSE_CAPTURE_CONTENT"] = "0"
    _obs.reset()
    _secret = "고객 홍길동의 잔액"
    _llm.generate(_secret, name="test.masked")
    _obs.flush(timeout=5.0)
    _masked = [e for batch in _sent for e in batch["batch"] if e["type"] == "generation-create"]
    # 그 스위치는 «개인정보를 내보내지 않는다»는 약속이다. 본문만 가리고 이름을 user_id·
    # 태그로 내보내면 약속이 거짓이 된다 — id 만 남고 이름은 전부 빠져야 한다.
    # 요건 코드는 이름이 아니지만 «그 고객의 상태»라 함께 가린다.
    _masked_who = _obs.customer_ref("171203-4815062", "김서연", ["isa", "tax"])
    check(_masked_who == {"user_id": "171203-4815062",
                          "metadata": {"customer_id": "171203-4815062"}, "tags": []},
          "observability: CAPTURE_CONTENT=0 이면 고객 이름·요건이 전부 빠진다",
          str(_masked_who))

    check(bool(_masked)
          and _masked[0]["body"]["input"] == {"omitted": True, "chars": len(_secret)},
          "observability: CAPTURE_CONTENT=0 이면 본문 대신 길이만 나간다",
          str(_masked[0]["body"]["input"]) if _masked else "이벤트 없음")

    # 전송이 깨져도 LLM 호출은 산다
    os.environ.pop("LANGFUSE_CAPTURE_CONTENT", None)
    _obs.reset()
    _before = _obs.stats()["failed"]

    def _urlopen_langfuse_down(req, timeout=None):
        if "langfuse" in req.full_url:
            raise _http_error(500)
        return _FakeResp()

    _llm.urllib.request.urlopen = _urlopen_langfuse_down
    check(_llm.generate("q", name="test.down") == "답",
          "observability: 관측 전송이 깨져도 LLM 호출은 성공한다")
    _obs.flush(timeout=5.0)
    check(_obs.stats()["failed"] > _before,
          "observability: 전송 실패는 예외 대신 stats 에 쌓인다")
    # 삼키되 침묵하지는 않는다 — 원인이 남아야 «전송이 깨졌다»와 «안 켜졌다»가 갈린다.
    check("HTTP Error 500" in (_obs.last_error() or ""),
          "observability: 실패 원인이 last_error 에 남는다", str(_obs.last_error()))
finally:
    for _k, _v in _saved_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
    _obs.reset()
    (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.urllib.request.urlopen) = _saved_llm2
