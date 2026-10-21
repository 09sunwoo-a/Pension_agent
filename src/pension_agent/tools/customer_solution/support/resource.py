"""⑧ 상담에 참고하세요 — 직원용 상담 참고 자료. `customer_facing` False 는 «고객 직접 제공 금지» 표기."""

from __future__ import annotations

from itertools import zip_longest

from pension_agent.tools.customer_solution.customer import CONDS, Profile
from pension_agent.tools.customer_solution.support.reference_kb import pitch_kb, pitch_kb_module
from pension_agent.tools.customer_solution.support.objection import _type_eligible
from pension_agent.tools.customer_solution.support.matching import (
    MAX_RESOURCE_CANDIDATES,
    card_source,
    situation_cards,
    situation_methods,
    situation_procedures,
)


def _snippet(card: dict) -> str:
    """카드 한 줄 요약(종류별 본문 위치 상이)."""
    if card.get("key_points"):
        return " ".join(card["key_points"][:2])
    if card["_kind"] == "method":
        parts = [x for x in (card.get("situation"), card.get("action")) if x]
        return " → ".join(parts)
    return card.get("content") or card.get("summary") or card.get("implication") or ""


def _resource_entry(card: dict, seg: dict | None = None) -> dict:
    entry = {"title": card["title"], "snippet": _snippet(card)}
    if seg is not None:
        entry.update({"card_id": card["id"], "situation": seg["title"],
                      "source": card_source(card)})
    if card.get("screens"):
        entry["screens"] = list(card["screens"])
    return entry


def _situation_resources(situations: list[dict] | None, limit: int) -> list[dict]:
    """문제상황 참고 자료 — 방법론·guide 카드·절차. proposal 미포함(⑥ 과 중복)."""
    situations = situations or []
    lanes = (
        [_resource_entry(c, s) for c, s in situation_methods(situations, limit)],
        [_resource_entry(c, s) for c, s in situation_cards(situations, "guide", limit)],
        [_resource_entry(c, s) for c, s in situation_procedures(situations, limit)],
    )
    # 종류별 교차 배치 — 한 종류의 상위 독점 방지
    out, seen = [], set()
    for row in zip_longest(*lanes):
        for entry in row:
            if entry is None or entry["title"] in seen:
                continue
            seen.add(entry["title"])
            out.append(entry)
    return out[:limit]


def consult_resources(p: Profile, conds: list[str], situations: list[dict] | None = None,
                      n: int = 2) -> list[dict]:
    """⑧ 참고 자료 n개 — 1차 문제상황 자료, 2차 요건 라벨 의사-발화로 kb.retrieve()."""
    scoped = _situation_resources(situations, limit=n)
    if len(scoped) >= n:
        return scoped[:n]
    if not conds:
        return scoped
    kb_mod = pitch_kb_module
    kb = pitch_kb()
    if kb_mod is None or kb is None:
        return scoped
    utterance = "、".join(CONDS[c] for c in conds)
    hits = kb_mod.retrieve(kb, top_k=n * 2, customer_type=p.customer_type, utterance=utterance)
    titles = {r["title"] for r in scoped}
    for _, c in hits:
        if len(scoped) >= n:
            break
        if c.get("type") == "guide" and c["title"] not in titles:
            scoped.append(_resource_entry(c))
    return scoped[:n]


def consult_resource_candidates(p: Profile, situations: list[dict] | None = None) -> list[dict]:
    """⑧ 후보군 — 문제상황 자료, 없으면 노출 가능 guide 카드 전체."""
    kb = pitch_kb()
    if kb is None:
        return []
    scoped = _situation_resources(situations, limit=MAX_RESOURCE_CANDIDATES)
    if scoped:
        return scoped
    return [
        _resource_entry(c)
        for c in sorted(kb.pitches, key=lambda c: c["id"])
        if c["type"] == "guide" and _type_eligible(c, p)
    ]
