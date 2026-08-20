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
import sys
from pathlib import Path
from typing import Any

_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.agent_loader import load_strategy_agent  # noqa: E402
from common.session_store import append_turn  # noqa: E402

from llm import generate
from router import AgentState

_strategy = load_strategy_agent()

CORRECTION_SYSTEM = """당신은 퇴직연금(IRP) AI브리핑 화면의 수정 요청을 분류하고, 편집
가능한 항목이면 수정 문장을 작성하는 사내 에이전트입니다.

편집 가능한 항목은 이 세 가지뿐입니다 — 전부 LLM 이 쓴 산문(의견)이지, 시스템이 계산한
숫자·상품명·조건 판정이 아닙니다.
  - "ai_briefing_sentence": AI 브리핑 문장(종합 제안)
  - "ai_briefing_insight": AI 브리핑의 근거 해설(insight)
  - "card_benefit": 개별 전략 카드의 '한 줄 혜택' 문구

평가금액·수익률·백분위·포트폴리오 비중·세액공제 금액·상품명·전략 선정 여부 등은 시스템이
결정론적으로 계산한 값이라 대화로 고칠 수 없습니다 — 이런 요청이면 target 을 "not_editable"
로 분류하고 revised_text 는 비웁니다.

반드시 지킬 것
1. revised_text 는 제시된 사실(facts) 안의 수치·상품명만 사용합니다. 새로 만들지 않습니다.
2. 애매하면 "not_editable" 로 분류합니다 — 편집 가능 여부가 불확실한 요청을 조용히 수용하지
   않습니다.

출력은 JSON 객체 하나만 반환합니다. 다른 설명을 덧붙이지 않습니다."""

CORRECTION_PROMPT = """## 현재 AI브리핑 사실(facts) — 이 안의 값만 근거로 삼습니다
{facts}

## 직원의 수정 요청
{question}

## 지시
1. `target`: "ai_briefing_sentence" | "ai_briefing_insight" | "card_benefit" | "not_editable" 중 하나.
2. `item_id`: target 이 "card_benefit" 일 때만, 어느 전략 카드인지 items 의 id. 아니면 null.
3. `revised_text`: target 이 편집 가능한 값이면 수정된 문장(원래 톤 유지, facts 밖 수치·
   상품명 창작 금지). "not_editable" 이면 빈 문자열.
4. `reject_reason`: target 이 "not_editable" 이면 왜 대화로 못 고치는지 직원에게 설명할
   한 문장. 아니면 빈 문자열.

{{"target": "...", "item_id": "..." | null, "revised_text": "...", "reject_reason": "..."}}"""


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
    profile = _strategy["customer"].get_profile(customer_id) if customer_id else None
    if profile is None:
        return {"answer": "지금 조회 중인 고객을 찾을 수 없어요. 고객 화면을 먼저 열어주세요.", "sources": []}

    result = _strategy["agent"].propose(profile)
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
    if target not in _strategy["agent"].EDITABLE_FIELDS:
        reason = (str(data.get("reject_reason") or "").strip()
                  or "이 값은 시스템이 계산한 사실이라 대화로 고칠 수 없어요. 값 자체를 바꾸려면 데이터 저작(담당 부서)을 통해야 해요.")
        _log_correction(customer_id, field=target or "unknown", before=None, after=None,
                        status="rejected_not_editable", note=reason)
        return {"answer": reason, "sources": []}

    revised = str(data.get("revised_text") or "").strip()
    if not revised or not _strategy["engine"].verify(revised, facts)[0]:
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
