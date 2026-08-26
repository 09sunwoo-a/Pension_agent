"""⑦ 예상 반론 및 대응 화법 — 고객이 할 법한 말과 그에 대한 답.

후보를 넉넉히 모아(MAX_OBJECTION_CANDIDATES) LLM 이 고르게 한다. 고객 유형에 맞지 않는
반론은 `_type_eligible()` 이 미리 뺀다 — 고를 수 없는 것을 후보에 넣으면 선별이 흐려진다.
"""

from __future__ import annotations

from pension_agent.strategy_agent.customer import Profile
from pension_agent.strategy_agent.support.kb import pitch_kb
from pension_agent.strategy_agent.support.matching import (
    MAX_OBJECTION_CANDIDATES,
    card_source,
    situation_cards,
)


# ─────────────────────────────────────────────────────────────
# ⑦ 예상 반론 및 대응 화법
# ─────────────────────────────────────────────────────────────

def _objection_entry(card: dict) -> dict:
    """반론 카드를 화면 표시용 {objection, response} 로 압축한다.

    objection 은 고객이 실제로 할 법한 말(trigger_examples 첫 문장, 없으면 카드 제목),
    response 는 카드의 대사(dialogue 중 '행원' 발화 첫 줄)를 그대로 쓴다 — key_points 는
    직원용 요약 불릿이라 '자연스러운 문장'을 요구하는 REQUIREMENTS.md ⑦ 표시에는 부적합하다.
    """
    triggers = card.get("trigger_examples") or []
    reply = next((d["text"] for d in (card.get("dialogue") or []) if d.get("speaker") == "행원"), None)
    return {
        "objection": triggers[0] if triggers else card["title"],
        "response": reply or " / ".join(card.get("key_points") or []),
    }


def _situation_objections(situations: list[dict] | None, limit: int) -> list[dict]:
    """문제상황에 걸린 반론 카드를 ⑦ 표시 형태로 돌려준다."""
    out = []
    for card, seg in situation_cards(situations or [], "objection", limit):
        entry = _objection_entry(card)
        entry.update({"card_id": card["id"], "situation": seg["title"],
                      "source": card_source(card)})
        out.append(entry)
    return out


def pick_objections(p: Profile, selected: list[dict], situations: list[dict] | None = None,
                    n: int = 2) -> list[dict]:
    """예상 반론 카드를 정확히 n개(기본 2개) 선별한다(REQUIREMENTS.md ⑦ "예상 반론 및 대응 화법").

    1차: 선정 항목(selected)에 저작된 objection_refs 를 customer_type 매칭으로 취합한다
    (pitch_talk() 과 같은 방식 — 내용을 복사하지 않고 매 요청마다 consult_agent 쪽 원본을
    실시간 조회).

    2차: 문제상황(situations)에 걸린 반론 카드. objection_refs 저작이 아직 없어서(현재 0건)
    1차는 사실상 비어 있는데, 예전 폴백은 카드 id 순으로 집어 전 고객에게 같은 반론 2개가
    나왔다 — 편중 고객에게 "지금 쓸 돈도 없어요" 가 뜨는 식이었다. 관리 사유에서 찾으면
    고객마다 다른, 실제로 나올 법한 반론이 나온다.

    3차: 그래도 못 채우면 노출 가능한 카드를 id 순으로 채운다(결정론적 최후 보루).
    """
    # 제안할 것도 없고 관리 사유도 없으면 반박할 대상도 없다. 반대로 문제상황이 잡혀 있으면
    # ⑥ 이 그 사유로 화법을 보여주므로, 전략이 없더라도 예상 반론은 보여줘야 짝이 맞는다.
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
    """이 고객에게 노출 가능한 카드인지(고객유형 적합성)만 본다. 관련도 판단은 하지 않는다."""
    types = card["tags"].get("customer_type") or []
    return not p.customer_type or p.customer_type in types or "공통" in types


def objection_candidates(p: Profile, selected: list[dict],
                         situations: list[dict] | None = None) -> list[dict]:
    """⑦ 예상 반론의 후보군 — 이 고객 상담에서 나올 법한 반론 카드.

    REQUIREMENTS.md §15 가 예상 반론을 '반론 DB(Rule) + 선별(LLM)' 로 지정한다. 규칙은 후보를
    모으는 데까지, 그중 무엇을 보여줄지는 LLM 이 고른다(agent._select_db_sections).

    후보를 '노출 가능한 카드 전체'로 두지 않는 이유: 지식베이스가 커지면서 적격 카드가 45건이
    되었는데, 그 정도 목록을 주면 LLM 이 고객 상태와 무관한 반론을 고르기 쉽고 프롬프트도 커진다.
    문제상황에 걸린 것으로 좁히면 후보 자체가 이미 '이 고객 이야기'가 된다. 상황이 없을 때만
    고객유형 적격 카드 전체로 물러선다(기존 동작).
    """
    kb = pitch_kb()
    if kb is None or (not selected and not situations):
        return []
    scoped = _situation_objections(situations, limit=MAX_OBJECTION_CANDIDATES)
    if scoped:
        return scoped
    return [_objection_entry(c) for c in sorted(kb.pitches, key=lambda c: c["id"])
            if c["type"] == "objection" and _type_eligible(c, p)]
