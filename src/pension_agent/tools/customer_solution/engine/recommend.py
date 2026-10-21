"""⑤ 추천 상품 — 폐쇄 후보군 산출과 표시용 사실. LLM 은 후보군 안에서만 선택(불완전판매 방지)."""

from __future__ import annotations

from pension_agent.tools.customer_solution.customer import PREF, RISK, Profile, conditions
from pension_agent.tools.customer_solution.engine.catalog import PORTFOLIOS, PRODUCTS, TOP_HOLDINGS
from pension_agent.tools.customer_solution.engine.products import gate_static, query_products
from pension_agent.tools.customer_solution.engine.text import _ret_of, won


def product_return(r: dict) -> float | None:
    """상품 표시용 수익률(_ret_of 공개 래퍼)."""
    return _ret_of(r)


def candidate_pool_for_recommendation(p: Profile) -> dict[str, list[dict]]:
    """⑤ 폐쇄 후보군 — gate_static() 통과 상품, 구성상품 전부 통과한 포트폴리오."""
    conds = conditions(p)
    cap = p.grade
    if "mis" in conds and PREF.get(p.rk):
        cap = RISK[min(RISK.index(cap), RISK.index(PREF[p.rk]))]

    products = [r for r in query_products(PRODUCTS) if gate_static(r, p, cap)[0]]
    eligible_ids = {r["id"] for r in products}
    portfolios = []
    for pf in PORTFOLIOS:
        alloc_ids = {a["product_id"] for a in pf.get("allocation", [])}
        if alloc_ids and alloc_ids <= eligible_ids:
            portfolios.append(pf)
    return {"products": products, "portfolios": portfolios}


def _product_label(r: dict) -> str:
    """검증용 표기 — 이름 + 최근 1년 수익률."""
    ret = _ret_of(r)
    return f"{r['name']}(최근 1년 {ret}%)" if ret is not None else r["name"]


def recommendation_facts(p: Profile, product: dict, portfolio: dict | None) -> dict:
    """⑤ 추천 사유 검증 재료 — 선정 상품·포트폴리오 구성상품의 이름·수익률만(facts 형태 모방)."""
    products = {"추천상품": _product_label(product)}
    if portfolio:
        for a in portfolio.get("allocation", []):
            row = next((r for r in PRODUCTS if r["id"] == a["product_id"]), None)
            if row:
                products[f"포트폴리오_{row['id']}"] = f"{_product_label(row)} 비중 {a['weight_pct']}%"
    return {
        "customer": {"연령": p.ag, "평가금액": won(p.bal), "투자성향": p.rk, "수익률": f"{p.ret}%"},
        "conditions": [],
        "briefing": {},
        "items": [{"clause": "", "evidence": "", "amount": None, "formula": "",
                   "talk": "", "evidence_extra": [], "products": products}],
    }


def render_recommendation(
    product: dict, portfolio: dict | None,
    product_reason: str, portfolio_reason: str, combined_reason: str,
) -> dict:
    """⑤ 화면 표시용 포맷 — 상품명·수익률은 코드, 사유 문장만 LLM."""
    out = {
        "product": {"name": product["name"], "description": product.get("strategy_desc") or "",
                    "return_1y": _ret_of(product), "reason": product_reason},
        "portfolio": None,
        "combined_reason": combined_reason,
    }
    if portfolio:
        by_id = {r["id"]: r for r in PRODUCTS}
        out["portfolio"] = {
            "name": portfolio["name"], "description": portfolio.get("description", ""),
            "allocation": [
                {"product_name": by_id[a["product_id"]]["name"], "weight_pct": a["weight_pct"]}
                for a in portfolio["allocation"] if a["product_id"] in by_id
            ],
            "reason": portfolio_reason,
        }
    return out


def top_reference_products(p: Profile | None = None, n: int = 2) -> list[dict]:
    """④ 동연령대 수익률 상위 1% 고객 상품 사례 n개. 설명·수익률은 원장에 없어 빈칸(지어내기 금지)."""
    if p is not None and p.peer and (p.peer.get("top1_funds") or p.peer.get("top1_etfs")):
        names = [*(p.peer.get("top1_funds") or []), *(p.peer.get("top1_etfs") or [])]
        return [{"product_name": nm, "description": "", "return_1y": None,
                 "peer_top1_return": p.peer.get("top1_ret")} for nm in names[:n]]
    # 동연령 비교 없는 프로필 — 자리표시자 카탈로그로 폴백
    ranked = sorted(TOP_HOLDINGS, key=lambda r: r["return_1y"], reverse=True)
    return [
        {"product_name": r["product_name"], "description": r["description"],
         "return_1y": r["return_1y"]}
        for r in ranked[:n]
    ]
