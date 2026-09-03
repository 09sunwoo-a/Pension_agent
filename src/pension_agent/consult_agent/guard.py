"""대화형 「하지 말 것」 가드 — 지식베이스에 있는 것만.

07_에이전트_기능정의/01 ① 필수 구성 요소 6("⚠ 하지 말 것")을 대화형에 반영한다. 브리핑
화면(왼쪽)에 칸을 만들지 않고 대화형(오른쪽)에서 처리하는 이유는, 이 항목이 "지금 이 고객을
어떻게 대할 것인가"라 상담 흐름 안에서만 의미가 있기 때문이다.

**기준은 지식베이스다.** 규칙을 새로 쓰지 않고 행원들이 이미 정리해둔 재료만 쓴다 —
`method.cautions` 중 역할이 caution 인 것 · 민감 응대 화법 카드. 지식베이스에 없는
금지는 만들지 않는다. 그래서 이 파일에는 "무엇을 하지 말라"는 문장이 하나도 없고, 고객 요건
→ 지식베이스 검색어의 연결만 있다(아래 TRIGGERS).

경고는 **코드가 붙인다.** 프롬프트로 톤만 잡으면 LLM 이 무시해도 아무도 모른다 —
`verify()` 는 수치·상품명만 검사하지 톤은 검사하지 않아서, "수익률이 1.9%로 저조합니다"는
재료 안의 값이라 그대로 통과한다. 그래서 프롬프트 지시(톤)와 화면 경고(사실)를 함께 쓴다.

**무엇에 걸리는지도 코드가 판단한다.** 예전에는 이번 턴의 근거 원장에 고객 요건이 실려
있을 때만 가드가 붙었다 — 즉 LLM 이 `customer` 도구를 부른 턴에만 붙었다. 고객 상태는
코드가 이미 아는 값인데 LLM 의 도구 선택에 의존한 셈이고, 그래서 화법만 물은 턴에는
"이 고객에게 하면 안 되는 것"이 조용히 빠졌다(CLAUDE.md §8 · §12 gap 10). 지금은
`customer_id` 하나로 코드가 요건을 읽는다(`conditions_of`).
"""

from __future__ import annotations

from typing import Any


from pension_agent.consult_agent.kb import KnowledgeBase, origin_of, role_texts, source_url

# 고객 요건(customer.conditions() 의 코드) → 지식베이스에서 찾을 말.
# 값은 "무엇을 하지 말라"가 아니라 **어디를 뒤질까**다 — 문장은 지식베이스가 갖고 있다.
TRIGGERS: dict[str, tuple[str, ...]] = {
    "low": ("수익률 하위", "수익률이 낮", "저조", "민감"),
    "dor": ("미접촉", "장기 미거래"),
    "dep": ("현금성", "고유계정대", "편중"),
    "nod": ("디폴트옵션 미",),
    "mat": ("만기",),
}

# 요건별로 몇 건까지 올릴지. 상담 중에 읽을 수 있는 분량을 넘기면 아무도 안 읽는다.
PER_COND = 2

# 주의사항을 가져올 종류. method(관리 방법론)의 caution 역할 항목만 쓴다.
#
# procedure 의 `cautions` 는 형태는 같지만 성격이 다르다 — 대부분 "⚠ 유의: 자료마다 화면명
# 표기가 다름 / 필자 해석 / 확인 필요" 같은 **문서 검증 메모**라, 변환기가 일괄 authoring
# 으로 선언한다(build_kb). 그걸 상담 경고로 띄우면 행원에게 아무 쓸모가 없는 문장이 뜨고,
# 진짜 경고가 그 사이에 묻힌다.
SOURCE_KINDS = ("method",)


def _texts(card: dict) -> list[str]:
    """이 카드의 상담 주의. 역할은 데이터 선언(role)만 본다 — method 안에 섞여 있는 저작
    메모(예: m.010 "…팀 논의 필요")는 변환기가 authoring 으로 선언해 여기서 걸러진다.
    예전에는 문자열 휴리스틱(_AUTHORING 표지)으로 런타임에 걸렀는데, 분류가 데이터에 남지
    않아 검토할 수 없었다."""
    return role_texts(card.get("cautions"), "caution")


def conditions_of(customer_id: str | None) -> list[str]:
    """지금 열려 있는 고객의 성립 요건. 고객 화면이 닫혀 있거나 못 읽으면 빈 목록.

    판정은 strategy_agent 것을 그대로 쓴다 — 같은 판정을 두 번 구현하지 않는다(§3).
    임포트를 함수 안에 두는 이유는 strategy_agent 가 무겁기 때문이다.
    """
    if not customer_id:
        return []
    from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        return list(strategy_customer.conditions(profile)) if profile else []
    except Exception:
        return []


def cautions_for(kb: KnowledgeBase, conditions: list[str]) -> list[dict[str, Any]]:
    """이 고객 상태에서 지켜야 할 주의사항. 근거 카드 id 를 함께 돌려준다.

    `conditions` 는 `customer.conditions()` 결과("low:수익률 하위 30%" 형태 포함)를 받는다.
    해당하는 재료가 없으면 **빈 목록**이다 — 없는 기준을 만들어 채우지 않는다.
    """
    codes = [c.split(":")[0] for c in conditions or ()]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for code in codes:
        words = TRIGGERS.get(code)
        if not words:
            continue
        picked = 0
        for card in kb.cards:
            if picked >= PER_COND:
                break
            if card.get("_kind") not in SOURCE_KINDS:
                continue
            blob = " ".join(str(card.get(k) or "") for k in ("title", "situation", "summary"))
            if not any(w in blob for w in words):
                continue
            for text in _texts(card):
                key = text[:60]
                if key in seen:
                    continue
                seen.add(key)
                out.append({"cond": code, "text": text, "card": card["id"],
                            "doc": origin_of(kb, card), "url": source_url(kb, card)})
                picked += 1
                break
    return out


def sensitive_cards(kb: KnowledgeBase, conditions: list[str]) -> list[dict[str, Any]]:
    """"지적 대신 이렇게" 를 담은 민감 응대 화법 카드.

    금지만 알려주면 행원은 할 말이 없어진다. 지식베이스는 대안까지 갖고 있으므로
    (예: pitch.k03.028 "수익률 하위 고객에게 → 지적 없이 비교그룹 대조로 접근") 함께 올린다.
    """
    codes = {c.split(":")[0] for c in conditions or ()}
    if "low" not in codes:
        return []
    return [{"card": c["id"], "title": c.get("title"), "doc": origin_of(kb, c),
             "url": source_url(kb, c)}
            for c in kb.pitches
            if "민감" in (c.get("title") or "") or
            ("지적" in (c.get("title") or "") and "없이" in (c.get("title") or ""))]


def prompt_note(guards: list[dict[str, Any]], alts: list[dict[str, Any]]) -> str:
    """LLM 프롬프트에 얹을 톤 지시. 화면 경고와 **같은 재료**에서 만든다."""
    if not guards and not alts:
        return ""
    lines = ["[이 고객 상담에서 지켜야 할 것 — 지식베이스 근거]"]
    lines += [f"- {g['text']}" for g in guards]
    lines += [f"- 대안 화법: {a['title']}" for a in alts if a.get("title")]
    lines.append("위 내용을 어기는 표현을 쓰지 않는다. 지적보다 개선안 쪽으로 말한다.")
    return "\n".join(lines)
