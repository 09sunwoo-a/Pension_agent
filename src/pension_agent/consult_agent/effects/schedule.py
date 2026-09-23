"""예약 쪽지의 발송 시각 — 직원의 말에서 **코드가** 읽는다(§10 「예약 발송」).

「10월 5일에 쪽지 보내줘」의 10월 5일은 발송일이고, 「10월 5일 만기 고객 정리해서 쪽지
보내줘」의 10월 5일은 쪽지 **내용**이다. 둘을 잘못 가르면 즉시 보낼 쪽지가 예약으로 바뀌거나
(제때 도착하지 않는다), 예약한 쪽지가 즉시 나간다(되돌릴 수 없다). 수신자 사번을 읽는
규칙(`nodes/act.employee_no`)과 같은 원칙으로 푼다 — **발송일이라는 단서가 붙은 날짜만 읽는다.**

    단서(뒤)  날짜·시각 바로 뒤의 「에 보내·에 발송·에 쪽지·에 예약·에 맞춰 보내·까지 보내」
    단서(앞)  날짜·시각 바로 앞의 「예약·발송일·발송 시각」
    단서(사이) 「날짜(에) + 받는 사람(한테·에게·께) + 보내」 — 사이 말에 「만기·예정·되는」 같은
             날짜를 꾸미는 말이 있으면 단서가 아니다
    고칠 때   초안이 걸린 턴(`edit=True`)에서는 「로 바꿔·로 변경·로 미뤄」도 단서다

읽지 못하면 «발송일 없음»이고 그 쪽지는 지금처럼 즉시 발송이다. 후보가 둘 이상이거나 이미
지난 시각이면 코드가 고르지 않는다 — 부르는 쪽이 언제 보낼지 되묻는다.

LLM 이 날짜를 뽑게 하지 않는 이유는 루트 규칙 2 다 — 발송 시각은 받는 사람과 같은 **실행
인자**이고, LLM 이 요일·월말을 잘못 세면 조용히 다른 날로 예약된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

#: 시각을 말하지 않았을 때의 발송 시각 — 영업 시작 무렵.
DEFAULT_TIME = time(9, 0)

#: 연도 없이 말한 날짜가 올해 기준으로 이만큼 안쪽에서 지났으면 «지난 날짜»로 본다(되묻는다).
#: 그보다 전이면 내년으로 읽는다 — 9월에 「1월 5일에 보내줘」는 내년 1월 5일이다.
PAST_WINDOW_DAYS = 60

_WEEKDAYS = "월화수목금토일"

_DATE_ABS = re.compile(
    r"(?<!\d)(?:(?P<y>20\d{2})\s*(?:년|[.\-/])\s*)?(?P<m>1[0-2]|0?[1-9])\s*(?:월|[./])\s*"
    r"(?P<d>3[01]|[12]\d|0?[1-9])(?:\s*일)?(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)(?P<y>20\d{2})-(?P<m>\d{2})-(?P<d>\d{2})(?!\d)")
_DATE_REL = re.compile(r"오늘|내일|모레|글피")
_DATE_AFTER = re.compile(r"(?<!\d)(?P<n>\d{1,2})\s*일\s*(?:후|뒤)")
#: 월 없이 일만 말한 날(「6일에 보내줘」). 날짜로 읽지 않지만, 발송일 단서가 붙어 있으면
#: «언제인지 못 읽었다»로 친다 — 조용히 무시하면 예약하려던 쪽지가 즉시 발송 초안이 된다.
_DAY_ONLY = re.compile(r"(?<![\d월/.])(?P<d>3[01]|[12]\d|0?[1-9])\s*일(?!\s*(?:후|뒤))")
_DATE_WEEK = re.compile(r"(?:(?P<w>이번|다음|담)\s*주\s*)?(?P<wd>[월화수목금토일])요일")
_TIME = re.compile(
    r"(?P<mer>오전|오후|아침|저녁|낮|밤)?\s*(?P<h>2[0-3]|1\d|0?\d)\s*시"
    r"(?:\s*(?P<half>반)|\s*(?P<mi>[0-5]?\d)\s*분)?"
    r"|(?<!\d)(?P<h2>2[0-3]|[01]?\d):(?P<m2>[0-5]\d)(?!\d)")

_CUE_AFTER = re.compile(
    r"^\s*(?:에도|에는|에|까지|쯤에?|경에?)?\s*(?:맞춰서?\s*)?"
    r"(?:보내|발송|전송|쪽지|예약|전달)")
#: 날짜와 «보내» 사이에 받는 사람이 끼어 있는 꼴 — 「10월 7일에 정석희 대리에게 쪽지로 보내줘」.
#: 처음에는 날짜 바로 뒤만 봐서 이 말이 **즉시 발송**이 됐다(2026-09-23 행내 실측). 사이에
#: 들어올 수 있는 것은 사람 조사로 끝나는 짧은 말 하나뿐이다. 날짜 뒤의 «에»는 빠지기도 한다 —
#: 「10월 7일 정석희 대리에게 쪽지로 보내줄래」도 즉시 발송이 됐다(같은 날 두 번째 실측).
_CUE_VIA = re.compile(
    r"^\s*(?:에|에는)?\s+(?P<mid>[^\n.,!?]{1,25}?(?:한테|에게|께|앞으로))\s*"
    r"(?:쪽지로?\s*|메모로?\s*)?(?:보내|발송|전송|전달|예약)")
#: 그 사이 말에 이것이 있으면 받는 사람이 아니라 날짜를 꾸미는 말이다 — 「10월 5일에 만기되는
#: 고객에게」의 10월 5일은 만기일(쪽지 내용)이다.
_VIA_NOT = re.compile(r"만기|도래|예정|해지|가입|개시|되는|하는|있는|인\s|까지")
_CUE_EDIT = re.compile(r"^\s*(?:으로|로)\s*(?:바꿔|변경|옮겨|미뤄|당겨|해\s*줘|해줘|예약)")
_CUE_BEFORE = re.compile(r"(?:예약|발송일|발송\s*시각|발송\s*시간|보낼\s*날짜)\s*(?:은|는|을|를|:|：)?\s*$")

#: 예약을 지우고 즉시 발송으로 되돌리는 말(초안이 걸린 턴에서만 읽는다).
_CLEAR = ("지금 보내", "지금 바로", "바로 보내", "즉시", "당장", "예약 말고", "예약 없이",
          "예약하지 말", "예약 취소", "예약은 취소")


@dataclass(frozen=True)
class When:
    """발송 시각 판정 하나.

    kind  none(발송일 단서 없음) · ok(읽었다) · many(후보가 둘 이상) · past(이미 지났다) ·
          clear(예약을 지우라는 말)
    """

    kind: str
    at: datetime | None = None

    @property
    def label(self) -> str:
        return label(self.at) if self.at else ""


def label(at: datetime) -> str:
    """화면에 세우는 표기 — 「10월 5일(월) 오전 9시」. 분이 있으면 「오후 2시 30분」."""
    hour = at.hour % 12 or 12
    mer = "오전" if at.hour < 12 else "오후"
    minute = f" {at.minute}분" if at.minute else ""
    return f"{at.month}월 {at.day}일({_WEEKDAYS[at.weekday()]}) {mer} {hour}시{minute}"


def parse(text: str, now: datetime, *, edit: bool = False) -> When:
    """직원의 말에서 발송 시각을 읽는다. 단서가 없으면 `When("none")`."""
    raw = text or ""
    if edit and any(w in raw for w in _CLEAR):
        return When("clear")
    found: list[datetime] = []
    past = False
    for start, end, day, clock_at in _expressions(raw, now):
        if not _cued(raw, start, end, edit):
            continue
        if day is None:
            day = now.date()
        at = datetime.combine(day, clock_at or DEFAULT_TIME)
        if at <= now:
            past = True
            continue
        if at not in found:
            found.append(at)
    loose = any(_cued(raw, m.start(), m.end(), edit) for m in _DAY_ONLY.finditer(raw)
                if not _inside(m, _expressions(raw, now)))
    if len(found) > 1 or (found and (past or loose)) or (loose and not found):
        return When("many")
    if found:
        return When("ok", found[0])
    return When("past") if past else When("none")


def _inside(m: re.Match, spans: list[tuple[int, int, date | None, time | None]]) -> bool:
    return any(s <= m.start() and m.end() <= e for s, e, _d, _t in spans)


def _cued(text: str, start: int, end: int, edit: bool) -> bool:
    after = text[end:]
    via = _CUE_VIA.match(after)
    return bool(_CUE_AFTER.match(after) or _CUE_BEFORE.search(text[:start])
                or (via and not _VIA_NOT.search(via.group("mid")))
                or (edit and _CUE_EDIT.match(after)))


def _expressions(text: str, now: datetime) -> list[tuple[int, int, date | None, time | None]]:
    """(시작, 끝, 날짜, 시각) — 날짜 바로 뒤에 시각이 붙으면 한 표현으로 합친다.

    날짜 없이 시각만 말하면(「오후 3시에 보내줘」) 날짜는 None 이고 오늘이다.
    읽을 수 없는 날짜(2월 30일)는 표현으로 치지 않는다.
    """
    dates: list[tuple[int, int, date]] = []
    taken: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return all(e <= a or s >= b for a, b in taken)

    for rx in (_DATE_ISO, _DATE_ABS):
        for m in rx.finditer(text):
            if not free(m.start(), m.end()):
                continue
            day = _absolute(m, now.date())
            taken.append((m.start(), m.end()))
            if day is not None:
                dates.append((m.start(), m.end(), day))
    for m in _DATE_REL.finditer(text):
        offset = {"오늘": 0, "내일": 1, "모레": 2, "글피": 3}[m.group()]
        dates.append((m.start(), m.end(), now.date() + timedelta(days=offset)))
    for m in _DATE_AFTER.finditer(text):
        if free(m.start(), m.end()):
            dates.append((m.start(), m.end(), now.date() + timedelta(days=int(m.group("n")))))
    for m in _DATE_WEEK.finditer(text):
        dates.append((m.start(), m.end(), _weekday(m, now.date())))

    times = [(m.start(), m.end(), _clock(m)) for m in _TIME.finditer(text)
             if free(m.start(), m.end())]
    out: list[tuple[int, int, date | None, time | None]] = []
    used: set[int] = set()
    for s, e, day in sorted(dates):
        joined = next((i for i, (ts, _te, _t) in enumerate(times)
                       if i not in used and ts >= e and not text[e:ts].strip()), None)
        if joined is not None:
            used.add(joined)
            out.append((s, times[joined][1], day, times[joined][2]))
        else:
            out.append((s, e, day, None))
    out += [(s, e, None, t) for i, (s, e, t) in enumerate(times) if i not in used]
    return out


def _absolute(m: re.Match, today: date) -> date | None:
    month, day = int(m.group("m")), int(m.group("d"))
    year = int(m.group("y")) if m.group("y") else today.year
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    if m.group("y") is None and found < today - timedelta(days=PAST_WINDOW_DAYS):
        try:
            found = date(year + 1, month, day)
        except ValueError:
            return None
    return found


def _weekday(m: re.Match, today: date) -> date:
    target = _WEEKDAYS.index(m.group("wd"))
    which = m.group("w")
    if which in ("다음", "담"):
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return monday + timedelta(days=target)
    if which == "이번":
        return today - timedelta(days=today.weekday()) + timedelta(days=target)
    ahead = (target - today.weekday()) % 7 or 7        # 요일만 말하면 다가오는 그 요일(오늘 제외)
    return today + timedelta(days=ahead)


def _clock(m: re.Match) -> time:
    if m.group("h2") is not None:
        return time(int(m.group("h2")), int(m.group("m2")))
    hour = int(m.group("h"))
    minute = 30 if m.group("half") else int(m.group("mi") or 0)
    mer = m.group("mer")
    if mer in ("오후", "저녁", "밤") and hour < 12:
        hour += 12
    elif mer is None and 1 <= hour <= 6:
        # 오전·오후 없이 「3시」는 영업시간의 오후 3시다. 새벽에 쪽지를 예약하는 일은 드물고,
        # 틀려도 제안 문장에 「오후 3시」로 서므로 직원이 승낙 전에 본다.
        hour += 12
    return time(hour % 24, minute)
