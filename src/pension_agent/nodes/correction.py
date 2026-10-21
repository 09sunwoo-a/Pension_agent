"""브리핑 수정 요청 노드 — «이 부분 틀렸어/고쳐줘» 처리.

편집 가능 범위(solution.EDITABLE_FIELDS)는 코드가 강제, 계산값 수정은 거절. 반영은 감사로그까지만.
not_briefing(직전 답변 수정 요청)이면 답 없이 계획 루프로(routing.route_correction).
"""

from __future__ import annotations

import json
from typing import Any

from pension_agent.prompts import CORRECTION_PROMPT, CORRECTION_SYSTEM
from pension_agent.routing import DEFAULT_INTENT
from pension_agent.state import AgentState, format_history
from pension_agent.tools import last_answered
from pension_agent.common.llm import generate, json_object
from pension_agent.common.session_store import append_turn
from pension_agent.tools.customer_solution import customer as strategy_customer, engine, solution

#: 분류 결과 «브리핑 문장이 아니라 방금 한 답변 수정 요청».
NOT_BRIEFING = "not_briefing"

#: 계획 루프로 넘기는 반환값 — answer 비움이 분기 신호(routing.route_correction).
_FALL_THROUGH: dict[str, Any] = {"intent": DEFAULT_INTENT}

def _before_value(target: str, item_id: str | None, facts: dict, narrative: dict) -> str | None:
    if target.startswith("narrative."):
        value = narrative.get(target.split(".", 1)[1])
        return " / ".join(value) if isinstance(value, list) else value
    if target == "card_benefit":
        item = next((it for it in facts["items"] if it["id"] == item_id), None)
        return item["card"]["benefit"] if item else None
    return None


def _log_correction(customer_id: str, *, field: str, before: str | None, after: str | None,
                     status: str, note: str = "") -> None:
    append_turn(customer_id, "correction-log", {
        "role": "correction",
        "text": note or f"[{field}] {before!r} → {after!r}",
        "tool_calls": [{"name": "correction", "args": {
            "field_path": field, "before": before, "after": after, "status": status,
        }, "result": {"status": status}}],
    })


def correction(state: AgentState) -> dict[str, Any]:
    customer_id = state.get("customer_id")
    profile = strategy_customer.get_profile(customer_id) if customer_id else None
    if profile is None:
        # 고객 없음 + 직전 답변 있음 → 답변 수정 요청으로 보고 계획 루프로
        if last_answered(state.get("history")) is not None:
            return dict(_FALL_THROUGH)
        return {"answer": "지금 조회 중인 고객을 찾을 수 없어요. 고객 화면을 먼저 열어주세요.", "sources": []}

    facts = solution.facts(profile)
    # 서술은 기존 브리핑에서만 조회, 새로 생성 안 함
    narrative = solution.peek(profile) or {}
    prompt = CORRECTION_PROMPT.format(
        facts=json.dumps({**facts, "narrative": narrative}, ensure_ascii=False, indent=1),
        # 이전 대화 필요 — 화면 문장 수정인지 직전 답변 수정인지 구분용
        history_block=format_history(state.get("history")) or "(없음)\n",
        question=state["question"],
    )
    try:
        raw = generate(prompt, system=CORRECTION_SYSTEM, max_tokens=500,
                       name="consult.correction")
    except Exception:
        return {"answer": "지금은 수정 요청을 처리할 수 없어요. 잠시 후 다시 시도해주세요.", "sources": []}

    data = json_object(raw)
    if not isinstance(data, dict):
        return {"answer": "요청을 이해하지 못했어요. 어느 부분을 어떻게 고칠지 다시 말씀해주시겠어요?", "sources": []}

    target = data.get("target")
    if target == NOT_BRIEFING:
        # 반영·기록 없이 계획 루프로
        return dict(_FALL_THROUGH)
    if target not in solution.EDITABLE_FIELDS or (
            str(target).startswith("narrative.") and not narrative.get(target.split(".", 1)[1])):
        reason = (str(data.get("reject_reason") or "").strip()
                  or "이 값은 시스템이 계산한 사실이라 대화로 고칠 수 없어요. 값 자체를 바꾸려면 데이터 저작(담당 부서)을 통해야 해요.")
        _log_correction(customer_id, field=target or "unknown", before=None, after=None,
                        status="rejected_not_editable", note=reason)
        return {"answer": reason, "sources": []}

    revised = str(data.get("revised_text") or "").strip()
    if not revised or not engine.verify(revised, facts)[0]:
        _log_correction(customer_id, field=target, before=None, after=revised or None,
                        status="rejected_verify_failed")
        return {"answer": "수정 문장이 사실 범위를 벗어나 반영할 수 없어요. 다시 표현해서 말씀해주시겠어요?", "sources": []}

    item_id = data.get("item_id")
    before = _before_value(target, item_id, facts, narrative)
    field_path = target if target != "card_benefit" else f"card_benefit:{item_id}"
    _log_correction(customer_id, field=field_path, before=before, after=revised,
                    status="applied_this_session")

    return {
        "answer": f"이렇게 반영할게요 — \"{revised}\" (이번 세션 기록에만 반영되며, 다음 브리핑 생성에는 자동 반영되지 않아요. 화면에도 반영하려면 별도 확인이 필요해요.)",
        "sources": [],
    }
