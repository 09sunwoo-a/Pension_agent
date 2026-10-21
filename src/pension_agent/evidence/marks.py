"""재료 성격 표시 — 신뢰 등급(문서 레지스트리)과 내부용 주의(customer_facing). 표시 생성의 단일 지점.

선언이 없으면 아무것도 붙이지 않음(추론 금지). 내부용도 막지 않고 주의만 표시.
"""

from __future__ import annotations

from pension_agent.knowledge.kb import KnowledgeBase

#: 답변 끝 재료 성격 표시 블록 머리말(compose 가 붙이고 answered 가 뗌).
MATERIAL_MARKS = "── 참고한 자료"

#: 문서 레지스트리 tier → 신뢰 표시.
TIER_NOTE: dict[str, str] = {
    "본부공식": "본부 공식 자료",
    "현장팁": "영업점 현장 노하우 — 본부 확정 지침이 아닙니다",
    "대외공개": "대외 공개 자료 — 고객에게 그대로 안내 가능",
    "교육자료": "직원 교육자료",
}

#: 내부용 재료가 답변에 실렸을 때의 주의(차단 아님).
INTERNAL_NOTE = "이 내용은 고객에게 그대로 안내하지는 마세요 — 내부용으로 표시된 자료입니다."

#: 재료 안에 싣는 customer_facing 표시(작성 LLM 용). 대상은 «고객에게 할 말».
FACING_NOTE: dict[bool, str] = {
    True: "고객에게 그대로 안내 가능",
    False: "내부용 — 이 내용을 고객에게 할 말로 옮기지 마세요",
}


def facing_note(card: dict) -> str | None:
    """customer_facing 재료 표시. 선언 없음(None)은 None — 참·거짓 어느 쪽으로도 취급 금지."""
    value = card.get("customer_facing")
    return FACING_NOTE[value] if isinstance(value, bool) else None


def tier_of(kb: KnowledgeBase, card: dict) -> str | None:
    """카드 출처 문서의 신뢰 표시. 문서 미발견 시 None."""
    doc = kb.docs_by_id.get((card.get("_source") or {}).get("doc") or "")
    return TIER_NOTE.get((doc or {}).get("tier") or "") if doc else None


def notes_for(kb: KnowledgeBase, cards: list[dict]) -> list[str]:
    """재료들의 성격 표시(순서 유지, 중복 제거)."""
    out: list[str] = []
    for card in cards:
        tier = tier_of(kb, card)
        if tier and tier not in out:
            out.append(tier)
    # customer_facing 이 False 인 카드만 계수 — None 은 미계수
    if any(card.get("customer_facing") is False for card in cards) and INTERNAL_NOTE not in out:
        out.append(INTERNAL_NOTE)
    return out
