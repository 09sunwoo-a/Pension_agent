"""Tool 선언 · ToolFailure. 모든 도구 모듈이 여기에 기댄다.

━━ 반환 규약 ━━
Evidence 또는 None. None 은 «이 도구로는 근거를 못 찾았다»이고, 루프는 다른 도구를
시도하거나 원장이 빈 채로 끝낸다(→ 정직한 '없음' 답변). 도구가 억지로 뭔가 만들어내는
경로는 두지 않는다. 세 번째 결과가 ToolFailure 다 — 확인한 0건과 확인하지 못한 것은 다른
사건이고, `run()`(tools/__init__)이 그 경계를 세운다.

Evidence 의 규약과 조립 헬퍼(_ev)는 `evidence/record.py` 에 있다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pension_agent.consult_agent.evidence.record import Evidence
from pension_agent.consult_agent.state import AgentState


class ToolFailure(Exception):
    """도구가 죽었다 — **«확인했는데 0건»이 아니라 «확인하지 못함»이다**.

    예전에는 `tools.run` 이 이 자리에서 `None` 을 돌려줬다(= 빗나간 호출). 그러면 렌더러·
    매핑에서 난 예외 하나가 계획에는 «질의가 빗나갔다»로, 원장이 빈 채 끝난 턴에는
    «지식베이스에서 찾지 못했습니다»로 나갔다 — 자료는 멀쩡히 있는데 없다고 답하는 것이고,
    §11 이 LLM 미연결에 대해 막는 실패와 정확히 같은 모양이다(찾아보고 없는 것과 찾아보지도
    못한 것을 같은 문장으로 말하면 안 된다).

    `LLMError` 와 갈라 두는 이유는 **처분이 다르기 때문**이다. LLM 이 죽으면 그 턴에는
    아무것도 할 수 없어 루프를 끊지만, 도구 하나가 죽은 것은 나머지 도구로 답이 나올 수
    있다 — 루프는 계속되고(`nodes/plan.py::plan_step`), 그 도구만 이번 턴의 능력 표면에서
    빠진다(`tools.usable`). 원장이 끝내 비었을 때만 답이 갈린다.
    """

    def __init__(self, tool: str, reason: str):
        super().__init__(f"{tool}: {reason}")
        self.tool = tool
        self.reason = reason


@dataclass(frozen=True)
class Tool:
    name: str
    desc: str            # 계획 프롬프트에 실리는 한 줄 설명
    run: Callable[[AgentState, str], Evidence | None]
    # 진행 표시에 찍히는 재료 이름("단말 화면번호"…). 문구는 코드가 정한다는 규칙
    # (progress.py ①)이 도구에 적용된 자리다 — LLM 이 만든 질의는 진행 표시에 싣지
    # 않는다(질의가 곧 지어낸 문장일 수 있다). 비어 있으면 그 도구는 진행을 알리지 않는다.
    progress: str = ""
