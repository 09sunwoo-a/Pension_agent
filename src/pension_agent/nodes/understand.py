"""의도 분류 노드 — 직원 발화를 어느 기능으로 보낼지만 결정.

도메인별 슬롯 추출 미포함(화법 슬롯은 tools/kb_search/pitch_slots.py).
LLM 실패 시 규칙 분류 대체 없음 — LLM_DOWN 으로 종료.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pension_agent.common import observability
from pension_agent.common import progress
from pension_agent.prompts import ROUTE_PROMPT
from pension_agent.routing import DEFAULT_INTENT, INTENTS, LLM_DOWN
from pension_agent.state import AgentState, format_history
from pension_agent.common.llm import LLMError, generate


def understand(state: AgentState) -> dict[str, Any]:
    progress.emit("질문 내용을 파악하고 있어요")
    prompt = ROUTE_PROMPT.format(
        history_block=format_history(state.get("history")),
        question=state["question"],
    )
    try:
        text = generate(prompt, max_tokens=150, name="consult.understand")
    except LLMError as exc:
        # 규칙 분류 대체 금지 — LLM 없이 반쯤 도는 경로가 굳음
        observability.step("understand", error=f"{type(exc).__name__}: {exc}",
                           level=logging.WARNING)
        return {"intent": LLM_DOWN, "utterance": state["question"],
                "llm_error": f"{type(exc).__name__}: {exc}"}

    try:
        m = re.search(r"\{.*\}", text, re.S)
        slots = json.loads(m.group()) if m else {}
    except (json.JSONDecodeError, AttributeError):
        slots = {}  # 분해 실패해도 원문으로 검색은 된다

    intent = slots.get("intent")
    # 목록 밖 값은 기본값(계획 루프)으로 — 오분류돼도 능력 유지
    chosen = intent if intent in INTENTS else DEFAULT_INTENT
    observability.step("understand", intent=chosen)
    return {"intent": chosen, "utterance": slots.get("utterance") or state["question"]}
