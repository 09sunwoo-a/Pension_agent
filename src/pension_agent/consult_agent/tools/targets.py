"""오늘의 타겟 고객 목록 도구(targets) — 고객 화면을 열기 «전»의 재료.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from pension_agent import clock
from pension_agent.consult_agent.state import AgentState
from pension_agent.consult_agent.tools.base import Evidence, _ev


# ─────────────────────────────────────────────────────────────
# 오늘의 타겟 고객 — 고객 화면을 열기 «전»의 재료
#
# 브리핑이 고객 하나를 펼치는 자리라면, 이 목록은 그 앞에 놓이는 «오늘 볼 사람들»이다
# (서비스를 열면 가장 먼저 뜨는 화면 — strategy_agent/target_list.py).
#
# 대화 쪽에 이 도구가 없던 동안 「오늘 누구부터 봐야 해」·「타겟 목록 쪽지로 보내줘」는
# 재료 0건으로 끝났다. 화면에는 떠 있는데 원장에는 없어서다 — 답은 원장 안에서만 나오므로
# (규칙 2), 화면이 아는 것을 대화가 말하려면 도구가 있어야 한다.
#
# 고객 식별번호(KB-PIN)는 싣지 않는다. 목록에서 사람을 특정하는 데 필요한 것은 이름과
# 순번이고, 실제 조회는 그 고객을 열어서 한다(`customer` 도구는 직원이 이미 연 고객이라
# 싣는다). 원장에 없는 값은 답변에도 쪽지에도 나갈 수 없다 — 경계를 여기서 긋는 것이
# 「쓰지 말라」는 지시보다 확실하다.
# ─────────────────────────────────────────────────────────────

def _targets(state: AgentState, query: str) -> Evidence | None:
    """오늘의 타겟 고객 목록. 선정도 순서도 여기서 만들지 않는다(target_list 그대로)."""
    from pension_agent import workb  # noqa: PLC0415 — strategy_agent 임포트를 지연시킨다

    try:
        found = workb.today_targets()
    except Exception:
        return None
    day = clock.today().isoformat()
    lines = [f"■ 오늘의 타겟 고객 — {day} 기준 · {len(found)}명"]
    if not found:
        # 0건도 이 도구가 확인한 값이다 — 이 줄이 없으면 «질의가 빗나감»과 구별되지 않는다
        # (_history 의 HISTORY_NONE 과 같은 자리).
        lines.append("· 오늘 사후관리 타겟으로 선정된 고객이 없습니다.")
    for i, t in enumerate(found, 1):
        p = t.profile
        attrs = " · ".join([f"{p.ag}세", p.rk] + ([p.club_grade] if p.club_grade else []))
        lines.append(f"· {i}. {p.nm} — {attrs} · 평가금액 {workb.won(p.bal)}")
        # 요건 이름 옆에 «무엇 때문에 걸렸나»의 원장 값을 붙인다. 이름만 적으면 답변도
        # 이름만 옮기게 되고, 그러면 직원은 결국 고객을 하나씩 열어봐야 한다.
        lines += [f"    - {workb.cond_text(t, c)}" for c in t.conds]
    text = "\n".join(lines)
    return _ev("targets", query, text,
               [{"id": "targets.today",
                 "title": f"오늘의 타겟 고객 {len(found)}명 ({day})",
                 "doc": "고객 정보 — 계좌 원장 조회값 · 선정 기준은 사후관리 타겟 룰베이스",
                 "score": None, "page": None}],
               allow=[text],
               meta={"count": len(found)})
