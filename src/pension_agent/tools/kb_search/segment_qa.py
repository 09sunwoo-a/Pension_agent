"""고객군 정의 — segment 도구의 검색과 근거 블록 조립.

고객 화면이 열려 있으면 해당 여부 한 줄 추가(판정은 situations.problem_situations 재사용).
"""

from __future__ import annotations

from pension_agent.knowledge.kb import origin_of, role_texts
from pension_agent.tools.kb_search.select import pick
from pension_agent.state import KB
from pension_agent.tools.customer_solution import customer as strategy_customer
from pension_agent.tools.customer_solution.situations import problem_situations

TOP_K = 2


def _matched_ids(customer_id: str) -> set[str] | None:
    """고객에게 성립하는 세그먼트 id. 실패 시 None(해당 여부 미표시)."""
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        return {s["id"] for s in problem_situations(profile)}
    except Exception:
        return None


def _render(card: dict, matched: set[str] | None, customer_id: str | None) -> list[str]:
    lines = [f"■ {card['title']}  ({card.get('group')})"]
    if card.get("condition_text"):
        lines += ["", f"· 누구를 골라내나: {card['condition_text']}"]
    if card.get("reason_text"):
        lines.append(f"· 왜 관리하나: {card['reason_text']}")
    for q in (card.get("quotes") or [])[:1]:
        if q.get("text"):
            lines += ["", f"  “{q['text'].strip().strip(chr(34))}”"]
    for text in role_texts(card.get("note"), "caution"):
        lines.append(f"⚠ {text}")
    for text in role_texts(card.get("note"), "info"):
        lines.append(f"· {text}")
    if card.get("conds"):
        lines.append(f"· 판정 요건: {', '.join(card['conds'])}")
    else:
        lines.append("· 이 세그먼트는 자동 판정 요건이 없습니다(이벤트형이거나 보유하지 않은 데이터).")
    if matched is not None and customer_id:
        # KB-PIN 미포함 — 게이트웨이 개인정보 필터가 400 응답
        hit = card["id"] in matched
        lines.append("· 지금 열려 있는 고객: "
                     + ("이 세그먼트에 해당합니다." if hit else "해당하지 않습니다."))
    lines.append(f"· 출처 {origin_of(KB, card)}")
    return lines


def search(question: str) -> list[tuple[float, dict]]:
    """세그먼트 카드 top-2."""
    return pick(("segment",), question, top_k=TOP_K)


def render(hits: list[tuple[float, dict]], customer_id: str | None = None) -> str:
    """세그먼트 정의 블록(+열린 고객의 해당 여부)."""
    matched = _matched_ids(customer_id) if customer_id else None
    return "\n\n".join("\n".join(_render(c, matched, customer_id)) for _, c in hits)
