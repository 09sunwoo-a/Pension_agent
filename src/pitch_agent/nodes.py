"""그래프 노드 함수 + 라우팅.

노드 7개(understand/capabilities/retrieve/broaden/verify/respond/fallback)의 역할과 분기는
README.md 표·다이어그램 참고. 각 노드의 세부 규칙은 아래 함수 주석에 있다.
프롬프트 문구는 prompts.py, LLM 클라이언트·모델 설정은 llm.py, 그래프 조립은 graph.py.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from kb import build_context, load_kb, retrieve
from llm import generate
from prompts import SYSTEM, UNDERSTAND_PROMPT, USER, VERIFY_PROMPT

TOP_K = 3
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
# Node 1. understand — 자연어 질문을 검색 조건으로 분해
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
# Node 2. capabilities — 메타 질문에 지식베이스 기준으로 답변 (LLM 없음)
# ─────────────────────────────────────────────────────────────

def capabilities(state: AgentState) -> dict[str, Any]:
    """지식베이스 메타데이터만으로 생성한다. 새 장(chapter)이 추가돼도
    코드를 고치지 않아도 자동으로 최신 내용이 반영된다."""
    by_type: dict[str, list[dict]] = {}
    for p in _kb.pitches:
        by_type.setdefault(p["type"], []).append(p)

    lines = [
        "■ 제가 도와드릴 수 있는 것",
        "IRP(개인형 퇴직연금) 상담 화법을 안내해 드려요. 예를 들면:",
        "",
    ]
    if by_type.get("proposal"):
        who = ", ".join(_kb.customer_types) or "다양한"
        lines.append(f"· 신규 제안 — {who} 고객에게 IRP를 처음 권할 때")
    if by_type.get("objection") and _kb.objection_types:
        lines.append(f"· 거절 대응 — {', '.join(_kb.objection_types)} 같은 반응이 나왔을 때")
    guides_by_stage: dict[str, list[dict]] = {}
    for g in by_type.get("guide", []):
        guides_by_stage.setdefault(g["tags"].get("stage", "공통"), []).append(g)
    for stage, guides in guides_by_stage.items():
        label = "상품 비교·설명 자료" if stage == "공통" else f"{stage} 업무 가이드"
        lines.append(f"· {label} — 예: {guides[0]['title']}")

    lines += [
        "",
        "■ 활용 팁",
        '"사업자 고객인데 수수료 부담된다고 하시네요" 처럼 실제 상황을 그대로 말씀해 주시면 바로 화법을 찾아드려요.',
    ]
    return {"answer": "\n".join(lines), "sources": []}


# ─────────────────────────────────────────────────────────────
# Node 3. retrieve — 결정적 검색 (LLM 호출 없음)
# ─────────────────────────────────────────────────────────────

def retrieve_node(state: AgentState) -> dict[str, Any]:
    hits = retrieve(
        _kb,
        top_k=TOP_K,
        customer_type=state.get("customer_type"),
        objection_type=state.get("objection_type"),
        stage=state.get("stage"),
        utterance=state.get("utterance"),
    )
    return {"hits": hits}


# ─────────────────────────────────────────────────────────────
# Node 4. broaden — 조건을 풀고 재검색 (최대 2회)
# ─────────────────────────────────────────────────────────────

def broaden(state: AgentState) -> dict[str, Any]:
    """1차: 고객유형·단계 조건을 풀어 순위를 재조정한다.
    2차: 거절유형(objection_type)까지 지워 순수 발화·주제어 유사도만으로 재검색한다.

    채택 기준(kb.MIN_TOPICAL)은 두 단계 모두 그대로 둔다 — 낮추면 무관한
    질문까지 통과시키는 걸 실측으로 확인해서(예: "주택청약 금리 어떻게
    되나요?"가 원픽 가이드 카드와 우연히 겹쳐 통과됨) 되돌렸다. broaden 은
    순수 0건(그 어떤 카드도 문턱을 못 넘긴 경우)만 구제한다 — hits 가 있으면
    애초에 이 단계를 타지 않는다. 카드는 걸렸지만 질문 의도와 안 맞는 경우는
    verify 게이트가 걸러 fallback 으로 보낸다."""
    n = state.get("broaden_count", 0) + 1
    update: dict[str, Any] = {"broaden_count": n}
    if n == 1:
        update["customer_type"] = None
        update["stage"] = None
    else:
        update["objection_type"] = None
    return update


def route(state: AgentState) -> str:
    if state.get("hits"):
        return "verify"
    return "broaden" if state.get("broaden_count", 0) < 2 else "fallback"


# ─────────────────────────────────────────────────────────────
# Node 5. verify — 검색 결과가 질문 의도에 맞는지 판정 (오답 차단)
# ─────────────────────────────────────────────────────────────

def verify(state: AgentState) -> dict[str, Any]:
    """검색된 카드가 질문의 '실제 의도'에 맞는지 LLM 으로 한 번 더 확인한다.
    주제어만 겹치고 상황이 다른 "확신 있는 오답"을 여기서 걸러 fallback 시킨다."""
    cards = "\n".join(
        f"- [{p['id']}] {p['title']} · 핵심: {'; '.join(p.get('key_points', [])[:2])}"
        for _, p in state["hits"]
    )
    verdict = generate(
        VERIFY_PROMPT.format(question=state["question"], cards=cards), max_tokens=5
    )
    return {"verified": verdict.strip().upper().startswith("YES")}


def route_verify(state: AgentState) -> str:
    return "respond" if state.get("verified") else "fallback"


# ─────────────────────────────────────────────────────────────
# Node 6. respond — 화법 생성
# ─────────────────────────────────────────────────────────────

def _situation_line(state: AgentState) -> str:
    """USER 프롬프트에 넣을 '파악된 상황' 한 줄. intent에 따라 다르게 구성한다 —
    guide 질문엔 애초에 없는 고객유형·거절유형을 '미파악'으로 채워 보여주지 않는다."""
    stage = state.get("stage") or "미파악"
    if state.get("intent") == "guide":
        return f"파악된 상황 — 직원 업무 절차 질문 (분야: {stage})"
    customer_type = state.get("customer_type") or "미파악"
    objection_type = state.get("objection_type") or "미파악"
    return f"파악된 상황 — 고객유형 {customer_type} / 거절유형 {objection_type} / 단계 {stage}"


def respond(state: AgentState) -> dict[str, Any]:
    context = build_context(_kb, state["hits"])
    prompt = USER.format(
        context=context,
        question=state["question"],
        situation_line=_situation_line(state),
    )
    return {
        "answer": generate(prompt, max_tokens=1500, system=SYSTEM),
        "sources": [
            {"id": p["id"], "title": p["title"], "score": round(s, 2), "page": p.get("page")}
            for s, p in state["hits"]
        ],
    }


# ─────────────────────────────────────────────────────────────
# Node 7. fallback — 환각 대신 정직한 응답
# ─────────────────────────────────────────────────────────────

def fallback(state: AgentState) -> dict[str, Any]:
    return {
        "answer": (
            "현재 지식베이스에 이 상황에 맞는 화법이 없습니다. "
            f"수록된 거절유형은 {', '.join(_kb.objection_types)}, "
            f"상담단계는 {', '.join(_kb.stages)}까지입니다. "
            "위 범위 밖의 상담은 마케팅 보물지도 또는 담당부서를 통해 확인해 주세요."
        ),
        "sources": [],
    }
