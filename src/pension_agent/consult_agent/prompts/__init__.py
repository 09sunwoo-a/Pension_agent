"""LLM에 보내는 프롬프트 템플릿 — 노드(nodes/)와 같은 이름의 모듈에 나눠 둔다.

문구만 고칠 때 노드 로직을 건드리지 않도록 분리했다. 부르는 쪽은 예전처럼
`from pension_agent.consult_agent import prompts` 로 받아 `prompts.COMPOSE_SYSTEM` 처럼 쓴다 —
이름은 전부 여기서 재노출한다(이 패키지의 공개 표면이다).

    understand.py   ROUTE_PROMPT — 어떤 기능으로 보낼지 (nodes/understand.py)
    pitch.py        SITUATION_PROMPT — 화법 슬롯 (tools/pitch.py)
    select.py       BUCKET_PROMPT · SELECT_PROMPT — LLM 카드 선택 (select.py)
    adequacy.py     ADEQUACY_PROMPT — 적합성 게이트 (tools/adequacy.py)
    compose.py      COMPOSE_* · ANSWER_SHAPES · *_BLOCK — 답변 생성과 답의 형태 (nodes/plan.py compose)
    correction.py   CORRECTION_* — 브리핑 수정 (nodes/correction.py)
    plan.py         PLAN_* — 계획 루프 (nodes/plan.py)
    clarify.py      CLARIFY_PROMPT · JUDGE_BRANCHES_BLOCK — 되묻기 (nodes/clarify.py)
    memo.py         MEMO_* — WorkB 쪽지 (memo.py)
"""

from __future__ import annotations

from pension_agent.consult_agent.prompts.understand import (  # noqa: F401
    ROUTE_PROMPT,
)
from pension_agent.consult_agent.prompts.pitch import (  # noqa: F401
    SITUATION_PROMPT,
)
from pension_agent.consult_agent.prompts.select import (  # noqa: F401
    BUCKET_PROMPT,
    SELECT_PROMPT,
)
from pension_agent.consult_agent.prompts.adequacy import (  # noqa: F401
    ADEQUACY_PROMPT,
)
from pension_agent.consult_agent.prompts.compose import (  # noqa: F401
    COMPOSE_SYSTEM,
    COMPOSE_PROMPT,
    MUST_BLOCK,
    COMPOSE_RETRY_BLOCK,
    COMPOSE_PREMISE_BLOCK,
    COMPOSE_MISSING_BLOCK,
    ANSWER_SHAPES,
    REPEAT_BLOCK,
    ACCEPTED_BLOCK,
    REWRITE_BLOCK,
    SHAPE_BLOCK,
)
from pension_agent.consult_agent.prompts.correction import (  # noqa: F401
    CORRECTION_SYSTEM,
    CORRECTION_PROMPT,
)
from pension_agent.consult_agent.prompts.plan import (  # noqa: F401
    PLAN_PROMPT,
    PLAN_MISSES_BLOCK,
    PLAN_BUDGET_BLOCK,
    PLAN_RETRY_BLOCK,
)
from pension_agent.consult_agent.prompts.clarify import (  # noqa: F401
    CLARIFY_PROMPT,
    JUDGE_BRANCHES_BLOCK,
)
from pension_agent.consult_agent.prompts.memo import (  # noqa: F401
    MEMO_SYSTEM,
    MEMO_SELF_GUIDE,
    MEMO_OTHER_GUIDE,
    MEMO_TABLE_BLOCK,
    MEMO_PROMPT,
)
