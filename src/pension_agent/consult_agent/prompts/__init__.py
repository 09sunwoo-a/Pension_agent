"""LLM 프롬프트 문자열 — 쓰는 모듈과 같은 이름으로 나눠 둔다. 문구만 고칠 때 로직을 건드리지 않기 위해서다.

부르는 쪽은 `from pension_agent.consult_agent import prompts` 로 받아 `prompts.COMPOSE_SYSTEM` 처럼
쓴다 — 이름은 전부 여기서 재노출한다(이 패키지의 공개 표면이다).

    understand.py   ROUTE_PROMPT                        질문을 어느 기능으로 보낼지 (nodes/understand)
    plan.py         PLAN_PROMPT · PLAN_*_BLOCK          다음에 부를 도구 하나 고르기 · 빗나감·예산·재시도 블록 (nodes/plan.plan_step)
    select.py       BUCKET_PROMPT · SELECT_PROMPT       버킷 → 카드 2단 선택 (evidence/select)
    pitch.py        SITUATION_PROMPT                    화법 검색용 상황 슬롯 추출 (evidence/pitch_slots)
    adequacy.py     ADEQUACY_PROMPT                     고른 근거가 질문에 답이 되는가 (tools/adequacy)
    clarify.py      CLARIFY_PROMPT · JUDGE_BRANCHES_BLOCK  답의 형태 판정 · 되묻기 선택지 (nodes/clarify)
    compose.py      COMPOSE_SYSTEM · COMPOSE_PROMPT · ANSWER_SHAPES · *_BLOCK
                                                        원장만으로 답변 작성 · 재시도·전제·누락 보완·형태 블록 (nodes/plan.compose)
    correction.py   CORRECTION_SYSTEM · CORRECTION_PROMPT  브리핑 산문 수정 (nodes/correction)
    memo.py         MEMO_SYSTEM · MEMO_*_GUIDE · MEMO_PROMPT  쪽지 제목·본문 — 본인용/타인용 안내 갈래 (effects/memo)
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
