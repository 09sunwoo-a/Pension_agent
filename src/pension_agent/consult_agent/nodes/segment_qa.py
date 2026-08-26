"""고객군 정의 — `segment` 도구(tools.py)의 검색과 근거 블록 조립.

브리핑 화면의 ⑥~⑨ 가 '이 고객의 문제상황'에서 출발하는데, 그 문제상황이 무엇이고 왜 관리
대상인지는 화면에 한 줄로만 나온다. 직원이 근거를 더 묻는 자리가 여기다.

재료는 06/01 고객세그먼트다 — 조건(누구를 골라내나)·이유(왜 관리하나)·원문 인용·출처가
한 레코드에 다 있다. 예전에는 이걸 그대로 답변으로 내보내는 `segment_explain` 즉답 노드가
있었으나 §11 에 따라 지웠다(facts_qa 주석).

고객 화면이 열려 있으면(customer_id) 그 고객이 실제로 이 세그먼트에 해당하는지 한 줄 덧붙인다 —
판정은 strategy_agent 의 situations.problem_situations() 를 그대로 쓴다(같은 판정을 두 번
구현하지 않는다).
"""

from __future__ import annotations

from pension_agent.consult_agent.kb import origin_of, role_texts
from pension_agent.consult_agent.select import pick
from pension_agent.consult_agent.state import KB
from pension_agent.strategy_agent import customer as strategy_customer
from pension_agent.strategy_agent.situations import problem_situations

TOP_K = 2


def _matched_ids(customer_id: str) -> set[str] | None:
    """이 고객에게 성립하는 세그먼트 id. 브리핑 쪽을 못 부르면 None(해당 여부를 말하지 않는다)."""
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
        hit = card["id"] in matched
        lines.append(f"· 지금 열려 있는 고객({customer_id}): "
                     + ("이 세그먼트에 해당합니다." if hit else "해당하지 않습니다."))
    lines.append(f"· 출처 {origin_of(KB, card)}")
    return lines


def search(question: str) -> list[tuple[float, dict]]:
    """세그먼트 카드 top-2. `segment` 도구가 부른다."""
    return pick(("segment",), question, top_k=TOP_K)


def render(hits: list[tuple[float, dict]], customer_id: str | None = None) -> str:
    """세그먼트 정의 블록. compose 의 재료이자 복구 블록이다.
    고객 화면이 열려 있으면 그 고객의 해당 여부를 한 줄 덧붙인다."""
    matched = _matched_ids(customer_id) if customer_id else None
    return "\n\n".join("\n".join(_render(c, matched, customer_id)) for _, c in hits)
