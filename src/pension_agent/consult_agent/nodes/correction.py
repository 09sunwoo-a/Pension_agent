"""브리핑 수정 요청 노드 — "이 부분 틀렸어/고쳐줘" 같은 대화형 수정 요청을 처리한다.

이번 범위는 사용자가 확인한 대로 **검토 기록(감사로그)까지만** — 승인된 수정이 세션이력에
남지만, 다음 브리핑 생성 시 자동 반영되는 재적용 루프는 만들지 않는다(그 루프는 "코드=사실"
경계를 실제로 어떻게 지킬지 더 구체적인 설계가 필요하다고 판단된 다음 단계 항목).

편집 가능/불가능 경계(strategy_agent.agent.EDITABLE_FIELDS 참고)는 코드가 강제한다 — LLM 은
요청을 세 가지 편집 가능 항목(AI브리핑 문장·근거해설·카드 한줄혜택) 중 하나로 분류하거나
"not_editable"로 분류할 뿐, 분류 결과와 무관하게 이 노드가 최종적으로 반영 여부를 판정한다.
수치·상품명·조건 판정처럼 시스템이 계산한 값을 고쳐달라는 요청은 절대 조용히 수용하지 않고
명확히 거절한다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.consult_agent.prompts import CORRECTION_PROMPT, CORRECTION_SYSTEM
from pension_agent.consult_agent.state import AgentState
from pension_agent.llm import generate
from pension_agent.session_store import append_turn
from pension_agent.strategy_agent import agent as strategy_agent, customer as strategy_customer, engine

def _parse(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _before_value(target: str, item_id: str | None, result: dict) -> str | None:
    if target == "ai_briefing_sentence":
        return result["sentence"]
    if target == "ai_briefing_insight":
        return result["insight"]
    if target == "card_benefit":
        item = next((it for it in result["facts"]["items"] if it["id"] == item_id), None)
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
        return {"answer": "지금 조회 중인 고객을 찾을 수 없어요. 고객 화면을 먼저 열어주세요.", "sources": []}

    result = strategy_agent.propose(profile)
    facts = result["facts"]
    prompt = CORRECTION_PROMPT.format(
        facts=json.dumps({**facts, "sentence": result["sentence"], "insight": result["insight"]},
                         ensure_ascii=False, indent=1),
        question=state["question"],
    )
    try:
        raw = generate(prompt, system=CORRECTION_SYSTEM, max_tokens=500)
    except Exception:
        return {"answer": "지금은 수정 요청을 처리할 수 없어요. 잠시 후 다시 시도해주세요.", "sources": []}

    data = _parse(raw)
    if not isinstance(data, dict):
        return {"answer": "요청을 이해하지 못했어요. 어느 부분을 어떻게 고칠지 다시 말씀해주시겠어요?", "sources": []}

    target = data.get("target")
    if target not in strategy_agent.EDITABLE_FIELDS:
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
    before = _before_value(target, item_id, result)
    field_path = target if target != "card_benefit" else f"card_benefit:{item_id}"
    _log_correction(customer_id, field=field_path, before=before, after=revised,
                    status="applied_this_session")

    return {
        "answer": f"이렇게 반영할게요 — \"{revised}\" (이번 세션 기록에만 반영되며, 다음 브리핑 생성에는 자동 반영되지 않아요. 화면에도 반영하려면 별도 확인이 필요해요.)",
        "sources": [],
    }
