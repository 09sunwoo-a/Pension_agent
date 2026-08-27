"""관계 기반 답변 점검 — 값과 조건을 잘못 짝지었는가, 알려진 오답을 말했는가 (§6).

`verify_texts()` 는 수치의 **집합 포함** 검사다. 원장에 있는 숫자를 답변이 썼는지만 보므로
**잘못 짝지은 것을 못 잡는다** — "총급여 5,500만원 이하 16.5%, 초과 13.2%" 가 원장에 있으면
"초과면 16.5%" 도 통과한다(두 숫자가 다 원장에 있으므로). 그 구멍을 막으려고 지금까지는
원문 문장을 통째로 답변에 싣게 강제했고(`atomic`), 그래서 답변이 인용문 나열이 됐다.

여기서는 **데이터가 선언한 관계**로 본다(`knowledge/CLAUDE.md` §1·§2).

  tiers      조건–값 쌍. 답변이 어떤 조건을 말하면서 **다른 조건의 값**을 붙였는지 본다.
  pitfalls   행원들이 적어둔 "자주 틀리는 지점" 중 틀린 표현을 인용한 것. 답변에 그 표현이
             그대로 있으면 알려진 오답을 말한 것이다.
  pairs      항목–값 짝. 답변이 어떤 항목을 말하면서 **다른 항목의 값**을 붙였는지 본다.

━━ tiers 와 pairs 는 왜 갈리나 ━━
조건이 갈리는 방식이 다르다. `tiers` 는 **구간**이라 경계어(이하·초과·미만·이상)가 어느
쪽을 말하는지 가려준다 — 제도 수치가 그렇다. `pairs` 는 **범주**다. "이 만기는 예금 것,
저건 GIC 것"에는 경계어가 없어서 `mispaired()` 의 판정 축이 통째로 성립하지 않는다.

그래서 pairs 는 **거리**로 가른다: 값이 등장한 자리에서 가장 가까운 항목 이름을 찾고,
그 항목이 그 값의 임자가 아니면 잘못 붙인 것이다. 사람이 값을 읽는 방식과 같다.

pairs 는 저작물이 아니다 — 고객 원장처럼 관계가 **이미 구조로 있는** 재료가 스스로
내보낸다(`strategy_agent/engine/render.py::briefing_pairs`). 카드가 없어 사람이 선언할
데가 없는 재료를 이 검사에 태우는 통로다.

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

import re

from pension_agent.verify import numbers

#: 조건과 값이 "같은 자리에서" 만났다고 볼 거리(글자 수). 한 문장 안에서 값과 조건이
#: 붙어 나오는 것을 보려는 것이라, 문단 전체로 넓히면 서로 다른 문장의 조건과 값이
#: 우연히 짝지어져 오탐이 난다.
NEAR = 60

#: 판정 단위. 항목–값 짝은 **한 문장 안에서만** 본다 — 문단으로 넓히면 앞 문장의 이름이
#: 뒤 문장의 값을 데려가 오짝으로 읽힌다("고유계정대가 많습니다. 예금은 4,050만원입니다").
#:
#: 숫자 사이의 마침표는 문장 끝이 아니다. 이 예외가 없으면 "수익률 31.7%" 가 "…31" 과
#: "7%" 두 문장으로 갈려 값이 통째로 사라진다 — 판정이 조용히 비어버리는 자리다.
_SENTENCE = re.compile(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+")


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


# ─────────────────────────────────────────────────────────────
# 항목–값 짝 (pairs) — 범주형. 값을 엉뚱한 항목에 붙였는가
# ─────────────────────────────────────────────────────────────

def _at(text: str, needle: str) -> list[tuple[int, int]]:
    """`needle` 이 등장한 자리 전부. 숫자로 시작·끝나는 값은 **경계**를 함께 본다.

    값은 짧은 문자열이라 다른 수치 안에 우연히 박힌다 — "3%" 는 "23%" 안에 있고
    "120만원" 은 "3,120만원" 안에 있다. 그 자리를 등장으로 세면 답변이 말하지도 않은
    값을 말했다고 판정하게 된다(검증기가 옳은 문장을 거부하는 것이 가장 나쁘다).
    """
    edge = "0123456789,."
    out: list[tuple[int, int]] = []
    at = 0
    while True:
        i = text.find(needle, at)
        if i < 0:
            return out
        at = i + 1
        if needle[0].isdigit() and i > 0 and text[i - 1] in edge:
            continue
        end = i + len(needle)
        if needle[-1].isdigit() and end < len(text) and text[end] in edge:
            continue
        out.append((i, end))


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """두 자리 사이의 거리. 겹치면 0."""
    return max(0, max(a[0], b[0]) - min(a[1], b[1]))


def _owners(pairs: list[dict]) -> dict[str, set[str]]:
    """항목 이름 → 그 항목이 가진 값 전부. **종류(kind)를 넘어 합친다.**

    같은 이름이 축을 넘나들기 때문이다 — "예금"은 자산군별의 한 칸이면서 만기도래의 한
    건이고, "고유계정대"는 자산군별의 칸이면서 보유상품의 한 종목이다. 축마다 따로 보면
    한 축의 값을 다른 축의 이름이 가장 가까운 이름으로 잡혀 오탐이 난다("고유계정대
    600만원, 예금 120만원" 을 보유상품 축이 판정하면 120만원의 임자가 고유계정대가 된다).
    이름이 값을 가지느냐만 물으면 축을 나눌 이유가 없다.
    """
    owned: dict[str, set[str]] = {}
    for row in pairs:
        label = (row.get("label") or "").strip()
        if not label:
            continue
        owned.setdefault(label, set()).update(
            v.strip() for v in row.get("values") or [] if v and v.strip())
    return owned


def miscategorized(answer: str, pairs: list[dict]) -> list[str]:
    """답변이 값을 엉뚱한 항목에 붙인 자리. 판정할 수 없으면 빈 목록.

    한 문장 안에서 본다. 값이 나온 자리에서 가장 가까운 항목 이름을 찾고, **그 이름이 그
    값의 임자가 아닌데 자기 값은 하나도 데리고 있지 않으면** 잘못 붙인 것이다
    ("GIC 4,050만원이 D-221에 만기" — 둘 다 예금 것이고 GIC 값은 문장에 없다).

    「자기 값을 데리고 있지 않을 때만」이 판정의 핵심이다. 나열문은 값이 자기 이름 **뒤**에
    오므로("수익증권 7,900만원(37.6%) · ETF 5,530만원(26.3%)") 각 값에 가장 가까운 이름이
    자꾸 **다음** 항목이 된다 — 거리만 보면 전부 오짝으로 읽힌다. 그런 자리의 이름은 자기
    값을 옆에 끼고 있다는 것이 나열과 오짝을 가른다. 오짝은 이름이 **맨몸으로** 서서 남의
    값만 데리고 있는 모양이다.

    판정하지 않는 경우를 넓게 둔다(§6 판정 불가를 위반으로 바꾸지 않는다):
    · 값 근처에 항목 이름이 없다 — 어느 항목 얘기인지 답변이 말하지 않았다.
    · 가장 가까운 이름이 둘 이상으로 갈린다.
    · 그 이름이 자기 값도 함께 말했다 — 나열이거나, 한 항목을 옳게 말하며 곁들인 값이다.
      후자는 못 잡는 자리로 남는다("예금은 4,050만원이고 비중 37.6%"). 나열문을 통째로
      오답 처리하는 것보다 이쪽이 낫다.
    · 이름이 다른 이름 안에 박혀 있다 — "정기예금 1년" 안의 "예금"은 그 상품을 가리키는
      것이지 자산군 «예금»을 부른 것이 아니다. 긴 이름이 이긴다.
    """
    owned = _owners(pairs)
    if len(owned) < 2:
        return []

    bad: list[str] = []
    for sentence in _SENTENCE.findall(answer):
        # 이름이 실제로 불린 자리. 다른 이름 안에 박힌 것은 뺀다.
        spots = [(span, label) for label, _v in owned.items() for span in _at(sentence, label)]
        spots = [(span, label) for span, label in spots
                 if not any(o is not span and o[0] <= span[0] and span[1] <= o[1]
                            for o, _l in spots)]
        if not spots:
            continue
        said = {label for _span, label in spots}
        # 이 문장에서 «자기 값을 데리고 있는» 이름 — 나열문의 이름들이 여기 들어온다.
        anchored = {label for label in said
                    if any(_at(sentence, v) for v in owned[label])}
        for label, values in owned.items():
            for value in values:
                for vspan in _at(sentence, value):
                    near = sorted((_gap(vspan, span), lbl) for span, lbl in spots)
                    if near[0][0] > NEAR:
                        continue
                    if len(near) > 1 and near[0][0] == near[1][0] and near[0][1] != near[1][1]:
                        continue
                    nearest = near[0][1]
                    if value in owned[nearest] or nearest in anchored:
                        continue
                    line = f"{value} 는 「{label}」의 값인데 답변은 「{nearest}」에 붙였다"
                    if line not in bad:
                        bad.append(line)
    return bad


def declared(card: dict) -> bool:
    """이 카드가 관계를 선언했는가 — 선언이 있어야 관계 검사가 원문 강제를 대신한다."""
    return bool(card.get("tiers")) or bool(card.get("pairs")) or bool(
        any(p.get("wrong") for p in card.get("pitfalls") or []))


def check(answer: str, cards: list[dict]) -> list[str]:
    """관계 위반 전부. 비어 있으면 위반이 없거나 판정할 수 없다는 뜻이다."""
    out: list[str] = []
    for card in cards:
        out += mispaired(answer, card.get("tiers") or [])
        out += miscategorized(answer, card.get("pairs") or [])
        out += known_wrong(answer, card.get("pitfalls") or [])
    return out


__all__ = ["NEAR", "check", "declared", "known_wrong", "mispaired",
           "miscategorized", "numbers"]
