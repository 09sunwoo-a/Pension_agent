"""퇴직연금 상담 대화 에이전트 (consult_agent) — 직원이 상담 전·중에 옆에 두고 묻는 대화형 (LangGraph).

층은 넷이고 방향은 한쪽이다 — evidence/ ← tools/ ← effects/ ← 그래프(nodes/·graph.py).
아래 층은 위 층을 임포트하지 않는다(tests/infra/s03_boundaries.py). 파일별 설명은 각 폴더의
__init__.py 머리말, 사용법과 그래프 그림은 README.md, 동작의 기준은 CLAUDE.md.

    그래프 뼈대
    graph.py          build_agent · ask      LangGraph 조립 · 단발 진입점
    state.py          AgentState · Turn      그래프 상태 · 대화이력 한 턴 · 이력 포맷
                      KB · HISTORY_* · FENCE 공용 지식베이스 · 이력 창 크기 · 쪽지 펜스
    routing.py        INTENTS · route_*      의도 목록 · 분기 predicate (상태만 본다)
    context_store.py  get · put · drop       대화 맥락 보관 — 게이트웨이 경로용 메모리
    __main__.py                              REPL
    progress.py       emit · reporting       진행 표시 — nodes·tools 가 함께 쓴다

    하위 패키지
    nodes/            그래프 노드 8
    prompts/          프롬프트 상수 29
    tools/            LLM 이 고르는 도구 20종
    evidence/         도구가 근거를 찾고·맞추고·대조하는 층 12
    effects/          답변 뒤·그래프 밖 5

`ask` 는 지연 재노출한다. 여기서 바로 임포트하면 이 패키지를 건드리는 모든 경로가
LangGraph 와 지식베이스 적재를 함께 끌고 오게 된다.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "ask":
        from pension_agent.consult_agent.graph import ask

        return ask
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
