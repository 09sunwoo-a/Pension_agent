"""HTTP 진입점(main.py) 회귀 테스트 — 플랫폼 I/O 스키마를 지키는가.

행내 GenAI 플랫폼은 요청·응답 형태를 고정해 두었다(skills/genai-platform-agent-dev/refs/
genai-platform.md «API I/O 스키마 (고정)»). 이 스키마가 어긋나면 에이전트가 아무리 잘 답해도 플랫폼이 못 읽는다 —
그런데 그 사실은 **행내에 들고 가서야** 드러난다. 여기서 미리 잡는다.

  · input_value 는 JSON «문자열» 이고, 그 안에 message·x_client_user 가 있어야 한다
  · 필수 키가 없으면 422 (500 이 아니다 — 호출자가 무엇이 빠졌는지 알아야 한다)
  · 응답은 줄마다 {"event": "CHUNK", "content": ...} 이고, 이어 붙이면 답변 본문 +
    출처가 된다. 출처는 «항상» 실린다 — 근거를 못 보여주면 이 에이전트의 답이 아니다
  · 진행 표시는 stream_progress 를 켤 때만, 그리고 답변보다 **먼저** 흐른다
  · x_client_user 가 에이전트(graph.ask)까지 실제로 도달한다 — 거기서 이 턴의 모든
    LLM 호출 주체가 된다(그 배선 자체는 test_consult_agent 가 본다)

LLM 은 부르지 않는다 — graph.ask 를 갈아끼워 위 계약만 본다.

실행: python -m tests.test_api   (src/ 에서)
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

import main
from pension_agent import config as _cfg
from pension_agent import llm
from pension_agent.consult_agent import render
from pension_agent.strategy_agent import briefing_store

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))


def _body(**payload) -> dict:
    return {"input_value": json.dumps(payload, ensure_ascii=False), "message_hists": None}


def _chunks(resp, sse: bool = True) -> list[str]:
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
        out.append(d["content"])
    return out


_seen: dict = {}
ANSWER = "첫 줄입니다.\n둘째 줄에는 «인용»과 숫자 12.4% 가 있습니다.\n셋째 줄."
SOURCES = [
    {"id": "kb_fact_001", "doc": "연금사업부 업무가이드", "title": "IRP 수수료 체계",
     "score": 0.82},
    {"id": "kb_pitch_009", "doc": "스타런 교육자료", "title": "수수료 반론 대응",
     "score": None, "role": "주의"},
]
PROGRESS = ["질문을 이해하고 있어요", "제도·상품 수치를 찾고 있어요", "답변을 쓰고 있어요"]


def _fake_ask(question, history=None, **kw):
    _seen.clear()
    _seen.update(kw, question=question, history=history)
    cb = kw.get("on_progress")
    if cb:
        for line in PROGRESS:
            cb(line)
    # 실물처럼 이번 턴을 덧붙인 history 를 돌려준다 — 진입점이 그것을 다음 턴에 되찾는지 본다.
    return {"answer": ANSWER, "sources": SOURCES, "followups": [],
            "history": [*(history or []), {"question": question, "tools": []}]}


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

    r = client.post("/chat", json={"input_value": json.dumps(["배열"]), "message_hists": None})
    check(r.status_code == 422, "input_value 가 JSON «객체»가 아니면 422", str(r.status_code))

    # ── 정상 턴 ──────────────────────────────────────────────
    r = client.post("/chat", json=_body(message="IRP 수수료 질문", x_client_user="emp-0417"))
    check(r.status_code == 200, "정상 요청은 200", str(r.status_code))
    check(r.headers["content-type"].startswith("text/event-stream"),
          "Content-Type 은 text/event-stream", r.headers.get("content-type", ""))
    check(r.text.startswith("data: {") and "\n\n" in r.text,
          "SSE 프레임 — data: 로 시작하고 이벤트는 빈 줄로 끝난다", repr(r.text[:60]))
    body = "".join(_chunks(r))
    check(body.startswith(ANSWER),
          "CHUNK 를 이어 붙이면 답변 원문으로 시작한다(앞에 아무것도 안 붙는다)",
          repr(body[:80]))
    # 진행 콜백은 **항상** 넘긴다 — 로그(Grafana)가 «지금 어디까지 갔나»를 보는 자리다.
    # 화면에 흐르는 것은 stream_progress 를 켤 때만이다(아래).
    check(_seen.get("on_progress") is not None,
          "진행 콜백은 로그용으로 항상 넘긴다", str(_seen.get("on_progress")))
    check("⋯" not in body, "진행 문구가 답변에 섞이지 않는다", repr(body[:80]))
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
          "진행 단계가 화면에 안 흘러도 로그에는 찍힌다", str(_mine[1:2]))
    check(any("완료" in m and "출처 2건" in m for m in _mine),
          "완료 로그에 소요시간·답변 길이·출처 건수가 실린다", str(_mine[-1:]))
    check(all(r.levelno == logging.INFO for r in _logs if _rid and r.getMessage().startswith(f"[{_rid}]")),
          "정상 턴의 로그는 전부 INFO 다", str([r.levelname for r in _logs]))
    check(logging.getLogger().handlers, "루트 로거에 핸들러가 잡혀 있다(stdout → 수집기)",
          str(logging.getLogger().handlers))

    # 출처는 «항상» 실린다 — 근거를 못 보여주면 이 에이전트의 답이 아니다.
    # 문서명이 먼저 읽히고, 카드 id 는 역추적용으로 뒤에 남고, 관련도는 있을 때만 찍힌다.
    check(render.GROUND_HEADER in body and "연금사업부 업무가이드" in body
          and "[kb_fact_001 · 관련도 0.82]" in body,
          "출처(근거)가 답변 뒤에 실린다", repr(body[-200:]))
    check(render.CAUTION_HEADER in body and "[kb_pitch_009]" in body,
          "«지켜야 할 것»은 근거와 갈라서 실리고, 관련도 없는 재료엔 관련도를 안 찍는다",
          repr(body[-200:]))
    check(body.endswith(render.sources_block(SOURCES) + "\n"),
          "출처 블록은 CLI 와 같은 글자다(render 한 곳에서 나온다)", repr(body[-80:]))
    check(_seen.get("x_client_user") == "emp-0417",
          "x_client_user 가 에이전트까지 전달된다", str(_seen.get("x_client_user")))
    check(_seen.get("session_id") == "default" and _seen.get("customer_id") is None,
          "선택 키는 기본값으로 떨어진다", str(_seen))

    r = client.post("/chat", json=_body(
        message="이 고객 브리핑 요약해줘", x_client_user="emp-0417",
        customer_id="154821-4938201", session_id="S-1"))
    _chunks(r)
    check(_seen.get("customer_id") == "154821-4938201" and _seen.get("session_id") == "S-1",
          "customer_id·session_id 가 전달된다", str(_seen))

    # 진행 표시 — 켜면 답변 **앞**에 흘러야 한다. 다 끝난 뒤 몰아서 주면 진행 표시가 아니다.
    r = client.post("/chat", json=_body(
        message="q", x_client_user="emp-1", stream_progress=True))
    streamed = _chunks(r)
    marks = [i for i, c in enumerate(streamed) if c.startswith("⋯")]
    first_answer = next(i for i, c in enumerate(streamed) if c.startswith("첫 줄"))
    check([c.strip() for c in streamed if c.startswith("⋯")]
          == [f"⋯ {p}" for p in PROGRESS],
          "stream_progress 를 켜면 진행 표시가 그대로 흐른다", str(streamed[:4]))
    check(marks and max(marks) < first_answer,
          "진행 표시는 답변보다 먼저 나간다", f"progress={marks} answer={first_answer}")
    check("".join(streamed[first_answer:]).startswith(ANSWER),
          "진행 표시를 켜도 답변 본문은 그대로다", repr("".join(streamed[first_answer:])[:60]))

    # ── 대화 맥락 — 게이트웨이 경로는 history 를 돌려줄 자리가 없어 진입점이 세션별로 맡아 둔다 ──
    from pension_agent.consult_agent import context_store
    context_store.clear()
    r = client.post("/chat", json=_body(message="첫 질문", x_client_user="emp-7", session_id="S-7"))
    _chunks(r)
    check(_seen.get("history") is None, "세션의 첫 턴은 맥락 없이 간다", str(_seen.get("history")))
    r = client.post("/chat", json=_body(message="그럼 안 된다고 하면요?", x_client_user="emp-7", session_id="S-7"))
    _chunks(r)
    check(_seen.get("history") == [{"question": "첫 질문", "tools": []}],
          "같은 (직원, 세션)의 다음 턴은 이전 턴의 history 를 이어받는다", str(_seen.get("history")))
    check(any("맥락=1턴(store)" in rec.getMessage() for rec in _captured),
          "요청 로그에 맥락이 몇 턴이고 어디서 왔는지 찍힌다",
          str([m for m in (rec.getMessage() for rec in _captured) if "맥락=" in m][-1:]))
    r = client.post("/chat", json=_body(message="다른 세션", x_client_user="emp-7", session_id="S-8"))
    _chunks(r)
    check(_seen.get("history") is None, "세션이 다르면 맥락이 섞이지 않는다", str(_seen.get("history")))
    r = client.post("/chat", json=_body(message="다른 직원", x_client_user="emp-8", session_id="S-7"))
    _chunks(r)
    check(_seen.get("history") is None, "직원이 다르면 같은 session_id 라도 맥락이 섞이지 않는다",
          str(_seen.get("history")))
    # 호출자가 Turn 형식을 실어 보내면 저장본보다 우선한다 — 프론트가 맥락을 들고 다니게 될 때 자리.
    r = client.post("/chat", json={**_body(message="셋째", x_client_user="emp-7", session_id="S-7"),
                                   "message_hists": [{"question": "프론트가 든 턴"}]})
    _chunks(r)
    check(_seen.get("history") == [{"question": "프론트가 든 턴"}],
          "message_hists 가 Turn 형식이면 저장본보다 우선한다", str(_seen.get("history")))
    # Turn 이 아닌 형식(OpenAI 식)은 버리고 저장본을 쓴다 — 넘기면 format_history 가 500 을 낸다.
    r = client.post("/chat", json={**_body(message="넷째", x_client_user="emp-7", session_id="S-7"),
                                   "message_hists": [{"role": "user", "content": "x"}]})
    _chunks(r)
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
    expected_full = ANSWER + "\n" + render.sources_block(SOURCES) + "\n"
    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1"),
                    headers={"Accept": "application/json"})
    check(r.status_code == 200 and r.headers["content-type"].startswith("application/json"),
          "Accept: application/json 이면 JSON 하나로 답한다", r.headers.get("content-type", ""))
    check(r.json() == {"event": "CHUNK", "content": expected_full},
          "비스트림 JSON 은 답변+출처 전체를 content 에 담은 CHUNK 하나다", r.text[:80])
    check(_seen.get("on_progress") is not None,
          "비스트림에서도 진행 콜백(로그용)은 넘긴다", str(_seen.get("on_progress")))

    r = client.post("/chat", json={**_body(message="q", x_client_user="emp-1"), "isStream": False})
    check(r.headers["content-type"].startswith("application/json") and r.json()["content"] == expected_full,
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
        check(r.text.startswith("{") and "".join(_chunks(r, sse=False)).startswith(ANSWER),
              "CHAT_SSE_FRAMING=0 이면 문서의 «줄마다 JSON» 형식이다", repr(r.text[:40]))
    finally:
        main.SSE_FRAMING = True

    # ── 실패해도 스트림은 끊지 않는다 ────────────────────────
    def _boom(*a, **k):
        raise llm.LLMError("LLM 미설정 — 테스트")

    main.consult_graph.ask = _boom
    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1"))
    text = "".join(_chunks(r))
    check(r.status_code == 200 and "LLMError" in text and "LLM 미설정" in text,
          "에이전트가 죽어도 무엇이 깨졌는지 CHUNK 로 알려준다(빈 응답 금지)", text[:100])
    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1"),
                    headers={"Accept": "application/json"})
    check(r.status_code == 200 and "LLMError" in r.json().get("content", ""),
          "비스트림에서도 실패는 200 + content 로 알려준다", r.text[:100])
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
