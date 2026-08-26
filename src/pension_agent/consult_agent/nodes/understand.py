"""의도 분류 노드 — 직원의 말을 어떤 기능으로 보낼지만 정한다.

**특정 기능에 종속되지 않는 라우팅만** 담당한다. 화법 카드 검색에 필요한 고객유형·거절유형·
상담단계 슬롯 추출은 여기서 하지 않는다 — 그건 화법 검색만의 관심사라 `nodes/pitch.py`
의 situation_slots 로 분리돼 있다. 라우팅과 도메인별 NLU 를 한 프롬프트에 뒤섞으면, 새
기능을 추가할 때마다 이 파일이 모든 기능의 어휘를 알아야 해서 비대해진다.

**LLM 이 죽으면 규칙으로 대신 분류하지 않는다.** 예전에는 키워드 힌트 표(`guess_intent`)로
의도를 어림하고 값·절차·정의 질문을 결정론 노드로 흘려 답을 만들었다. 그 경로를 지운 이유는
CLAUDE.md §11 이다 — 근거가 덜 갖춰진 답변이 정상 답변처럼 보이는 것이 무응답보다 위험하다.
지금은 분류에 실패하면 원인을 남기고 턴이 'LLM 연결이 안 되어 있다'로 끝난다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.consult_agent.prompts import ROUTE_PROMPT
from pension_agent.consult_agent.routing import DEFAULT_INTENT, INTENTS, LLM_DOWN
from pension_agent.consult_agent.state import AgentState, format_history
from pension_agent.llm import LLMError, generate


def understand(state: AgentState) -> dict[str, Any]:
    prompt = ROUTE_PROMPT.format(
        history_block=format_history(state.get("history")),
        question=state["question"],
    )
    try:
        text = generate(prompt, max_tokens=150)
    except LLMError as exc:
        # 규칙으로 대신 분류하지 않는다 — 분류가 됐다고 답이 되는 것도 아니고(답을 쓰는 것도
        # LLM 이다), 규칙 라우팅이 살아 있으면 "LLM 없이도 절반은 도는" 경로가 굳는다.
        return {"intent": LLM_DOWN, "utterance": state["question"],
                "llm_error": f"{type(exc).__name__}: {exc}"}

    try:
        m = re.search(r"\{.*\}", text, re.S)
        slots = json.loads(m.group()) if m else {}
    except (json.JSONDecodeError, AttributeError):
        slots = {}  # 분해 실패해도 원문으로 검색은 된다

    intent = slots.get("intent")
    return {
        # 목록 밖 값은 기본값으로 떨어뜨린다. 기본값이 계획 루프라서, 분류가 어긋나도
        # 능력이 잘리지 않는다 — 무엇으로 답할지는 어차피 도구 목록이 정한다.
        "intent": intent if intent in INTENTS else DEFAULT_INTENT,
        "utterance": slots.get("utterance") or state["question"],
    }
