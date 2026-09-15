"""직원 상담 대화 에이전트 (LangGraph).

    graph.py · state.py · routing.py · context_store.py · __main__.py
                 그래프 뼈대 — 조립·ask() · 상태·대화이력 포맷 · 분기 표 · 게이트웨이용 맥락 보관 · REPL
    nodes/       그래프 노드만 — understand · plan · answer(+clarify) · meta · lms · correction · act
    prompts/     프롬프트 문자열 — 노드와 같은 이름의 모듈로 나눠 둔다 (문구만 고칠 때 로직을 건드리지 않도록 분리)
    tools/       LLM 이 고르는 도구 20종 — 레지스트리(__init__) · 도구 규약(base) · 도구별 모듈 · 결합(combine) · 적합성 게이트(adequacy)
    evidence/    도구가 근거를 찾고(kb_index·select·*_qa·guard) 원장 항목으로 맞추고(record·ledger) 대조하는(relations·marks) 것
    effects/     답변 뒤·그래프 밖 — actions(승낙 뒤 행위) · memo(쪽지 초안) · screens(딥링크) · suggest(칩) · render(텍스트)
    progress.py  진행 표시 — nodes·tools 가 함께 쓰는 가로지르는 모듈

층의 방향은 한쪽이다: evidence ← tools ← effects ← nodes/graph (tests/infra/s03_boundaries.py).
파일마다의 한 줄 설명은 각 폴더의 __init__.py 머리말에 있다.

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
