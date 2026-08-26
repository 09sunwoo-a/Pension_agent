"""⑥ 이렇게 말해보세요 — 선정된 전략에 붙일 화법 후보.

카드 원문을 그대로 넘기고, 대고객 문장화는 agent.py 의 LLM 단계가 한다. 시효성 수치가
들어간 카드는 `_freshness_notes()` 가 기준시점을 함께 달아 보낸다 — 원문은 고치지 않고
파생 텍스트에만 현재값을 끼우는 규약(CLAUDE.md 1번)의 표시 쪽이다.
"""

from __future__ import annotations

from pension_agent.strategy_agent.customer import Profile
from pension_agent.strategy_agent.support.kb import pitch_kb
from pension_agent.strategy_agent.support.matching import card_source, situation_cards


# ─────────────────────────────────────────────────────────────
# ⑥ 이렇게 말해보세요
# ─────────────────────────────────────────────────────────────

def pitch_talk(spec: dict, customer_type: str | None) -> str:
    """전략의 상담 화법을 가져온다. 두 갈래를 모두 지원한다.

    ① `pitch_refs` 가 있으면 연결된 화법 카드의 핵심 포인트·주의사항을 그 자리에서 조회한다.
    내용을 strategies.json 에 복사해두지 않으므로, consult_agent 쪽 카드가 수정되면 다음
    호출부터 바로 반영된다 — '따로 관리되다 원본과 어긋나는' 문제가 생기지 않는다.
    customer_type 이 일치하는 참조를 먼저 찾고, 없으면 "공통" 참조를 쓴다.

    ② `talk` 필드는 pitch_refs 가 없는 전략의 화법이다(현재 st.chn_retain·st.dor_contact).
    카드 참조가 준비되지 않은 전략과, 화법 KB 자체를 못 읽는 상황(kb is None — 두 에이전트가
    한 프로세스에 없을 때)을 함께 받는다. 폐기 대상이 아니라 이 두 경로의 정상 공급원이다.

    반환값이 빈 문자열이면 그 전략은 ⑥ 화법 후보에서 제외된다(pick_talking_points 참고).
    """
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
    """시효성 표시 — 확인 요구 경고 · 원문 대비 현재 금리(consult_agent.kb 가 붙인 값).

    카드 대사에 금리가 없고 인용문에만 있는 경우엔 원문을 고치지 않으므로, 대신 "원문 X ·
    현재 Y" 를 재료에 얹는다. 확인 요구(_verify_first)는 주장을 판정할 근거가 시스템에
    없는 카드에 붙는다 — 지어내지 않고 행원에게 확인을 넘긴다.
    """
    out = [f"(확인 필요) {card['_verify_first']}"] if card.get("_verify_first") else []
    for n in card.get("_rate_notes") or []:
        out.append(f"(현재 금리) {n['what']} 원문 {n['was']} · 현재 {n['now']}"
                   f"{' ' + card['_rate_as_of'] + ' 기준' if card.get('_rate_as_of') else ''}")
    return out


def _card_talk(card: dict) -> str:
    """화법 카드를 ⑥ 재료 한 줄로 압축한다. pitch_talk() 의 카드 경로와 같은 형식."""
    parts = list(card.get("key_points") or [])
    parts += [f"(주의) {c}" for c in (card.get("cautions") or [])]
    parts += _freshness_notes(card)
    return " / ".join(parts) or (card.get("summary") or card.get("content") or "")


def pick_talking_points(p: Profile, selected: list[dict], alternatives: list[dict],
                        situations: list[dict] | None = None, n: int = 2) -> list[dict]:
    """대고객 TM 화법을 정확히 n개(기본 2개) 보장한다(REQUIREMENTS.md ⑥ "이렇게 말해보세요").

    세 갈래로 채운다.
      ① 선정 전략(selected)에 저작된 화법 — 전략과 화법이 1:1 로 붙어 있어 가장 구체적이다.
      ② 대안 전략(alternatives)의 화법 — 접촉 성격의 전략만 화법을 가지므로 여기서 보충한다.
      ③ 문제상황(situations)에 걸린 제안 화법 카드 — ①②는 '전략이 있어야' 나오는데, 전략이
         없거나 화법이 저작되지 않은 고객(실측상 6명 중 5명)이 그대로 빈칸이 되기 때문이다.
         고객의 관리 사유 자체에서 화법을 찾으므로 전략 저작 상태와 무관하게 채워진다.

    반환 항목은 대고객 스크립트 생성(agent.py)의 재료가 된다 — amount·products 를 함께 담아
    스크립트가 이 고객의 실제 금액·상품명을 반영하게 한다. 금액·상품명 표기(`amount_fmt`·
    `products_fmt`)는 engine.prepare() 가 미리 렌더링해 넘긴다(이 모듈은 engine 을 쓰지 않는다).

    `title` 은 화면 제목이자 agent 쪽 스크립트 매핑 키라 항목마다 달라야 한다 — 겹치면 접미를 붙인다.
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
