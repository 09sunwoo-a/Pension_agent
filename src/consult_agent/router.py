"""의도 분류(understand) + 상태 정의 + 모든 그래프 분기(route_*) predicate.

직원의 자연어 입력을 받아 어떤 기능(화법 검색·메타 질문·브리핑질의·LMS발송·수정)으로 보낼지
정하는 진입 지점 — **특정 기능에 종속되지 않는 라우팅만** 담당한다. 화법 카드 검색에 필요한
고객유형·거절유형·상담단계 슬롯 추출은 여기서 하지 않는다 — 그건 화법 검색(pitch.py)만의
관심사라 `pitch.py::situation_slots` 로 분리돼 있다(understand 가 라우팅과 도메인별 NLU를
한 프롬프트에 뒤섞으면, 새 기능을 추가할 때마다 이 파일이 모든 기능의 어휘를 알아야 해서
비대해진다 — briefing_qa/lms_send/correction 이 각자 자기 프롬프트로 스스로 해석하는 것과
같은 원칙을 situation/guide 에도 적용한 것). 메타 질문 응답은 meta.py, 그래프 조립은
graph.py 가 한다.

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
from prompts import ROUTE_PROMPT

HISTORY_LIMIT = 4  # 프롬프트에 넣는 최근 대화 턴 수 (understand·situation_slots 공통)
INTENTS = ("situation", "guide", "capability", "briefing_qa", "lms_send", "correction")

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
    customer_id: str | None          # [입력] 현재 열려 있는 브리핑 화면의 고객 id (호출자가 넘김)
    intent: str                      # understand 가 채움 — situation|guide|capability|
                                      # briefing_qa|lms_send|correction
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
# Node. understand — 어떤 기능으로 보낼지만 판단(도메인 어휘 없는 라우팅 전용)
# ─────────────────────────────────────────────────────────────

def understand(state: AgentState) -> dict[str, Any]:
    prompt = ROUTE_PROMPT.format(
        history_block=_format_history(state.get("history")),
        question=state["question"],
    )
    text = generate(prompt, max_tokens=150)

    try:
        m = re.search(r"\{.*\}", text, re.S)
        slots = json.loads(m.group()) if m else {}
    except (json.JSONDecodeError, AttributeError):
        slots = {}  # 분해 실패해도 원문으로 검색은 된다

    return {
        "intent": slots.get("intent") if slots.get("intent") in INTENTS else "situation",
        "utterance": slots.get("utterance") or state["question"],
        "broaden_count": 0,
    }


# situation/guide 는 화법 검색 전에 pitch.py::situation_slots 를 한 번 더 거친다(도메인
# 슬롯 추출) — 그 외 기능은 각자 자기 노드가 스스로 해석하므로 바로 연결한다.
_INTENT_NODE = {
    "capability": "capabilities",
    "briefing_qa": "briefing_qa",
    "lms_send": "lms_send",
    "correction": "correction",
}


def route_intent(state: AgentState) -> str:
    return _INTENT_NODE.get(state.get("intent"), "situation_slots")


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
