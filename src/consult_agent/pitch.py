"""화법 검색 노드 — retrieve/broaden/llm_rerank/verify/respond/fallback.

원래 nodes.py 에 다른 기능들과 함께 있던 화법 검색 흐름을 그대로(로직 무변경) 옮긴 것이다.
분기 predicate(route/route_rerank/route_verify)는 router.py 에 있다 — 여기는 노드 본체만.
"""

from __future__ import annotations

import json
import re
from typing import Any

from kb import build_context, card_index, retrieve
from llm import generate
from prompts import RERANK_PROMPT, SYSTEM, USER, VERIFY_PROMPT
from router import AgentState, _kb

TOP_K = 3


# ─────────────────────────────────────────────────────────────
# Node. retrieve — 결정적 검색 (LLM 호출 없음)
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
# Node. broaden — 조건을 풀고 재검색 (최대 2회)
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


# ─────────────────────────────────────────────────────────────
# Node. llm_rerank — 결정론적 검색이 broaden까지 다 쓰고도 0건일 때만
# 시도하는 LLM 보조 검색 (동의어·패러프레이즈로 n-gram이 못 잡은 경우)
# ─────────────────────────────────────────────────────────────

def llm_rerank(state: AgentState) -> dict[str, Any]:
    """카드 인덱스(제목·태그·트리거예시)만 LLM에 보여주고 관련 id를 고르게 한다.

    안전장치는 두 겹이다 — ① 검색 대상 자체가 card_index(kb.pitches)로 한정돼
    있어 저작되지 않은 내용은 애초에 후보가 될 수 없고, ② LLM이 목록에 없는
    id를 지어내더라도 아래에서 kb.pitches 실재 id와 대조해 걸러낸다. LLM 미설정·
    장애 시에는 예외를 삼키고 빈 결과로 fallback 시킨다(운영 중단 방지).
    """
    utterance = state.get("utterance") or state["question"]
    try:
        text = generate(
            RERANK_PROMPT.format(card_index=card_index(_kb), question=utterance),
            max_tokens=200,
        )
        m = re.search(r"\[.*\]", text, re.S)
        picked_ids = json.loads(m.group()) if m else []
    except Exception:
        picked_ids = []

    by_id = {p["id"]: p for p in _kb.pitches}
    hits = [(2.0, by_id[pid]) for pid in picked_ids if pid in by_id]  # 존재하는 id만 통과
    return {"hits": hits}


# ─────────────────────────────────────────────────────────────
# Node. verify — 검색 결과가 질문 의도에 맞는지 판정 (오답 차단)
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


# ─────────────────────────────────────────────────────────────
# Node. respond — 화법 생성
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
# Node. fallback — 환각 대신 정직한 응답
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
