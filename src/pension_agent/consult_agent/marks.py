"""재료 성격 표시 — 이 답이 어느 자료에서 왔고, 고객에게 그대로 옮겨도 되는지 (CLAUDE.md §7).

답을 읽는 사람은 영업점 직원이고, 고객에게 무엇을 어떻게 옮길지는 직원이 거른다. 그래서
에이전트의 책임은 "고객에게 보여도 되는 문장만 만드는 것"이 아니라 **직원이 거를 수 있도록
재료의 성격을 함께 알려주는 것**이다(§1 "누가 읽나"). 이 파일이 그 표시를 만든다.

━━ 두 갈래 ━━
신뢰 등급    어느 자료에서 왔는지가 곧 그 말을 얼마나 단정해도 되는지다(07/01 ② 신뢰 표시).
             등급은 **문서 레지스트리**가 갖고 있고 답변은 그것을 그대로 옮긴다 — 코드가
             카드 내용을 보고 추론하지 않는다. 현장 노하우가 본부 지침으로 읽히면 그게 곧
             잘못된 안내다.
내부용 주의  카드의 `customer_facing` 선언(원문의 ▶·⭕ 표시)이 거짓이면, 답변에 그 내용이
             실릴 때 "고객에게 그대로 안내하지는 마세요"를 함께 준다. **막지는 않는다** —
             내부용 자료도 답변 재료로 쓰고, 무엇을 옮길지는 직원이 정한다(§7 마지막).

━━ 선언이 없으면 아무것도 붙이지 않는다 ━━
`customer_facing` 은 fact·procedure 에만 선언돼 있고, 나머지 종류는 값이 없다. 없는 것을
"내부용일 것이다"로 채우지 않는다 — 판단의 근거는 데이터의 선언이고, 추론이 아니다(§7).
문서를 못 찾은 카드(레지스트리에 doc 이 없는 경우)의 등급도 마찬가지로 비운다.

━━ 왜 한 곳인가 ━━
예전에는 등급 표시가 즉답 카드 노드에만 있었고, 일반 답변은 현장 관찰 한 종류만
전용 문구로 붙였다 — 본부 공식·대외 공개·교육자료 구분은 답변에 아예 나타나지 않았다
(§12 gap 13). 표시를 만드는 곳이 여러 군데면 한 곳만 고쳐지고 나머지는 남는다.
"""

from __future__ import annotations

from pension_agent.consult_agent.kb import KnowledgeBase

#: 문서 레지스트리의 tier → 답변에 옮길 신뢰 표시. 07_에이전트_기능정의/01 ② 의 분류다.
TIER_NOTE: dict[str, str] = {
    "본부공식": "본부 공식 자료",
    "현장팁": "영업점 현장 노하우 — 본부 확정 지침이 아닙니다",
    "대외공개": "대외 공개 자료 — 고객에게 그대로 안내 가능",
    "교육자료": "직원 교육자료",
}

#: 내부용 재료가 답변에 실렸을 때의 주의. 막는 문장이 아니라 알리는 문장이다.
INTERNAL_NOTE = "이 내용은 고객에게 그대로 안내하지는 마세요 — 내부용으로 표시된 자료입니다."


def tier_of(kb: KnowledgeBase, card: dict) -> str | None:
    """이 카드가 온 자료의 신뢰 표시. 문서를 못 찾으면 None(지어내지 않는다)."""
    doc = kb.docs_by_id.get((card.get("_source") or {}).get("doc") or "")
    return TIER_NOTE.get((doc or {}).get("tier") or "") if doc else None


def notes_for(kb: KnowledgeBase, cards: list[dict]) -> list[str]:
    """이 재료들에 붙는 성격 표시. 등장 순서를 지키고 중복은 한 번만 남긴다.

    같은 등급의 카드를 여러 장 썼다고 같은 문장을 여러 번 세우지 않는다 — 상담 중에 읽는
    화면이라, 표시가 늘어설수록 관계있는 표시를 읽고 넘어갈 확률이 올라간다(§7).
    """
    out: list[str] = []
    for card in cards:
        tier = tier_of(kb, card)
        if tier and tier not in out:
            out.append(tier)
    # 내부용 선언이 **거짓인** 카드가 하나라도 있으면 붙인다. 선언이 없는 카드(None)는
    # 판단 근거가 없다는 뜻이므로 아무 쪽으로도 세지 않는다.
    if any(card.get("customer_facing") is False for card in cards) and INTERNAL_NOTE not in out:
        out.append(INTERNAL_NOTE)
    return out
