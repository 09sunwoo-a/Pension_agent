"""관계 기반 답변 점검 — 값과 조건을 잘못 짝지었는가, 알려진 오답을 말했는가 (§6).

`verify_texts()` 는 수치의 **집합 포함** 검사다. 원장에 있는 숫자를 답변이 썼는지만 보므로
**잘못 짝지은 것을 못 잡는다** — "총급여 5,500만원 이하 16.5%, 초과 13.2%" 가 원장에 있으면
"초과면 16.5%" 도 통과한다(두 숫자가 다 원장에 있으므로). 그 구멍을 막으려고 지금까지는
원문 문장을 통째로 답변에 싣게 강제했고(`atomic`), 그래서 답변이 인용문 나열이 됐다.

여기서는 **데이터가 선언한 관계**로 본다(`knowledge/CLAUDE.md` §1·§2).

  tiers      조건–값 쌍. 답변이 어떤 조건을 말하면서 **다른 조건의 값**을 붙였는지 본다.
  pitfalls   행원들이 적어둔 "자주 틀리는 지점" 중 틀린 표현을 인용한 것. 답변에 그 표현이
             그대로 있으면 알려진 오답을 말한 것이다.

━━ 커버리지는 저작된 범위와 같다 ━━
선언이 없는 카드의 오짝은 못 잡는다. 그래서 이 검사는 원문 강제를 **선언이 있는 카드에
한해** 대신한다 — 선언이 없으면 `atomic` 이 그대로 남는다(tools.py). 저작이 넓어지는 만큼
원문 강제가 물러난다.

━━ 못 잡는 것을 잡은 척하지 않는다 ━━
판정은 문자열 대조다. 답변이 조건을 다른 말로 풀어 쓰면(“5,500만원을 넘으면”) 이 검사는
아무 말도 하지 않는다. 그건 통과가 아니라 **판정 불가**이고, 판정 불가를 위반으로 바꾸면
맞는 답이 막힌다 — 검증기가 옳은 문장을 거부하는 것은 틀린 문장을 통과시키는 것보다
나쁘다(직원은 왜 막혔는지 알 수 없다).
"""

from __future__ import annotations

from pension_agent.verify import numbers

#: 조건과 값이 "같은 자리에서" 만났다고 볼 거리(글자 수). 한 문장 안에서 값과 조건이
#: 붙어 나오는 것을 보려는 것이라, 문단 전체로 넓히면 서로 다른 문장의 조건과 값이
#: 우연히 짝지어져 오탐이 난다.
NEAR = 60


def _tokens(when: str) -> list[str]:
    """조건 문장에서 대조에 쓸 조각. 괄호 안 보충 설명은 떼고 앞부분만 본다.

    조건 전체를 통째로 찾으면 답변이 한 글자만 달리 써도 못 찾는다. 그렇다고 잘게 쪼개면
    다른 조건과 겹친다("총급여 5,500만원"은 이하·초과 양쪽에 다 있다). 그래서 **경계를
    가르는 말**(이하·초과·미만·이상)을 포함한 마지막 조각을 쓴다.
    """
    head = when.split("(")[0].strip()
    return [head] if head else []


def _edge(when: str) -> str | None:
    """이 조건을 다른 조건과 가르는 말. 없으면 조건끼리 문자열로 구분되지 않는다는 뜻이다."""
    for word in ("이하", "미만", "초과", "이상"):
        if word in when:
            return word
    return None


def mispaired(answer: str, tiers: list[dict]) -> list[str]:
    """답변이 조건과 값을 잘못 짝지은 자리. 판정할 수 없으면 빈 목록.

    경계어(이하·초과…)로 갈리는 조건만 본다 — 그것이 값을 뒤집는 자리이고, 경계어가 없는
    조건은 답변에서 어느 쪽을 말하는지 문자열로 가릴 수 없다.
    """
    edged = [t for t in tiers if _edge(t.get("when") or "")]
    if len(edged) < 2:
        return []

    bad: list[str] = []
    for tier in edged:
        edge = _edge(tier["when"])
        value = (tier.get("value") or "").strip()
        others = [t for t in edged if t is not tier and (t.get("value") or "").strip() != value]
        if not value or not others:
            continue
        for token in _tokens(tier["when"]):
            start = 0
            while True:
                at = answer.find(token, start)
                if at < 0:
                    break
                start = at + len(token)
                window = answer[at:at + NEAR]
                if edge not in window:
                    continue          # 이 조건을 말한 자리가 아니다
                # 이 조건을 말하면서 **다른 조건의 값**을 붙였는가.
                for other in others:
                    other_value = (other["value"] or "").strip()
                    if other_value in window and value not in window:
                        bad.append(f"{tier['when']} → {value} (답변은 {other_value})")
                        break
    return bad


def known_wrong(answer: str, pitfalls: list[dict]) -> list[str]:
    """답변이 그대로 말한 '알려진 오답'. 행원들이 틀렸다고 적어둔 표현만 본다."""
    hit: list[str] = []
    for item in pitfalls or []:
        for wrong in item.get("wrong") or []:
            if wrong and wrong in answer and wrong not in hit:
                hit.append(wrong)
    return hit


def declared(card: dict) -> bool:
    """이 카드가 관계를 선언했는가 — 선언이 있어야 관계 검사가 원문 강제를 대신한다."""
    return bool(card.get("tiers")) or bool(
        any(p.get("wrong") for p in card.get("pitfalls") or []))


def check(answer: str, cards: list[dict]) -> list[str]:
    """관계 위반 전부. 비어 있으면 위반이 없거나 판정할 수 없다는 뜻이다."""
    out: list[str] = []
    for card in cards:
        out += mispaired(answer, card.get("tiers") or [])
        out += known_wrong(answer, card.get("pitfalls") or [])
    return out


__all__ = ["NEAR", "check", "declared", "known_wrong", "mispaired", "numbers"]
