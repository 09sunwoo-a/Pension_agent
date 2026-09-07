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
화법 슬롯 분해는 pitch.py, 계획 루프는 plan.py, 되묻기 판정·답변 작성은 answer.py,
메타 질문 응답은 meta.py, LMS 화면 연계는 lms.py, 브리핑수정은 correction.py.
LLM 프롬프트는 prompts.py.

**답변을 만드는 경로는 계획 루프 하나다.** 값·절차·고객군·브리핑 질의가 각자 노드를 갖고
있던 시절이 있었는데, 같은 재료를 두 경로로 답하면 프롬프트·검증·표시 규약이 갈리고
(CLAUDE.md §3·§12 gap 11) LLM 이 죽어도 절반은 도는 경로가 굳는다(§11). 지금 남은 전용
노드는 계획 루프로 답할 수 없는 것들뿐이다 — routing.py 주석 참고.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph


from pension_agent import observability
from pension_agent.session_store import append_turn
from pension_agent.strategy_agent import customer as CUST

from pension_agent.consult_agent import progress, suggest

from pension_agent.consult_agent.nodes.act import confirm_action, offer
from pension_agent.consult_agent.nodes.answer import answer
from pension_agent.consult_agent.nodes.correction import correction
from pension_agent.consult_agent.nodes.lms import lms_link
from pension_agent.consult_agent.nodes.meta import agent_help
from pension_agent.consult_agent.nodes.plan import llm_down, plan_step
from pension_agent.consult_agent.nodes.understand import understand
from pension_agent.consult_agent.routing import (
    LLM_DOWN, route_answer, route_confirm, route_intent, route_plan,
)
from pension_agent.consult_agent.state import HISTORY_LIMIT, AgentState

#: 답변 끝 추천질문 블록의 머리말. `plan.MISSING_NOTICES`·`MATERIAL_MARKS` 와 같은 꼴로,
#: **프론트가 이 블록만 떼어낼 수 있게** 고정 문자열로 둔다(반환값의 "followups" 를 쓰면
#: 떼어낼 필요도 없다). 지금은 텍스트로 붙이고, 실서비스 프론트가 칩 UI 를 따로 만든다.
FOLLOWUP_HEADER = "── 이어서 물어보실 수 있어요"



def build_agent():
    g = StateGraph(AgentState)
    g.add_node("understand", understand)
    g.add_node("agent_help", agent_help)
    g.add_node("plan", plan_step)
    # 노드 라벨은 compose 다 — 상태 키에 `answer`(최종 화법)가 있는데, 행내 환경의
    # langgraph(구버전)는 노드 이름이 상태 키와 같으면 add_node 에서 거부한다
    # ("'answer' is already being used as a state key"). 개발 환경(1.x)은 그 검사가
    # 없어 여기서만 통과했었다. 함수 이름(answer)은 그대로라 트레이스 계측은 안 변한다.
    g.add_node("compose", answer)
    g.add_node("lms_link", lms_link)
    g.add_node("correction", correction)
    g.add_node(LLM_DOWN, llm_down)
    g.add_node("confirm_action", confirm_action)
    g.add_node("offer", offer)

    g.add_edge(START, "understand")
    g.add_conditional_edges(
        "understand", route_intent,
        ["agent_help", "plan", "lms_link", "correction", "confirm_action", LLM_DOWN],
    )
    # 계획 루프 — LLM 이 도구를 고르고(plan), 코드가 상한에서 끊고(route_plan),
    # 모은 근거만으로 답을 낸다(answer). 능력 표면은 intent enum 이 아니라 tools.TOOLS 다.
    g.add_conditional_edges("plan", route_plan, ["plan", "compose"])
    # answer 안에서 되묻기 판정과 답변 작성이 함께 끝난다(nodes/answer.py). 되묻기로
    # 끝난 턴에는 화면 연계 제안이 붙지 않는다 — 제안이 붙을 수 있는 자리는 여기뿐이고,
    # 붙일지는 offer 안의 규칙이 정한다(§10).
    g.add_conditional_edges("compose", route_answer, {"offer": "offer", "__end__": END})
    # 승낙 턴 — 화면 연계는 URL 하나로 끝나고, 화법 제시는 근거만 실린 채 answer 로 간다.
    # 답변을 만드는 경로를 둘로 늘리지 않기 위해서다(routing.route_confirm). 그 턴에는
    # 되묻기 판정이 돌지 않는다 — 입력이 "네" 한 글자다(clarify.applicable).
    g.add_conditional_edges("confirm_action", route_confirm,
                            {"compose": "compose", "__end__": END})
    g.add_edge("offer", END)
    for node in ("agent_help", "lms_link", "correction", LLM_DOWN):
        g.add_edge(node, END)
    return g.compile()


_AGENT = None


def _customer_name(customer_id: str | None) -> str | None:
    """관측 표시용 고객 이름. 없으면 None — 답변 경로는 이 값을 쓰지 않는다.

    이름을 못 찾아도 조용히 지나간다(로스터에 없는 id·원장 미적재). 관측이 답변을
    막지 않는다는 규약이 여기에도 적용된다.
    """
    if not customer_id:
        return None
    try:
        profile = CUST.get_profile(customer_id)
    except Exception:                          # noqa: BLE001 — 표시용 값이 턴을 죽이지 않는다
        return None
    return getattr(profile, "nm", None)


def ask(
    question: str, history: list[dict] | None = None,
    *, customer_id: str | None = None, session_id: str = "default",
    on_progress: Callable[[str], None] | None = None,
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
    on_progress: 진행 표시 콜백. 답변이 만들어지는 동안 "무엇을 하고 있는지" 한 줄씩
    받는다(문구는 전부 코드가 정한다 — progress.py). ContextVar 로 전달되므로 상태·
    history 에 콜러블이 들어가지 않고, 콜백이 죽어도 답변 생성은 계속된다.
    """
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()
    # 관측 트레이스 — 이 턴에서 나가는 LLM 호출(계획·판정·작성, 보통 4~7회)이 전부 이
    # 하나에 묶인다. 키가 없으면 통째로 꺼진다(observability). session_id 를 넘겨 같은
    # 상담의 턴들이 대시보드에서 한 줄로 이어지게 한다.
    # 관측의 «누구인가» — 고객이다. Langfuse 의 user 는 보통 최종 사용자를 뜻하지만,
    # 대시보드를 열고 찾는 것은 「이 고객에 대한 실행 전부」(브리핑 + 대화 턴)이고, 두
    # 진입점에 함께 있는 안정된 id 는 이것뿐이다(직원 id 는 아직 진입점이 받지 않는다).
    # 표기 꼴은 브리핑 쪽과 어긋나면 안 되므로 `customer_ref` 한 곳이 정한다.
    who = observability.customer_ref(customer_id, _customer_name(customer_id))
    with observability.trace(
        "consult.turn", input=question, session_id=session_id,
        user_id=who["user_id"], metadata=who["metadata"],
        tags=["consult", *who["tags"]],
    ) as span:
        with progress.reporting(on_progress):
            out = _AGENT.invoke(
                {"question": question, "history": history or [], "customer_id": customer_id,
                 # history 도구가 «지난번»에서 이번 세션을 제외할 수 있게 세션 구분자를 싣는다.
                 "session_id": session_id})
        evidence = out.get("evidence") or []
        span.update(output=out.get("answer"), intent=out.get("intent"),
                    tools=sorted({e["tool"] for e in evidence}))
        # 턴 하나가 어떻게 끝났는지. 「되묻기가 몇 %인가 · 근거 0건이 몇 %인가 · LLM 이
        # 죽은 턴이 있었나」를 대시보드가 집계한다 — 트레이스를 한 건씩 열어서는 못 센다.
        observability.score(
            "turn_outcome",
            "llm_down" if out.get("llm_error") else "clarify" if out.get("clarify") else "answer",
            comment=out.get("llm_error"))
        observability.score("evidence_count", len(evidence))
    answer = out["answer"]
    # 답변 끝 추천질문 — 조건이 아니면 아무것도 붙지 않는다(suggest.followup_questions).
    # **모든 intent 가 지나는 여기 한 곳**에서 붙인다. 노드마다 붙이면 새 intent 가
    # 추가될 때 빠지고, 빠진 것이 눈에 안 띈다(상담이력 기록을 여기서 하는 것과 같은 이유).
    followups = suggest.followup_questions(out)
    if followups:
        answer += "\n\n" + FOLLOWUP_HEADER + "\n" + "\n".join(f"· {q}" for q in followups)
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
        # 무슨 재료로 답했는지(이름만). 다음 턴이 «이미 나열한 것»을 알아야 좁히는 후속
        # 질문에 앞 답을 통째로 반복하지 않는다(state.Turn 의 tools 주석).
        "tools": sorted({e["tool"] for e in (out.get("evidence") or [])}),
    }
    new_history = [*(history or []), turn][-HISTORY_LIMIT:]

    if customer_id:
        append_turn(customer_id, session_id, {
            "role": "user", "text": question, "intent": out.get("intent"),
        })
        append_turn(customer_id, session_id, {
            # 기록에는 **추천질문을 붙이기 전의 답변**을 남긴다. 추천질문은 화면 장치이지
            # 고객에게 한 안내가 아니고, 이 기록은 `history` 도구가 다음 상담에서 재료로
            # 되읽는 텍스트다 — 거기에 UI 문구가 섞이면 그게 지난 상담 내용이 된다.
            "role": "agent", "text": out.get("answer", ""), "intent": out.get("intent"),
        })

    return {
        "answer": answer, "sources": out.get("sources", []), "history": new_history,
        # 추천질문만 따로 쓰고 싶은 프론트를 위해 리스트로도 준다 — answer 끝의 블록과
        # 같은 내용이다(프론트가 붙이면 answer 쪽 블록은 떼면 된다).
        "followups": followups,
        "intent": out.get("intent"),  # 프론트가 correction 처럼 미완성 기능을 표시할 때 씀
        # 확인을 기다리는 도구 제안. 화면이 버튼으로 실행하고 싶을 때 이걸 그대로 쓰면 된다
        # (대화로 "네" 라고 답해도 같은 경로로 실행된다).
        "pending_action": out.get("pending_action"),
        # 이 턴이 답변 대신 판별 질문으로 끝났으면 그 질문과 선택지. 화면이 선택지를
        # 버튼으로 띄우고 싶을 때 쓴다.
        "clarify": out.get("clarify"),
    }
