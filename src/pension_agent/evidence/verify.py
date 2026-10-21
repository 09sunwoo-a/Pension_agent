"""LLM 산출물이 재료(원장·facts) 밖 수치·상품명을 말했는지 판정하는 공용 검증기."""

from __future__ import annotations

import re
from collections.abc import Iterable

from pension_agent.common.clock import today

# 수치 토큰. 마지막 글자는 숫자 — 뒤따르는 쉼표("D-17, 4,050")를 토큰에 넣으면 원장과 불일치
_NUM = re.compile(r"\d(?:[\d,]*\d)?(?:\.\d+)?%?")

# 상품명 후보. 강조·따옴표·괄호·`|` 가 경계 — 없으면 실재·가짜 두 이름이 한 토큰으로 붙음
# `KB` 뒤 공백 필수 — 없으면 "KB국민은행" 같은 기관명이 후보가 됨
_PROD_STOP = r"[^\s,·()\[\]{}<>*`|\"'“”‘’\n]"
_PROD = re.compile(rf"KB\s{_PROD_STOP}+(?:\s{_PROD_STOP}+){{0,3}}")

#: 상품명 대조용 정규형 — 공백·괄호 제거("KB 정기예금(1년)" = "KB 정기예금 1년").
def _prod_key(name: str) -> str:
    return re.sub(r"[\s()\[\]{}]+", "", name)

# 같은 값의 다른 표기("1,485,000원" = "148만 5천원") — 토큰이 아니라 값 덩이로 대조
_UNITS = {"십": 10, "백": 100, "천": 1_000, "만": 10_000, "억": 100_000_000}
_NUM_BARE = re.compile(r"\d(?:[\d,]*\d)?(?:\.\d+)?")

# 날짜는 연·월·일 토큰이 아니라 정규형 한 덩이로 대조 — 흩어 놓으면 오답 날짜가 집합 검사 통과
# 원장 쪽은 표기 무관하게 정규형 산출. 연도 (19|20)·달·날 범위 검사 — 대표번호·화면번호 오인 방지
_DATE_KO = re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?")
_DATE_ISO = re.compile(r"(?<![\d.,])((?:19|20)\d{2})-(\d{1,2})(?:-(\d{1,2}))?(?![\d-])")
_DATE_MD = re.compile(r"(?<!\d)(?<!\d\s)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
# 단위가 붙으면 날짜 아님("2026.5만원"). 끝 마침표("2026.06.10.")는 허용 — 행내 문서 표기
_DATE_DOT = re.compile(
    r"(?<![\d.,])((?:19|20)\d{2})\.(\d{1,2})(?:\.(\d{1,2}))?(?!\d|[십백천만억원%])")

#: 답변에서 날짜 한 덩이로 끊을 표기. 순서 = 우선순위(긴 것 먼저).
_CHUNK = (_DATE_KO, _DATE_ISO, _DATE_DOT, _DATE_MD)

#: 기간 표기(«2026.03~04» · «2026년 3~4월»). 끝점 둘이 아닌 통짜 정규형으로 대조 — 따로면 한쪽만 맞아도 통과.
_RANGE_DOT = re.compile(
    r"(?<![\d.,])((?:19|20)\d{2})\.(\d{1,2})\s*~\s*(\d{1,2})(?!\d|[십백천만억원%])")
_RANGE_KO = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월?\s*~\s*(\d{1,2})\s*월")

#: `_CHUNK` 보다 먼저 검사 — 뒤에 두면 앞 끝점만 잡히고 뒤 끝점이 맨숫자로 남음.
_RANGES = (_RANGE_DOT, _RANGE_KO)

#: 정규형 접두 — 없으면 `_canon` 의 끝자리 0 제거로 "2026.10" 이 "2026.1" 이 됨.
_DATE_TAG = "날짜:"


def _dform(year: str | None, month: str, day: str | None = None) -> str:
    """날짜 정규형. 연도 미상이면 자리 비움."""
    out = f"{_DATE_TAG}{year or '____'}-{int(month):02d}"
    return out if day is None else f"{out}-{int(day):02d}"


def _rform(year: str, first: str, last: str) -> str:
    """기간 정규형 — 끝점 둘을 한 덩이로."""
    return f"{_DATE_TAG}{year}-{int(first):02d}~{int(last):02d}"


def _ranges(text: str) -> list[tuple[re.Match, str, str, str]]:
    """기간 표기 — (매치, 연, 시작 월, 끝 월). 달 범위 밖 제외."""
    out: list[tuple[re.Match, str, str, str]] = []
    for pattern in _RANGES:
        for m in pattern.finditer(text):
            year, first, last = m.group(1), m.group(2), m.group(3)
            if _valid(year, first, None) and _valid(year, last, None):
                out.append((m, year, first, last))
    return out


def _date_parts(m: re.Match, pattern: re.Pattern) -> tuple[str | None, str, str | None]:
    """매치 → (연, 월, 일)."""
    if pattern is _DATE_MD:
        return None, m.group(1), m.group(2)
    return m.group(1), m.group(2), m.group(3)


def _valid(year: str | None, month: str, day: str | None) -> bool:
    """달·날 범위 검사 — 없으면 화면번호("04-12-640")가 날짜로 읽힘."""
    return 1 <= int(month) <= 12 and (day is None or 1 <= int(day) <= 31)


#: 연도 없는 날짜("12월 31일")를 올해로 볼 범위(±N년). 좁히면 맞는 답변이 버려짐.
BARE_YEAR_SPAN = 1


def _near_today(year: str | None, this_year: int) -> bool:
    """연도 없이 불러도 되는 날짜인가. 연도 미기재 원장 표기는 통과."""
    return year is None or abs(int(year) - this_year) <= BARE_YEAR_SPAN


def _date_forms(text: str, this_year: int) -> set[str]:
    """텍스트가 말하는 날짜 정규형 전부(연월 형태 포함). 연도 생략형은 올해 언저리만."""
    out: set[str] = set()
    # 기간은 범위형 + 끝점 둘 모두 산출(허용 쪽이라 넓혀도 무방)
    for _m, year, first, last in _ranges(text):
        out.add(_rform(year, first, last))
        out.add(_dform(year, first))
        out.add(_dform(year, last))
    for pattern in _CHUNK:
        for m in pattern.finditer(text):
            year, month, day = _date_parts(m, pattern)
            if not _valid(year, month, day):
                continue
            if year is not None:
                out.add(_dform(year, month))
            if day is not None:
                out.add(_dform(year, month, day))
                if _near_today(year, this_year):
                    out.add(_dform(None, month, day))
    return out


def _canon(tok: str) -> str:
    """수치 토큰의 값 보존 정규형 — 쉼표·소수 끝 0 제거("15.0%" = "15%"). %-유무는 보존."""
    pct = tok.endswith("%")
    t = tok.rstrip("%").replace(",", "")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t + ("%" if pct else "")


def _factor(units: str) -> int:
    """단위 문자열의 배수. 붙여 쓴 단위는 곱("천만" = 10^3 × 10^4)."""
    out = 1
    for ch in units:
        out *= _UNITS[ch]
    return out


def _plain(value: float) -> str:
    """접은 값의 문자열 — 지수 표기·불필요한 소수점 제거."""
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _chunk_dates(text: str) -> dict[int, tuple[int, list[str], set[str], str]]:
    """날짜 덩이 시작 위치 → (끝 위치, 원표기 토큰, 요구 형태, 원표기). 긴 표기 우선, 요구 형태는 답변이 말한 정밀도만."""
    out: dict[int, tuple[int, list[str], set[str], str]] = {}
    # 기간 먼저 — 요구 형태는 범위형 하나
    for m, year, first, last in _ranges(text):
        if m.start() in out:
            continue
        out[m.start()] = (m.end(), [year, first, last],
                          {_rform(year, first, last)}, m.group())
    for pattern in _CHUNK:
        for m in pattern.finditer(text):
            year, month, day = _date_parts(m, pattern)
            if not _valid(year, month, day) or m.start() in out:
                continue
            toks = [t for t in (year, month, day) if t is not None]
            out[m.start()] = (m.end(), toks, {_dform(year, month, day)}, m.group())
    return out


def _measures(text: str) -> list[tuple[list[str], set[str], str | None]]:
    """수치를 값 한 덩이씩 — (원표기 토큰, 허용 형태, 날짜면 원표기).

    덩이 조건: 앞 토큰에 단위 · 사이는 공백뿐 · 단위가 작아짐. 허용 형태 = 원표기 + 접은 값.
    """
    out: list[tuple[list[str], set[str], str | None]] = []
    # 날짜 먼저 — 흩으면 오답 날짜가 집합 검사 통과
    dates = _chunk_dates(text)
    pos = 0
    while (m := _NUM_BARE.search(text, pos)) is not None:
        if m.start() in dates:
            end, toks, forms, raw = dates[m.start()]
            out.append((toks, forms, raw))
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
        out.append((toks, forms, None))
    return out


def numbers(text: str, this_year: int | None = None) -> set[str]:
    """텍스트가 말하는 수치(정규형). 날짜는 통짜 정규형 + 흩어진 토큰 둘 다 산출."""
    out: set[str] = set()
    for toks, forms, _raw in _measures(text):
        out |= forms
        out |= {_canon(t) for t in toks}
    return out | _date_forms(text, this_year if this_year is not None else today().year)


def first_measure(text: str) -> tuple[str, set[str]] | None:
    """처음 나오는 수치 한 덩이 — (원표기, 허용 형태). 없으면 None. 뒤따르는 무관한 수치 배제용."""
    for toks, forms, date in _measures(text):
        return (date or " ".join(toks)), forms
    return None


def measures(text: str) -> list[tuple[str, set[str]]]:
    """수치 덩이 전부 — (원표기, 허용 형태), 순서대로. 화면 표시는 원표기 사용."""
    return [((date or " ".join(toks)), forms) for toks, forms, date in _measures(text)]


def first_amount(text: str) -> tuple[str, int] | None:
    """처음 나오는 금액과 그 값(원). 단위 붙은 수치만 — 맨숫자는 금액으로 보지 않음."""
    for toks, forms, date in _measures(text):
        if date is not None:
            continue
        plain = [f for f in forms if re.fullmatch(r"\d+(?:\.\d+)?", f)]
        if len(plain) < 2:
            continue                       # 단위 없음 → 금액 아님
        return " ".join(toks), int(float(max(plain, key=lambda f: float(f))))
    return None


def allowed_from_texts(texts: Iterable[str]) -> tuple[set[str], set[str]]:
    """텍스트 묶음에서 인용 가능한 수치. 상품명은 비움(호출부가 known_products 로 전달)."""
    nums: set[str] = set()
    for t in texts:
        nums.update(numbers(t))
    return nums, set()


def allowed_facts(facts: dict, extra: Iterable[str] = ()) -> tuple[set[str], set[str]]:
    """facts 에서 인용 가능한 수치·상품명 집합.

    extra: facts 스키마 밖 재료(⑨ 일정·링크 등) — 수치만 넓힘.
    상품명: 권할 상품(items[*].products) + 이미 보유한 상품(owned_products, 계좌 원장에서 읽음).
    """
    prods: set[str] = set()
    blob = [str(v) for v in facts["customer"].values()] + list(facts["conditions"]) + list(extra)
    blob += [str(v) for k, v in facts["briefing"].items() if k != "source"]
    for name in facts.get("owned_products") or ():
        prods.add(str(name).split("(")[0].strip())
    for it in facts["items"]:
        blob += [it["clause"], it["evidence"], it["amount"] or "", it["formula"], it["talk"]]
        blob += it["evidence_extra"]
        for name in it["products"].values():
            prods.add(name.split("(")[0].strip())
            blob.append(name)
    nums, _ = allowed_from_texts(blob)
    return nums, prods


def verify(
    sentence: str, facts: dict, known_products: set[str] = frozenset(),
    *, extra: Iterable[str] = (),
) -> tuple[bool, list[str]]:
    """재료 밖 수치 또는 게이트 미통과·미등록 상품명 검사. known_products 는 거부 사유 구분용."""
    nums, prods = allowed_facts(facts, extra)
    return _judge(sentence, nums, prods, known_products)


def verify_texts(
    sentence: str, texts: Iterable[str], known_products: set[str] = frozenset(),
    *, echoable: Iterable[str] = (),
) -> tuple[bool, list[str]]:
    """근거 원장(텍스트 묶음) 대조판.

    echoable: 되받아 적어도 되는 텍스트(이번 턴 질문) — 수치만 허용, 그 수치로 계산한 값·상품명은 미허용.
    상품명: 등록부 ∩ 이번 턴 원장 — 원장만 보면 지어낸 이름이 순환 통과.
    """
    nums, _ = allowed_from_texts(texts)
    for text in echoable:
        nums |= numbers(text)
    return _judge(sentence, nums, _cited(texts, known_products), known_products)


def _cited(texts: Iterable[str], known_products: set[str]) -> set[str]:
    """이번 턴 원장이 말한 등록 상품. 원장의 짧은 표 행 라벨(「KB 온국민」)은 등록부와 접두 일치 시 포함."""
    ledger = "\n".join(texts)
    blob = _prod_key(ledger)
    cited = {p for p in known_products if p and _prod_key(p) in blob}
    keys = [_prod_key(p) for p in known_products if p]
    for cand in (m.strip().rstrip(".!?。") for m in _PROD.findall(ledger)):
        key = _prod_key(cand)
        if key and any(k.startswith(key) or key.startswith(k) for k in keys):
            cited.add(cand)
    return cited


def _judge(
    sentence: str, nums: set[str], prods: set[str], known_products: set[str]
) -> tuple[bool, list[str]]:
    # 거부 사유에는 답변의 원표기 — 대조만 정규형
    bad: list[str] = []
    for toks, forms, date in _measures(sentence):
        if forms & nums:
            continue
        if date is not None:
            # 날짜는 토큰별로 물러서지 않음 — 물러서면 오답 날짜 통과
            bad.append(f"날짜 '{date}'")
            continue
        bad += [f"수치 '{t}'" for t in toks if _canon(t) not in nums]
    allowed = [_prod_key(x) for x in prods if x]
    registered = [_prod_key(x) for x in known_products if x]
    # 문장 끝 구두점은 이름에서 제외
    for m in (x.strip().rstrip(".!?。") for x in _PROD.findall(sentence)):
        key = _prod_key(m)
        if any(key.startswith(x) or x.startswith(key) for x in allowed):
            continue
        # 등록됐지만 이번 재료에 없음(게이트 미통과) vs 어디에도 없음(미등록). 접두 판정 — 조사 포함 표기
        seen = any(key.startswith(x) or x.startswith(key) for x in registered)
        bad.append(f"상품명 '{m}' ({'게이트 미통과' if seen else '미등록'})")
    return (not bad), bad
