"""대화형 「하지 말 것」 가드 — 고객 요건 → 지식베이스 주의 문장(method caution·민감 응대 카드).

금지 문장 자체 저작 금지(지식베이스 재료만). 요건은 LLM 도구 선택과 무관하게 `customer_id` 로 코드가 판정.
경고는 코드가 부착 — verify() 는 톤 미검사.
"""

from __future__ import annotations

from typing import Any


from pension_agent.knowledge.kb import KnowledgeBase, origin_of, role_texts, source_url

# 요건 코드 → 지식베이스 검색어. caution 가진 method 카드가 있는 요건만 등록
# 재료 없는 코드(pen·isa·tax·add·nch…)는 미등록 — 검색어 확장으로 억지 매칭 금지
TRIGGERS: dict[str, tuple[str, ...]] = {
    "low": ("수익률 하위", "수익률이 낮", "저조", "민감"),
    "dor": ("미접촉", "장기 미거래"),
    "dep": ("현금성", "고유계정대", "편중"),
    # 미운용 현금성·이탈위험 — m.1-1·m.006
    "idl": ("현금성", "고유계정대"),
    "out": ("현금성", "고유계정대"),
    # 판매중단·환매추천 펀드 — m.023
    "hlt": ("판매중단", "환매추천"),
    # 투자성향 불일치 — m.005
    "mis": ("투자성향",),
    "nod": ("디폴트옵션 미",),
    "mat": ("만기",),
}

# 요건별 최대 건수
PER_COND = 2

# 주의 문장 출처 종류. procedure `cautions` 제외 — 대부분 문서 검증 메모(authoring)
SOURCE_KINDS = ("method",)


def _texts(card: dict) -> list[str]:
    """카드의 상담 주의 문장 — 데이터 role 선언만 사용(문자열 휴리스틱 금지)."""
    return role_texts(card.get("cautions"), "caution")


def conditions_of(customer_id: str | None) -> list[str]:
    """열린 고객의 성립 요건(customer.conditions 재사용). 없거나 실패 시 빈 목록."""
    if not customer_id:
        return []
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        return list(strategy_customer.conditions(profile)) if profile else []
    except Exception:
        return []


def cautions_for(kb: KnowledgeBase, conditions: list[str]) -> list[dict[str, Any]]:
    """요건별 주의사항 + 근거 카드 id. 재료 없으면 빈 목록("low:…" 형태 입력 허용)."""
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
    """«지적 대신 이렇게» 민감 응대 대안 화법 카드(low 요건 시)."""
    codes = {c.split(":")[0] for c in conditions or ()}
    if "low" not in codes:
        return []
    return [{"card": c["id"], "title": c.get("title"), "doc": origin_of(kb, c),
             "url": source_url(kb, c)}
            for c in kb.pitches
            if "민감" in (c.get("title") or "") or
            ("지적" in (c.get("title") or "") and "없이" in (c.get("title") or ""))]


def prompt_note(guards: list[dict[str, Any]], alts: list[dict[str, Any]]) -> str:
    """LLM 프롬프트 톤 지시 — 화면 경고와 같은 재료."""
    if not guards and not alts:
        return ""
    lines = ["[이 고객 상담에서 지켜야 할 것 — 지식베이스 근거]"]
    lines += [f"- {g['text']}" for g in guards]
    lines += [f"- 대안 화법: {a['title']}" for a in alts if a.get("title")]
    lines.append("위 내용을 어기는 표현을 쓰지 않는다. 지적보다 개선안 쪽으로 말한다.")
    return "\n".join(lines)
