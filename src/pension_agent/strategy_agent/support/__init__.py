"""⑥~⑨ 지원 섹션의 규칙 계층 — 상담 화법·예상 반론·참고 자료·안내 콘텐츠.

AI 브리핑 화면의 ⑥ 이렇게 말해보세요 · ⑦ 예상 반론 및 대응 화법 · ⑧ 상담에 참고하세요 ·
⑨ 고객님께 안내해보세요(docs/REQUIREMENTS.md §2) 네 섹션의 '규칙(Rule)' 몫. 후보군 조회·
적격 판정·폴백 확정까지가 여기 일이고, 후보 중 무엇을 고를지(선별)와 대고객 문장화는
agent.py 의 LLM 단계가 맡는다(§15). 이 패키지는 LLM 을 호출하지 않는다.

    kb.py         근거 대조용 지식베이스 적재 (지연 로딩)
    matching.py   문제상황 → 지식 카드 — ⑥⑦⑧ 공통 후보군 산출
    talking.py    ⑥ 이렇게 말해보세요
    objection.py  ⑦ 예상 반론 및 대응 화법
    resource.py   ⑧ 상담에 참고하세요
    outreach.py   ⑨ 고객님께 안내해보세요

engine 에서 분리한 이유 — engine 의 ①~⑤(요건 판정·자금 배분·기대효과·추천)는 다른 담당자가
고치는 영역이고, ⑥~⑨ 는 공용 지식(knowledge/data)을 읽는 영역이라 변경 축이 다르다. 한 곳에
두면 서로의 수정이 충돌한다. engine 은 아래 이름들을 그대로 재노출하므로 기존 호출부
(`engine.pick_talking_points` 등)는 변함없이 동작한다.

의존 방향은 `customer ← situations ← support ← engine ← agent` 로 고정한다 — 이 패키지는
engine 을 임포트하지 않는다. 그래서 engine 의 표기 유틸(won·_pname)이 필요한 값은
engine.prepare() 가 항목에 미리 렌더링해 넘긴다(`amount_fmt`·`products_fmt`).
"""

from __future__ import annotations

from pension_agent.strategy_agent.customer import AS_OF, today  # noqa: F401 — 두 시간축 재노출
from pension_agent.strategy_agent.support.kb import (  # noqa: F401
    load_reference_kb,
    pitch_kb,
    pitch_kb_module,
)
from pension_agent.strategy_agent.support.matching import (  # noqa: F401
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
from pension_agent.strategy_agent.support.objection import (  # noqa: F401
    objection_candidates,
    pick_objections,
    _objection_entry,
    _type_eligible,
)
from pension_agent.strategy_agent.support.outreach import (  # noqa: F401
    AD_PREFIX,
    KEYWORD_CONDS,
    OPT_OUT,
    conds_of,
    lms_frame,
    next_event_and_seminar,
    outreach_candidates,
    rule_body,
    schedule_text,
    _outreach_row,
)
from pension_agent.strategy_agent.support.resource import (  # noqa: F401
    consult_resource_candidates,
    consult_resources,
)
from pension_agent.strategy_agent.support.talking import (  # noqa: F401
    pick_talking_points,
    pitch_talk,
    _card_talk,
)
