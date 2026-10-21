"""LLM 프롬프트 — 쓰는 모듈과 같은 이름의 파일. 전부 여기서 재노출(`prompts.COMPOSE_SYSTEM`).

`*_BLOCK` 은 조건이 맞을 때 코드가 끼우는 조각(판정은 코드).

    라우팅
    understand.py   ROUTE_PROMPT               질문을 어느 기능으로 보낼지

    근거 수집
    plan.py         PLAN_PROMPT                다음에 부를 도구 하나
                    PLAN_MISSES_BLOCK          빗나간 호출 목록(반복 방지)
                    PLAN_BUDGET_BLOCK          남은 바퀴 수 한 줄
                    PLAN_RETRY_BLOCK           근거 0건 종료 시도 시 재계획 지시
    select.py       BUCKET_PROMPT · SELECT_PROMPT   버킷 → 카드 2단 선택
    pitch.py        SITUATION_PROMPT           화법 검색용 상황 슬롯 추출
    adequacy.py     ADEQUACY_PROMPT            고른 근거가 질문에 답이 되는가

    답변 작성
    compose.py      COMPOSE_SYSTEM · COMPOSE_PROMPT   원장만으로 답변 작성
                    ANSWER_SHAPES · SHAPE_BLOCK  도구별 답 필수 요소와 그 블록
                    MUST_BLOCK                 필수 인용 스팬이 있을 때
                    COMPOSE_RETRY_BLOCK        검증에서 잘린 뒤 다시 쓸 때
                    COMPOSE_PREMISE_BLOCK      전제를 밝히고 답할 때
                    COMPOSE_MISSING_BLOCK      핵심 대상이 없을 때
                    REPEAT_BLOCK               직전 턴과 같은 재료일 때
                    REWRITE_BLOCK              이전 답변을 다시 쓰는 턴(줄여줘·쉽게)
                    ACCEPTED_BLOCK             승낙받아 실은 참고자료 턴
    clarify.py      CLARIFY_PROMPT             답의 형태 판정
                    JUDGE_BRANCHES_BLOCK       적합성 게이트가 표시한 갈래

    메인 브리핑
    branch.py       BRANCH_SQL_SYSTEM · BRANCH_SQL_PROMPT   부점 데이터 질문 → 고정 뷰 SELECT 한 문장
                    BRANCH_SQL_RETRY_BLOCK     검사·실행에 걸린 SQL 을 다시 쓸 때

    특수 턴 · 쪽지
    meta.py         HELP_SYSTEM · HELP_PROMPT  에이전트 자신에 대한 질문
    correction.py   CORRECTION_SYSTEM · CORRECTION_PROMPT   브리핑 산문 수정
    memo.py         MEMO_SYSTEM · MEMO_PROMPT  이번 턴의 재료로 제목·본문
                    MEMO_SELF_GUIDE · MEMO_OTHER_GUIDE   받는 사람이 본인 / 다른 직원
                    MEMO_TABLE_BLOCK           코드가 값 표를 붙일 때
                    MEMO_ADDRESSEE_LINE        받는 사람 호칭(이름·직급)을 알 때
                    MEMO_EDIT_SYSTEM · MEMO_EDIT_PROMPT  걸린 초안을 직원 지시대로 수정
                    MEMO_EDIT_RECIPIENT_BLOCK  받는 사람 변경 시 호칭 맞춤
                    MEMO_EDIT_ECHO_BLOCK       직원 말이 본문에 통째로 들어갔을 때 재작성
"""

from __future__ import annotations

from pension_agent.prompts.understand import (  # noqa: F401
    ROUTE_PROMPT,
)
from pension_agent.prompts.pitch import (  # noqa: F401
    SITUATION_PROMPT,
)
from pension_agent.prompts.select import (  # noqa: F401
    BUCKET_PROMPT,
    SELECT_PROMPT,
)
from pension_agent.prompts.adequacy import (  # noqa: F401
    ADEQUACY_PROMPT,
)
from pension_agent.prompts.compose import (  # noqa: F401
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
from pension_agent.prompts.correction import (  # noqa: F401
    CORRECTION_SYSTEM,
    CORRECTION_PROMPT,
)
from pension_agent.prompts.plan import (  # noqa: F401
    PLAN_PROMPT,
    PLAN_MISSES_BLOCK,
    PLAN_BUDGET_BLOCK,
    PLAN_RETRY_BLOCK,
)
from pension_agent.prompts.clarify import (  # noqa: F401
    CLARIFY_PROMPT,
    JUDGE_BRANCHES_BLOCK,
)
from pension_agent.prompts.meta import (  # noqa: F401
    HELP_SYSTEM,
    HELP_PROMPT,
)
from pension_agent.prompts.memo import (  # noqa: F401
    MEMO_SYSTEM,
    MEMO_SELF_GUIDE,
    MEMO_OTHER_GUIDE,
    MEMO_TABLE_BLOCK,
    MEMO_ADDRESSEE_LINE,
    MEMO_PROMPT,
    MEMO_EDIT_SYSTEM,
    MEMO_EDIT_PROMPT,
    MEMO_EDIT_RECIPIENT_BLOCK,
    MEMO_EDIT_ECHO_BLOCK,
)
from pension_agent.prompts.branch import (  # noqa: F401
    BRANCH_SQL_SYSTEM,
    BRANCH_SQL_PROMPT,
    BRANCH_SQL_RETRY_BLOCK,
)
