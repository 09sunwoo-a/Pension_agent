"""계측 세션 실행 — CLI·회귀 검사 공용. 운영 진입점 `graph.ask()` 그대로 호출(히스토리·상담이력 포함).

계측은 세션 단위 — 턴마다 열면 재컴파일·히스토리 단절.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext

from pension_agent.common import session_store
from pension_agent import graph as G
from tests.debug import script, trace as TR


@contextmanager
def session(*, customer_id: str | None = None,
            scenario: script.Scenario | None = None,
            on_progress: Callable[[str], None] | None = None,
            history: list[dict] | None = None,
            session_id: str | None = None,
            employee_id: str | None = None,
            ) -> Iterator[tuple[Callable[[str], dict], TR.Trace]]:
    """계측을 건 채 여러 턴 질의. 반환: (ask 함수, 트레이스).

    - 스크립트 위에 계측 — 스텁 호출도 트레이스에 기록
    - on_progress: 진행 표시 콜백(graph.ask 와 동일). 미지정 시 진행 줄 미출력
    - session_id 미지정 시 컨텍스트마다 새 id — 실행별 세션 분리
    - history / session_id: 대화 상태를 직접 드는 호출자(Streamlit 등)용
    """
    tr = TR.Trace()
    # 직접 만든 id 만 종료 시 정리
    ours = session_id is None
    # 첫 턴 전에 측정 — finally 에서 재면 항상 «있었다»
    existed = bool(customer_id) and session_store._path(customer_id).is_file()
    session_id = session_id or f"debug-{uuid.uuid4().hex[:8]}"
    outer = script.installed(scenario) if scenario else nullcontext()
    try:
        with outer, TR.instrument(tr):
            turns: list[dict] = list(history or [])

            def ask(question: str) -> dict:
                nonlocal turns
                tr.begin_turn(question)
                r = G.ask(question, history=turns, customer_id=customer_id,
                          session_id=session_id, on_progress=on_progress,
                          employee_id=employee_id)
                turns = r["history"]
                return r

            yield ask, tr
    finally:
        # 리허설 기록 정리 — 시연 시드와 같은 커밋 파일이라 남으면 «지난 상담»으로 읽힘
        # - 호출자가 준 id 는 미정리 · 정리 실패는 무시
        # - 고정 id 세션(correction-log·tool-log)도 함께 제거
        # - 기록 없던 고객은 파일째 삭제(빈 `{"sessions": []}` 잔존 방지), 있던 파일은 시드라 유지
        if ours and customer_id:
            try:
                for sid in (session_id, "correction-log", "tool-log"):
                    session_store.drop_session(customer_id, sid)
                if not existed:
                    session_store._path(customer_id).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 — 정리 실패가 리허설을 죽이지 않는다
                pass


def run(questions: list[str], *, customer_id: str | None = None,
        scenario: script.Scenario | None = None) -> tuple[list[dict], TR.Trace]:
    """멀티턴 실행. 반환: (턴별 결과, 트레이스)."""
    with session(customer_id=customer_id, scenario=scenario) as (ask, tr):
        return [ask(q) for q in questions], tr
