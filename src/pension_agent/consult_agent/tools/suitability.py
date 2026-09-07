"""적합성 범위 도구(suitable) — 이 고객에게 안내할 수 있는 상품의 범위.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from pension_agent.consult_agent import kb as KBMOD
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.consult_agent.tools.base import Evidence, _ev, _scope
from pension_agent.consult_agent.tools.cards import advisory_mark


# ─────────────────────────────────────────────────────────────
# 적합성 범위 — "이 고객에게 뭘 추천하지?" 에 답할 수 있는 것
#
# **권유가 아니라 정보 제공이다.** 직원이 상품을 물으면 «이 고객 투자성향에서 어디까지
# 가능한가»와 «그 안에 무엇이 있는가»를 답하고, 범위 안의 특정 상품을 짚어 말할 수도
# 있다 — 다만 «이런 상품이 있습니다» 톤까지이고, 무엇을 권유할지는 자본시장법과 당행
# 규정에 따라 직원이 정한다(§8 관리대장, 2026-09-02 개정).
#
# 판정은 **하지 않는다.** strategy_agent 의 적합성 게이트가 이미 계산한 것을 그대로
# 옮긴다(위험등급 상한·거래채널). 같은 판정을 두 번 구현하면 브리핑 화면 ⑤ 「이런 상품이
# 적합할 수 있어요」와 대화형이 다른 목록을 말하게 된다.
#
# 이 도구가 없던 동안, 「이 고객 무슨 상품 추천해주지?」는 lineup 을 세 바퀴 돌고 재료
# 0건으로 끝났다. 계산은 코드가 이미 해뒀는데 **대화형에 그걸 부를 도구가 없었다** —
# 능력 표면은 도구 목록이므로(§3) 없는 도구는 없는 능력이다.
# ─────────────────────────────────────────────────────────────

#: 제외 상품을 몇 건까지 싣나. "왜 이건 없어?" 에 답하려면 사유가 필요하고, 열두 줄이
#: 늘어서면 정작 통과 목록이 묻힌다.
BLOCKED_MAX = 5


def _suitable(state: AgentState, query: str) -> Evidence | None:
    """적합성 게이트가 허용하는 범위와 그 안의 상품. 고객 화면이 닫혀 있으면 없다(§3)."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.strategy_agent import engine  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        pool = engine.candidate_pool_for_recommendation(profile)
        passed = pool["products"]
        # 상한은 게이트가 쓰는 것과 같은 값이어야 한다 — 통과 목록만 옮기고 상한을 따로
        # 셈하면 "다소높은위험까지 됩니다"와 목록이 어긋난다.
        cap = profile.grade
        conds = strategy_customer.conditions(profile)
        if "mis" in conds and strategy_customer.PREF.get(profile.rk):
            cap = strategy_customer.RISK[min(
                strategy_customer.RISK.index(cap),
                strategy_customer.RISK.index(strategy_customer.PREF[profile.rk]))]
        blocked: list[tuple[dict, str]] = []
        for row in engine.query_products(engine.PRODUCTS):
            ok, why = engine.gate_static(row, profile, cap)
            if not ok:
                blocked.append((row, why))
    except Exception:
        return None
    if not passed and not blocked:
        return None
    advice = advisory_mark({"advisory": KBMOD.advisory_note(KB)})

    lines = [f"■ 고객 {customer_id} — 투자성향 {profile.rk} · 위험등급 {profile.grade}",
             f"· 적합성 허용 상한: {cap} (이 등급까지의 상품만 안내할 수 있다)",
             "",
             f"── 안내할 수 있는 상품 {len(passed)}종"]
    for r in passed:
        ret = engine.product_return(r)
        tail = f" · 최근 1년 {ret}%" if ret is not None else ""
        lines.append(f"· {r['name']} — {r['risk']}{tail}"
                     + (f" · {r['category']}" if r.get("category") else ""))
    for pf in pool["portfolios"]:
        lines.append(f"· [포트폴리오] {pf['name']} — {pf.get('description') or ''}".rstrip())
    if blocked:
        lines += ["", f"── 안내할 수 없는 상품 {len(blocked)}종 (왜 목록에 없는지)"]
        lines += [f"· {r['name']} — {why}" for r, why in blocked[:BLOCKED_MAX]]
    else:
        # **0건일 때 침묵하지 않는다.** 재료가 아무 말도 안 하면 답변 형태가 요구하는
        # 「안내할 수 없는 상품」을 LLM 이 통과 목록에서 만들어 채운다(실측: 정민석 —
        # 12종을 11종이라 말하고 하나를 뺐다). 그리고 직원 입장에서도 «없는 것»과
        # «안 알려준 것»은 다르다 — 바로 앞 고객에서는 제외 4종이 나왔기 때문이다.
        lines += ["", "── 안내할 수 없는 상품 없음 "
                      f"(허용 상한이 {cap}이라 카탈로그 전부가 범위 안이다)"]
    return _ev("suitable", query, "\n".join(lines),
               [{"id": f"suitable.{customer_id}",
                 "title": f"{profile.nm} 고객 적합성 판정 (KB-PIN {customer_id})",
                 "doc": "투자성향 적합성 확인 — 위험등급 상한·거래채널 판정 결과 "
                        "(브리핑 화면 ⑤ 와 같은 후보군)",
                 "score": None, "page": None}],
               # 고지 문구를 **여기서 만들지 않는다.** 지식베이스가 선언한 것을 그대로
               # 옮긴다 — 재료 종류마다 코드 상수를 하나씩 두면 §7 이 사실상 없어진다
               # (§12 gap 20 이 그 경고다).
               notices=[advice] if advice else [],
               scopes=[_scope("적합성 판정", [], [advice])] if advice else [])
