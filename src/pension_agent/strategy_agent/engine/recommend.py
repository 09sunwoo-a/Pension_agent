"""⑤ 추천 상품 — 후보군 산출과 표시용 사실.

LLM 은 이 후보군 **안에서만** 고를 수 있다(agent._recommend 가 목록 밖 id 를 거부한다).
후보군을 코드가 확정하는 것이 불완전판매를 막는 지점이다.
"""

from __future__ import annotations

from pension_agent.strategy_agent.customer import PREF, RISK, Profile, conditions
from pension_agent.strategy_agent.engine.catalog import PORTFOLIOS, PRODUCTS, TOP_HOLDINGS
from pension_agent.strategy_agent.engine.products import gate_static, query_products
from pension_agent.strategy_agent.engine.text import _ret_of, won


def product_return(r: dict) -> float | None:
    """상품의 표시용 수익률(공개 API — _ret_of() 의 공개 래퍼)."""
    return _ret_of(r)


def candidate_pool_for_recommendation(p: Profile) -> dict[str, list[dict]]:
    """⑤ '이런 상품이 적합할 수 있어요' 의 폐쇄 후보군(REQUIREMENTS.md ⑤).

    적합성 게이트(위험등급 상한·거래채널 — static_candidates() 가 개별 전략에 쓰는 것과 같은
    gate_static())를 통과한 상품·포트폴리오만 담는다. LLM 은 이 안에서만 상품 1개·포트폴리오
    1개를 고른다 — consult_agent.llm_select() 와 같은 패턴으로, 응답 id 를 이 목록과 다시
    대조해 목록 밖의 값을 지어내도 걸러낸다(agent.py 쪽에서 재검증).
    """
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
    """상품추천 검증용 표기: 이름 + 최근 1년 수익률(실적배당은 기대수익률, 원리금보장은 확정금리)."""
    ret = _ret_of(r)
    return f"{r['name']}(최근 1년 {ret}%)" if ret is not None else r["name"]


def recommendation_facts(p: Profile, product: dict, portfolio: dict | None) -> dict:
    """⑤ 추천 사유 LLM 산출물을 검증할 재료 범위.

    common.verify.verify() 가 이 안의 숫자·상품명만 허용한다 — 선정된 상품·포트폴리오
    구성상품의 이름·수익률 밖의 값을 언급하면 재료 이탈로 거부된다. strategy_agent.verify()
    가 기대하는 facts 형태(customer/conditions/briefing/items)를 이 좁은 범위로 흉내낸다.
    """
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
    """⑤ 화면 표시용 포맷 — 상품명·수익률 표기는 여기(코드)가 확정하고, LLM 은 사유 문장만 썼다."""
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


def top_reference_products(n: int = 2) -> list[dict]:
    """수익률 상위 1% 고객 상품 사례 n개(기본 2개) — REQUIREMENTS.md ④.

    고객별 필터가 없다 — 이 섹션 자체가 "이 고객에 대한 추천"이 아니라 고성과 고객의 실제
    운용 사례를 비교·참고 정보로 보여주는 것이기 때문이다(REQUIREMENTS.md §7). 수익률 내림차순
    상위만 뽑는 순수 데이터 조회이며 LLM 이 개입하지 않는다.
    """
    ranked = sorted(TOP_HOLDINGS, key=lambda r: r["return_1y"], reverse=True)
    return [
        {"product_name": r["product_name"], "description": r["description"],
         "return_1y": r["return_1y"]}
        for r in ranked[:n]
    ]
