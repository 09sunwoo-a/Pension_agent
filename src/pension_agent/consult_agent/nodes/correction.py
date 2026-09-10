"""브리핑 수정 요청 노드 — "이 부분 틀렸어/고쳐줘" 같은 대화형 수정 요청을 처리한다.

이번 범위는 사용자가 확인한 대로 **검토 기록(감사로그)까지만** — 승인된 수정이 세션이력에
남지만, 다음 브리핑 생성 시 자동 반영되는 재적용 루프는 만들지 않는다(그 루프는 "코드=사실"
경계를 실제로 어떻게 지킬지 더 구체적인 설계가 필요하다고 판단된 다음 단계 항목).

편집 가능/불가능 경계(strategy_agent.agent.EDITABLE_FIELDS 참고)는 코드가 강제한다 — LLM 은
요청을 세 가지 편집 가능 항목(AI브리핑 문장·근거해설·카드 한줄혜택) 중 하나로 분류하거나
"not_editable"로 분류할 뿐, 분류 결과와 무관하게 이 노드가 최종적으로 반영 여부를 판정한다.
수치·상품명·조건 판정처럼 시스템이 계산한 값을 고쳐달라는 요청은 절대 조용히 수용하지 않고
명확히 거절한다.

━━ 화면 문장이 아니라 «방금 한 답변»을 고쳐 달라는 요청 (2026-09-10) ━━
「고객에게 해야 할 말 좀 더 짧게 줄여줘」가 이 노드로 왔다 — 직전 턴이 「증권사는 ETF
종류가 많던데요」 화법이었고 직원이 가리킨 것은 그 답변이었는데, 분류(understand)가
correction 으로 읽었고 이 노드는 화법과 무관한 AI브리핑 문장(「만기 예정 예금이 있어…」)을
고쳐 «이렇게 반영할게요»로 끝냈다. 가리키지 않은 문장을 고쳐 반영했다고 말하는 것은 이
노드가 낼 수 있는 가장 나쁜 결과다. 그래서 분류 LLM 이 «브리핑 문장이 아니다»
(`not_briefing`)로 판정하면 **답을 내지 않고** 그래프가 계획 루프로 보낸다
(routing.route_correction) — 직전 답변은 `last_answer` 도구의 재료다. 고객 화면이 닫혀
있는데 다시 쓸 답변은 있는 턴도 같다: 그 요청이 브리핑 수정일 수는 없다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.consult_agent.prompts import CORRECTION_PROMPT, CORRECTION_SYSTEM
from pension_agent.consult_agent.routing import DEFAULT_INTENT
from pension_agent.consult_agent.state import AgentState, format_history
from pension_agent.consult_agent.tools import last_answered
from pension_agent.llm import generate
from pension_agent.session_store import append_turn
from pension_agent.strategy_agent import agent as strategy_agent, customer as strategy_customer, engine

#: 분류 LLM 이 «화면 문장이 아니라 방금 한 답변을 고쳐 달라는 것»이라고 판정한 값.
NOT_BRIEFING = "not_briefing"

#: 계획 루프로 넘기는 반환값 — 답을 내지 않고 의도를 기본값으로 되돌린다. `answer` 를
#: 비워 두는 것이 곧 분기 신호다(routing.route_correction 은 코드가 아는 값만 본다).
_FALL_THROUGH: dict[str, Any] = {"intent": DEFAULT_INTENT}

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
        # 고객 화면이 닫혀 있으면 고칠 브리핑이 없다. 그런데 다시 쓸 **답변**은 있으면 그
        # 요청은 브리핑 수정일 수 없다 — 계획 루프가 직전 답변으로 답한다(모듈 머리말).
        if last_answered(state.get("history")) is not None:
            return dict(_FALL_THROUGH)
        return {"answer": "지금 조회 중인 고객을 찾을 수 없어요. 고객 화면을 먼저 열어주세요.", "sources": []}

    result = strategy_agent.propose(profile)
    facts = result["facts"]
    prompt = CORRECTION_PROMPT.format(
        facts=json.dumps({**facts, "sentence": result["sentence"], "insight": result["insight"]},
                         ensure_ascii=False, indent=1),
        # 이전 대화를 준다 — 「좀 더 짧게 줄여줘」가 화면 문장인지 방금 한 답변인지는 앞 턴에
        # 무엇을 물었는지를 봐야 갈린다. 없던 동안 이 프롬프트는 화면 사실과 이번 요청만
        # 보고 화법 답변을 줄여 달라는 말을 브리핑 문장 수정으로 읽었다.
        history_block=format_history(state.get("history")) or "(없음)\n",
        question=state["question"],
    )
    try:
        raw = generate(prompt, system=CORRECTION_SYSTEM, max_tokens=500,
                       name="consult.correction")
    except Exception:
        return {"answer": "지금은 수정 요청을 처리할 수 없어요. 잠시 후 다시 시도해주세요.", "sources": []}

    data = _parse(raw)
    if not isinstance(data, dict):
        return {"answer": "요청을 이해하지 못했어요. 어느 부분을 어떻게 고칠지 다시 말씀해주시겠어요?", "sources": []}

    target = data.get("target")
    if target == NOT_BRIEFING:
        # 화면 문장이 아니라 방금 한 답변을 고쳐 달라는 것 — 여기서 고칠 것이 없다. 아무것도
        # 반영하지 않고 기록도 남기지 않는다(고친 것이 없다). 답은 계획 루프가 낸다.
        return dict(_FALL_THROUGH)
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
