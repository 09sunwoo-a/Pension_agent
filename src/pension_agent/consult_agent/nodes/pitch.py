"""화법 검색 전용 슬롯 분해 — `situation_slots` 와 그것이 만드는 '파악된 상황' 한 줄.

예전에는 이 파일이 화법 검색 흐름 전체(llm_select·retrieve·broaden·verify·respond·fallback)를
갖고 있었다. 그 여섯 노드는 계획 루프가 들어오면서 자리를 옮겼다 —

  llm_select·retrieve·broaden  →  tools.py::_pitch (화법 도구의 3단 선택)
  verify                       →  tools.py::fits_question (오답 게이트 — 지금은 모든 도구가 쓴다)
  respond                      →  nodes/plan.py::_talk (compose 가 대사를 쓸 때)
  fallback                     →  nodes/plan.py::NO_EVIDENCE (원장이 비었을 때)

옮긴 이유는 그 흐름이 **그래프의 한 갈래**였기 때문이다. 화법이 하나의 도구가 되면 다른
재료(수치·절차·고객)와 한 답변에 섞일 수 있는데, 그래프 갈래로 있으면 END 로 직행해서 섞일
자리가 없었다.

남은 것은 화법 검색에만 필요한 고객유형·거절유형·상담단계 분해다. 뽑은 슬롯은 화법 도구의
n-gram 폴백 후보를 좁히는 데 쓰이고, 답변 작성 프롬프트의 '파악된 상황' 한 줄이 된다.

━━ 노드가 아니라 도구 안에서 돈다 ━━
한때 이것이 계획 루프 앞의 노드였다(`situation_slots`) — 모든 턴이 화법 검색을 한다고
전제한 배선이다. 능력 표면이 도구 목록이 되면서 그 전제가 깨졌다: "이 고객 예금 잔액
얼마지"는 `customer` 도구 하나면 끝나는데도 화법 슬롯 분해에 LLM 호출을 한 번 썼다.
값 하나 묻는 턴이 6번의 순차 LLM 호출로 끝나던 이유 중 하나가 이것이다.

지금은 `_pitch` 가 필요할 때 한 번 부른다. 화법을 안 부르는 턴은 이 호출 자체가 없다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.consult_agent.prompts import SITUATION_PROMPT
from pension_agent.consult_agent.state import KB, AgentState, format_history
from pension_agent.llm import LLMError, generate


# ─────────────────────────────────────────────────────────────
# 화법 검색 전용 슬롯 분해 — 화법 도구가 필요할 때만 부른다
# ─────────────────────────────────────────────────────────────

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
        text = generate(prompt, max_tokens=150)
    except LLMError:
        # 슬롯 분해가 실패해도 화법 검색은 원문(utterance)만으로 돈다 — 슬롯은 n-gram
        # 폴백의 후보를 좁히는 보조 정보다. LLM 이 정말 죽었다면 뒤이은 적합성 판정·
        # 답변 작성이 같은 이유로 실패하고, 턴은 'LLM 연결이 안 되어 있다'로 끝난다(§11).
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


# ─────────────────────────────────────────────────────────────
# 프롬프트에 넣는 '파악된 상황' 한 줄
# ─────────────────────────────────────────────────────────────

def situation_line(intent: str | None, slots: dict[str, Any] | None) -> str:
    """답변 작성 프롬프트에 넣을 '파악된 상황' 한 줄.

    화법 도구가 슬롯을 뽑았을 때만 만든다 — 값·절차만 물은 턴에 "고객유형 미파악 / 거절유형
    미파악"을 실어 보내면 LLM 이 있지도 않은 상담 상황을 상상하게 된다. guide 질문엔 애초에
    없는 고객유형·거절유형을 '미파악'으로 채워 보여주지 않는 것과 같은 이유다.
    """
    if not slots:
        return ""
    stage = slots.get("stage") or "미파악"
    if intent == "guide":
        return f"파악된 상황 — 직원 업무 절차 질문 (분야: {stage})"
    customer_type = slots.get("customer_type") or "미파악"
    objection_type = slots.get("objection_type") or "미파악"
    return f"파악된 상황 — 고객유형 {customer_type} / 거절유형 {objection_type} / 단계 {stage}"
