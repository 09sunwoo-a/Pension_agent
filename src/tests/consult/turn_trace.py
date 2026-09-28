"""턴 절차 기록(`turn_trace`) — 응답의 `trace` 이벤트가 싣는 것을 진짜 그래프로 잰다.

LLM 만 스크립트한다(`tests/debug/script.py` — 검색·근거 조립·게이트는 진짜 코드다). 그래야
«패널에 서는 것이 코드가 실제로 판정한 것»인지를 잴 수 있다.
"""

from __future__ import annotations

import json

from pension_agent import observability
from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent.nodes import plan as P, understand as U
from tests.consult._common import print  # noqa: A001 — 집계용
from tests.debug import script as SC


def _ask(name: str) -> dict:
    """진짜 노드로 그래프를 새로 만들어 한 턴 돌린다. `test_consult_agent.main` 이 노드를
    전역 스텁으로 바꿔 두므로(그래프도 그 스텁으로 캐시돼 있다) 원래대로 돌려놓고 부르고,
    끝나면 그 상태를 되돌린다."""
    scn = SC.SCENARIOS[name]
    saved = (G.understand, G.plan_step, G._AGENT)
    G.understand, G.plan_step, G._AGENT = U.understand, P.plan_step, None
    try:
        with SC.installed(scn), observability.request_id("trace001"):
            return G.ask(scn.question, customer_id=scn.customer_id)
    finally:
        G.understand, G.plan_step, G._AGENT = saved


def check_turn_trace() -> int:
    ok = 0

    def say(hit: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        print(f"{'✓' if hit else '✗'} {label}" + ("" if hit or not detail else f" — {detail}"))
        ok += bool(hit)

    # ── 통과한 턴 ─────────────────────────────────────────────
    r = _ask("tax_credit_clean")
    t = r.get("trace") or {}
    say(set(t) >= {"started_at", "finished_at", "intent", "timeline", "rounds", "evidence",
                   "sources", "sentences"},
        "trace: ask() 결과에 절차 기록이 실린다(시각·단계·도구 호출·근거·출처·문장)", str(sorted(t)))
    stages = [s["stage"] for s in t.get("timeline", [])]
    say(stages[:1] == ["turn"] and stages[-1:] == ["turn"]
        and {"understand", "plan", "tool", "compose", "verify"} <= set(stages),
        "trace: timeline 이 로그 단계 줄과 같은 사건을 시간순으로 싣는다", str(stages))
    say(all(s.get("at") and isinstance(s.get("elapsed_ms"), int) for s in t.get("timeline", [])),
        "trace: 단계마다 벽시계 시각(ms)과 턴 시작 뒤 경과가 있다", str(t.get("timeline", [])[:1]))
    say(t.get("rounds") == [{"n": 1, "tool": "fact", "query": "세액공제 한도",
                             "outcome": "found", "reason": None}],
        "trace: rounds 가 계획이 부른 도구·질의 전문·결과를 싣는다", str(t.get("rounds")))
    ev = (t.get("evidence") or [{}])[0]
    say(ev.get("tool") == "fact" and "900" in ev.get("text", "")
        and [c["id"] for c in ev.get("cards", [])] == ["fact.k04.f2"]
        and ev["cards"][0]["used"] is True,
        "trace: 근거 카드가 원문·카드 id·«답변이 썼나»와 함께 실린다", str(ev.get("cards")))
    first = (t.get("sentences") or [{}])[0]
    say(any(m.get("by") == "수치" and m.get("card") == "fact.k04.f2" and m.get("values") == ["900"]
            for m in first.get("matches", [])),
        "trace: 문장–근거 대응은 문장에 적힌 표기 그대로의 수치로 선다(정규형 9000000 이 아니라)",
        str(first))
    say(any(not s["matches"] for s in t.get("sentences", [])),
        "trace: 코드가 대응을 증명하지 못한 문장은 대응이 빈 목록이다(지어내지 않는다)",
        str([s["matches"] for s in t.get("sentences", [])]))
    json.dumps(t, ensure_ascii=False)   # 직렬화가 되어야 응답에 실린다 — 안 되면 여기서 죽는다
    say(True, "trace: JSON 으로 직렬화된다")

    # ── 검증에 걸린 턴 — 사유는 싣고 초안은 싣지 않는다 ─────────
    r = _ask("out_of_ledger")
    t = r.get("trace") or {}
    verifies = [s for s in t.get("timeline", []) if s["stage"] == "verify"]
    say(len(verifies) == 2 and all(s["facts"].get("passed") is False for s in verifies)
        and all(s["facts"].get("faults") for s in verifies),
        "trace: 폐기된 시도마다 판정과 폐기 사유 목록(detail.faults)이 실린다",
        str([s["facts"] for s in verifies]))
    drafted = SC.SCENARIOS["out_of_ledger"].compose
    say(drafted not in json.dumps(t, ensure_ascii=False),
        "trace: 폐기된 초안 전문은 싣지 않는다", drafted)

    # ── LLM 이 죽은 턴 — 있는 단계만 ─────────────────────────
    r = _ask("llm_dead")
    t = r.get("trace") or {}
    say(isinstance(t.get("sentences"), list) and isinstance(t.get("rounds"), list)
        and any(s["stage"] == "compose" and s["level"] == "WARNING" for s in t.get("timeline", [])),
        "trace: LLM 이 죽은 턴도 기록이 서고 실패 단계가 WARNING 으로 남는다",
        str([(s["stage"], s["level"]) for s in t.get("timeline", [])]))
    return ok
