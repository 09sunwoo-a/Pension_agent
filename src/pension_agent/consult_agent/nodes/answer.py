"""턴의 마지막 — 형태 판정(clarify)과 답변 작성(compose)을 **동시에** 돌린다.

━━ 왜 동시인가 ━━
둘은 서로를 보지 않는다. 판정도 작성도 원장(evidence)·질문·이전 대화만 먹고, 판정이
"되묻자"면 작성 결과는 쓰이지 않으며 "그냥 답하자"면 작성은 판정의 출력을 한 글자도
쓰지 않는다. 그런데 배선은 직렬이라 작성이 판정을 기다렸다 — **순수 대기**다. 가장 단순한
지식 질문도 순차 LLM 왕복 6~7번인 파이프라인에서, 그중 하나가 아무 일도 하지 않고 있었다.

그래서 둘을 같이 던지고, 판정이 "되묻자"로 나오면 **이미 쓴 답을 버린다**(투기적 실행).
되묻기는 코드 관문 넷을 통과한 자리에서만 일어나므로 버려지는 일이 잦지 않고, 버려질 때
잃는 것은 토큰이지 답의 품질이 아니다.

━━ 판정이 등급이 되면서 생긴 자리 — 다시 쓰기 ━━
판정값이 넷이 되면서(§5 · nodes/clarify.py) 둘은 «작성에 지시를 준다»가 됐다: 전제를
밝히고 답하라(assume) · 핵심 대상이 자료에 없다고 먼저 밝혀라(none). 그 둘만은 작성이
판정의 출력을 쓴다.

**그렇다고 판정을 작성 앞에 세우지 않는다.** 그러면 위의 동시 실행이 통째로 사라지고 모든
턴이 직렬 왕복 하나를 더 치른다 — 대기 시간도 요건의 일부다(§5 "한 번 더 묻는 비용이
직원 쪽에 크다"와 같은 이유). 지금대로 동시에 던지고, 그 두 등급일 때만 블록을 얹어 한 번
다시 쓴다. 비용을 내는 것은 지시가 실제로 필요한 턴뿐이고, 그 모양은 이미 있는 재작성
경로(plan.COMPOSE_RETRIES)와 같다.

**다시 쓴 것이 비면 처음 것을 낸다.** 판정을 도우려던 장치가 답을 없애면 안 된다 —
게이트에 걸려 폐기되는 경우가 그렇고, 그때 남는 것은 판정 전에 쓴 검증된 답이다.

━━ 무엇이 바뀌지 않았나 ━━
**판정도 작성도 함수 본체가 그대로다.** 코드 관문·게이트 어느 것도 손대지 않았다.
되묻기가 «틀린 답을 막는 것»이라는 §5 의 지위도 그대로다 — 판정이 되묻자고 하면 작성이
아무리 그럴듯한 답을 써 놨어도 그 답은 나가지 않는다.

━━ 판정이 깨졌을 때 ━━
직렬일 때와 같은 답으로 끝난다. 판정이 LLM 장애로 죽으면 원인만 상태에 남기고 작성 결과가
나갔다(`routing.route_clarify` 가 clarify 키만 봤다) — 그 규약을 그대로 옮긴다. 판정 실패와
작성 실패를 다르게 다루지 않는 이유는 §11 이다: 어느 단계에서 깨졌든 직원이 받는 답은
같아야 하고, 작성이 성공했다면 그 답은 근거 안에서 나온 검증된 답이다.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pension_agent.consult_agent import progress
from pension_agent.consult_agent.nodes.clarify import applicable, clarify
from pension_agent.consult_agent.nodes.plan import compose
from pension_agent.consult_agent.state import AgentState


def answer(state: AgentState) -> dict[str, Any]:
    """판정과 답변 작성을 함께 끝낸다. 되묻기로 결정되면 작성분은 버린다."""
    if not applicable(state):
        # 판정이 아예 없는 턴(근거 0건·갈래를 만들 수 없는 재료·직전 턴이 되물음).
        # 스레드를 띄울 이유가 없다 — 지금까지와 완전히 같은 경로다.
        return compose(state)

    # 진행 표시 콜백은 ContextVar 로 전달된다(progress.py) — 스레드에는 자동으로 따라가지
    # 않으므로 현재 컨텍스트를 복사해 넘긴다. 안 하면 "작성하고 있어요"가 조용히 사라진다.
    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="compose") as pool:
        drafting = pool.submit(ctx.run, compose, state)
        verdict = clarify(state)
        written = drafting.result()

    if verdict.get("clarify"):
        return verdict          # 되묻기로 턴이 끝난다 — 써 둔 답은 버린다(§5)

    note = verdict.get("judge_note")
    if note and written.get("answer") and not written.get("llm_error"):
        progress.emit("판정한 형태에 맞춰 답변을 다시 쓰고 있어요")
        again = compose({**state, "judge_note": note})
        # 다시 쓴 것이 게이트에 걸려 근거 원문 폴백으로 떨어졌는지는 여기서 알 수 없다 —
        # 볼 수 있는 것은 «답이 있나 · LLM 이 죽었나»다. 그 둘만 보고 바꾼다.
        if again.get("answer") and not again.get("llm_error"):
            written = again

    # 되묻지 않는다. 판정이 남긴 것(등급·장애 원인)에 작성 결과를 얹는다 — 직렬로 두 노드를
    # 지날 때 상태가 갱신되던 순서와 같다.
    return {**verdict, **written}
