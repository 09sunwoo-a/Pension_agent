"""LangGraph 기반 IRP 상담 화법 에이전트 — 그래프 조립 · 실행 진입점.

직원이 "이런 상황인데 뭐라고 답변하지?" 라고 자연어로 물으면
지식베이스에서 관련 화법을 찾아 핵심 포인트와 근거를 짚어주는 코칭 조언을 만들어 준다.
후속 질문(이전 대화 맥락 이어가기)도 지원한다.

    from graph import ask
    r = ask("사업자 고객인데 수수료 부담된다고 하시네요")
    print(r["answer"])
    r2 = ask("그럼 안 된다고 하면요?", history=r["history"])   # 후속 질문

그래프 구조(노드·분기 다이어그램)는 README.md 참고. 이 파일은 그래프를
조립하고(build_agent) 단발 호출 헬퍼(ask)와 CLI 진입점만 담당한다.
노드 함수는 기능별로 나뉘어 있다 — 상태정의는 state.py, 분기 predicate 는 routing.py,
화법 슬롯 분해는 pitch.py, 계획 루프는 plan.py, 메타 질문 응답은 meta.py, LMS발송은 lms.py,
브리핑수정은 correction.py. LLM 프롬프트는 prompts.py.

**답변을 만드는 경로는 계획 루프 하나다.** 값·절차·고객군·브리핑 질의가 각자 노드를 갖고
있던 시절이 있었는데, 같은 재료를 두 경로로 답하면 프롬프트·검증·표시 규약이 갈리고
(CLAUDE.md §3·§12 gap 11) LLM 이 죽어도 절반은 도는 경로가 굳는다(§11). 지금 남은 전용
노드는 계획 루프로 답할 수 없는 것들뿐이다 — routing.py 주석 참고.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph


from pension_agent.session_store import append_turn

from pension_agent.consult_agent.nodes.act import confirm_action, offer
from pension_agent.consult_agent.nodes.clarify import clarify
from pension_agent.consult_agent.nodes.correction import correction
from pension_agent.consult_agent.nodes.lms import lms_send
from pension_agent.consult_agent.nodes.meta import agent_help
from pension_agent.consult_agent.nodes.plan import compose, llm_down, plan_step
from pension_agent.consult_agent.nodes.understand import understand
from pension_agent.consult_agent.routing import (
    LLM_DOWN, route_clarify, route_confirm, route_intent, route_plan,
)
from pension_agent.consult_agent.state import HISTORY_LIMIT, AgentState

# 답변을 만든 뒤 화면 연계를 제안할 노드. 제안 여부는 offer 안의 규칙이 정한다(§10) —
# 여기 있다는 것은 "제안이 붙을 수 있는 자리"라는 뜻이지 매번 붙는다는 뜻이 아니다.
_OFFERING_NODES = ("compose",)


def build_agent():
    g = StateGraph(AgentState)
    g.add_node("understand", understand)
    g.add_node("agent_help", agent_help)
    g.add_node("plan", plan_step)
    g.add_node("clarify", clarify)
    g.add_node("compose", compose)
    g.add_node("lms_send", lms_send)
    g.add_node("correction", correction)
    g.add_node(LLM_DOWN, llm_down)
    g.add_node("confirm_action", confirm_action)
    g.add_node("offer", offer)

    g.add_edge(START, "understand")
    g.add_conditional_edges(
        "understand", route_intent,
        ["agent_help", "plan", "lms_send", "correction", "confirm_action", LLM_DOWN],
    )
    # 계획 루프 — LLM 이 도구를 고르고(plan), 코드가 상한에서 끊고(route_plan),
    # 모은 근거만으로 답을 쓴다(compose). 능력 표면은 intent enum 이 아니라 tools.TOOLS 다.
    g.add_conditional_edges("plan", route_plan, ["plan", "clarify"])
    # 되묻기 턴은 여기서 끝난다 — 답변도 화면 연계 제안도 붙지 않는다(§5).
    g.add_conditional_edges("clarify", route_clarify, {"compose": "compose", "__end__": END})
    # 승낙 턴 — 화면 연계는 URL 하나로 끝나고, 화법 제시는 근거만 실린 채 compose 로 간다.
    # 답변을 만드는 경로를 둘로 늘리지 않기 위해서다(routing.route_confirm).
    g.add_conditional_edges("confirm_action", route_confirm,
                            {"compose": "compose", "__end__": END})
    for node in _OFFERING_NODES:
        g.add_edge(node, "offer")
    g.add_edge("offer", END)
    for node in ("agent_help", "lms_send", "correction", LLM_DOWN):
        g.add_edge(node, END)
    return g.compile()


_AGENT = None


def ask(
    question: str, history: list[dict] | None = None,
    *, customer_id: str | None = None, session_id: str = "default",
) -> dict[str, Any]:
    """단발 호출용 헬퍼. FastAPI 핸들러에서 이것만 부르면 된다.

    후속 질문에 맥락을 이어가려면 이번 호출이 돌려준 "history"를 다음
    호출에 그대로 넘긴다. 세션(대화 묶음) 유지는 호출자 책임 — 이 함수
    자체는 상태를 들고 있지 않는다.

    customer_id: 현재 열려 있는 브리핑 화면의 고객 id. 고객 관련 기능(브리핑 질의·수정·
    화면 연계)은 이것이 있어야 성립한다 — 없으면 "고객 화면을 먼저 열어달라"고 답한다
    (CLAUDE.md §3). 지식 질의응답·화법 코칭은 없이도 답한다.
    session_id: 상담 세션 구분자(REQUIREMENTS.md §14 상담이력 단위). 넘기지 않으면 "default"
    세션으로 기록된다 — 모든 턴은 intent 와 무관하게 이 진입점 한 곳에서 기록되므로, 새
    intent 가 추가돼도 상담이력 기록을 빠뜨릴 일이 없다.
    """
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()
    out = _AGENT.invoke({"question": question, "history": history or [], "customer_id": customer_id})
    turn = {
        "question": question,
        "customer_type": out.get("customer_type"),
        "objection_type": out.get("objection_type"),
        "stage": out.get("stage"),
        "utterance": out.get("utterance"),
        # 이번 턴이 도구 실행을 제안했다면 실행 인자만 남긴다 — 다음 턴의 "네" 가 무엇에 대한
        # 승낙인지 알아야 실행할 수 있다(state.Turn 주석 참고).
        "pending_action": out.get("pending_action"),
        # 이 턴이 되묻기로 끝났으면 무엇을 물었는지 남긴다 — 다음 턴의 "타행에서요"는
        # 그 질문의 답이지 새 질문이 아니다(state.Turn).
        "pending_clarify": out.get("clarify"),
    }
    new_history = [*(history or []), turn][-HISTORY_LIMIT:]

    if customer_id:
        append_turn(customer_id, session_id, {
            "role": "user", "text": question, "intent": out.get("intent"),
        })
        append_turn(customer_id, session_id, {
            "role": "agent", "text": out.get("answer", ""), "intent": out.get("intent"),
        })

    return {
        "answer": out["answer"], "sources": out.get("sources", []), "history": new_history,
        "intent": out.get("intent"),  # 프론트가 correction 처럼 미완성 기능을 표시할 때 씀
        # 확인을 기다리는 도구 제안. 화면이 버튼으로 실행하고 싶을 때 이걸 그대로 쓰면 된다
        # (대화로 "네" 라고 답해도 같은 경로로 실행된다).
        "pending_action": out.get("pending_action"),
        # 이 턴이 답변 대신 판별 질문으로 끝났으면 그 질문과 선택지. 화면이 선택지를
        # 버튼으로 띄우고 싶을 때 쓴다.
        "clarify": out.get("clarify"),
    }
