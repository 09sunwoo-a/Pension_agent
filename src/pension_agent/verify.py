"""LLM 산출물이 재료 밖으로 나갔는지 판정하는 공용 검증기.

원래 strategy_agent/engine.py 전용이었으나, 대화형 에이전트(consult_agent)가 브리핑 질의·
브리핑 수정 등에서 LLM으로 문장을 쓰는 접점이 늘면서 "숫자·상품명은 재료 밖으로 못 나간다"는
같은 가드가 여러 곳에 필요해져 공용화했다. strategy_agent.engine.verify()는 이 함수를
PRODUCTS로 얇게 감싸 호출한다 — 기존 호출부(agent.py·test_engine.py)는 무수정.

━━ 재료를 무엇으로 보나 ━━
처음에는 재료가 브리핑 facts dict 하나뿐이어서 allowed_facts() 가 그 스키마
(customer·conditions·briefing·items)를 직접 꺼내 읽었다. 도구 루프가 들어오면서 재료가
"이번 턴에 도구들이 반환한 근거 원장"으로 넓어졌고, 그 원장은 dict 가 아니라 텍스트 묶음이다.
그래서 판정의 알맹이를 allowed_from_texts()/verify_texts() 로 내리고, 기존 두 함수는 facts
스키마를 텍스트로 펴서 그것을 부르는 얇은 껍데기로 남겼다 — 호출부는 무수정이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# 수치 토큰. 천단위 쉼표는 숫자의 일부지만(4,050) **뒤따라오는 쉼표는 아니다** — 예전
# 패턴(`\d[\d,]*`)은 "만기 D-17, 4,050만원"에서 `17,` 을 통째로 집어 원장의 `17` 과
# 어긋났고, 그래서 **맞는 답변이 '원장 밖 수치'로 버려졌다**(compose 가 근거 원문을 그대로
# 덤프하던 자리). 마지막 글자는 반드시 숫자여야 한다.
_NUM = re.compile(r"\d(?:[\d,]*\d)?(?:\.\d+)?%?")

# 상품명 후보. **경계가 판정을 뒤집는다** — 예전 패턴(`KB\s[^\s,·)]+(?:\s[^\s,·)]+)*`)은
# 쉼표·`·`·`)` 를 만날 때까지 문장을 통째로 삼켰다. 그래서
#
#   "**KB 온국민 TDF 시리즈**나 **KBSTAR 미국나스닥100**"
#     → 'KB 온국민 TDF 시리즈**나 **KBSTAR 미국나스닥100'  (한 이름으로 붙음)
#
# 처럼 **실재 상품과 지어낸 상품이 한 토큰으로 붙어**, 등록부에 있는 쪽까지 미등록으로
# 판정됐다 — 답변이 통째로 버려지고 compose 가 근거 원문을 덤프한 실제 사고다. 마크다운
# 강조·따옴표·괄호를 경계에 넣고 어절 수를 묶어, 두 이름이 따로 잡히게 한다(실재 상품은
# 통과하고 지어낸 이름만 신고된다).
#
# `KB` 뒤의 공백은 그대로 요구한다. 없애면 "KB국민은행"·"KB자산운용" 같은 기관명이
# 상품명 후보가 되고, 그건 맞는 문장을 거부하는 쪽의 사고다.
_PROD_STOP = r"[^\s,·()\[\]{}<>*`\"'“”‘’\n]"
_PROD = re.compile(rf"KB\s{_PROD_STOP}+(?:\s{_PROD_STOP}+){{0,3}}")

#: 상품명 대조용 정규형. **표기의 공백이 판정을 뒤집으면 안 된다** — 지식베이스는
#: "KB 온국민 TDF 시리즈"와 "KB온국민적격TDF2035(H)" 두 표기를 다 쓰고, LLM 은 그 사이
#: 어디로도 쓴다. 뒤에 붙는 조사("…C-P의")는 접두 대조가 알아서 흡수한다.
def _prod_key(name: str) -> str:
    return re.sub(r"\s+", "", name)

# 같은 값의 다른 표기 — 단위로 끊어 쓴 금액과 풀어 쓴 연월.
#
# 원장은 "1,485,000원"·"148.5만원"·"2026.06" 이라 적고, LLM 은 직원이 실제로 말하는 대로
# "148만 5천원"·"2026년 6월" 이라 쓴다. 토큰 집합으로만 대조하던 동안 그 답변은 원장 밖
# 수치(148 · 5 · 118 · 8 · 6)를 말한 것으로 판정돼 **통째로 버려졌고**, compose 는 답변
# 대신 근거 원문을 덤프했다 — 세액공제 한도를 물었더니 카드 원문이 문어체로 떨어진
# 실제 사고다(재현: `tests/debug --script korean_units`).
#
# 위 _canon 주석의 두 사고(15.0% vs 15% · 뒤따르는 쉼표)와 **같은 부류**다. 표기가 판정을
# 뒤집으면 안 된다. 다만 정규형 한 겹으로는 안 되는데, 여기서는 값 하나가 토큰 여럿으로
# 쪼개지기 때문이다("148만 5천" = 토큰 둘, 값 하나). 그래서 토큰이 아니라 **덩이**로 읽는다.
_UNITS = {"십": 10, "백": 100, "천": 1_000, "만": 10_000, "억": 100_000_000}
_NUM_BARE = re.compile(r"\d(?:[\d,]*\d)?(?:\.\d+)?")
_DATE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월")


def _canon(tok: str) -> str:
    """수치 토큰의 값 보존 정규형 — **같은 값은 같은 토큰**이 되게 한다.

    원장은 원본 xlsx 의 소수(0.15)를 백분율로 펴며 "15.0%" 로 적고, LLM 은 자연스럽게
    "15%" 라 쓴다. 이 둘을 문자열로 비교하던 동안 **맞는 답변이 '원장 밖 수치'로
    버려졌고**, compose 는 답변 대신 근거 원문을 통째로 덤프했다 — 직원이 "예금 비중
    몇 프로야?" 라고 물었는데 화면에 브리핑 재료 표가 떨어진 실제 사고다. 뒤따르는 쉼표
    버그(아래 _NUM 주석)와 같은 부류로, 표기가 판정을 뒤집으면 안 된다.

    값이 바뀌는 변형은 하지 않는다: 천단위 쉼표 제거(4,050→4050)와 소수 끝자리 0 제거
    (15.0→15 · 10.20→10.2)뿐이고, %-유무는 다른 주장이므로 보존한다(15% ≠ 15).
    """
    pct = tok.endswith("%")
    t = tok.rstrip("%").replace(",", "")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t + ("%" if pct else "")


def _factor(units: str) -> int:
    """단위 문자열의 배수. 붙여 쓴 단위는 곱한다 — "천만" = 10^3 × 10^4."""
    out = 1
    for ch in units:
        out *= _UNITS[ch]
    return out


def _plain(value: float) -> str:
    """접은 값의 문자열. 지수 표기와 불필요한 소수점을 남기지 않는다."""
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _measures(text: str) -> list[tuple[list[str], set[str]]]:
    """텍스트의 수치를 **값 한 덩이씩** 끊어 돌려준다 — (원표기 토큰들, 허용 형태들).

    "148만 5천원" 은 토큰이 둘이지만 값은 하나다. 덩이로 묶는 조건은 셋이고, 셋 다
    **다른 수치를 우연히 합치지 않기 위한 것**이다:
      · 앞 토큰에 단위가 붙어 있을 것 ("148만" 다음이라야 "5천" 이 끝수다)
      · 사이가 공백뿐일 것 ("16.5%라 148만" 처럼 글자가 끼면 다른 수치다)
      · 단위가 작아질 것 (만 → 천 → 끝수. 커지거나 같으면 새 수치다)

    허용 형태에는 **원표기 토큰과 접은 값을 함께** 담는다. 접은 값만 남기면 원장이
    "900만원" 이라 적고 답변이 "900" 이라 쓴 경우처럼 지금 통과하던 것이 막힌다 —
    이 변경은 판정을 넓히기만 하고 좁히지 않는다.
    """
    out: list[tuple[list[str], set[str]]] = []
    # 연월 먼저. "2026년 6월" 은 원장의 "2026.06" 과 같은 값이지만 토큰으로는 2026 과 6 이다.
    dates = {m.start(): (m.end(), f"{m.group(1)}.{int(m.group(2)):02d}",
                         [m.group(1), m.group(2)])
             for m in _DATE.finditer(text)}
    pos = 0
    while (m := _NUM_BARE.search(text, pos)) is not None:
        if m.start() in dates:
            end, form, raw = dates[m.start()]
            out.append((raw, {form, *(_canon(t) for t in raw)}))
            pos = end
            continue

        toks: list[str] = []
        value, last, folded = 0.0, None, False
        cur = m
        while True:
            tok, j = cur.group(), cur.end()
            k = j
            while k < len(text) and text[k] in _UNITS:
                k += 1
            units = text[j:k]
            factor = _factor(units) if units else 1
            if last is not None and factor >= last:
                break                       # 단위가 안 작아지면 다른 수치다
            if not units and text[j:j + 1] == "%":
                tok, k = tok + "%", j + 1   # %- 유무는 다른 주장이라 토큰에 남긴다
            toks.append(tok)
            value += float(tok.rstrip("%").replace(",", "")) * factor
            folded = folded or bool(units)
            last = factor
            pos = k
            if not units:
                break                       # 단위 없는 끝수에서 덩이가 끝난다
            nxt = _NUM_BARE.search(text, k)
            if nxt is None or text[k:nxt.start()].strip():
                break
            cur = nxt

        forms = {_canon(t) for t in toks}
        if folded:
            forms.add(_canon(_plain(value)))
        out.append((toks, forms))
    return out


def numbers(text: str) -> set[str]:
    """텍스트가 말하는 수치(정규형). 스팬 안에 값이 있는지 판정할 때 호출부가 쓴다
    (compose 가 '값을 말했는데 원문 스팬을 안 실었다'를 잡는 데 필요하다).

    같은 값의 다른 표기를 함께 담는다 — "148만 5천원" 은 `148`·`5` 와 `1485000` 을 다 낸다.
    """
    return {form for _toks, forms in _measures(text) for form in forms}


def allowed_from_texts(texts: Iterable[str]) -> tuple[set[str], set[str]]:
    """텍스트 묶음에서 인용 가능한 숫자를 걷는다. 상품명은 텍스트만으로는 판별할 수 없어
    비워 둔다 — 상품 목록을 아는 호출부가 known_products 로 넘긴다.

    도구 루프의 근거 원장이 이 형태다(도구가 만든 근거 블록들). 원장 밖 숫자가 답변에
    있으면 그 도구가 내놓지 않은 값이므로 지어낸 것이다.
    """
    nums: set[str] = set()
    for t in texts:
        nums.update(numbers(t))
    return nums, set()


def allowed_facts(facts: dict) -> tuple[set[str], set[str]]:
    """LLM 이 인용할 수 있는 숫자·상품명 집합. 이 범위를 벗어난 표현은 환각으로 판정한다."""
    prods: set[str] = set()
    blob = [str(v) for v in facts["customer"].values()] + list(facts["conditions"])
    blob += [str(v) for k, v in facts["briefing"].items() if k != "source"]
    for it in facts["items"]:
        blob += [it["clause"], it["evidence"], it["amount"] or "", it["formula"], it["talk"]]
        blob += it["evidence_extra"]
        for name in it["products"].values():
            prods.add(name.split("(")[0].strip())
            blob.append(name)
    nums, _ = allowed_from_texts(blob)
    return nums, prods


def verify(
    sentence: str, facts: dict, known_products: set[str] = frozenset()
) -> tuple[bool, list[str]]:
    """재료에 없는 수치 또는 게이트 미통과·미등록 상품명이 포함되었는지 검사한다.

    known_products 는 "게이트 미통과"(재료엔 없지만 시스템엔 등록된 상품)와 "미등록"(시스템에도
    없는 상품)을 구분해 거부 사유를 더 정확히 남기기 위한 선택 인자다. 호출부가 넘기지 않으면
    모두 "미등록"으로 보고한다.
    """
    nums, prods = allowed_facts(facts)
    return _judge(sentence, nums, prods, known_products)


def verify_texts(
    sentence: str, texts: Iterable[str], known_products: set[str] = frozenset()
) -> tuple[bool, list[str]]:
    """근거 원장(텍스트 묶음) 대조판. 원장에 없는 수치·상품명이 있으면 거부한다.

    상품명은 **등록부 ∩ 이번 턴 원장**이다. 두 겹인 이유가 각각 있다.

    · 등록부(`known_products`) — 실재하는 상품인가. 원장 텍스트에서 "KB ○○" 패턴을 긁어
      허용 집합을 만들면, LLM 이 지어낸 이름이 답변과 원장에 함께 실려 있을 때 서로를
      근거로 통과한다. 그래서 이름의 상한은 **닫힌 목록**이 쥔다.
    · 원장 — 이번 답의 재료였는가. 등록부만 보면 이번 턴에 부르지도 않은 문서의 상품을
      끌어다 말할 수 있다. 등록부가 데모 12종이던 동안은 이 구멍이 좁았지만, 지식베이스가
      선언한 이름까지 합쳐 80여 종이 되면서 넓어졌다.

    닫힌 목록이 바깥에 있으므로 원장을 함께 보는 것이 위 순환을 되살리지 않는다 — 지어낸
    이름은 등록부에 없어 어느 쪽이든 막힌다.

    대조는 공백을 무시한다. 원장은 줄을 바꿔 적고 LLM 은 붙여 쓰는데, 그 차이로 맞는
    답변이 막히면 안 된다(`_prod_key` 머리말).
    """
    nums, _ = allowed_from_texts(texts)
    blob = _prod_key("\n".join(texts))
    cited = {p for p in known_products if p and _prod_key(p) in blob}
    return _judge(sentence, nums, cited, known_products)


def _judge(
    sentence: str, nums: set[str], prods: set[str], known_products: set[str]
) -> tuple[bool, list[str]]:
    # 덩이로 본다 — "148만 5천원" 은 접은 값이 원장에 있으면 통과다. 접은 값이 없으면
    # 토큰별로 따진다. 거부 사유에는 **답변의 원표기**를 남긴다 — 정규형이나 접은 값으로
    # 적으면 답변 어디를 말하는지 사람이 못 찾는다. 대조만 정규형으로 한다.
    bad: list[str] = []
    for toks, forms in _measures(sentence):
        if forms & nums:
            continue
        bad += [f"수치 '{t}'" for t in toks if _canon(t) not in nums]
    allowed = [_prod_key(x) for x in prods if x]
    registered = [_prod_key(x) for x in known_products if x]
    # 문장 끝 구두점은 이름의 일부가 아니다 — 거부 사유가 "'KB 무지개 펀드를 권합니다.'"
    # 처럼 문장째로 찍히면 사람이 답변 어디를 봐야 할지 모른다.
    for m in (x.strip().rstrip(".!?。") for x in _PROD.findall(sentence)):
        key = _prod_key(m)
        if any(key.startswith(x) or x.startswith(key) for x in allowed):
            continue
        # 시스템에 등록은 됐지만 이번 재료에 없는 것("게이트 미통과")과 어디에도 없는 것
        # ("미등록")은 대응이 다르다. 접두로 판정한다 — 조사가 붙은 표기까지 같이 본다.
        seen = any(key.startswith(x) or x.startswith(key) for x in registered)
        bad.append(f"상품명 '{m}' ({'게이트 미통과' if seen else '미등록'})")
    return (not bad), bad
