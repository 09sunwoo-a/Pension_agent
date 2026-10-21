"""⑥~⑨ 지원 섹션 규칙 계층 — 후보군 조회·적격 판정·폴백 확정(LLM 미호출).

    reference_kb.py  근거 대조용 지식베이스 적재 (지연 로딩)
    matching.py      문제상황 → 지식 카드 — ⑥⑦⑧ 공통 후보군 산출
    talking.py       ⑥ 이렇게 말해보세요
    objection.py     ⑦ 예상 반론 및 대응 화법
    resource.py      ⑧ 상담에 참고하세요
    assets.py        ⑨ 고객님께 안내해보세요 — 세미나·이벤트 선정·발송 문구 골격

engine 임포트 금지(의존: customer ← situations ← support ← engine) — 표기값은 engine.prepare() 가 미리 렌더링.
"""

from __future__ import annotations

from pension_agent.common.clock import today  # noqa: F401 — 두 시간축 재노출(오늘)
from pension_agent.tools.customer_solution.customer import AS_OF  # noqa: F401 — 두 시간축 재노출(원장 기준일)
from pension_agent.tools.customer_solution.support.reference_kb import (  # noqa: F401
    load_reference_kb,
    pitch_kb,
    pitch_kb_module,
)
from pension_agent.tools.customer_solution.support.matching import (  # noqa: F401
    ASSETS,
    GENERAL_METHOD_GROUP,
    GENERAL_PITCH_GROUP,
    GENERAL_PROCEDURE_GROUP,
    MAX_OBJECTION_CANDIDATES,
    MAX_RESOURCE_CANDIDATES,
    card_source,
    situation_cards,
    situation_methods,
    situation_procedures,
)
from pension_agent.tools.customer_solution.support.objection import (  # noqa: F401
    objection_candidates,
    pick_objections,
    _objection_entry,
    _type_eligible,
)
from pension_agent.tools.customer_solution.support.assets import (  # noqa: F401
    AD_PREFIX,
    KEYWORD_CONDS,
    OPT_OUT,
    conds_of,
    lms_frame,
    next_event_and_seminar,
    outreach_candidates,
    relevant_outreach,
    rule_body,
    schedule_text,
    _outreach_row,
)
from pension_agent.tools.customer_solution.support.resource import (  # noqa: F401
    consult_resource_candidates,
    consult_resources,
)
from pension_agent.tools.customer_solution.support.talking import (  # noqa: F401
    pick_talking_points,
    pitch_talk,
    _card_talk,
)
