"""결정적 처리 계층 — 전략 제안 문장에 필요한 확정 사실(facts)의 산출.

이 패키지는 LLM 을 호출하지 않는다. 문장 작성은 `agent.py` 의 LLM 단계가 맡고, 여기는
그 입력이 되는 사실과 제약을 확정하는 역할만 한다.

    prepare(profile)          요건 판정 → 후보 소집 → 적합성 게이트 → 자금 배분
                              → 기대효과 산출 → 상위 N 선정. 반환값이 LLM 입력 재료.
    compose_rule(facts)       규칙 기반 문장 합성. LLM 미가용·검증 실패 시 폴백.
    verify(sentence, facts)   LLM 문장이 재료 범위를 벗어났는지 검사.
    validate()                정의 자체의 무결성 검사. 지식베이스와의 근거 정합성을 포함한다.

본 계층이 LLM 이 아닌 코드로 구현되어야 하는 근거는 다음과 같다.
    · 요건 판정·금액·기대효과는 산출식 기반 항목으로 재현성이 요구된다.
    · 적합성 판정(위험등급·최소가입금액·거래채널·투자한도)은 규정 사항이며,
      오판은 불완전판매에 해당한다.
    · 자금 풀 배분은 복수 전략이 동일 재원을 중복 사용하는 것을 차단하기 위한 제약이다.

━━ 모듈 ━━
    catalog.py    데이터와 상수 — 스토어가 적재한 레코드 + 사람이 정한 임계값 (잎)
    text.py       표기 유틸 — 금액·조사·출처 문자열
    products.py   상품 질의와 적합성 게이트 (정적 게이트 / 금액 게이트 2단)
    scoring.py    금액 · 시급성 · 기대효과 · 점수
    render.py     절·고객 헤더·보유 현황 briefing·관리 사유
    pipeline.py   prepare() — 위 다섯을 한 줄기로 엮는다
    compose.py    규칙 기반 문장 합성 · 섹션 요약 · verify()
    recommend.py  ⑤ 추천 상품 후보군과 표시용 사실
    validate.py   정의 무결성 검사 (`python -m pension_agent.strategy_agent.engine`)

⑥~⑨(상담 화법·예상 반론·참고 자료·안내 콘텐츠)의 규칙 계층은 `support.py` 에 있다.
의존 방향은 customer ← situations ← support ← engine ← agent 이며, support 는 engine 을
임포트하지 않는다. 호출부 편의를 위해 support·situations 의 공개 함수를 여기서 함께
재노출한다.
"""

from __future__ import annotations

from pension_agent.strategy_agent.engine.catalog import (  # noqa: F401
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
from pension_agent.strategy_agent.engine.compose import (  # noqa: F401
    compose_rule,
    final_clause,
    section_summaries,
    verify,
)
from pension_agent.strategy_agent.engine.pipeline import prepare  # noqa: F401
from pension_agent.strategy_agent.engine.products import (  # noqa: F401
    finalize_products,
    gate_amount,
    gate_static,
    query_products,
    static_candidates,
    _best,
)
from pension_agent.strategy_agent.engine.recommend import (  # noqa: F401
    candidate_pool_for_recommendation,
    product_return,
    recommendation_facts,
    render_recommendation,
    top_reference_products,
)
from pension_agent.strategy_agent.engine.render import (  # noqa: F401
    customer_facing_asset,
    customer_header_line,
    _action,
    _return_label,
    _three_way_breakdown,
    _verb,
    _why_this_customer,
)
from pension_agent.strategy_agent.engine.scoring import (  # noqa: F401
    effect_label,
    _card,
    _effect_grade,
    _score,
    _value_tag,
)
from pension_agent.strategy_agent.engine.text import (  # noqa: F401
    format_sources,
    won,
    _pname,
    _ret_of,
)
from pension_agent.strategy_agent.engine.validate import validate  # noqa: F401

# customer 상수 재노출 — 예전 engine.py 가 모듈 전역으로 갖고 있던 이름들.
from pension_agent.strategy_agent.customer import (  # noqa: F401
    CONDS,
    PREF,
    PRIO,
    RISK,
    RISK_ASSET_CAP_PCT,
    TAX_CREDIT_CAP_WON,
    TODAY,
    Profile,
    churn,
    conditions,
    days_to_year_end,
)

# ⑥~⑨ 규칙 계층 재노출 — 호출부(engine.pick_talking_points 등)와 테스트가 그대로 동작한다.
from pension_agent.strategy_agent.situations import problem_situations  # noqa: F401
from pension_agent.strategy_agent.support import (  # noqa: F401
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
