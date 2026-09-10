"""HTTP 진입점(main.py) 회귀 테스트 — 플랫폼 I/O 스키마를 지키는가.

행내 GenAI 플랫폼은 요청·응답 형태를 고정해 두었다(skills/genai-platform-agent-dev/refs/
genai-platform.md «API I/O 스키마 (고정)»). 이 스키마가 어긋나면 에이전트가 아무리 잘 답해도 플랫폼이 못 읽는다 —
그런데 그 사실은 **행내에 들고 가서야** 드러난다. 여기서 미리 잡는다.

  · input_value 는 JSON «문자열» 이고, 그 안에 message·x_client_user 가 있어야 한다
  · 필수 키가 없으면 422 (500 이 아니다 — 호출자가 무엇이 빠졌는지 알아야 한다)
  · 응답은 SSE 프레임의 CHUNK 이고, 각 content 는 JSON 이벤트 하나다(main.py «출력 형식»):
    progress… → answer → [action | clarify] → sources → followups → done. 출처는 «항상»
    실린다(0건이면 빈 목록) — 근거를 못 보여주면 이 에이전트의 답이 아니다
  · 진행 표시는 항상, 답변보다 **먼저** 흐른다
  · x_client_user 가 에이전트(graph.ask)까지 실제로 도달한다 — 거기서 이 턴의 모든
    LLM 호출 주체가 된다(그 배선 자체는 test_consult_agent 가 본다)

LLM 은 부르지 않는다 — graph.ask 를 갈아끼워 위 계약만 본다.

실행: python -m tests.test_api   (src/ 에서)
"""

from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

import main
from pension_agent import config as _cfg
from pension_agent import llm, observability
from pension_agent.strategy_agent import briefing_store

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))


def _body(**payload) -> dict:
    return {"input_value": json.dumps(payload, ensure_ascii=False), "message_hists": None}


def _events_in(text: str) -> list[dict]:
    """content 문자열 안의 JSON 이벤트들 — 프론트 파서와 같은 규칙(연달아 있어도 읽는다)."""
    dec = json.JSONDecoder()
    out, i = [], 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return out
        try:
            obj, i = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            out.append(obj)


def _events(resp, sse: bool = True) -> list[dict]:
    """스트림 응답 → 이벤트 목록. CHUNK 하나에 이벤트 하나여야 한다."""
    out = []
    for line in resp.text.splitlines():
        if not line.strip():
            continue
        if sse:
            # 게이트웨이는 SSE 파서다 — `data:` 없는 줄은 버린다(main.py 머리말 «응답 프레이밍»).
            check(line.startswith("data: "), "스트림 이벤트는 SSE 프레임(data: …)이다", line[:40])
            line = line[len("data:"):]
        d = json.loads(line)
        check(d.get("event") == "CHUNK", "응답 이벤트는 CHUNK 뿐이다", str(d.get("event")))
        evs = _events_in(d["content"])
        check(len(evs) == 1 and "type" in evs[0], "CHUNK 하나에 JSON 이벤트 하나다", d["content"][:60])
        out.extend(evs)
    return out


def _types(events: list[dict]) -> list[str]:
    return [e.get("type") for e in events]


_seen: dict = {}
ANSWER = "첫 줄입니다.\n둘째 줄에는 «인용»과 숫자 12.4% 가 있습니다.\n셋째 줄."
SOURCES = [
    {"id": "kb_fact_001", "doc": "연금사업부 업무가이드", "title": "IRP 수수료 체계",
     "url": None, "score": 0.82, "page": None, "role": "근거"},
    {"id": "kb_pitch_009", "doc": "스타런 교육자료", "title": "수수료 반론 대응",
     "url": None, "score": None, "page": None, "role": "주의"},
]
PROGRESS = ["질문을 이해하고 있어요", "제도·상품 수치를 찾고 있어요", "답변을 쓰고 있어요"]
FOLLOWUPS = ["이 고객한테 안내할 만한 세미나나 이벤트 있어?", "이 고객 지금 현황은 어때?"]
ACTION = {"kind": "lms", "label": "75-08-110 발송 화면 열기",
          "prompt": "«금리 변화기» 안내 문구로 75-08-110 발송 화면 열기, 연계해드릴까요? (네 / 아니오)",
          "params": {"customer_id": "c"}, "html": "<b>내부</b>"}
CLARIFY = {"question": "어느 계좌 기준으로 안내할까요?", "options": ["개인형IRP", "연금저축"]}


def _fake_ask(question, history=None, **kw):
    _seen.clear()
    _seen.update(kw, question=question, history=history)
    cb = kw.get("on_progress")
    if cb:
        for line in PROGRESS:
            cb(line)
    # 실물처럼 «상태» 한 건 — 진입점이 연 request_id 컨텍스트가 스레드 안까지 따라오는지 본다.
    observability.score("evidence_count", 2)
    # 실물처럼: 추천질문은 answer 끝에 블록으로도 붙고 followups 로도 온다(graph.ask).
    out = {"answer": ANSWER + "\n\n" + main.consult_graph.FOLLOWUP_HEADER + "\n"
           + "\n".join(f"· {q}" for q in FOLLOWUPS),
           "sources": SOURCES, "followups": FOLLOWUPS, "intent": "situation",
           "pending_action": None, "clarify": None,
           "history": [*(history or []), {"question": question, "tools": []}]}
    if question == "연계":
        out.update(answer=ANSWER + "\n\n— " + ACTION["prompt"], followups=[], pending_action=ACTION)
    if question == "되묻기":
        out.update(answer=CLARIFY["question"] + "\n\n· 개인형IRP\n· 연금저축", followups=[], clarify=CLARIFY)
    if question == "근거없음":
        out.update(sources=[])
    return out


_saved_ask = main.consult_graph.ask
main.consult_graph.ask = _fake_ask
client = TestClient(main.app)

# 로그 캡처 — 루트에 핸들러를 하나 더 단다(main 이 잡아 둔 stdout 핸들러는 그대로).
_captured: list[logging.LogRecord] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _captured.append(record)


logging.getLogger().addHandler(_Capture())

try:
    # ── /health ──────────────────────────────────────────────
    r = client.get("/health")
    h = r.json()
    check(r.status_code == 200 and h["status"] == "ok", "/health 200", str(r.status_code))
    check("api_key_set" in h["llm"] and "sk-" not in r.text,
          "/health 는 키 «설정 여부»만 내보내고 값은 내보내지 않는다", r.text[:120])
    check(h["rate_gate"]["max_concurrency"] == llm.MAX_CONCURRENCY,
          "/health 가 429 게이트 설정을 보여준다", str(h.get("rate_gate")))
    # 「.env 를 고쳤는데 먹었나」가 화면에서 끝나야 한다 — 안 먹은 것과 안 듣는 것은
    # 처방이 정반대다(버킷을 나누는 설정이 그렇다).
    check(h["rate_gate"].get("client_user_spread") == llm.CLIENT_USER_SPREAD,
          "/health 가 쿼터 버킷 분산 설정을 보여준다", str(h.get("rate_gate")))
    # «키를 넣었는데 왜 안 되나»의 첫 질문은 어느 파일이 읽혔나다.
    check("exists" in h["env"], "/health 가 설정 파일 유무를 보여준다", str(h.get("env")))
    # 행내 .env 에는 URL 이 두 벌(trnn·serv)이라, 배포된 컨테이너가 train URL 을 보고 있는
    # 사고를 여기서 바로 잡아야 한다.
    check(h["llm"].get("stage") in ("train", "serving"),
          "/health 가 어느 단계(ENV_PATH)의 URL 을 읽었는지 보여준다", str(h["llm"].get("stage")))
    # 행내 MCP(쪽지 발송)가 붙었나. 안 붙어 있으면 쪽지는 보내지 않고 «미연결»로 답하는데,
    # 화면에는 초안까지 똑같이 뜨므로 승낙 뒤에야 드러난다 — 여기서 먼저 갈려야 한다.
    # 여기서도 키 값은 나가지 않는다(설정 «여부»와 무엇이 비었는지의 이름까지).
    check(set(h["mcp"]) >= {"configured", "missing", "servers"}
          and isinstance(h["mcp"]["configured"], bool),
          "/health 가 행내 MCP 연결 상태를 보여준다", str(h.get("mcp")))

    # 미리 만들어 둔 브리핑을 지금 읽고 있나. 저장소는 실패가 전부 조용해서(꺼짐 · 지문
    # 불일치 · 쓰기 불가) 어느 쪽이든 답변은 정상으로 나가고 «느리다»로만 보인다 —
    # 배포된 컨테이너에서 그 셋을 로그 없이 가르는 수단이 여기 말고 없다.
    # 체크아웃에는 briefing_cache/ 가 있으므로(커밋한다) 여기서는 켜져 있는 것이 정상이다.
    check(h["briefing_cache"]["enabled"] is True and h["briefing_cache"]["dir"].endswith(
        "briefing_cache"), "/health: 저장소가 어디를 보고 있는지 말한다", str(h.get("briefing_cache")))
    _saved_cache_dir = _cfg.BRIEFING_CACHE_DIR
    try:
        # 디렉터리가 없으면 통째로 꺼진 것이다 — 그때는 지문 계산까지 가지 않는다.
        _cfg.BRIEFING_CACHE_DIR = _saved_cache_dir / "__none__"
        off = client.get("/health").json()["briefing_cache"]
        check(off["enabled"] is False and "stored" not in off,
              "/health: 디렉터리가 없으면 꺼졌다고만 말한다(지문 계산도 안 한다)", str(off))
    finally:
        _cfg.BRIEFING_CACHE_DIR = _saved_cache_dir
    with tempfile.TemporaryDirectory() as _tmp:
        try:
            _cfg.BRIEFING_CACHE_DIR = Path(_tmp)
            # 한 건은 지금 지문, 한 건은 낡은 지문 — «있다»와 «읽힌다»는 다른 수다.
            (Path(_tmp) / "now.json").write_text(json.dumps(
                {"fingerprint": briefing_store.fingerprint(), "briefing": {}}), encoding="utf-8")
            (Path(_tmp) / "old.json").write_text(json.dumps(
                {"fingerprint": "어제만든것", "briefing": {}}), encoding="utf-8")
            bc = client.get("/health").json()["briefing_cache"]
            check(bc["enabled"] is True and bc["stored"] == 2 and bc["usable"] == 1,
                  "/health: 저장된 건수와 «지금 지문으로 읽히는» 건수를 갈라 보여준다", str(bc))
            # 파일시스템이 읽기전용이면 런타임 저장이 조용히 실패한다(save 가 예외를 삼킨다).
            # 그러면 재기동할 때마다 고객당 LLM 11 회를 처음부터 다시 치른다.
            check(bc["writable"] is True,
                  "/health: 런타임 저장이 가능한지 보여준다", str(bc))
        finally:
            _cfg.BRIEFING_CACHE_DIR = _saved_cache_dir

    # 행내 첫 연결에서 실제로 걸린 자리 — 인증도 쿼터도 아니고 DNS 였다. LLM Gateway 의
    # base_url 은 *.svc.cluster.local 이라 그 쿠버네티스 클러스터 안에서만 풀리는데,
    # 실패는 첫 대화 턴에 가서야 «LLM 호출이 실패했습니다»로 나타나 원인이 안 보인다.
    _saved_base = llm.BASE_URL
    try:
        llm.BASE_URL = "http://litellm.aidc-prod.svc.cluster.local:4000"
        hh = client.get("/health").json()["llm"]
        check(hh["host"] == "litellm.aidc-prod.svc.cluster.local" and hh["resolves"] is False,
              "/health 가 «이름이 안 풀린다»를 첫 턴 전에 알려준다", str(hh))
        llm.BASE_URL = "http://localhost:8000"
        hh = client.get("/health").json()["llm"]
        check(hh["resolves"] is True, "/health: 풀리는 호스트는 참으로 답한다", str(hh))
    finally:
        llm.BASE_URL = _saved_base

    # ── 필수 키 검증 ─────────────────────────────────────────
    r = client.post("/chat", json=_body(message="안녕"))
    check(r.status_code == 422 and "x_client_user" in r.text,
          "x_client_user 가 없으면 422", f"{r.status_code} {r.text[:80]}")
    check(any(rec.levelno == logging.WARNING and "422" in rec.getMessage()
              and "x_client_user" in rec.getMessage() for rec in _captured),
          "422 는 무엇이 빠졌는지 WARNING 으로 남긴다(접속 로그엔 상태코드뿐이다)",
          str([rec.getMessage() for rec in _captured][-1:]))

    r = client.post("/chat", json=_body(x_client_user="emp-1"))
    check(r.status_code == 422 and "message" in r.text,
          "message 가 없으면 422", f"{r.status_code} {r.text[:80]}")

    r = client.post("/chat", json={"input_value": "이건 JSON 이 아니다", "message_hists": None})
    check(r.status_code == 422, "input_value 가 JSON 문자열이 아니면 422", str(r.status_code))
    # 거부된 요청도 모양을 남긴다 — 행내에서 4턴째에 이 422 가 났을 때 게이트웨이가 무엇을
    # 보냈는지(빈 문자열인지 · 질문 원문인지) 로그로 알 수 없었다.
    rejected = next((rec.getMessage() for rec in reversed(_captured)
                     if "거부된 요청 모양" in rec.getMessage()), "")
    check('"input_value_raw"' in rejected and '"len": 13' in rejected
          and "이건 JSON 이 아니다" in rejected and '"headers"' in rejected,
          "422 로 거부된 요청은 헤더와 input_value 원문 앞부분을 WARNING 으로 남긴다", rejected[:300])

    r = client.post("/chat", json={"input_value": "", "message_hists": None})
    check(r.status_code == 422, "input_value 가 빈 문자열이면 422", str(r.status_code))
    rejected = next((rec.getMessage() for rec in reversed(_captured)
                     if "거부된 요청 모양" in rec.getMessage()), "")
    check('"len": 0' in rejected, "빈 input_value 는 길이 0 으로 남는다", rejected[:300])

    r = client.post("/chat", json={"input_value": json.dumps(["배열"]), "message_hists": None})
    check(r.status_code == 422, "input_value 가 JSON «객체»가 아니면 422", str(r.status_code))

    # ── 정상 턴 ──────────────────────────────────────────────
    r = client.post("/chat", json=_body(message="IRP 수수료 질문", x_client_user="emp-0417"))
    check(r.status_code == 200, "정상 요청은 200", str(r.status_code))
    check(r.headers["content-type"].startswith("text/event-stream"),
          "Content-Type 은 text/event-stream", r.headers.get("content-type", ""))
    check(r.text.startswith("data: {") and "\n\n" in r.text,
          "SSE 프레임 — data: 로 시작하고 이벤트는 빈 줄로 끝난다", repr(r.text[:60]))
    evs = _events(r)
    check(_types(evs) == ["progress"] * 3 + ["answer", "sources", "followups", "done"],
          "이벤트 순서 — progress… → answer → sources → followups → done", str(_types(evs)))
    check([e["text"] for e in evs if e["type"] == "progress"] == PROGRESS,
          "진행 문구가 그대로, 답변보다 먼저 흐른다", str(evs[:3]))
    answer = next(e for e in evs if e["type"] == "answer")
    check(answer["text"] == ANSWER and answer.get("intent") == "situation",
          "answer.text 는 본문만이다 — 추천질문 블록은 떼어낸다 · intent 가 실린다", repr(answer["text"][-60:]))
    check(main.consult_graph.FOLLOWUP_HEADER not in answer["text"],
          "answer.text 에 추천질문 헤더가 남지 않는다")
    check(next(e for e in evs if e["type"] == "followups")["items"] == FOLLOWUPS,
          "추천질문은 followups.items 로 따로 간다", str(evs[-2]))
    src = next(e for e in evs if e["type"] == "sources")
    check(src["items"] == SOURCES,
          "근거는 구조 그대로(id·doc·title·url·score·page·role) 간다", str(src)[:120])
    check([s["role"] for s in src["items"]] == ["근거", "주의"],
          "role 로 «근거»와 «주의(지켜야 할 것)»를 가를 수 있다")
    check(_seen.get("on_progress") is not None, "진행 콜백을 넘긴다", str(_seen.get("on_progress")))
    # 행내에서 보인 로그가 접속 로그 한 줄뿐이었다 — 루트 로거에 핸들러가 없어서 log.info
    # 가 어디에도 안 나갔다. 요청 한 건이 «받음 → 진행 → 완료»로 묶여 찍히는지 본다.
    _logs = [r for r in _captured if r.name == "main"]
    _rid = next((m.split("]")[0][1:] for m in (r.getMessage() for r in _logs)
                 if m.startswith("[") and "요청 ·" in m), None)
    check(_rid and len(_rid) == 8, "요청 로그에 8자리 요청 id 가 붙는다", str(_rid))
    _mine = [r.getMessage() for r in _logs if _rid and r.getMessage().startswith(f"[{_rid}]")]
    check(any("요청 ·" in m and "x_client_user=emp-0417" in m and "'IRP 수수료 질문'" in m
              for m in _mine), "요청 로그에 호출자·질문 미리보기가 실린다", str(_mine[:1]))
    check([m for m in _mine if "진행" in m and PROGRESS[0] in m],
          "진행 단계가 로그에도 찍힌다", str(_mine[1:2]))
    check(any("완료" in m and "출처 2건" in m and "추천질문 2건" in m for m in _mine),
          "완료 로그에 소요시간·답변 길이·출처·추천질문 건수가 실린다", str(_mine[-1:]))
    _state = [r.getMessage() for r in _captured if r.name == "agent" and _rid and r.getMessage().startswith(f"[{_rid}]")]
    check(_state == [f"[{_rid}] 상태 evidence_count=2"],
          "에이전트 안의 «상태» 줄에 같은 요청 id 가 붙는다(워커 스레드까지 컨텍스트가 따라간다)", str(_state))
    check(all(r.levelno == logging.INFO for r in _logs if _rid and r.getMessage().startswith(f"[{_rid}]")),
          "정상 턴의 로그는 전부 INFO 다", str([r.levelname for r in _logs]))
    check(logging.getLogger().handlers, "루트 로거에 핸들러가 잡혀 있다(stdout → 수집기)",
          str(logging.getLogger().handlers))
    check(_seen.get("x_client_user") == "emp-0417",
          "x_client_user 가 에이전트까지 전달된다", str(_seen.get("x_client_user")))
    check(_seen.get("session_id") == "default" and _seen.get("customer_id") is None,
          "선택 키는 기본값으로 떨어진다", str(_seen))

    r = client.post("/chat", json=_body(
        message="이 고객 브리핑 요약해줘", x_client_user="emp-0417",
        customer_id="154821-4938201", session_id="S-1"))
    _events(r)
    check(_seen.get("customer_id") == "154821-4938201" and _seen.get("session_id") == "S-1",
          "customer_id·session_id 가 전달된다", str(_seen))

    # ── 사번(employee_id) ────────────────────────────────────
    # 쪽지의 수신자이자 발송 주체이고 상담이력에 «누가 상담했나»로 남는다. x_client_user
    # 는 쿼터 버킷 이름이라 사번이라는 보장이 없어서, 진입점은 **판정하지 않고 그대로**
    # 넘기고 사번 꼴 판정은 graph.employee_no 한 곳이 한다(두 곳이 판정하면 로그에 찍힌
    # 사번과 실제로 쪽지가 나가는 사번이 갈린다).
    r = client.post("/chat", json=_body(message="쪽지 보내줘", x_client_user="emp-0417",
                                        employee_id="3902172"))
    _events(r)
    check(_seen.get("employee_id") == "3902172",
          "input_value 의 employee_id 가 에이전트까지 전달된다", str(_seen.get("employee_id")))
    # x_client_user 는 사번 뒤에 접미(LLM 호출을 가르는 uuid 등)가 붙어 올 수 있다 —
    # 구분자로 이었으면 앞의 사번을 읽는다(workb.as_emp_no).
    check(main.consult_graph.employee_no("3902172", "emp-0417") == "3902172"
          and main.consult_graph.employee_no(
              None, "3902172-550e8400-e29b-41d4-a716-446655440000") == "3902172"
          and main.consult_graph.employee_no(None, "3902172") == "3902172"
          and main.consult_graph.employee_no(None, "emp-0417") is None
          and main.consult_graph.employee_no(None, "pension-agent") is None,
          "사번은 명시한 값이 먼저이고, x_client_user 에서는 앞 7자리를 읽는다")
    def _last_request_log() -> str:
        return next((m for m in reversed([r.getMessage() for r in _captured if r.name == "main"])
                     if "요청 ·" in m), "")

    check("emp_no=3902172" in _last_request_log(),
          "요청 로그가 이 턴의 사번을 남긴다 — 쪽지가 누구 앞으로 나갈지가 그 값이다",
          _last_request_log())

    r = client.post("/chat", json=_body(message="쪽지 보내줘", x_client_user="emp-0417"))
    _events(r)
    check(_seen.get("employee_id") is None and "emp_no=-" in _last_request_log(),
          "사번을 못 찾으면 «-» 로 남긴다 — 환경변수 폴백으로 떨어졌다는 뜻이다(위험 10)",
          _last_request_log())

    # 연계 제안 — 본문 끝 문장은 남고, action 이벤트가 버튼용으로 따로 간다(실행 인자는 안 실린다).
    evs = _events(client.post("/chat", json=_body(message="연계", x_client_user="emp-1")))
    check(_types(evs)[3:] == ["answer", "action", "sources", "followups", "done"],
          "연계 제안 턴은 answer 다음에 action 이 온다", str(_types(evs)))
    action = next(e for e in evs if e["type"] == "action")
    check(action.get("label") == ACTION["label"] and action.get("prompt") == ACTION["prompt"]
          and action.get("kind") == "lms" and "params" not in action and "html" not in action,
          "action 에는 kind·label·prompt 만(실행 인자·html 은 뺀다)", str(action))
    check(next(e for e in evs if e["type"] == "answer")["text"].endswith("— " + ACTION["prompt"]),
          "연계 제안 문장은 answer.text 의 마지막 문장으로 남는다")
    check(next(e for e in evs if e["type"] == "followups")["items"] == [],
          "연계 제안 턴에는 추천질문이 없다(빈 목록으로는 온다)")

    # 되묻기 — 선택지가 clarify 이벤트로 간다.
    evs = _events(client.post("/chat", json=_body(message="되묻기", x_client_user="emp-1")))
    clar = next((e for e in evs if e["type"] == "clarify"), None)
    check(clar == {"type": "clarify", **CLARIFY}, "되묻기 턴은 clarify 에 질문·선택지가 실린다", str(clar))

    # 근거 0건 — 이벤트는 그래도 온다(«근거 없음»을 화면이 말해야 한다).
    evs = _events(client.post("/chat", json=_body(message="근거없음", x_client_user="emp-1")))
    check(next(e for e in evs if e["type"] == "sources")["items"] == [],
          "근거가 0건이어도 sources 이벤트가 빈 목록으로 온다", str(_types(evs)))

    # ── 대화 맥락 — 게이트웨이 경로는 history 를 돌려줄 자리가 없어 진입점이 세션별로 맡아 둔다 ──
    from pension_agent.consult_agent import context_store
    context_store.clear()
    r = client.post("/chat", json=_body(message="첫 질문", x_client_user="emp-7", session_id="S-7"))
    _events(r)
    check(_seen.get("history") is None, "세션의 첫 턴은 맥락 없이 간다", str(_seen.get("history")))
    r = client.post("/chat", json=_body(message="그럼 안 된다고 하면요?", x_client_user="emp-7", session_id="S-7"))
    _events(r)
    check(_seen.get("history") == [{"question": "첫 질문", "tools": []}],
          "같은 (직원, 세션)의 다음 턴은 이전 턴의 history 를 이어받는다", str(_seen.get("history")))
    check(any("맥락=1턴(store)" in rec.getMessage() for rec in _captured),
          "요청 로그에 맥락이 몇 턴이고 어디서 왔는지 찍힌다",
          str([m for m in (rec.getMessage() for rec in _captured) if "맥락=" in m][-1:]))
    r = client.post("/chat", json=_body(message="다른 세션", x_client_user="emp-7", session_id="S-8"))
    _events(r)
    check(_seen.get("history") is None, "세션이 다르면 맥락이 섞이지 않는다", str(_seen.get("history")))
    r = client.post("/chat", json=_body(message="다른 직원", x_client_user="emp-8", session_id="S-7"))
    _events(r)
    check(_seen.get("history") is None, "직원이 다르면 같은 session_id 라도 맥락이 섞이지 않는다",
          str(_seen.get("history")))
    # 호출자가 Turn 형식을 실어 보내면 저장본보다 우선한다 — 프론트가 맥락을 들고 다니게 될 때 자리.
    r = client.post("/chat", json={**_body(message="셋째", x_client_user="emp-7", session_id="S-7"),
                                   "message_hists": [{"question": "프론트가 든 턴"}]})
    _events(r)
    check(_seen.get("history") == [{"question": "프론트가 든 턴"}],
          "message_hists 가 Turn 형식이면 저장본보다 우선한다", str(_seen.get("history")))
    # Turn 이 아닌 형식(OpenAI 식)은 버리고 저장본을 쓴다 — 넘기면 format_history 가 500 을 낸다.
    r = client.post("/chat", json={**_body(message="넷째", x_client_user="emp-7", session_id="S-7"),
                                   "message_hists": [{"role": "user", "content": "x"}]})
    _events(r)
    check(r.status_code == 200 and _seen.get("history") and
          all("question" in h for h in _seen["history"]),
          "message_hists 가 Turn 형식이 아니면 버리고 저장본을 쓴다(500 이 아니다)", str(_seen.get("history")))
    check(client.get("/health").json()["context_store"]["sessions"] >= 3,
          "/health 가 살아 있는 맥락 세션 수를 보여준다", str(client.get("/health").json().get("context_store")))

    # 저장소 자체 — 만료·상한·비우기.
    import time as _time
    context_store.clear()
    context_store.put("e", "s1", [{"question": "a"}])
    check(context_store.get("e", "s1") == [{"question": "a"}], "put 한 것을 get 으로 되찾는다")
    context_store.put("e", "s1", [])
    check(context_store.get("e", "s1") is None, "빈 history 를 put 하면 세션이 지워진다")
    _saved_mono = context_store.time.monotonic
    try:
        _now = [1000.0]
        context_store.time.monotonic = lambda: _now[0]
        context_store.put("e", "old", [{"question": "o"}])
        _now[0] += context_store.TTL_SEC + 1
        check(context_store.get("e", "old") is None, "TTL 이 지난 세션은 버린다")
        _saved_max = context_store.MAX_SESSIONS
        context_store.MAX_SESSIONS = 3
        for i in range(5):
            _now[0] += 1
            context_store.put("e", f"s{i}", [{"question": str(i)}])
        check(context_store.stats()["sessions"] == 3 and context_store.get("e", "s0") is None
              and context_store.get("e", "s4") is not None,
              "상한을 넘으면 가장 오래 안 쓴 세션부터 버린다", str(context_store.stats()))
        context_store.MAX_SESSIONS = _saved_max
    finally:
        context_store.time.monotonic = _saved_mono
        context_store.clear()

    # ── 비스트림 — 게이트웨이가 본문 전체를 json.loads 한다(행내 실측). JSON 하나로 답한다 ──
    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1"),
                    headers={"Accept": "application/json"})
    check(r.status_code == 200 and r.headers["content-type"].startswith("application/json"),
          "Accept: application/json 이면 JSON 하나로 답한다", r.headers.get("content-type", ""))
    body = r.json()
    ns_events = _events_in(body.get("content", ""))
    check(body.get("event") == "CHUNK" and _types(ns_events) == ["answer", "sources", "followups", "done"],
          "비스트림 JSON 의 content 에 같은 이벤트들이 연달아 실린다(진행은 뺀다)", str(_types(ns_events)))
    check(ns_events[0]["text"] == ANSWER and ns_events[1]["items"] == SOURCES,
          "비스트림 이벤트의 내용은 스트림과 같다", str(ns_events[0])[:80])
    check(_seen.get("on_progress") is not None,
          "비스트림에서도 진행 콜백(로그용)은 넘긴다", str(_seen.get("on_progress")))

    r = client.post("/chat", json={**_body(message="q", x_client_user="emp-1"), "isStream": False})
    check(r.headers["content-type"].startswith("application/json")
          and _types(_events_in(r.json()["content"]))[0] == "answer",
          "본문 최상위 isStream=false 도 비스트림 신호다", r.headers.get("content-type", ""))
    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1", stream=False))
    check(r.headers["content-type"].startswith("application/json"),
          "input_value 안의 stream=false 도 비스트림 신호다", r.headers.get("content-type", ""))

    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1"),
                    headers={"Accept": "*/*"})
    check(r.headers["content-type"].startswith("text/event-stream"),
          "Accept: */* 는 스트림(기본)이다", r.headers.get("content-type", ""))
    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1", stream=True))
    check(r.headers["content-type"].startswith("text/event-stream"),
          "stream=true 는 스트림이다", r.headers.get("content-type", ""))

    # 요청 모양 로그 — 게이트웨이가 무엇을 보내는지 보는 유일한 자리. 비밀 헤더 값은 가린다.
    r = client.post("/chat", json={**_body(message="q", x_client_user="emp-1"), "isStream": False},
                    headers={"x-openapi-token": "Bearer SECRET-1", "x-generative-ai-client": "cli"})
    shape = next(rec.getMessage() for rec in reversed(_captured) if "요청 모양" in rec.getMessage())
    check("SECRET-1" not in shape and '"x-openapi-token": "***"' in shape,
          "요청 모양 로그는 토큰 값을 가린다", shape[:200])
    check('"x-generative-ai-client": "cli"' in shape and '"body_extra": ["isStream"]' in shape
          and '"input_value_keys": ["message", "x_client_user"]' in shape and "응답=json" in shape,
          "요청 모양 로그에 헤더·본문 추가 키·input_value 키·응답 방식이 실린다", shape[:300])

    # 문서 형식(줄마다 JSON)으로 되돌리는 스위치.
    main.SSE_FRAMING = False
    try:
        r = client.post("/chat", json=_body(message="q", x_client_user="emp-1"))
        check(r.text.startswith("{") and _types(_events(r, sse=False))[-1] == "done",
              "CHAT_SSE_FRAMING=0 이면 문서의 «줄마다 JSON» 형식이다", repr(r.text[:40]))
    finally:
        main.SSE_FRAMING = True

    # ── 계약 문서와 코드가 갈리지 않는다 — client/README.md 는 프론트가 읽는 계약이다 ──
    _readme = (_cfg.SRC_ROOT.parent / "client" / "README.md").read_text(encoding="utf-8")
    _emitted = {"progress", "answer", "action", "clarify", "sources", "followups", "error", "done"}
    _in_code = set(re.findall(r'"type":\s*"(\w+)"', Path(main.__file__).read_text(encoding="utf-8")))
    check(_in_code == _emitted,
          "main.py 가 내보내는 이벤트 type 목록이 테스트가 아는 것과 같다(새 type 은 여기와 문서에 등록)",
          str(sorted(_in_code ^ _emitted)))
    _missing = [t for t in _emitted if f"`{t}`" not in _readme]
    check(not _missing, "client/README.md 가 모든 이벤트 type 을 설명한다", str(_missing))
    _keys = ["message", "x_client_user", "customer_id", "session_id"]
    check(all(f"`{k}`" in _readme for k in _keys), "client/README.md 가 요청 키 4개를 설명한다")

    # ── 실패해도 스트림은 끊지 않는다 ────────────────────────
    def _boom(*a, **k):
        raise llm.LLMError("LLM 미설정 — 테스트")

    main.consult_graph.ask = _boom
    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1"))
    evs = _events(r)
    check(r.status_code == 200 and _types(evs) == ["error", "done"]
          and "LLMError" in evs[0]["text"] and "LLM 미설정" in evs[0]["text"],
          "에이전트가 죽어도 error 이벤트로 알려주고 done 으로 닫는다(빈 응답 금지)", str(evs))
    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1"),
                    headers={"Accept": "application/json"})
    check(r.status_code == 200 and _types(_events_in(r.json().get("content", ""))) == ["error", "done"],
          "비스트림에서도 실패는 200 + error·done 이벤트다", r.text[:100])
    check(any(rec.levelno == logging.ERROR and "ask() 실패" in rec.getMessage()
              and rec.exc_info for rec in _captured),
          "실패는 스택과 함께 ERROR 로 남고, «연결 끊김»으로 오인되지 않는다",
          str([rec.getMessage() for rec in _captured if rec.levelno >= logging.WARNING][-2:]))
    check(not any("연결이 끊겼다" in rec.getMessage() for rec in _captured),
          "정상 완료·실패 응답에는 «연결 끊김» 경고가 찍히지 않는다")
finally:
    main.consult_graph.ask = _saved_ask

_failed = [r for r in _results if not r[0]]
for ok, label, detail in _results:
    print(f"{'✓' if ok else '✗'} {label}" + (f" — {detail}" if not ok and detail else ""))
print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(_failed)} · 실패 {len(_failed)}")
if _failed:
    print("❌ 회귀 발생")
    raise SystemExit(1)
print("✅ HTTP 진입점 회귀 테스트 통과")
