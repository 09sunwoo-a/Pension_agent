"""결정적 처리 계층 — 전략 제안 문장의 확정 사실(facts) 산출(LLM 미호출).

    catalog.py    데이터·상수 (잎)
    text.py       표기 유틸 — 금액·조사·출처
    products.py   상품 질의·적합성 게이트(정적 / 금액 2단)
    scoring.py    금액·시급성·기대효과·점수
    render.py     절·고객 헤더·보유 현황 briefing·관리 사유
    pipeline.py   prepare() — 요건 판정 → 후보 → 게이트 → 배분 → 기대효과 → 상위 N
    compose.py    규칙 기반 문장 합성(폴백)·섹션 요약·verify()
    recommend.py  ⑤ 추천 상품 후보군·표시용 사실
    validate.py   정의 무결성 검사 (`python -m pension_agent.tools.customer_solution.engine`)

support·situations 공개 함수 재노출(support 는 engine 임포트 금지).
"""

from __future__ import annotations

from pension_agent.tools.customer_solution.engine.catalog import (  # noqa: F401
    ACTOR_SUFFIX,
    ALT_N,
    ASSETS,
    BASELINES,
    BRIEFING_SOURCE,
    BY_ID,
    CAPS,
    CARD_INDEX,
    CLAUSE_ENDINGS,
    DOC_TITLES,
    EFFECT_BANDS,
    LOOKUP_MARKERS,
    MIN_ALLOC,
    PORT_LABELS,
    PORTFOLIOS,
    PRODUCTS,
    PROTECTION_LIMIT,
    SPECS,
    SYSTEM_STRATEGIES,
    TIME_PRESSURE_MARKERS,
    TOP_HOLDINGS,
    TOP_N,
    VERBS,
    _STORE,
)
from pension_agent.tools.customer_solution.engine.compose import (  # noqa: F401
    compose_rule,
    final_clause,
    section_summaries,
    verify,
)
from pension_agent.tools.customer_solution.engine.pipeline import prepare  # noqa: F401
from pension_agent.tools.customer_solution.engine.products import (  # noqa: F401
    finalize_products,
    gate_amount,
    gate_static,
    query_products,
    static_candidates,
    _best,
)
from pension_agent.tools.customer_solution.engine.recommend import (  # noqa: F401
    candidate_pool_for_recommendation,
    product_return,
    recommendation_facts,
    render_recommendation,
    top_reference_products,
)
from pension_agent.tools.customer_solution.engine.render import (  # noqa: F401
    customer_facing_asset,
    customer_header_line,
    _action,
    _return_label,
    _three_way_breakdown,
    _verb,
    _why_this_customer,
)
from pension_agent.tools.customer_solution.engine.scoring import (  # noqa: F401
    effect_label,
    _card,
    _effect_grade,
    _score,
    _value_tag,
)
from pension_agent.tools.customer_solution.engine.text import (  # noqa: F401
    format_sources,
    won,
    _pname,
    _ret_of,
)
from pension_agent.tools.customer_solution.engine.validate import validate  # noqa: F401

# customer 상수 재노출
from pension_agent.tools.customer_solution.customer import (  # noqa: F401
    CONDS,
    PREF,
    PRIO,
    RISK,
    RISK_ASSET_CAP_PCT,
    AS_OF,
    TAX_CREDIT_CAP_WON,
    Profile,
    churn,
    conditions,
    days_to_year_end,
    today,
)

# ⑥~⑨ 규칙 계층 재노출
from pension_agent.tools.customer_solution.situations import problem_situations  # noqa: F401
from pension_agent.tools.customer_solution.support import (  # noqa: F401
    consult_resource_candidates,
    consult_resources,
    load_reference_kb,
    next_event_and_seminar,
    objection_candidates,
    outreach_candidates,
    pick_objections,
    pick_talking_points,
    pitch_kb,
    pitch_kb_module,
    pitch_talk,
    _objection_entry,
    _outreach_row,
    _type_eligible,
)
