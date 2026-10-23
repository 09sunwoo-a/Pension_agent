"""턴의 마지막 — 형태 판정(clarify)과 답변 작성(compose)을 동시 실행.

판정이 ask 면 작성분 폐기. assume·none 이면 judge_note 를 얹어 한 번 재작성(실패 시 처음 답).
판정 LLM 실패 시 작성 결과를 그대로 반환.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pension_agent.common import progress
from pension_agent.nodes.clarify import applicable, clarify
from pension_agent.nodes.plan import compose, document_answer
from pension_agent.state import AgentState


def answer(state: AgentState) -> dict[str, Any]:
    """판정·작성 병행. 원장에 완성 문서(브리핑·관리 대상 표)가 있으면 그대로 반환."""
    doc = document_answer(state)
    if doc is not None:
        return doc
    if not applicable(state):
        # 판정 없는 턴 — 스레드 불필요
        return compose(state)

    # 진행 표시 콜백은 ContextVar — 컨텍스트 복사 없으면 스레드에서 진행 표시 소실
    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="compose") as pool:
        drafting = pool.submit(ctx.run, compose, state)
        verdict = clarify(state)
        written = drafting.result()

    if verdict.get("clarify"):
        return verdict          # 되물음 — 작성분 폐기

    note = verdict.get("judge_note")
    if note and written.get("answer") and not written.get("llm_error"):
        progress.emit("판정한 형태에 맞춰 답변을 다시 쓰고 있어요")
        again = compose({**state, "judge_note": note})
        # 재작성이 폴백이면 처음 것 유지 — 폴백도 answer 가 채워지므로 fallback 으로 구분
        if again.get("answer") and not again.get("llm_error") and not again.get("fallback"):
            written = again

    # 판정 결과(등급·장애 원인) 위에 작성 결과를 덮어씀
    return {**verdict, **written}
