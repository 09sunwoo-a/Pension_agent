"""브리핑/고객정보 질의 노드 — "이 고객 평가금액 얼마야?" 같은 대화형 질문에 답한다.

현재 열려 있는 브리핑 화면의 고객(state["customer_id"])에 대해 strategy_agent.agent.propose()
를 그대로 호출해 이미 계산·작성된 결과(sentence·insight·facts)를 근거로 답한다 — 화면에
보이는 것과 다른 사실을 말하면 안 되므로, engine.prepare() 의 결정적 재료뿐 아니라 화면에
실제로 노출되는 AI브리핑 문장(sentence/insight)까지 포함해서 답변 재료로 삼는다. 이 노드
자체가 질문에 맞춰 답변 문장을 새로 쓴다.

코드=사실 원칙은 여기서도 유지한다 — LLM 산출물은 common.verify.verify() 로 재료(facts) 밖
숫자·상품명이 있는지 재검증하고, 실패하면 "확인해달라"는 안전한 답으로 대체한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.agent_loader import load_strategy_agent  # noqa: E402
from common.verify import verify  # noqa: E402

from llm import generate
from router import AgentState

_strategy = load_strategy_agent()

BRIEFING_QA_SYSTEM = """당신은 퇴직연금(IRP) 상담 직원을 돕는 사내 에이전트입니다.
직원이 현재 조회 중인 고객의 AI브리핑 내용에 대해 묻습니다.

반드시 지킬 것
1. 아래 제공된 사실(facts)에 있는 값만 답합니다. 새 수치·상품명을 만들지 않습니다.
2. 사실에 없는 것을 물으면 모른다고 답합니다(추측 금지).
3. 직원에게 답하듯 해요체로, 1~3문장으로 간결하게 답합니다.

출력은 답변 문장만 반환합니다. JSON·설명·코드펜스 없이 순수 텍스트로만 답합니다."""

BRIEFING_QA_PROMPT = """## 이 고객의 확정된 사실 (이 안의 값만 답변에 사용)
{facts}

## 직원 질문
{question}

## 답변"""


def briefing_qa(state: AgentState) -> dict[str, Any]:
    customer_id = state.get("customer_id")
    profile = _strategy["customer"].get_profile(customer_id) if customer_id else None
    if profile is None:
        return {
            "answer": "지금 조회 중인 고객을 찾을 수 없어요. 고객 화면을 먼저 열어주세요.",
            "sources": [],
        }

    result = _strategy["agent"].propose(profile)
    facts = result["facts"]
    briefing_view = {"AI브리핑_문장": result["sentence"], "AI브리핑_근거해설": result["insight"], **facts}
    prompt = BRIEFING_QA_PROMPT.format(
        facts=json.dumps(briefing_view, ensure_ascii=False, indent=1), question=state["question"],
    )
    try:
        answer = generate(prompt, system=BRIEFING_QA_SYSTEM, max_tokens=400)
    except Exception:
        return {"answer": "지금은 답변을 만들 수 없어요. 잠시 후 다시 시도해주세요.", "sources": []}

    known_products = {r["name"] for r in _strategy["engine"].PRODUCTS}
    ok, _ = verify(answer, facts, known_products=known_products)
    if not ok:
        return {
            "answer": "죄송해요, 정확한 근거 없이 답할 수 있는 질문이라 확답을 드리기 어려워요. 브리핑 화면에서 직접 확인해주세요.",
            "sources": [],
        }
    return {"answer": answer, "sources": []}
