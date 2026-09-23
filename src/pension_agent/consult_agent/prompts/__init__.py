"""LLM 프롬프트 — 쓰는 모듈과 같은 이름으로 나눠 둔다. 문구만 고칠 때 로직을 건드리지 않기 위해서다.

이름은 전부 여기서 재노출한다(`prompts.COMPOSE_SYSTEM`). `*_BLOCK` 은 조건이 맞을 때만
코드가 끼우는 조각이고, 끼울지는 전부 코드가 판정한다.

    라우팅
    understand.py   ROUTE_PROMPT               질문을 어느 기능으로 보낼지

    근거 수집
    plan.py         PLAN_PROMPT                다음에 부를 도구 하나
                    PLAN_MISSES_BLOCK          빗나간 호출 목록 — 같은 호출을 반복하지 않게
                    PLAN_BUDGET_BLOCK          남은 바퀴 수 한 줄
                    PLAN_RETRY_BLOCK           근거 0건으로 끝내려 할 때의 재계획 지시
    select.py       BUCKET_PROMPT · SELECT_PROMPT   버킷 → 카드 2단 선택
    pitch.py        SITUATION_PROMPT           화법 검색용 상황 슬롯 추출
    adequacy.py     ADEQUACY_PROMPT            고른 근거가 질문에 답이 되는가

    답변 작성
    compose.py      COMPOSE_SYSTEM · COMPOSE_PROMPT   원장만으로 답변 작성
                    ANSWER_SHAPES · SHAPE_BLOCK  도구별 «답에 꼭 들어갈 것»과 그 블록
                    MUST_BLOCK                 필수 인용 스팬이 있을 때
                    COMPOSE_RETRY_BLOCK        검증에서 잘린 뒤 다시 쓸 때
                    COMPOSE_PREMISE_BLOCK      전제를 밝히고 답할 때
                    COMPOSE_MISSING_BLOCK      핵심 대상이 없을 때
                    REPEAT_BLOCK               직전 턴과 같은 재료일 때 — 다시 세우지 않게
                    REWRITE_BLOCK              이전 답변을 다시 쓰는 턴(줄여줘·쉽게)
                    ACCEPTED_BLOCK             승낙받아 실은 참고자료 턴
    clarify.py      CLARIFY_PROMPT             답의 형태 판정
                    JUDGE_BRANCHES_BLOCK       적합성 게이트가 표시한 갈래

    특수 턴 · 쪽지
    meta.py         HELP_SYSTEM · HELP_PROMPT  에이전트 자신에 대한 질문 — 코드가 준 사실 안에서
    correction.py   CORRECTION_SYSTEM · CORRECTION_PROMPT   브리핑 산문 수정
    memo.py         MEMO_SYSTEM · MEMO_PROMPT  이번 턴의 재료로 제목·본문
                    MEMO_SELF_GUIDE · MEMO_OTHER_GUIDE   받는 사람이 본인 / 다른 직원
                    MEMO_TABLE_BLOCK           코드가 값 표를 붙일 때
                    MEMO_EDIT_SYSTEM · MEMO_EDIT_PROMPT  걸려 있는 초안을 직원의 지시대로 고친다
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
from pension_agent.consult_agent.prompts.meta import (  # noqa: F401
    HELP_SYSTEM,
    HELP_PROMPT,
)
from pension_agent.consult_agent.prompts.memo import (  # noqa: F401
    MEMO_SYSTEM,
    MEMO_SELF_GUIDE,
    MEMO_OTHER_GUIDE,
    MEMO_TABLE_BLOCK,
    MEMO_PROMPT,
    MEMO_EDIT_SYSTEM,
    MEMO_EDIT_PROMPT,
)
