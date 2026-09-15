"""직원 상담 대화 에이전트 (LangGraph).

    state.py     AgentState · 대화이력 · 공용 지식베이스(KB)
    routing.py   그래프 분기 표 (상태만 보고 다음 노드를 고른다)
    graph.py     그래프 조립 + 단발 진입점 ask()
    kb_index.py  LLM 카드 선택용 계층 인덱스 · 프롬프트 컨텍스트 (적재·검색은 knowledge/kb.py)
    tools/       도구 패키지 — 레지스트리(__init__) · 근거 규약(base) · 도구별 모듈 · 원장 helper(ledger) · 관계 점검(relations). 근거를 **찾는** 쪽
    actions.py   행위 레지스트리 — 승낙 뒤 코드가 실행하는 것(발송 화면 게이트 · 쪽지). 세상에 흔적을 **남기는** 쪽
    guard.py     「하지 말 것」 — 지식베이스에 있는 금지 문장만 띄운다
    prompts/     프롬프트 문자열 — 노드와 같은 이름의 모듈로 나눠 둔다 (문구만 고칠 때 로직을 건드리지 않도록 분리)
    nodes/       그래프 노드만 — understand · plan · answer(+clarify) · meta · lms · correction · act
                 (도구가 쓰는 검색·근거 조립 — facts_qa · procedure_qa · segment_qa · pitch_slots — 는 tools/ 에 있다)

`ask` 는 지연 재노출한다. 여기서 바로 임포트하면 이 패키지를 건드리는 모든 경로가
LangGraph 와 지식베이스 적재를 함께 끌고 오게 된다 — select 만 필요한 경로도
그 비용을 내야 한다.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "ask":
        from pension_agent.consult_agent.graph import ask

        return ask
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
