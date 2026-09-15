"""LLM 프롬프트 문자열 — 쓰는 모듈과 같은 이름으로 나눠 둔다. 문구만 고칠 때 로직을 건드리지 않기 위해서다.

부르는 쪽은 `from pension_agent.consult_agent import prompts` 로 받아 `prompts.COMPOSE_SYSTEM` 처럼
쓴다 — 이름은 전부 여기서 재노출한다(이 패키지의 공개 표면이다). `*_BLOCK` 은 조건이 맞을 때만
코드가 프롬프트에 끼우는 조각이고, 끼울지는 전부 코드가 판정한다.

    라우팅
    understand.py   ROUTE_PROMPT               질문(+이전 대화)을 어느 기능으로 보낼지 — intent·utterance 만 (nodes/understand)

    근거 수집 — 계획 루프와 카드 선택
    plan.py         PLAN_PROMPT                다음에 부를 도구 하나를 고른다 (nodes/plan.plan_step)
                    PLAN_MISSES_BLOCK          빗나간 호출 목록 — 같은 호출을 반복하지 않게
                    PLAN_BUDGET_BLOCK          남은 바퀴 수 한 줄. 끝내라고 시키지는 않는다(상한은 코드 MAX_STEPS)
                    PLAN_RETRY_BLOCK           근거 0건으로 끝내려 할 때 코드가 한 번 되돌리며 끼우는 재계획 지시
    select.py       BUCKET_PROMPT · SELECT_PROMPT  버킷 → 카드 2단 선택 (evidence/select)
    pitch.py        SITUATION_PROMPT           화법 검색용 상황 슬롯(고객유형·거절유형·상담단계) 추출 (evidence/pitch_slots)
    adequacy.py     ADEQUACY_PROMPT            고른 근거가 질문에 답이 되는가 · 갈래가 있으면 표시 (tools/adequacy)

    답변 작성 (nodes/plan.compose · nodes/clarify)
    compose.py      COMPOSE_SYSTEM · COMPOSE_PROMPT  원장만으로 답변 작성 — 직원에게 코칭하는 해요체
                    ANSWER_SHAPES · SHAPE_BLOCK  도구별 «답에 반드시 들어가야 하는 것» 표와 그것을 끼우는 블록
                    MUST_BLOCK                 필수 인용 스팬(atomic·notices)이 있을 때
                    COMPOSE_RETRY_BLOCK        검증에서 잘린 뒤 한 번 다시 쓸 때 — 무엇이 잘렸는지
                    COMPOSE_PREMISE_BLOCK      형태 판정이 «전제를 밝히고 답한다»일 때 — 「가정하면」 금지
                    COMPOSE_MISSING_BLOCK      형태 판정이 «핵심 대상이 없다»일 때 — 없다고 먼저 말하고 가진 것을 준다
                    REPEAT_BLOCK               직전 턴과 같은 재료로 답할 때 — 앞 답을 통째로 다시 세우지 않게
                    REWRITE_BLOCK              «줄여줘·쉽게» 처럼 이전 답변을 다시 쓰는 턴 — 자료를 이번 질문에 대고 재지 않게
                    ACCEPTED_BLOCK             승낙받아 실은 참고자료 턴 — 적합성 판정을 걸지 않는다
    clarify.py      CLARIFY_PROMPT             답의 형태 판정 — 답한다 · 전제를 밝힌다 · 되묻는다 · 없다 (§5)
                    JUDGE_BRANCHES_BLOCK       적합성 게이트가 표시한 갈래를 판정에 끼운다(state["branches"])

    특수 턴
    correction.py   CORRECTION_SYSTEM · CORRECTION_PROMPT  브리핑 산문 수정 — 편집 가능 필드만 (nodes/correction)

    쪽지 (effects/memo)
    memo.py         MEMO_SYSTEM · MEMO_PROMPT  이번 턴의 재료로 제목·본문
                    MEMO_SELF_GUIDE · MEMO_OTHER_GUIDE  받는 사람이 직원 본인 / 다른 직원일 때의 안내 갈래
                    MEMO_TABLE_BLOCK           코드가 값 표를 붙일 때 — 본문이 같은 값을 다시 나열하지 않게
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
