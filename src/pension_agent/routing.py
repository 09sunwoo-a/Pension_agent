"""그래프 분기 predicate — 상태 필드만으로 다음 노드 선택. LLM·지식베이스 미사용.

능력 표면은 `tools.TOOLS`. 전용 노드 의도는 계획 루프로 답할 수 없는 것만:

  agent_help      에이전트 자신에 대한 질문
  correction      화면 산문 수정(직전 답변 수정은 계획 루프로 반환)
  lms_link        화면 연계 요청
  confirm_action  직전 턴 제안에 대한 확인
  llm_down        LLM 장애로 분류 실패

나머지는 기본값(situation) → 계획 루프.
"""

from __future__ import annotations

from pension_agent.state import AgentState

#: 계획 루프로 가는 기본 의도.
DEFAULT_INTENT = "situation"

#: LLM 장애 턴 · 노드 이름(nodes/plan.py::llm_down).
LLM_DOWN = "llm_down"

# 메타 질문 의도는 `agent_help` — `capability` 는 기능정의 «가능 여부 확인»·capability kind 와 혼동돼 사용 금지
INTENTS = (
    DEFAULT_INTENT, "guide", "agent_help", "correction", "lms_link",
    # 직전 턴 제안에 대한 확인 응답("네"/"아니오")
    "confirm_action",
)


# 표에 없는 의도는 계획 루프로
_INTENT_NODE = {
    "agent_help": "agent_help",
    "correction": "correction",
    "lms_link": "lms_link",
    "confirm_action": "confirm_action",
    LLM_DOWN: LLM_DOWN,
}


def route_intent(state: AgentState) -> str:
    # LLM 장애 턴은 분류와 무관하게 안내 하나로 종료
    if state.get("llm_error"):
        return LLM_DOWN

    # 쪽지 초안이 걸려 있으면 분류와 무관하게 확인 노드로(수정 요청·새 질문 판정은 그 노드)
    last = ((state.get("history") or [{}])[-1]) or {}
    if (last.get("pending_action") or {}).get("kind") == "memo":
        return "confirm_action"

    # 제안 없이 되묻기만 있던 턴의 확인 응답 오분류(«2번째꺼» 등)는 되묻기의 답 — 계획 루프로
    if state.get("intent") == "confirm_action":
        if not last.get("pending_action") and last.get("pending_clarify"):
            return "plan"

    # «쪽지»만 말한 요청을 lms_link 로 오분류하면 쪽지 요청이 소실 — 계획 루프로
    if state.get("intent") == "lms_link":
        said = state.get("question") or ""
        if "쪽지" in said and not any(w in said for w in _LMS_WORDS):
            return "plan"

    return _INTENT_NODE.get(state.get("intent"), "plan")


#: 고객 문자 발송 표현 — 있으면 lms_link 분류 유지.
_LMS_WORDS = ("LMS", "lms", "문자", "SMS", "sms", "MMS", "mms")


def route_plan(state: AgentState) -> str:
    """도구를 더 부를지, 답을 낼지. 상한은 plan.MAX_STEPS."""
    return "compose" if state.get("plan_done") else "plan"


def route_answer(state: AgentState) -> str:
    """되묻기 턴은 종료, 아니면 offer. 되묻기와 연계 제안을 한 턴에 겹치지 않음."""
    return "__end__" if state.get("clarify") else "offer"


def route_correction(state: AgentState) -> str:
    """수정 노드가 답을 냈으면 종료, 아니면(직전 답변 수정 요청) 계획 루프."""
    return "__end__" if state.get("answer") else "plan"


def route_confirm(state: AgentState) -> str:
    """승낙 턴: 답 있으면 종료, 근거만 있으면 compose, 둘 다 없으면 plan. 승낙 노드의 카드 직접 렌더 금지."""
    if state.get("answer"):
        return "__end__"
    # 답·근거 모두 없음 = 쪽지 초안 중 새 질문(`act._memo_reply`)
    return "compose" if state.get("evidence") else "plan"
