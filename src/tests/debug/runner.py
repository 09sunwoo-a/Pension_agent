"""한 번 돌리고 트레이스를 돌려준다 — CLI 와 회귀 검사가 같은 경로를 쓰게 하는 자리.

실행은 **운영 진입점 `graph.ask()` 를 그대로** 부른다. 그래프를 직접 조립해 부르면
그때부터는 "운영과 같은 경로"라고 말할 수 없다 — `ask()` 가 하는 일(히스토리 갱신·상담이력
기록)까지 포함해서 봐야 화면에서 본 것과 같은 실행이다.

계측을 **세션 단위로** 연다. REPL 은 턴마다 열고 닫을 수 없다 — 그래프를 매번 다시
컴파일하게 되고, 무엇보다 히스토리가 이어지지 않으면 그건 멀티턴 재현이 아니다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext

from pension_agent.consult_agent import graph as G
from tests.debug import script, trace as TR


@contextmanager
def session(*, customer_id: str | None = None,
            scenario: script.Scenario | None = None,
            on_progress: Callable[[str], None] | None = None,
            history: list[dict] | None = None,
            session_id: str = "default",
            ) -> Iterator[tuple[Callable[[str], dict], TR.Trace]]:
    """계측을 걸어둔 채 여러 턴을 이어 묻는다. 반환: (ask 함수, 트레이스).

    스크립트를 먼저 걸고 그 위에 계측을 얹는다 — 계측이 스텁을 감싸므로 스텁 호출도
    트레이스에 남는다. 나갈 때는 역순으로 풀린다.

    on_progress: 진행 표시 콜백(graph.ask 와 같다). 리허설이 운영과 같은 경로로 돌려면
    화면이 대기 중에 보여주는 진행 줄까지 같은 배선으로 받아야 한다 — 넘기지 않으면
    emit 은 no-op 이라(progress.py) 리허설 출력에 진행 표시가 아예 안 나온다.

    history / session_id: 대화 상태를 **자기가 들고 있는 호출자**를 위한 자리다. CLI 는
    한 프로세스 안에서 여러 턴을 이어 물으므로 이 컨텍스트가 히스토리를 직접 들면 되지만,
    Streamlit 은 턴마다 스크립트를 처음부터 다시 돌리므로 컨텍스트가 턴 하나보다 오래
    살지 못한다. 그런 호출자가 앞 턴 히스토리를 넘겨 «이어지는 대화»로 계측할 수 있게
    받는다. 넘기지 않으면 예전처럼 빈 히스토리로 시작한다.
    """
    tr = TR.Trace()
    outer = script.installed(scenario) if scenario else nullcontext()
    with outer, TR.instrument(tr):
        turns: list[dict] = list(history or [])

        def ask(question: str) -> dict:
            nonlocal turns
            tr.begin_turn(question)
            r = G.ask(question, history=turns, customer_id=customer_id,
                      session_id=session_id, on_progress=on_progress)
            turns = r["history"]
            return r

        yield ask, tr


def run(questions: list[str], *, customer_id: str | None = None,
        scenario: script.Scenario | None = None) -> tuple[list[dict], TR.Trace]:
    """질문을 순서대로 한 턴씩 돌린다(멀티턴). 반환: (턴별 결과, 트레이스)."""
    with session(customer_id=customer_id, scenario=scenario) as (ask, tr):
        return [ask(q) for q in questions], tr
