"""한 번 돌리고 트레이스를 돌려준다 — CLI 와 회귀 검사가 같은 경로를 쓰게 하는 자리.

실행은 **운영 진입점 `graph.ask()` 를 그대로** 부른다. 그래프를 직접 조립해 부르면
그때부터는 "운영과 같은 경로"라고 말할 수 없다 — `ask()` 가 하는 일(히스토리 갱신·상담이력
기록)까지 포함해서 봐야 화면에서 본 것과 같은 실행이다.
"""

from __future__ import annotations

from contextlib import nullcontext

from pension_agent.consult_agent import graph as G
from tests.debug import script, trace as TR


def run(questions: list[str], *, customer_id: str | None = None,
        scenario: script.Scenario | None = None) -> tuple[list[dict], TR.Trace]:
    """질문을 순서대로 한 턴씩 돌린다(멀티턴). 반환: (턴별 결과, 트레이스)."""
    tr = TR.Trace()
    outer = script.installed(scenario) if scenario else nullcontext()
    # 스크립트를 먼저 걸고 그 위에 계측을 얹는다 — 계측이 스텁을 감싸므로 스텁 호출도
    # 트레이스에 남는다. 나갈 때는 역순으로 풀린다.
    with outer, TR.instrument(tr):
        results: list[dict] = []
        history: list[dict] = []
        for question in questions:
            tr.begin_turn(question)
            r = G.ask(question, history=history, customer_id=customer_id)
            history = r["history"]
            results.append(r)
    return results, tr
