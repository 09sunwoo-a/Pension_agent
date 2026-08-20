"""의도 분류(understand) + 상태 정의 + 모든 그래프 분기(route_*) predicate.

직원의 자연어 입력을 받아 어떤 기능(화법 검색·메타 질문·향후 브리핑질의/LMS발송/수정 등)으로
보낼지 정하는 진입 지점. 화법 검색 자체의 노드는 pitch.py, 메타 질문 응답은 meta.py 에 있고
그래프 조립은 graph.py 가 한다.

지식베이스(`_kb`)는 이 파일에서 한 번만 적재해 모듈 전역으로 들고 있다 — pitch.py·meta.py
모두 여기서 `_kb`/`AgentState`를 가져다 쓴다(반대 방향으로는 의존하지 않아 순환 임포트가
없다).
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from kb import load_kb
from llm import generate
from prompts import UNDERSTAND_PROMPT

HISTORY_LIMIT = 4  # understand 프롬프트에 넣는 최근 대화 턴 수

_kb = load_kb()


# ─────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────

class Turn(TypedDict, total=False):
    """history 한 턴. 답변 원문은 담지 않는다 (프롬프트 비용 억제)."""

    question: str
    customer_type: str | None
    objection_type: str | None
    stage: str | None
    utterance: str | None


class AgentState(TypedDict, total=False):
    question: str                    # [입력] 직원의 자연어 질문
    history: list[Turn]              # [입력] 이전 대화 턴 (호출자가 세션별로 들고 다님)
    intent: str                      # understand 가 채움 — "situation" | "guide" | "capability"
    customer_type: str | None
    objection_type: str | None
    stage: str | None
    utterance: str | None            # 질문에서 추출한 '고객이 한 말'
    hits: list                       # retrieve 결과 [(score, pitch), ...]
    broaden_count: int               # 재검색 횟수 (0→1: 고객유형·단계 완화, 1→2: 거절유형까지 완화)
    verified: bool                   # verify 가 채움 — 검색 결과가 질문 의도에 맞는지
    answer: str                      # [출력] 최종 화법
    sources: list[dict]              # [출력] 근거 카드 (역추적용)


def _format_history(history: list[Turn] | None) -> str:
    """최근 대화를 understand 프롬프트에 넣을 짧은 텍스트로 요약한다."""
    if not history:
        return ""
    lines = ["이전 대화:"]
    for i, turn in enumerate(history[-HISTORY_LIMIT:], 1):
        parsed = " / ".join(
            f"{label} {turn[key]}"
            for label, key in (("고객유형", "customer_type"), ("거절유형", "objection_type"), ("단계", "stage"))
            if turn.get(key)
        )
        lines.append(f"[{i}] 직원: {turn['question']}" + (f" → {parsed}" if parsed else ""))
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────
# Node. understand — 자연어 질문을 검색 조건으로 분해
# ─────────────────────────────────────────────────────────────

def understand(state: AgentState) -> dict[str, Any]:
    prompt = UNDERSTAND_PROMPT.format(
        customer_types=_kb.customer_types,
        objection_types=_kb.objection_types,
        stages=_kb.stages,
        history_block=_format_history(state.get("history")),
        question=state["question"],
    )
    text = generate(prompt, max_tokens=300)

    try:
        m = re.search(r"\{.*\}", text, re.S)
        slots = json.loads(m.group()) if m else {}
    except (json.JSONDecodeError, AttributeError):
        slots = {}  # 분해 실패해도 원문으로 검색은 된다

    return {
        "intent": slots.get("intent") if slots.get("intent") in ("situation", "guide", "capability") else "situation",
        "customer_type": slots.get("customer_type"),
        "objection_type": slots.get("objection_type"),
        "stage": slots.get("stage"),
        "utterance": slots.get("utterance") or state["question"],
        "broaden_count": 0,
    }


def route_intent(state: AgentState) -> str:
    return "capabilities" if state.get("intent") == "capability" else "retrieve"


# ─────────────────────────────────────────────────────────────
# 화법 검색 흐름(pitch.py)의 분기 predicate — 상태 필드만 보고 판단하므로
# 여기 모아둔다(그래프 전체 분기표를 한 곳에서 읽을 수 있게).
# ─────────────────────────────────────────────────────────────

def route(state: AgentState) -> str:
    if state.get("hits"):
        return "verify"
    return "broaden" if state.get("broaden_count", 0) < 2 else "llm_rerank"


def route_rerank(state: AgentState) -> str:
    return "verify" if state.get("hits") else "fallback"


def route_verify(state: AgentState) -> str:
    return "respond" if state.get("verified") else "fallback"
