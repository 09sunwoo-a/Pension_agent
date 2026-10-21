"""화법 검색용 슬롯 분해(고객유형·거절유형·단계)와 '파악된 상황' 한 줄.

슬롯은 화법 n-gram 폴백의 후보 축소용. _pitch 가 필요할 때만 호출.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.prompts import SITUATION_PROMPT
from pension_agent.state import KB, AgentState, format_history
from pension_agent.common.llm import LLMError, generate


def extract_slots(state: AgentState) -> dict[str, Any]:
    prompt = SITUATION_PROMPT.format(
        customer_types=KB.customer_types,
        objection_types=KB.objection_types,
        stages=KB.stages,
        history_block=format_history(state.get("history")),
        intent=state.get("intent"),
        utterance=state.get("utterance"),
        question=state["question"],
    )
    try:
        text = generate(prompt, max_tokens=150, name="consult.pitch")
    except LLMError:
        # LLM 장애는 삼키지 않음 — 턴이 «LLM 미연결»로 끝나야 함
        raise

    try:
        m = re.search(r"\{.*\}", text, re.S)
        slots = json.loads(m.group()) if m else {}
    except (json.JSONDecodeError, AttributeError):
        slots = {}  # 분해 실패해도 utterance 만으로 검색은 된다

    return {
        "customer_type": slots.get("customer_type"),
        "objection_type": slots.get("objection_type"),
        "stage": slots.get("stage"),
    }


def situation_line(intent: str | None, slots: dict[str, Any] | None) -> str:
    """작성 프롬프트용 '파악된 상황' 한 줄. 슬롯 없으면 빈 문자열 — 없는 상담 상황 상상 방지."""
    if not slots:
        return ""
    stage = slots.get("stage") or "미파악"
    if intent == "guide":
        return f"파악된 상황 — 직원 업무 절차 질문 (분야: {stage})"
    customer_type = slots.get("customer_type") or "미파악"
    objection_type = slots.get("objection_type") or "미파악"
    return f"파악된 상황 — 고객유형 {customer_type} / 거절유형 {objection_type} / 단계 {stage}"
