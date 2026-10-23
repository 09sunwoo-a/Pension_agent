"""그래프 조립(build_agent) · 단발 진입점(ask).

    r = ask("사업자 고객인데 수수료 부담된다고 하시네요")
    r2 = ask("그럼 안 된다고 하면요?", history=r["history"])   # 후속 질문

답변 생성 경로는 계획 루프 하나. 전용 노드는 계획 루프로 답할 수 없는 의도만(routing.py).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph


from pension_agent.common import llm, observability, note
from pension_agent.common.session_store import append_turn
from pension_agent.tools.customer_solution import customer as CUST
from pension_agent.tools import customer_selection

from pension_agent.common import progress
from pension_agent import tools, turn_trace
from pension_agent.tools.customer_solution import guard
from pension_agent.actions import messages as lms_messages, screens, suggest

from pension_agent.nodes.act import confirm_action, offer
from pension_agent.nodes.answer import answer
from pension_agent.nodes.correction import correction
from pension_agent.nodes.lms import lms_link
from pension_agent.nodes.meta import agent_help
from pension_agent.nodes import plan
from pension_agent.nodes.plan import llm_down, plan_step
from pension_agent.nodes.understand import understand
from pension_agent.routing import (
    LLM_DOWN, route_answer, route_confirm, route_correction, route_intent, route_plan,
)
from pension_agent.state import ANSWER_KEEP, HISTORY_LIMIT, KB, AgentState

#: 답변 끝 추천질문 블록 머리말 — 프론트가 떼어낼 수 있게 고정 문자열.
FOLLOWUP_HEADER = "── 이어서 물어보실 수 있어요"



def build_agent():
    g = StateGraph(AgentState)
    g.add_node("understand", understand)
    g.add_node("agent_help", agent_help)
    g.add_node("plan", plan_step)
    # 라벨 compose — 행내 구버전 langgraph 는 상태 키(answer)와 같은 노드 이름 거부
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
    # 계획 루프 — 능력 표면은 intent 가 아니라 tools.TOOLS
    g.add_conditional_edges("plan", route_plan, ["plan", "compose"])
    # 되묻기로 끝난 턴은 offer 생략
    g.add_conditional_edges("compose", route_answer, {"offer": "offer", "__end__": END})
    # 승낙 턴 — 화면 연계는 즉시 종료, 화법 제시는 근거만 싣고 compose. 쪽지 초안 중 새 질문은 plan
    g.add_conditional_edges("confirm_action", route_confirm,
                            {"compose": "compose", "plan": "plan", "__end__": END})
    g.add_edge("offer", END)
    # 직전 답변 수정 요청이면 답 없이 plan 으로(`last_answer` 도구)
    g.add_conditional_edges("correction", route_correction, {"plan": "plan", "__end__": END})
    for node in ("agent_help", "lms_link", LLM_DOWN):
        g.add_edge(node, END)
    return g.compile()


_AGENT = None


def _customer_name(customer_id: str | None) -> str | None:
    """관측 표시용 고객 이름. 조회 실패 시 None."""
    if not customer_id:
        return None
    try:
        profile = CUST.get_profile(customer_id)
    except Exception:                          # noqa: BLE001 — 표시용 값이 턴을 죽이지 않는다
        return None
    return getattr(profile, "nm", None)


def _pending_label(history: list[dict] | None) -> str | None:
    """직전 턴 제안 이름(로그용). 없으면 None."""
    if not history:
        return None
    last = history[-1] or {}
    pending = last.get("pending_action") or {}
    if pending.get("label"):
        return pending["label"]
    return "되묻기" if last.get("pending_clarify") else None


def _first_targets(history: list[dict] | None, evidence: list) -> dict | None:
    """이 대화의 첫 관리 대상 추천. 기록에 있으면 그것, 없으면 이번 턴 branch_targets 산출."""
    for ev in evidence:
        if ev.get("tool") == "branch_targets" and (ev.get("meta") or {}).get("first_targets"):
            return ev["meta"]["first_targets"]
    for turn in reversed(history or []):
        if turn.get("first_targets"):
            return turn["first_targets"]
    return None


def employee_no(explicit: str | None, x_client_user: str | None) -> str | None:
    """직원 WorkB 사번. 명시값 우선, x_client_user 는 사번 꼴일 때만. main.py 로그와 공용."""
    return (explicit or "").strip() or note.as_emp_no(x_client_user)


def ask(
    question: str, history: list[dict] | None = None,
    *, customer_id: str | None = None, session_id: str = "default",
    on_progress: Callable[[str], None] | None = None,
    x_client_user: str | None = None, employee_id: str | None = None,
) -> dict[str, Any]:
    """한 턴 실행. 상태 미보유 — 반환된 history 를 다음 호출에 넘김.

    customer_id: 열린 브리핑 화면 고객 id. 없으면 고객 관련 기능은 «화면을 먼저 열어달라»
    session_id: 상담이력 단위. 기본 "default"
    on_progress: 진행 표시 콜백(ContextVar 전달 · 실패해도 답변 계속)
    x_client_user: 모든 LLM 호출의 `x-client-user` 헤더(쿼터 버킷) — 비우면 429 위험
    employee_id: WorkB 사번(쪽지 수신·발송 주체). 없으면 x_client_user 가 사번 꼴일 때만 사용
    """
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()
    emp_no = employee_no(employee_id, x_client_user)
    saved = customer_selection.context(history, emp_no, session_id)
    selection = customer_selection.resolve(question, saved, emp_no, customer_id)
    customer_id = selection.customer_id
    # 관측 user_id 는 고객(브리핑과 같은 `customer_ref` 표기), 직원은 메타데이터
    who = observability.customer_ref(
        customer_id, _customer_name(customer_id), guard.conditions_of(customer_id))
    tags = ["consult", *who["tags"]]
    # 사번은 여기서 한 번만 판정 — 상태·상담이력·발송 공용
    # 요청 id 는 main.py 가 연 것을 잇고 경과초 시계만 턴 시작으로
    started_at = observability.wall_clock()
    with observability.request_id(observability.current_request_id()), \
            llm.client_user(x_client_user), observability.trace(
        "consult.turn", input=question, session_id=session_id,
        user_id=who["user_id"], tags=tags,
        metadata={**who["metadata"], "x_client_user": llm.current_client_user()},
    ) as span:
        observability.step("turn", customer="열림" if customer_id else "없음",
                           history=f"{len(history or [])}턴",
                           pending=(_pending_label(history)))
        with progress.reporting(on_progress):
            if selection.answer is not None:
                out = {"question": question, "answer": selection.answer, "sources": [],
                       "evidence": [], "intent": "situation", "clarify": selection.clarify,
                       "customer_id": customer_id}
            else:
                out = _AGENT.invoke(
                    {"question": selection.question or question, "history": history or [], "customer_id": customer_id,
                     "customer_context": selection.context,
                     # history 도구가 «지난번»에서 이번 세션 제외용
                     "session_id": session_id,
                     # 쪽지 수신·발송 주체
                     "employee_id": emp_no})
                if selection.notice and not out.get("llm_error"):
                    out["answer"] = selection.notice + "\n\n" + out["answer"]
        evidence = out.get("evidence") or []
        # 닫을 때 태그를 덮어쓰므로 여는 태그도 함께 전달
        span.update(output=out.get("answer"), intent=out.get("intent"),
                    tools=sorted({e["tool"] for e in evidence}),
                    tags=[*tags, *observability.tag("intent", out.get("intent"))])
        # 턴 결과 집계용 — 재료 없이 도구가 실패한 턴은 answer 가 아닌 tool_failed
        failed = [s for s in (out.get("steps") or []) if s.get("outcome") == "failed"]
        outcome = ("llm_down" if out.get("llm_error") else "clarify" if out.get("clarify")
                   else "tool_failed" if failed and not evidence else "answer")
        observability.score(
            "turn_outcome", outcome,
            comment=out.get("llm_error")
            or ("; ".join(f.get("reason") or "" for f in failed) if failed else None))
        observability.score("evidence_count", len(evidence))
        # 답 형태 판정 등급 분포. 판정 미실행 턴은 "n/a"
        observability.score("judge_verdict", out.get("judge_verdict") or "n/a")
        # 같은 사실 + LLM 호출 합계를 로그 한 줄로
        tally = observability.llm_tally()
        observability.step(
            "turn", result=outcome, evidence=f"{len(evidence)}건",
            failed=f"{len(failed)}건" if failed else None,
            verdict=out.get("judge_verdict"), llm=f"{tally['calls']}회",
            chars=f"{tally['chars'] / 1000:.1f}k자" if tally["chars"] else None,
            level=logging.WARNING if outcome in ("llm_down", "tool_failed") else logging.INFO)
        # 단계 기록은 블록 종료 시 소멸 — 닫기 전 수집
        journal = observability.journal()
    # 응답 `trace` 이벤트용 절차 기록(매 턴 생성)
    trace = turn_trace.build(out, journal, started_at=started_at,
                             finished_at=observability.wall_clock())
    answer = out["answer"]
    customer_context = customer_selection.remember(selection.context, evidence)
    # 이번 턴에 새로 표시한 목록·선택 상태로 추천질문 생성(이전 목록 번호를 추천하지 않음).
    out["customer_context"] = customer_context
    out["selection_case"] = selection.case
    out.setdefault("customer_id", customer_id)
    # 추천질문·딥링크·발송 문구는 모든 intent 가 지나는 여기 한 곳에서 생성
    followups = suggest.followup_questions(out)
    if followups:
        answer += "\n\n" + FOLLOWUP_HEADER + "\n" + "\n".join(f"· {q}" for q in followups)
    # 딥링크 재료는 추천질문 붙이기 전 답변. 승낙 턴은 노드가 미리 채움(`nodes/act._link`)
    links = out.get("links")
    if links is None:
        links = screens.links_in(out.get("answer") or "",
                                 screens.declared(evidence), screens.names(KB))
    # 본문이 인용한 고객 발송 문구(LMS)
    messages = lms_messages.messages_in(out.get("answer") or "",
                                        lms_messages.canonical(evidence))
    turn = {
        "question": question,
        "customer_type": out.get("customer_type"),
        "objection_type": out.get("objection_type"),
        "stage": out.get("stage"),
        "utterance": out.get("utterance"),
        # 다음 턴 «네»의 실행 대상
        "pending_action": out.get("pending_action"),
        # 다음 턴은 이 되묻기의 답
        "pending_clarify": out.get("clarify"),
        # 사용 도구 이름 — 후속 질문에서 앞 답 반복 방지
        "tools": sorted({e["tool"] for e in (out.get("evidence") or [])}),
        # `last_answer` 도구 재료. 되묻기·LLM 장애·실패 안내 턴은 None — 실패 안내가 «이전 답변»이 되는 것 방지
        "answer": (None if out.get("clarify") or out.get("llm_error")
                   or plan.is_failure_notice(out.get("answer"))
                   else (out.get("answer") or "")[:ANSWER_KEEP] or None),
        "sources": list(out.get("sources") or []),
        "marks": tools.ledger_marks(evidence),
        # 이 대화의 첫 관리 대상 추천 — 이미 있으면 그대로 잇고, 없으면 이번 턴 추천을 첫 추천으로
        "first_targets": _first_targets(history, evidence),
        "customer_context": customer_context,
    }
    new_history = [*(history or []), turn][-HISTORY_LIMIT:]

    if customer_id:
        # 상담 직원 사번은 세션 첫 턴에만 기록
        append_turn(customer_id, session_id, {
            "role": "user", "text": question, "intent": out.get("intent"),
        }, employee_id=emp_no)
        append_turn(customer_id, session_id, {
            # 추천질문 붙이기 전 답변 — `history` 도구가 재료로 되읽음
            "role": "agent", "text": out.get("answer", ""), "intent": out.get("intent"),
        })

    return {
        "answer": answer, "sources": out.get("sources", []), "history": new_history,
        # 단말 화면 딥링크. 없으면 []
        "links": list(links or []),
        # 본문 속 고객 발송 문구 `{kind, label, text, copy}`. 없으면 []
        "messages": messages,
        # answer 끝 추천질문 블록과 같은 내용
        "followups": followups,
        "intent": out.get("intent"),  # 프론트가 correction 처럼 미완성 기능을 표시할 때 씀
        # 확인 대기 중인 제안
        "pending_action": out.get("pending_action"),
        # 되묻기 질문·선택지
        "clarify": out.get("clarify"),
        # 절차 기록(근거 패널용)
        "trace": trace,
    }
