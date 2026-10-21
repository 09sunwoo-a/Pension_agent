"""⑦ 예상 반론 및 대응 화법 — 후보 수집(LLM 선별), 고객유형 부적격은 `_type_eligible()` 로 사전 제외."""

from __future__ import annotations

from pension_agent.tools.customer_solution.customer import Profile
from pension_agent.tools.customer_solution.support.reference_kb import pitch_kb
from pension_agent.tools.customer_solution.support.matching import (
    MAX_OBJECTION_CANDIDATES,
    card_source,
    situation_cards,
)


def _objection_entry(card: dict) -> dict:
    """반론 카드 → {objection: trigger_examples[0]|제목, response: '행원' 첫 대사}(key_points 는 폴백만)."""
    triggers = card.get("trigger_examples") or []
    reply = next((d["text"] for d in (card.get("dialogue") or []) if d.get("speaker") == "행원"), None)
    return {
        "objection": triggers[0] if triggers else card["title"],
        "response": reply or " / ".join(card.get("key_points") or []),
    }


def _situation_objections(situations: list[dict] | None, limit: int) -> list[dict]:
    """문제상황 반론 카드 → ⑦ 표시 형태."""
    out = []
    for card, seg in situation_cards(situations or [], "objection", limit):
        entry = _objection_entry(card)
        entry.update({"card_id": card["id"], "situation": seg["title"],
                      "source": card_source(card)})
        out.append(entry)
    return out


def pick_objections(p: Profile, selected: list[dict], situations: list[dict] | None = None,
                    n: int = 2) -> list[dict]:
    """⑦ 반론 n개 — ① 선정 전략 objection_refs ② 문제상황 반론 카드 ③ 적격 카드 id 순."""
    if not selected and not situations:
        return []
    kb = pitch_kb()
    if kb is None:
        return []
    by_id = {c["id"]: c for c in kb.pitches if c["type"] == "objection"}

    picked_ids: list[str] = []
    for b in selected:
        for ref in b["spec"].get("objection_refs") or []:
            if len(picked_ids) >= n:
                break
            if ref.get("customer_type") not in (p.customer_type, "공통"):
                continue
            if ref["id"] in by_id and ref["id"] not in picked_ids:
                picked_ids.append(ref["id"])
        if len(picked_ids) >= n:
            break

    out = [_objection_entry(by_id[cid]) for cid in picked_ids[:n]]

    if len(out) < n:
        for entry in _situation_objections(situations, limit=n * 3):
            if len(out) >= n:
                break
            if entry["card_id"] not in picked_ids:
                picked_ids.append(entry["card_id"])
                out.append(entry)

    if len(out) < n:
        for cid in sorted(by_id):
            if len(out) >= n:
                break
            if cid in picked_ids:
                continue
            types = by_id[cid]["tags"].get("customer_type") or []
            if p.customer_type and p.customer_type not in types and "공통" not in types:
                continue
            picked_ids.append(cid)
            out.append(_objection_entry(by_id[cid]))

    return out[:n]


def _type_eligible(card: dict, p: Profile) -> bool:
    """고객유형 노출 적격 여부(관련도 판단 없음)."""
    types = card["tags"].get("customer_type") or []
    return not p.customer_type or p.customer_type in types or "공통" in types


def objection_candidates(p: Profile, selected: list[dict],
                         situations: list[dict] | None = None) -> list[dict]:
    """⑦ 후보군(LLM 선별용) — 문제상황 반론 카드, 없으면 고객유형 적격 카드 전체."""
    kb = pitch_kb()
    if kb is None or (not selected and not situations):
        return []
    scoped = _situation_objections(situations, limit=MAX_OBJECTION_CANDIDATES)
    if scoped:
        return scoped
    return [_objection_entry(c) for c in sorted(kb.pitches, key=lambda c: c["id"])
            if c["type"] == "objection" and _type_eligible(c, p)]
