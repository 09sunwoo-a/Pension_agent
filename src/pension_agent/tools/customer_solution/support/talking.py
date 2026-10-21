"""⑥ 이렇게 말해보세요 — 화법 후보. 카드 원문 그대로 전달, 시효성 수치는 `_freshness_notes()` 로 부기."""

from __future__ import annotations

from pension_agent.tools.customer_solution.customer import Profile
from pension_agent.tools.customer_solution.support.reference_kb import pitch_kb
from pension_agent.tools.customer_solution.support.matching import card_source, situation_cards


def pitch_talk(spec: dict, customer_type: str | None) -> str:
    """전략 화법 — `pitch_refs` 카드 조회(customer_type → "공통" 순), 없거나 KB 미적재 시 `talk` 필드. 빈 값이면 ⑥ 제외."""
    refs = spec.get("pitch_refs") or []
    pick = next((r for r in refs if r.get("customer_type") == customer_type), None) \
        or next((r for r in refs if r.get("customer_type") == "공통"), None)
    kb = pitch_kb()
    if pick is None or kb is None:
        return spec.get("talk", "")
    card = next((c for c in kb.pitches if c["id"] == pick["id"]), None)
    if card is None:
        return spec.get("talk", "")
    parts = list(card.get("key_points") or [])
    parts += [f"(주의) {c}" for c in (card.get("cautions") or [])]
    parts += _freshness_notes(card)
    return " / ".join(parts)


def _freshness_notes(card: dict) -> list[str]:
    """시효성 표시 — 확인 요구 경고 · «원문 X · 현재 Y» 금리(원문 수정 금지)."""
    out = [f"(확인 필요) {card['_verify_first']}"] if card.get("_verify_first") else []
    for n in card.get("_rate_notes") or []:
        out.append(f"(현재 금리) {n['what']} 원문 {n['was']} · 현재 {n['now']}"
                   f"{' ' + card['_rate_as_of'] + ' 기준' if card.get('_rate_as_of') else ''}")
    return out


def _card_talk(card: dict) -> str:
    """화법 카드 → ⑥ 재료 한 줄(pitch_talk 카드 경로와 같은 형식)."""
    parts = list(card.get("key_points") or [])
    parts += [f"(주의) {c}" for c in (card.get("cautions") or [])]
    parts += _freshness_notes(card)
    return " / ".join(parts) or (card.get("summary") or card.get("content") or "")


def pick_talking_points(p: Profile, selected: list[dict], alternatives: list[dict],
                        situations: list[dict] | None = None, n: int = 2) -> list[dict]:
    """⑥ 화법 최대 n개 — ① 선정 전략 화법 ② 대안 전략 화법 ③ 문제상황 제안 카드 순으로 채움.

    `title` 은 스크립트 매핑 키 — 중복 시 접미 부착.
    """
    def _entry(b: dict, talk: str) -> dict:
        return {
            "title": b["spec"]["title"],
            "talk": talk,
            "amount": b.get("amount_fmt"),
            "products": list(b.get("products_fmt") or []),
        }

    picked = [_entry(b, b["talk"]) for b in selected if b.get("talk")]

    used_ids = {b["spec"]["id"] for b in selected}
    for b in alternatives:
        if len(picked) >= n:
            break
        if b["spec"]["id"] in used_ids:
            continue
        talk = pitch_talk(b["spec"], p.customer_type)
        if talk:
            picked.append(_entry(b, talk))

    if len(picked) < n:
        titles = {t["title"] for t in picked}
        for card, seg in situation_cards(situations or [], "proposal", limit=n * 3):
            if len(picked) >= n:
                break
            talk = _card_talk(card)
            if not talk:
                continue
            title = card["title"]
            if title in titles:
                title = f"{title} ({seg['title']})"
            titles.add(title)
            picked.append({
                "title": title, "talk": talk, "amount": None, "products": [],
                "card_id": card["id"], "situation": seg["title"], "source": card_source(card),
            })
    return picked[:n]
