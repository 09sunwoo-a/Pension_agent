"""Tool 선언 · ToolFailure.

도구 반환: Evidence(근거) · None(0건) · ToolFailure(실행 실패).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pension_agent.evidence.record import Evidence
from pension_agent.state import AgentState


class ToolFailure(Exception):
    """도구 실행 실패(0건과 별개). LLMError 와 달리 루프는 계속, 해당 도구만 이번 턴 제외."""

    def __init__(self, tool: str, reason: str):
        super().__init__(f"{tool}: {reason}")
        self.tool = tool
        self.reason = reason


@dataclass(frozen=True)
class Tool:
    name: str
    desc: str            # 계획 프롬프트에 실리는 한 줄 설명
    run: Callable[[AgentState, str], Evidence | None]
    # 진행 표시용 재료 이름. 비면 진행 미표시. LLM 질의는 미노출
    progress: str = ""
