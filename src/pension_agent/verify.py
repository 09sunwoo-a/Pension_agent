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

from pension_agent.clock import today

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
#:
#: 괄호도 지운다. `_PROD` 가 괄호를 이름의 경계로 끊으므로 LLM 은 등록명의 괄호를 풀어
#: 쓸 수밖에 없는데("KB 정기예금(1년)" → 답변은 "KB 정기예금 1년"), 공백만 지우면 두
#: 표기가 다른 키가 되어 **등록된 상품을 말한 맞는 답변이 '미등록'으로 통째로 버려진다**
#: (실 LLM 시연 대본 T10 — suitable 재료의 8종을 정확히 옮긴 답이 이걸로 폐기됐다).
def _prod_key(name: str) -> str:
    return re.sub(r"[\s()\[\]{}]+", "", name)

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


#: 연도 없이 말한 날짜를 «그 해»로 읽어 줄 범위(올해 기준 ±N년).
#:
#: 사람은 "12월 31일" 을 **올해**로 읽는다. 올해 그 날이 이미 지났으면 다음 occurrence
#: (내년)로, 지난 일을 되짚는 말이면 작년으로 읽는다 — 어느 쪽이든 **오늘 언저리**다.
#: 3년 전 납입이력의 월일을 빌려와 "12월 31일까지 납입하세요" 라고 말하는 것은 사람이
#: 하는 해석이 아니고, 실제로 그게 이 검증기가 오답을 흘리던 마지막 경로였다.
#:
#: ±1 인 이유는 좁히는 쪽이라 넉넉히 잡아야 하기 때문이다. 연도를 안 쓰는 것은 정상
#: 어법이고(지난 상담 "11월 13일에" · 만기 "2월 14일에"), 여기서 잘못 좁히면 **맞는 답변이
#: 통째로 버려진다** — 이 파일이 네 번 겪은 사고가 전부 그것이다. 2년 넘게 떨어진 날짜를
#: 연도 없이 부르는 말은 그 자체로 오해를 부르므로, 그때는 답변이 연도를 밝혀야 한다.
BARE_YEAR_SPAN = 1


def _near_today(year: str | None, this_year: int) -> bool:
    """연도 없이 불러도 되는 날짜인가. 연도를 안 적은 원장 표기는 그대로 통과시킨다."""
    return year is None or abs(int(year) - this_year) <= BARE_YEAR_SPAN


def _date_forms(text: str, this_year: int) -> set[str]:
    """텍스트가 말하는 날짜의 정규형 전부 — **굵은 것도 함께** 낸다.

    원장이 "2026-09-10" 이라 적었으면 답변이 "2026년 9월" 이라 말하는 것도 참이므로
    연월 형태를 함께 담는다. 반대 방향(원장은 연월까지만 아는데 답변이 일까지 말하는 것)은
    담지 않는다 — 그건 답변이 재료보다 많이 주장한 것이라 걸러야 한다.

    연도를 뺀 형태("9월 10일")는 **오늘 언저리의 날짜에서만** 낸다(위 BARE_YEAR_SPAN).
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
                if _near_today(year, this_year):
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


def numbers(text: str, this_year: int | None = None) -> set[str]:
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
    return out | _date_forms(text, this_year if this_year is not None else today().year)


def first_measure(text: str) -> tuple[str, set[str]] | None:
    """텍스트에 **처음 나오는** 수치 한 덩이 — (원표기, 허용 형태). 수치가 없으면 None.

    "이 항목이 말하는 값"을 가리려는 호출부(relations.labeled_mispaired)를 위해 있다.
    창 안의 수치를 전부 보면 뒤따르는 무관한 수치까지 그 항목의 값으로 오해하게 된다 —
    "잔여한도는 0만원이라 900만원 한도를 다 채우셨어요" 의 900 이 그것이다.
    """
    for toks, forms, date in _measures(text):
        return (date or " ".join(toks)), forms
    return None


def first_amount(text: str) -> tuple[str, int] | None:
    """텍스트에 처음 나오는 **금액**과 그 값(원). 금액이 없으면 None.

    «금액»은 **단위가 붙은 수치**만이다("300만원"·"1억"·"148만 5천원"). 단위 없는 맨숫자
    ("300 더 넣으면")는 금액으로 보지 않는다 — 상담 맥락에서 300원인지 300만원인지 가릴
    근거가 없고, 그 추측이 계산기의 입력이 되면 **틀린 입력이 승인된 출력**이 되기 때문이다
    (계산 결과는 원장에 실려 인용이 허가된다). 못 가리면 그냥 없는 것으로 둔다.

    단위를 접는 일은 `_measures` 가 이미 한다 — 접은 값이 원표기 토큰과 함께 형태 집합에
    들어 있으므로, 형태가 둘 이상이면 그중 가장 큰 수가 접은 값이다.
    """
    for toks, forms, date in _measures(text):
        if date is not None:
            continue                       # 날짜는 금액이 아니다
        plain = [f for f in forms if re.fullmatch(r"\d+(?:\.\d+)?", f)]
        if len(plain) < 2:
            continue                       # 단위가 없어 접히지 않은 수 → 금액으로 보지 않는다
        return " ".join(toks), int(float(max(plain, key=lambda f: float(f))))
    return None


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
    sentence: str, texts: Iterable[str], known_products: set[str] = frozenset(),
    *, echoable: Iterable[str] = (),
) -> tuple[bool, list[str]]:
    """근거 원장(텍스트 묶음) 대조판. 원장에 없는 수치·상품명이 있으면 거부한다.

    ━━ `echoable` — 되받아 말해도 되는 텍스트(질문) ━━
    원장은 **턴 단위**인데 대화는 이어진다. 직원이 "총급여 6천만원이면 얼마 돌려받아?"
    라고 물으면 답변은 그 전제를 되받아 적는데("총급여 6,000만원이면 13.2% 가 적용돼…"),
    6,000 은 원장 어디에도 없다 — 카드가 아는 경계값은 5,500 이다. 그래서 **맞는 답변이
    통째로 버려지고** 근거 원문이 덤프됐다(재현: 아래 주석의 사고들과 같은 부류이고,
    기준서 §6 이 "검증기가 옳은 문장을 거부하는 것은 틀린 문장을 통과시키는 것보다
    나쁘다"고 적어 둔 바로 그 자리다).

    직원이 방금 말한 값을 옮겨 적는 것은 지어낸 것이 아니다. 그래서 질문의 수치를
    허용 집합에 더한다. **넓히는 폭은 «되받기» 하나뿐이다** — 질문의 수치로 계산한 값
    (6,000만원의 13.2% = 792만원)은 질문에도 원장에도 없으므로 여전히 거부된다.

    상품명은 넓히지 않는다. 질문이 이름을 부르는 것만으로 인용이 허가되면, 적합성
    게이트를 통과하지 못한 상품을 직원이 이름만 대서 답변에 올릴 수 있다 — 그건
    «되받기»가 아니라 게이트를 뚫는 것이다.

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
    for text in echoable:
        nums |= numbers(text)
    blob = _prod_key("\n".join(texts))      # 상품명의 재료는 원장뿐이다(위 머리말)
    cited = {p for p in known_products if p and _prod_key(p) in blob}
    return _judge(sentence, nums, cited, known_products)


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
