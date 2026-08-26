"""직원 상담 대화 에이전트 (LangGraph).

    state.py     AgentState · 대화이력 · 공용 지식베이스(KB)
    routing.py   그래프 분기 표 (상태만 보고 다음 노드를 고른다)
    graph.py     그래프 조립 + 단발 진입점 ask()
    kb.py        지식베이스 적재·검색·검증
    guard.py     「하지 말 것」 — 지식베이스에 있는 금지 문장만 띄운다
    prompts.py   프롬프트 문자열 (문구만 고칠 때 로직을 건드리지 않도록 분리)
    nodes/       노드 구현 — understand · pitch · plan · drill · lms · correction · act · meta

`ask` 는 지연 재노출한다. 여기서 바로 임포트하면 이 패키지를 건드리는 모든 경로가
LangGraph 와 지식베이스 적재를 함께 끌고 오게 된다 — strategy_agent.support 는 kb 만
필요한데도 그 비용을 내야 한다.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "ask":
        from pension_agent.consult_agent.graph import ask

        return ask
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
