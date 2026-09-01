"""HTTP 진입점(main.py) 회귀 테스트 — 플랫폼 I/O 스키마를 지키는가.

행내 GenAI 플랫폼은 요청·응답 형태를 고정해 두었다(refs/genai-platform.md «API I/O
스키마 (고정)»). 이 스키마가 어긋나면 에이전트가 아무리 잘 답해도 플랫폼이 못 읽는다 —
그런데 그 사실은 **행내에 들고 가서야** 드러난다. 여기서 미리 잡는다.

  · input_value 는 JSON «문자열» 이고, 그 안에 message·x_client_user 가 있어야 한다
  · 필수 키가 없으면 422 (500 이 아니다 — 호출자가 무엇이 빠졌는지 알아야 한다)
  · 응답은 줄마다 {"event": "CHUNK", "content": ...} 이고, content 를 이어 붙이면
    답변 원문이 정확히 복원된다 (진행 표시 같은 다른 문구가 섞이면 여기서 깨진다)
  · x_client_user 가 에이전트(graph.ask)까지 실제로 도달한다 — 거기서 이 턴의 모든
    LLM 호출 주체가 된다(그 배선 자체는 test_consult_agent 가 본다)

LLM 은 부르지 않는다 — graph.ask 를 갈아끼워 위 계약만 본다.

실행: python -m tests.test_api   (src/ 에서)
"""

from __future__ import annotations

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

import main
from pension_agent import llm

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))


def _body(**payload) -> dict:
    return {"input_value": json.dumps(payload, ensure_ascii=False), "message_hists": None}


def _chunks(resp) -> list[str]:
    out = []
    for line in resp.text.splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        check(d.get("event") == "CHUNK", "응답 이벤트는 CHUNK 뿐이다", str(d.get("event")))
        out.append(d["content"])
    return out


_seen: dict = {}
ANSWER = "첫 줄입니다.\n둘째 줄에는 «인용»과 숫자 12.4% 가 있습니다.\n셋째 줄."


def _fake_ask(question, history=None, **kw):
    _seen.clear()
    _seen.update(kw, question=question, history=history)
    return {"answer": ANSWER, "sources": [], "history": [], "followups": []}


_saved_ask = main.consult_graph.ask
main.consult_graph.ask = _fake_ask
client = TestClient(main.app)

try:
    # ── /health ──────────────────────────────────────────────
    r = client.get("/health")
    h = r.json()
    check(r.status_code == 200 and h["status"] == "ok", "/health 200", str(r.status_code))
    check("api_key_set" in h["llm"] and "sk-" not in r.text,
          "/health 는 키 «설정 여부»만 내보내고 값은 내보내지 않는다", r.text[:120])
    check(h["rate_gate"]["max_concurrency"] == llm.MAX_CONCURRENCY,
          "/health 가 429 게이트 설정을 보여준다", str(h.get("rate_gate")))

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
    check("".join(_chunks(r)) == ANSWER,
          "CHUNK 를 이어 붙이면 답변 원문 그대로다(진행 문구가 섞이지 않는다)",
          repr("".join(_chunks(r))[:80]))
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

    # ── 실패해도 스트림은 끊지 않는다 ────────────────────────
    def _boom(*a, **k):
        raise llm.LLMError("LLM 미설정 — 테스트")

    main.consult_graph.ask = _boom
    r = client.post("/chat", json=_body(message="q", x_client_user="emp-1"))
    text = "".join(_chunks(r))
    check(r.status_code == 200 and "LLMError" in text and "LLM 미설정" in text,
          "에이전트가 죽어도 무엇이 깨졌는지 CHUNK 로 알려준다(빈 응답 금지)", text[:100])
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
