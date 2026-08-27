"""그래프 분기 표 — 상태 필드만 보고 다음 노드를 고른다.

LLM 도 지식베이스도 건드리지 않는다. 그래프가 어떻게 갈라지는지 한 파일에서 읽히도록
predicate 를 여기 모았다(노드 구현은 nodes/, 조립은 graph.py).

━━ 의도 목록은 능력 표면이 아니다 ━━
예전에는 `INTENTS` 가 곧 에이전트가 할 수 있는 일의 목록이었다 — 값·절차·고객군·브리핑
질의가 각각 자기 intent 와 자기 노드를 갖고 있었고, 새 재료를 붙이려면 enum·분기표·노드를
함께 늘려야 했다. 지금 능력 표면은 `tools.TOOLS` 다(CLAUDE.md §3). 그래서 여기 남은
의도는 **계획 루프로 답할 수 없는 것들뿐**이다:

  agent_help      에이전트 자신에 대한 질문이라 지식 재료가 없다
  correction      답을 만드는 게 아니라 화면의 산문을 고치는 요청이다
  lms_send        재료 검색이 아니라 화면 연계 요청이다(§10)
  confirm_action  직전 턴의 제안에 대한 확인이라 근거를 모으지 않는다
  llm_down        LLM 이 죽어 분류조차 못 한 턴 (§11)

한때 `objection_drill`(고객 대사를 그대로 던졌을 때의 즉답 카드)이 여기 있었다. 같은
재료(화법)를 쓰면서 답의 **양식**만 다르다는 이유였는데, 그 하나 때문에 계획 루프를
통째로 우회했다 — 적합성 게이트도 재료 성격 표시도 형태 요구도 걸리지 않았고, 카드가
어긋나도 `[고객 발화]` 칸에 엉뚱한 트리거 예시가 그대로 박혀 나갔다. 양식은 도구가
아니다(§5).

나머지는 전부 기본값(situation)으로 떨어져 계획 루프가 재료를 고른다.
"""

from __future__ import annotations

from pension_agent.consult_agent.state import AgentState

#: 계획 루프로 가는 기본 의도. 값·절차·고객군·브리핑 질의가 전부 여기로 떨어진다 —
#: 무엇으로 답할지는 intent 가 아니라 도구 목록이 정하기 때문이다.
DEFAULT_INTENT = "situation"

#: LLM 이 죽어 분류를 못 한 턴. 노드 이름이기도 하다(nodes/plan.py::llm_down).
LLM_DOWN = "llm_down"

# 메타 질문 의도는 `agent_help` 다. `capability` 라는 이름은 쓰지 않는다 — 07_에이전트_기능정의/01 ②
# "가능 여부 즉시 확인"(고객 계좌 상태 플래그로 '이 제안 지금 되나'를 보는 기능, 미구현)과
# strategy_agent 의 `capability` kind(시스템 기능 지원 여부, cap.lms_send)와 혼동되기 때문이다.
INTENTS = (
    DEFAULT_INTENT, "guide", "agent_help", "correction", "lms_send",
    # 직전 턴이 제안한 화면 연계에 대한 확인 응답("네"/"아니오").
    "confirm_action",
)


# 기본 도착지는 계획 루프다. 한때 그 앞에 화법 슬롯 분해 노드가 하나 더 있었는데, 모든
# 턴이 화법 검색을 한다고 전제한 배선이라 값 하나 묻는 턴에도 LLM 호출을 한 번 썼다 —
# 지금은 화법 도구가 필요할 때 스스로 뽑는다(nodes/pitch.py::extract_slots).
_INTENT_NODE = {
    "agent_help": "agent_help",
    "correction": "correction",
    "lms_send": "lms_send",
    "confirm_action": "confirm_action",
    LLM_DOWN: LLM_DOWN,
}


def route_intent(state: AgentState) -> str:
    # LLM 이 죽은 턴은 무엇으로 분류됐든 안내 하나로 끝난다 — 어느 단계에서 실패하든
    # 결과가 같아야 한다(§11).
    if state.get("llm_error"):
        return LLM_DOWN

    # 되묻기의 답을 확인 응답으로 오분류해도 막다른 안내로 끝내지 않는다(§5 · gap 19).
    #
    # 확인할 제안(pending_action)이 있는지는 코드가 아는 값인데, 분류가 confirm_action 을
    # 골랐다는 이유로 그 노드로 보내면 "직전에 제안드린 작업이 없어요"로 턴이 끝난다.
    # 직전 턴이 되물은 턴이면("2번째꺼" 같은 짧은 답이 확인 응답과 닮아 실제로 오분류됐다)
    # 그 답은 되묻기의 답이므로 계획 루프로 보낸다 — 이전 대화(되물음·선택지)가 계획·작성
    # 프롬프트에 실려 있어 그 갈래로 답을 만든다. 제안도 되묻기도 없었다면 confirm_action
    # 노드가 "제안이 없다"고 사실대로 안내하는 것이 맞으므로 그대로 둔다.
    if state.get("intent") == "confirm_action":
        last = ((state.get("history") or [{}])[-1]) or {}
        if not last.get("pending_action") and last.get("pending_clarify"):
            return "plan"

    return _INTENT_NODE.get(state.get("intent"), "plan")


# ─────────────────────────────────────────────────────────────
# 계획 루프(nodes/plan.py)의 분기 predicate
#
# 예전에는 화법 검색 체인의 분기 셋(route_select·route·route_verify)이 여기 있었다.
# 그 체인이 도구 하나(tools.py::_pitch)로 접히면서 분기도 하나로 줄었다.
# ─────────────────────────────────────────────────────────────

def route_plan(state: AgentState) -> str:
    """도구를 한 번 더 부를지, 답을 쓸 준비를 할지. 상한은 plan.MAX_STEPS 가 정한다."""
    return "clarify" if state.get("plan_done") else "plan"


def route_clarify(state: AgentState) -> str:
    """되묻기로 턴을 끝낼지, 답변을 쓸지 (§5).

    되묻기 턴은 compose 도 offer 도 거치지 않고 끝난다 — 답변 전에 갈래를 정하는 것이
    되묻기이고, 화면을 열기 전에 승낙을 받는 것이 연계 확인이다. 둘을 한 턴에 겹치면
    직원은 무엇에 답해야 하는지 모른다.
    """
    return "__end__" if state.get("clarify") else "compose"


def route_confirm(state: AgentState) -> str:
    """승낙 턴을 그대로 끝낼지, 답변 작성으로 보낼지.

    화면 연계 승낙은 URL 하나가 답이라 그 자리에서 끝난다. 화법 제시 승낙은 **지식 카드가
    답**이라 답변을 써야 하고, 그 경로는 계획 루프의 compose 하나뿐이다(graph.py "답변을
    만드는 경로는 계획 루프 하나다") — 승낙 노드가 카드를 손으로 렌더하면 §5 형태 요구도
    §7 표시도 §6 점검도 그 경로만 빠진다.

    판정은 **코드가 아는 값**으로 한다: 근거를 실었는데 답변이 비어 있으면 아직 답이 없는
    턴이다. 제안 종류(kind)로 가르지 않는 이유는, 앞으로 붙는 제안이 늘어도 "근거만 싣고
    답은 compose 가 쓴다"는 규약 하나만 지키면 되기 때문이다.
    """
    return "compose" if state.get("evidence") and not state.get("answer") else "__end__"
