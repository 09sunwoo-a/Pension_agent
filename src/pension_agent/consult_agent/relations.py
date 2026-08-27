"""관계 기반 답변 점검 — 값과 조건을 잘못 짝지었는가, 알려진 오답을 말했는가 (§6).

`verify_texts()` 는 수치의 **집합 포함** 검사다. 원장에 있는 숫자를 답변이 썼는지만 보므로
**잘못 짝지은 것을 못 잡는다** — "총급여 5,500만원 이하 16.5%, 초과 13.2%" 가 원장에 있으면
"초과면 16.5%" 도 통과한다(두 숫자가 다 원장에 있으므로). 그 구멍을 막으려고 지금까지는
원문 문장을 통째로 답변에 싣게 강제했고(`atomic`), 그래서 답변이 인용문 나열이 됐다.

여기서는 **데이터가 선언한 관계**로 본다(`knowledge/CLAUDE.md` §1·§2).

  tiers      조건–값 쌍. 답변이 어떤 조건을 말하면서 **다른 조건의 값**을 붙였는지 본다.
  pitfalls   행원들이 적어둔 "자주 틀리는 지점" 중 틀린 표현을 인용한 것. 답변에 그 표현이
             그대로 있으면 알려진 오답을 말한 것이다.
  pairs      항목–값 짝. 답변이 어떤 항목을 말하면서 **다른 항목의 값**을 붙였는지 보고
             (miscategorized), 이름 없이 인용한 값에는 임자를 답변에 덧붙인다(unattributed).

━━ tiers 와 pairs 는 왜 갈리나 ━━
조건이 갈리는 방식이 다르다. `tiers` 는 **구간**이라 경계어(이하·초과·미만·이상)가 어느
쪽을 말하는지 가려준다 — 제도 수치가 그렇다. `pairs` 는 **범주**다. "이 만기는 예금 것,
저건 GIC 것"에는 경계어가 없어서 `mispaired()` 의 판정 축이 통째로 성립하지 않는다.

그래서 pairs 는 **임자**로 가른다. 거리로 가르지 않는다 — 한국어 나열문은 값이 자기 이름
뒤에 와서, 거리만 보면 각 값의 최근접 이름이 자꾸 다음 항목이 된다. 판정 순서:
  1. 값 곁에 그 값의 임자가 있으면 통과 — 어디에 붙는 값인지 답변이 옳게 말했다.
  2. 임자 없이 다른 항목 이름만 곁에 있으면, 그 이름이 **같은 유형의 자기 값**을 이미
     말했는지 본다. 말했으면 비교·나열의 생략("피델리티 31.7%로 최고, 다음은 22.9%")이라
     판정하지 않고, 안 말했으면 그 값을 제 것처럼 쓴 것이다 → 위반.
  3. 곁에 아무 이름도 없으면 위반이 아니다 — 대신 임자를 답변에 덧붙인다(unattributed).
     직원이 "만기 자금 3,020만원"만 보고 어느 상품 것인지 모른 채 안내하면 안 되기
     때문이다. 지우는 게 아니라 채우는 이유는 §6 과 같다 — 문장 자체는 틀리지 않았다.

pairs 는 저작물이 아니다 — 고객 원장처럼 관계가 **이미 구조로 있는** 재료가 스스로
내보낸다(`strategy_agent/engine/render.py::briefing_pairs`). 카드가 없어 사람이 선언할
데가 없는 재료를 이 검사에 태우는 통로다. 항목은 {kind, label, values:[{v, t}]} 다.

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
# 항목–값 짝 (pairs) — 범주형. 값을 엉뚱한 항목에 붙였는가 · 임자 없이 썼는가
# ─────────────────────────────────────────────────────────────

#: 값 유형 → 표시어. 위반 메시지와 임자 표시가 함께 쓴다.
_TYPE_WORD = {"date": "만기일", "dday": "잔여일", "amount": "금액",
              "share": "비중", "ret": "수익률", "rate": "금리"}

#: 임자 판정에 못 쓰는 이름 조각 — 여러 상품명에 두루 들어가는 낱말이라, 이걸 임자의
#: 등장으로 치면 "퇴직연금 계좌 전체는…" 같은 일반 문장이 특정 상품을 부른 것이 된다.
_GENERIC = {"퇴직연금", "정기예금", "저축은행", "디폴트옵션", "시리즈",
            "ETF", "TDF", "주식", "채권", "혼합", "재간접", "1년", "3년"}


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


def _alts(v: str) -> set[str]:
    """같은 값의 다른 표기. 원장은 "4,050만원"·"75.0%" 로 적고 답변은 "4050만원"·"75%" 로
    쓴다 — 표기가 어긋나면 임자를 못 찾아 판정이 조용히 비어버린다. 값이 같은 표기끼리만
    묶는다(verify._canon 과 같은 원칙: 값 보존)."""
    out = {v}
    if "," in v:
        out.add(v.replace(",", ""))
    for x in list(out):
        if x.endswith("%") and "." in x:
            try:
                f = float(x[:-1])
            except ValueError:
                continue
            if f.is_integer():
                out.add(f"{int(f)}%")
    return out


def _index(pairs: list[dict]):
    """짝 목록의 색인 셋 — 값→임자들, 값→유형들, 항목→(유형→자기 값들).

    같은 이름은 종류(kind)를 넘어 합친다 — "예금"은 자산군별의 칸이면서 만기도래의 한
    건이고, 같은 자산이 보유상품·자산군별·만기도래 세 축에 다 실리기도 한다(GIC 와
    이율보증형보험). 이름이 그 값을 가지느냐만 물으면 축을 나눌 이유가 없다.
    """
    owners: dict[str, set[str]] = {}
    types: dict[str, set[str]] = {}
    mine: dict[str, dict[str, set[str]]] = {}
    for row in pairs:
        label = (row.get("label") or "").strip()
        if not label:
            continue
        mine.setdefault(label, {})
        for cell in row.get("values") or []:
            v, t = (cell.get("v") or "").strip(), cell.get("t") or ""
            if not v:
                continue
            for alt in _alts(v):
                owners.setdefault(alt, set()).add(label)
                types.setdefault(alt, set()).add(t)
                mine[label].setdefault(t, set()).add(alt)
    return owners, types, mine


def _label_tokens(labels: list[str]) -> dict[str, list[str]]:
    """임자의 등장으로 인정할 이름 조각. 직원과 LLM 은 상품명을 줄여 부른다 —
    "피델리티 글로벌 테크놀로지 (주식-재간접)" 를 "피델리티"라고. 전체 이름만 찾으면
    임자를 말한 옳은 문장이 임자 없음으로 읽혀 엉뚱한 항목에 뒤집어씌운다.

    단 **그 고객 안에서 한 상품만 가리키는 조각**이어야 한다 — "RISE" 는 RISE 상품이
    둘인 고객에게서 어느 쪽도 특정하지 못하므로 빼고(뒤바꿔도 통과하게 된다), 흔한
    낱말(_GENERIC)도 뺀다. 임자 **판정을 관대하게** 하는 쪽에만 쓴다 — 위반을 씌우는
    쪽은 전체 이름으로만 한다(줄인 이름으로 뒤집어씌우면 오탐이 임자를 잃는 것보다
    나쁘다).
    """
    split = {L: [t for t in re.split(r"[\s·()]+", L) if len(t) >= 2] for L in labels}
    count: dict[str, int] = {}
    for L, ts in split.items():
        for t in set(ts):
            count[t] = count.get(t, 0) + 1
    return {L: [t for t in ts if count[t] == 1 and t not in _GENERIC and t != L]
            for L, ts in split.items()}


def _near_owner(sentence: str, span: tuple[int, int], owns: set[str],
                tokens: dict[str, list[str]]) -> bool:
    """이 값의 곁(NEAR 안)에 임자가 있는가 — 전체 이름 또는 그 고객 안에서 유일한 조각."""
    for label in owns:
        for probe in (label, *tokens.get(label, [])):
            if any(_gap(span, s) <= NEAR for s in _at(sentence, probe)):
                return True
    return False


def _spots(sentence: str, labels) -> list[tuple[tuple[int, int], str]]:
    """이름이 실제로 불린 자리. 다른 이름 안에 박힌 것은 뺀다 — "정기예금 1년" 안의
    "예금"은 그 상품을 가리키는 것이지 자산군 «예금»을 부른 것이 아니다. 긴 이름이 이긴다."""
    raw = [(span, label) for label in labels for span in _at(sentence, label)]
    return [(span, label) for span, label in raw
            if not any(o is not span and o[0] <= span[0] and span[1] <= o[1] for o, _l in raw)]


def miscategorized(answer: str, pairs: list[dict]) -> list[str]:
    """답변이 값을 엉뚱한 항목에 붙인 자리. 판정할 수 없으면 빈 목록.

    한 문장 안에서, 값마다 이렇게 본다(모듈 머리말의 판정 순서).
      1. 곁에 임자가 있다 → 통과. 나열문·비교문이 전부 여기서 통과한다 — 값이 자기
         이름과 함께 있는 문장은 옳게 붙인 문장이다.
      2. 곁에 임자가 없고 다른 이름이 있다 → 그 이름이 **같은 유형의 자기 값**을 이 문장에서
         말했는지 본다. 말했으면 비교의 생략이라 판정하지 않고(자기 수익률을 말한 항목
         곁의 남의 수익률은 "다음은 22.9%" 같은 문장이다), 안 말했으면 위반이다 —
         "예금은 4,050만원이고 비중은 37.6%" 는 금액은 자기 것이지만 비중은 남의 것을
         제 것처럼 쓴 문장이고, 예금이 자기 비중을 말한 적이 없다는 것이 그 증거다.
      3. 곁에 아무 이름도 없다 → 위반이 아니다. 임자 표시는 unattributed() 가 채운다.

    가장 가까운 이름부터 보고, 최근접이 둘로 갈리면(동거리·다른 이름) 판정하지 않는다.
    """
    owners, types, mine = _index(pairs)
    if len(mine) < 2:
        return []
    tokens = _label_tokens(list(mine))

    bad: list[str] = []
    for sentence in _SENTENCE.findall(answer):
        spots = _spots(sentence, mine)
        if not spots:
            continue
        for v, owns in owners.items():
            for vspan in _at(sentence, v):
                if _near_owner(sentence, vspan, owns, tokens):
                    continue                       # 1. 임자 곁에 있다
                cands = sorted((_gap(vspan, span), label) for span, label in spots)
                cands = [(g, label) for g, label in cands if g <= NEAR]
                if not cands:
                    continue                       # 3. 이름 없이 말한 값 — unattributed 몫
                if len(cands) > 1 and cands[0][0] == cands[1][0] and cands[0][1] != cands[1][1]:
                    continue                       # 최근접이 갈린다 — 씌울 수 없다
                verdict = None
                for _g, label in cands:
                    if label in owns:
                        break                      # 임자다(조각이 아니라 전체 이름으로 확인)
                    same_type = any(v2 for t in types[v] for v2 in mine[label].get(t, ())
                                    if _at(sentence, v2))
                    if same_type:
                        continue                   # 2. 자기 값을 이미 말한 이름 — 비교의 생략
                    verdict = label
                    break
                if verdict:
                    what = "·".join(sorted(_TYPE_WORD.get(t, t) for t in types[v]))
                    line = (f"{v} 는 「{'/'.join(sorted(owns))}」의 {what}인데 "
                            f"답변은 「{verdict}」에 붙였다")
                    if line not in bad:
                        bad.append(line)
    return bad


#: kind → 그 축을 말하고 있다는 신호어. 임자 표시(unattributed)는 이 신호가 있는 문장에만
#: 붙는다 — "연 납입한도 1,800만원"의 1,800만원이 우연히 어느 예금의 만기 금액과 같을 때,
#: 만기 얘기가 아닌 문장에 "1,800만원 — 예금 만기" 를 붙이면 표시가 거짓말을 한다.
_KIND_CUE = {"만기도래": ("만기", "D-"), "보유상품": ("수익률", "금리", "보유"),
             "자산군별": ("비중", "자산군")}


def unattributed(answer: str, pairs: list[dict]) -> list[str]:
    """임자 없이 인용된 짝 값 — 답변에 덧붙일 임자 표시. 없으면 빈 목록.

    "만기 자금 3,020만원을 재예치 안내해보세요" 는 틀린 문장이 아니라 **덜 갖춰진**
    문장이다 — 만기가 두 건인 고객에서 직원이 어느 상품 것인지 모른 채 안내하게 된다.
    §6 의 표시 누락과 같은 사건이므로 지우지 않고 채운다.

    임자가 답변 어딘가에 있으면(앞 문장 포함) 붙이지 않는다 — "예금 만기가 다가옵니다.
    금액은 4,050만원입니다" 는 이미 임자를 말한 답변이다.
    """
    owners, _types, mine = _index(pairs)
    tokens = _label_tokens(list(mine))
    named = {label for label in mine
             if any(_at(answer, probe) for probe in (label, *tokens.get(label, [])))}
    out: list[str] = []
    seen: set[str] = set()
    for sentence in _SENTENCE.findall(answer):
        for row in pairs:
            label = (row.get("label") or "").strip()
            cues = _KIND_CUE.get(row.get("kind") or "", ())
            if not label or label in named or not any(c in sentence for c in cues):
                continue
            for cell in row.get("values") or []:
                hit = next((alt for alt in _alts((cell.get("v") or "").strip())
                            if alt and _at(sentence, alt)), None)
                if not hit or hit in seen or (owners.get(hit, set()) & named):
                    continue                       # 같은 값의 다른 임자를 이미 말했으면 그쪽이다
                seen.add(hit)
                detail = " · ".join(c["v"] for c in row["values"] if c.get("v"))
                out.append(f"· {hit} — {row['kind']} {label} ({detail})")
    return out


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
           "miscategorized", "numbers", "unattributed"]
