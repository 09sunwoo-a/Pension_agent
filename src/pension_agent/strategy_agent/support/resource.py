"""⑧ 상담에 참고하세요 — 직원이 상담 중에 열어볼 자료.

`customer_facing` 이 False 인 자료는 "고객 직접 제공 금지"로 표기된다. 내부용 자료가
고객에게 그대로 건너가지 않게 하는 것이 이 섹션의 유일한 위험 지점이다.
"""

from __future__ import annotations

from itertools import zip_longest

from pension_agent.strategy_agent.customer import CONDS, Profile
from pension_agent.strategy_agent.support.kb import pitch_kb, pitch_kb_module
from pension_agent.strategy_agent.support.objection import _type_eligible
from pension_agent.strategy_agent.support.matching import (
    MAX_RESOURCE_CANDIDATES,
    card_source,
    situation_cards,
    situation_methods,
    situation_procedures,
)


# ─────────────────────────────────────────────────────────────
# ⑧ 상담에 참고하세요
# ─────────────────────────────────────────────────────────────

def _snippet(card: dict) -> str:
    """카드 한 줄 요약 — 종류마다 본문이 실린 자리가 다르다."""
    if card.get("key_points"):
        return " ".join(card["key_points"][:2])
    if card["_kind"] == "method":
        # 방법론은 '상황 → 액션' 이 곧 요약이다.
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
    """문제상황에 걸린 상담 참고 자료 — 노하우·논거·상담 태도(guide 카드) + 업무 처리 절차.

    제안 화법(proposal)은 넣지 않는다. ⑥ '이렇게 말해보세요' 가 이미 그걸 보여주므로, 같은 카드를
    ⑧ 에 다시 실으면 두 섹션이 같은 내용이 된다(실제로 그렇게 나왔다). ⑧ 은 '다음 액션 제안'이
    아니라 상담에 참고할 자료라는 요건(REQUIREMENTS.md ⑧)에 맞춰 종류로 갈라 둔다.

    절차를 함께 싣는 이유는 '통화 전 확인할 화면'(07_에이전트_기능정의/01 ① 7요소)이다 —
    상담 전에 무슨 화면을 봐야 하는지가 화법만큼이나 실무에서 필요한 참고 자료다.
    """
    situations = situations or []
    lanes = (
        [_resource_entry(c, s) for c, s in situation_methods(situations, limit)],
        [_resource_entry(c, s) for c, s in situation_cards(situations, "guide", limit)],
        [_resource_entry(c, s) for c, s in situation_procedures(situations, limit)],
    )
    # 방법론·노하우 화법·절차를 번갈아 싣는다 — 한 종류가 상위를 독점하면 다른 축이 화면에서
    # 사라진다(판단 규칙만 나오고 볼 화면은 안 나오는 식).
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
    """상담에 참고하세요 — 당행 노하우/가이드 스니펫 n개(기본 2개, REQUIREMENTS.md ⑧).

    1차는 문제상황에 걸린 자료다 — 이 고객을 관리 대상으로 만든 사유에 직접 붙는 자료라
    "지금 이 상담에 유용한 리소스"라는 요건에 가장 가깝다.

    2차는 기존 경로: 프로파일만 입력이라 자연어 질문이 없으므로 성립 요건 라벨을 의사-발화로
    합성해 consult_agent.kb.retrieve() 를 호출한다(기존 n-gram+태그 스코어링 재사용). 다만 이
    경로는 요건 라벨과 카드 표현이 문자 단위로 겹쳐야 해서 실측상 대부분 0건이었다 — 그래서
    1차를 앞에 두었다.
    """
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
    """⑧ 상담 참고 리소스의 후보군.

    ⑦ 과 같은 이유로 문제상황에 걸린 자료로 좁힌다(objection_candidates 주석 참고).
    상황이 없으면 노출 가능한 guide 카드 전체로 물러선다.
    """
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
