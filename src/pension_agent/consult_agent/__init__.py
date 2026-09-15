"""퇴직연금 상담 대화 에이전트 (consult_agent) — 직원이 상담 전과 상담 중에 옆에 두고 묻는 대화형 (LangGraph).

층은 넷이고 방향은 한쪽이다 — evidence/ ← tools/ ← effects/ ← 그래프(nodes/·graph.py). 아래 층은
위 층을 임포트하지 않는다(tests/infra/s03_boundaries.py). 파일마다의 한 줄 설명은 각 폴더의
__init__.py 머리말에 있다. 사용법과 그래프 그림은 README.md, 있어야 할 동작의 기준은 CLAUDE.md.

    그래프 뼈대
    graph.py          build_agent · ask        LangGraph 조립 · 단발 진입점. customer_id 가 있는 턴은 session_store 에 상담이력으로 기록
    state.py          AgentState · Turn        그래프 상태 · 대화이력 한 턴 · 프롬프트용 이력 포맷(format_history)
                      KB · HISTORY_* · FENCE   공용 지식베이스(프로세스당 한 번 적재) · 이력 창 크기 · 쪽지 초안 펜스
    routing.py        INTENTS · route_*        의도 목록 · 모든 분기 predicate — 상태 필드만 보고 다음 노드를 고른다
    context_store.py  get · put · drop         대화 맥락(history) 보관 — 프로세스 메모리, (x_client_user, session_id) 키. 게이트웨이가 history 를 못 넘기는 경로용
    __main__.py                                REPL — python -m pension_agent.consult_agent -c <KB-PIN>
    progress.py       emit · reporting         진행 표시 — 답변이 만들어지는 동안 무엇을 하는 중인지. 문구는 코드가 정하고 nodes·tools 가 함께 쓴다

    하위 패키지
    nodes/            그래프 노드 8            understand(의도) · plan(도구 루프·답변 작성) · answer · clarify · meta · lms · correction · act
    prompts/          프롬프트 상수 29         쓰는 모듈과 같은 이름 — understand · plan · select · pitch · adequacy · clarify · compose · correction · memo
    tools/            LLM 이 고르는 도구 20종   레지스트리(__init__) · 규약(base) · 도구 모듈 · 결합(combine) · 적합성 게이트(adequacy)
    evidence/         근거 층 12               찾고(kb_index·select·*_qa·pitch_slots·guard) 맞추고(record·ledger) 대조하는(relations·marks) 것
    effects/          효과 층 5                답변 뒤·그래프 밖 — actions(승낙 뒤 행위) · memo(쪽지 초안) · screens(딥링크) · suggest(칩) · render(텍스트)

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
