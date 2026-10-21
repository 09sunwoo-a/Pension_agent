"""연금 에이전트 — 퇴직연금·IRP 직원 상담을 돕는 LangGraph 에이전트.

한 턴은 understand → plan(도구 선택·실행 반복) → compose(답변) → offer(후속 제안) 순서로
흐른다. 경계(부를 수 있는 도구·수치 계산·답변 검사)는 코드가 정하고, LLM 은 그 안에서
도구를 고르고 문장을 쓴다.

    graph.py          build_agent · ask      그래프 조립 · 단발 진입점
    state.py          AgentState · Turn      그래프 상태 · 대화이력 한 턴
    routing.py        INTENTS · route_*      의도 목록 · 분기 판정
    context_store.py  get · put · drop       대화 맥락 보관(게이트웨이 경로용 메모리)
    turn_trace.py                            턴 트레이스
    __main__.py                              REPL (python -m pension_agent)

    nodes/            그래프 단계
    tools/            plan 이 고르는 기능 — 기능마다 하위 폴더
      customer_solution/   고객별 솔루션(고객 원장·타겟 판정·추천 계산·브리핑)
      kb_search/           지식베이스 검색(카드·시황·화법·참고자료)
      general/             날짜·세액공제 계산·상담 이력·직전 답변
    evidence/         근거 원장의 규약과 답변 검사(verify)
    actions/          답변 뒤 실행: 발송 화면·쪽지 초안·추천 칩
    prompts/          LLM 프롬프트
    knowledge/        지식 카드 적재·스키마·카드 데이터·시황 소스
    common/           설정·환경변수·시계·LLM·MCP·로깅 등 공용 기반

의존 방향과 허용 간선은 `tests/infra/s03_boundaries.py` 가 고정한다.

`ask` 는 지연 재노출한다. 여기서 바로 임포트하면 이 패키지를 건드리는 모든 경로가
LangGraph 와 지식베이스 적재를 함께 끌고 오게 된다.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "ask":
        from pension_agent.graph import ask

        return ask
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
