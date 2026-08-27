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
_PROD = re.compile(r"KB\s[^\s,·)]+(?:\s[^\s,·)]+)*")

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

# ━━ 날짜는 숫자가 아니라 **한 값**이다 ━━
#
# 이 검증기는 수치의 *집합 포함*을 본다. 그래서 날짜를 연·월·일 토큰으로 흩어 놓으면
# 원장 어딘가에 2026 과 11 과 10 이 있다는 이유로 **"만기는 2026년 11월 10일"(오답)이
# 통과한다** — 원장의 실제 만기는 2026-09-10 인데도. 토큰이 다 재료 안에 있으니 집합
# 검사로는 잡을 수 없고, 날짜야말로 «틀리면 고객이 헛걸음하는» 수치다.
#
# 그래서 날짜는 **통째로 하나의 정규형**으로 대조한다. 규칙은 둘이다.
#
#   ① 답변이 날짜 꼴로 말했으면 그 날짜의 정규형이 원장에 있어야 한다(연·월·일 토큰이
#      따로 있는 것으로는 안 된다).
#   ② 원장 쪽은 **표기를 가리지 않고** 정규형을 낸다 — 원장이 "2026-09-10" 이라 적고
#      답변이 "2026년 9월 10일" 이라 쓰는 것이 정상이기 때문이다. 표기가 판정을 뒤집으면
#      맞는 답변이 버려진다(이 파일이 이미 세 번 겪은 사고 — _canon·_NUM·_measures 주석).
#
# 연도는 (19|20)만 받고 달·날의 범위도 본다. 은행 문서에는 1588-1234·1599-0000 같은
# 대표번호가 흔하고 화면번호는 04-12-640 꼴이라, 열어 두면 그것들이 날짜로 읽혀 **답변의
# 전화번호·화면번호가 통째로 거부된다**. 좁히는 쪽에 애매한 것을 넣지 않는다.
_DATE_KO = re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?")
_DATE_ISO = re.compile(r"(?<![\d.,])((?:19|20)\d{2})-(\d{1,2})(?:-(\d{1,2}))?(?![\d-])")
_DATE_MD = re.compile(r"(?<!\d)(?<!\d\s)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
# 뒤에 단위가 붙으면 날짜가 아니라 금액·비율이다("2026.5만원" · "2026.5%"). 이 배제가
# 없으면 그 값이 날짜로 끊겨 **접은 값(20,265,000)이 허용 집합에서 사라지고**, 원장이
# 그렇게 적힌 답변이 통째로 거부된다 — 좁히려다 반대쪽을 잃는 자리다.
#
# 끝의 마침표는 막지 않는다. 행내 문서는 날짜를 "2026.06.10." 처럼 **점으로 닫아** 쓰고
# (준법감시인 심의필 유효기간이 전부 그 꼴이다), 그 점까지 배제하면 매치가 통째로 실패해
# 그 날짜가 허용 집합에서 사라진다 — 원장에 있는 날짜를 말한 답변이 거부된다.
# 숫자만 배제하면 된다: 더 긴 수(2026.123)는 그것으로 걸러지고, "2026.06.10" 은 일(日)
# 그룹이 먼저 붙으므로 연월만 떼어 읽히지 않는다.
_DATE_DOT = re.compile(
    r"(?<![\d.,])((?:19|20)\d{2})\.(\d{1,2})(?:\.(\d{1,2}))?(?!\d|[십백천만억원%])")

#: 답변에서 «날짜 한 덩이»로 끊을 표기. 순서가 곧 우선순위다(긴 것 먼저 — _chunk_dates).
#:
#: 점 표기(2026.03)도 넣는다. 넣지 않으면 그 표기는 흩어진 토큰 "2026.03" + "31" 로 읽혀,
#: 원장이 "2026-03-31" 이라 적었을 때 **맞는 답변이 거부된다** — 표기가 판정을 뒤집는
#: 이 파일의 네 번째 사고다(_canon·_NUM·_measures 주석의 셋과 같은 부류. 원장 날짜
#: 127종을 여섯 표기로 바꿔 대조하니 105건이 그렇게 걸렸다).
_CHUNK = (_DATE_KO, _DATE_ISO, _DATE_DOT, _DATE_MD)

#: 정규형의 이름공간. 숫자 토큰과 절대 겹치지 않게 접두를 붙인다 — 접두가 없으면
#: `_canon` 의 소수 끝자리 0 제거가 날짜에 닿아 "2026.10"(10월)이 "2026.1"(1월)이 된다.
_DATE_TAG = "날짜:"


def _dform(year: str | None, month: str, day: str | None = None) -> str:
    """날짜 정규형. 연도를 모르면(예: "12월 31일") 자리를 비워 둔다."""
    out = f"{_DATE_TAG}{year or '____'}-{int(month):02d}"
    return out if day is None else f"{out}-{int(day):02d}"


def _date_parts(m: re.Match, pattern: re.Pattern) -> tuple[str | None, str, str | None]:
    """정규식 매치 → (연, 월, 일). 패턴마다 그룹 배치가 달라 여기서 한 꼴로 맞춘다."""
    if pattern is _DATE_MD:
        return None, m.group(1), m.group(2)
    return m.group(1), m.group(2), m.group(3)


def _valid(year: str | None, month: str, day: str | None) -> bool:
    """달·날의 범위. 범위를 안 보면 화면번호("04-12-640")·대표번호가 날짜로 읽힌다."""
    return 1 <= int(month) <= 12 and (day is None or 1 <= int(day) <= 31)


def _date_forms(text: str) -> set[str]:
    """텍스트가 말하는 날짜의 정규형 전부 — **굵은 것도 함께** 낸다.

    원장이 "2026-09-10" 이라 적었으면 답변이 "2026년 9월" 이라 말하는 것도 참이므로
    연월 형태를 함께 담는다. 연도를 뺀 형태("9월 10일")도 마찬가지다. 반대 방향
    (원장은 연월까지만 아는데 답변이 일까지 말하는 것)은 담지 않는다 — 그건 답변이
    재료보다 많이 주장한 것이라 걸러야 한다.

    이 함수는 **허용 집합을 넓히기만 한다** — 여기서 무엇을 더 읽어도 답변이 더 거부되지는
    않는다. 좁히는 것은 답변 쪽 덩이 하나뿐이다(_chunk_dates · _judge).
    """
    out: set[str] = set()
    for pattern in _CHUNK:
        for m in pattern.finditer(text):
            year, month, day = _date_parts(m, pattern)
            if not _valid(year, month, day):
                continue
            if year is not None:
                out.add(_dform(year, month))
            if day is not None:
                out.add(_dform(year, month, day))
                out.add(_dform(None, month, day))
    return out


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


def _chunk_dates(text: str) -> dict[int, tuple[int, list[str], set[str], str]]:
    """날짜 덩이의 시작 위치 → (끝 위치, 원표기 토큰들, 요구 형태, 원표기 전체).

    긴 표기가 이긴다. "2026년 12월 31일" 안에는 "12월 31일" 도 들어 있는데, 짧은 쪽을
    집으면 연도가 덩이 밖으로 떨어져 나가 다시 흩어진 토큰이 된다.

    **요구 형태는 그 답변이 실제로 주장한 만큼만** 담는다 — 일까지 말했으면 일까지 맞아야
    하고, 연월까지만 말했으면 연월만 맞으면 된다. 굵은 형태를 함께 담아 주면 "9월 10일"
    이라는 오답이 "9월" 이 맞다는 이유로 통과한다.
    """
    out: dict[int, tuple[int, list[str], set[str], str]] = {}
    for pattern in _CHUNK:
        for m in pattern.finditer(text):
            year, month, day = _date_parts(m, pattern)
            if not _valid(year, month, day) or m.start() in out:
                continue
            toks = [t for t in (year, month, day) if t is not None]
            out[m.start()] = (m.end(), toks, {_dform(year, month, day)}, m.group())
    return out


def _measures(text: str) -> list[tuple[list[str], set[str], str | None]]:
    """텍스트의 수치를 **값 한 덩이씩** 끊어 돌려준다 — (원표기 토큰들, 허용 형태들,
    날짜면 그 원표기).

    "148만 5천원" 은 토큰이 둘이지만 값은 하나다. 덩이로 묶는 조건은 셋이고, 셋 다
    **다른 수치를 우연히 합치지 않기 위한 것**이다:
      · 앞 토큰에 단위가 붙어 있을 것 ("148만" 다음이라야 "5천" 이 끝수다)
      · 사이가 공백뿐일 것 ("16.5%라 148만" 처럼 글자가 끼면 다른 수치다)
      · 단위가 작아질 것 (만 → 천 → 끝수. 커지거나 같으면 새 수치다)

    허용 형태에는 **원표기 토큰과 접은 값을 함께** 담는다. 접은 값만 남기면 원장이
    "900만원" 이라 적고 답변이 "900" 이라 쓴 경우처럼 지금 통과하던 것이 막힌다 —
    이 변경은 판정을 넓히기만 하고 좁히지 않는다.
    """
    out: list[tuple[list[str], set[str], str | None]] = []
    # 날짜 먼저. 흩어 놓으면 집합 검사가 오답 날짜를 통과시킨다(위 _DATE_KO 주석).
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


def numbers(text: str) -> set[str]:
    """텍스트가 말하는 수치(정규형). 스팬 안에 값이 있는지 판정할 때 호출부가 쓴다
    (compose 가 '값을 말했는데 원문 스팬을 안 실었다'를 잡는 데 필요하다).

    같은 값의 다른 표기를 함께 담는다 — "148만 5천원" 은 `148`·`5` 와 `1485000` 을 다 낸다.

    날짜는 **양쪽**을 낸다 — 통짜 정규형(대조용)과 흩어진 연·월·일 토큰이다. 뒤엣것을
    빼면 "2026년 6월" 만 실린 원장에서 답변이 "6월분" 이라 말하는 것까지 막힌다. 좁히는
    것은 답변의 날짜 덩이 하나이고(_judge), 허용 집합은 넓힌 채로 둔다.
    """
    out: set[str] = set()
    for toks, forms, _raw in _measures(text):
        out |= forms
        out |= {_canon(t) for t in toks}
    return out | _date_forms(text)


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

    상품명은 원장 텍스트에서 뽑지 않고 known_products 만 허용한다 — 텍스트에서 "KB ○○"
    패턴을 긁어 허용 집합에 넣으면, LLM 이 지어낸 상품명이 답변과 원장에 함께 실려 있을 때
    서로를 근거로 통과해버린다.
    """
    nums, _ = allowed_from_texts(texts)
    return _judge(sentence, nums, known_products, known_products)


def _judge(
    sentence: str, nums: set[str], prods: set[str], known_products: set[str]
) -> tuple[bool, list[str]]:
    # 덩이로 본다 — "148만 5천원" 은 접은 값이 원장에 있으면 통과다. 접은 값이 없으면
    # 토큰별로 따진다. 거부 사유에는 **답변의 원표기**를 남긴다 — 정규형이나 접은 값으로
    # 적으면 답변 어디를 말하는지 사람이 못 찾는다. 대조만 정규형으로 한다.
    bad: list[str] = []
    for toks, forms, date in _measures(sentence):
        if forms & nums:
            continue
        if date is not None:
            # 날짜는 토큰별로 물러서지 않는다. 물러서면 «원장 어딘가에 2026 과 11 이
            # 있다»는 이유로 오답 날짜가 그대로 통과한다 — 이 덩이를 만든 이유가 그것이다.
            # 거부 사유에도 날짜를 통째로 남긴다(어느 숫자가 틀렸는지가 아니라 그 날짜가
            # 재료에 없다는 것이 사람이 확인할 사실이다).
            bad.append(f"날짜 '{date}'")
            continue
        bad += [f"수치 '{t}'" for t in toks if _canon(t) not in nums]
    for m in (x.strip() for x in _PROD.findall(sentence)):
        if any(m.startswith(x) or x.startswith(m) for x in prods):
            continue
        bad.append(f"상품명 '{m}' ({'게이트 미통과' if m in known_products else '미등록'})")
    return (not bad), bad
