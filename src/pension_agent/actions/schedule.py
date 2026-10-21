"""예약 쪽지 발송 시각을 직원의 말에서 코드로 파싱 — 발송일 단서가 붙은 날짜만 인정.

단서 없으면 즉시 발송(`none`), 후보 둘 이상·지난 시각이면 미선택(호출부가 되물음).
LLM 추출 금지 — 요일·월말 오산 시 조용히 다른 날로 예약됨.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

#: 시각 미지정 시 발송 시각.
DEFAULT_TIME = time(9, 0)

#: 연도 없는 날짜가 이 기간 안에서 지났으면 «지난 날짜», 그보다 전이면 내년으로 해석
PAST_WINDOW_DAYS = 60

_WEEKDAYS = "월화수목금토일"

_DATE_ABS = re.compile(
    r"(?<!\d)(?:(?P<y>20\d{2})\s*(?:년|[.\-/])\s*)?(?P<m>1[0-2]|0?[1-9])\s*(?:월|[./])\s*"
    r"(?P<d>3[01]|[12]\d|0?[1-9])(?:\s*일)?(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)(?P<y>20\d{2})-(?P<m>\d{2})-(?P<d>\d{2})(?!\d)")
_DATE_REL = re.compile(r"오늘|내일|모레|글피")
_DATE_AFTER = re.compile(r"(?<!\d)(?P<n>\d{1,2})\s*일\s*(?:후|뒤)")
#: 월 없는 일(「6일에」) — 단서가 붙으면 `many` 처리. 무시 시 예약이 즉시 발송으로 바뀜
_DAY_ONLY = re.compile(r"(?<![\d월/.])(?P<d>3[01]|[12]\d|0?[1-9])\s*일(?!\s*(?:후|뒤))")
_DATE_WEEK = re.compile(r"(?:(?P<w>이번|다음|담)\s*주\s*)?(?P<wd>[월화수목금토일])요일")
_TIME = re.compile(
    r"(?P<mer>오전|오후|아침|저녁|낮|밤)?\s*(?P<h>2[0-3]|1\d|0?\d)\s*시"
    r"(?:\s*(?P<half>반)|\s*(?P<mi>[0-5]?\d)\s*분)?"
    r"|(?<!\d)(?P<h2>2[0-3]|[01]?\d):(?P<m2>[0-5]\d)(?!\d)")

_CUE_AFTER = re.compile(
    r"^\s*(?:에도|에는|에|까지|쯤에?|경에?|날짜로|자로|으로|로)?\s*(?:맞춰서?\s*)?"
    r"(?:보내|발송|전송|쪽지|예약|전달)")
#: 「…10월 7일이야, 그날 쪽지 보내줘」 꼴.
_CUE_THAT_DAY = re.compile(
    r"^[^\n]{0,12}?그\s*날(?:에|로)?\s*[^\n.!?]{0,12}?(?:보내|발송|전송|쪽지|예약|받게|받도록)")
#: 날짜와 «보내» 사이에 받는 사람(사람 조사로 끝나는 짧은 말)이 낀 꼴. 날짜 뒤 «에» 생략 가능
_CUE_VIA = re.compile(
    r"^\s*(?:에|에는)?\s+(?P<mid>[^\n.,!?]{1,25}?(?:한테|에게|께|앞으로))\s*"
    r"(?:쪽지로?\s*|메모로?\s*)?(?:보내|발송|전송|전달|예약)")
#: 사이 말에 있으면 날짜 수식어(「만기되는 고객에게」) — 발송일 아님
_VIA_NOT = re.compile(r"만기|도래|예정|해지|가입|개시|되는|하는|있는|인\s|까지")
#: 직원이 잡은 약속 날짜(재접촉·상담·방문 등 + 예정·약속). 「만기·도래 예정」은 내용이라 제외
_CUE_APPT = re.compile(
    r"^\s*(?:에|에는)?\s*(?P<mid>[^\n.,!?]{0,20}?)"
    r"(?:재접촉|재상담|상담|방문|내방|내점|연락|통화|미팅|면담)\s*(?:이|을|를)?\s*"
    r"(?:예정|약속|일정|잡혀|잡았|하기로|드리기로)")
_APPT_NOT = re.compile(r"만기|도래|해지|가입|개시")
#: 약속 날짜는 뒤에 보내라는 말이 있을 때만 발송일 — 없으면 본문 수정 지시
_SEND_LATER = re.compile(r"보내|발송|전송|예약|받게|받도록|남겨")
_CUE_EDIT = re.compile(r"^\s*(?:으로|로)\s*(?:바꿔|변경|옮겨|미뤄|당겨|해\s*줘|해줘|예약|[.!?]*\s*$)")
_CUE_BEFORE = re.compile(r"(?:예약|발송일|발송\s*시각|발송\s*시간|보낼\s*날짜)\s*(?:은|는|을|를|:|：)?\s*$")

#: 예약 해제 → 즉시 발송(초안이 걸린 턴에서만).
_CLEAR = ("지금 보내", "지금 바로", "바로 보내", "즉시", "당장", "예약 말고", "예약 없이",
          "예약하지 말", "예약 취소", "예약은 취소")


@dataclass(frozen=True)
class When:
    """발송 시각 판정. kind: none · ok · many · past · clear."""

    kind: str
    at: datetime | None = None

    @property
    def label(self) -> str:
        return label(self.at) if self.at else ""


def label(at: datetime) -> str:
    """화면 표기 — 「10월 5일(월) 오전 9시」."""
    hour = at.hour % 12 or 12
    mer = "오전" if at.hour < 12 else "오후"
    minute = f" {at.minute}분" if at.minute else ""
    return f"{at.month}월 {at.day}일({_WEEKDAYS[at.weekday()]}) {mer} {hour}시{minute}"


def parse(text: str, now: datetime, *, edit: bool = False) -> When:
    """직원의 말 → 발송 시각. 단서 없으면 `When("none")`."""
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
    appt = _CUE_APPT.match(after)
    return bool(_CUE_AFTER.match(after) or _CUE_BEFORE.search(text[:start])
                or _CUE_THAT_DAY.match(after)
                or (via and not _VIA_NOT.search(via.group("mid")))
                or (appt and not _APPT_NOT.search(appt.group("mid"))
                    and _SEND_LATER.search(after[appt.end():]))
                or (edit and _CUE_EDIT.match(after)))


def _expressions(text: str, now: datetime) -> list[tuple[int, int, date | None, time | None]]:
    """(시작, 끝, 날짜, 시각) 목록. 날짜+시각은 하나로 병합, 시각만이면 날짜 None, 무효 날짜 제외."""
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
    ahead = (target - today.weekday()) % 7 or 7        # 요일만이면 다가오는 그 요일(오늘 제외)
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
        # 오전·오후 없는 1~6시는 오후 — 제안 문장에 표시되어 승낙 전 확인 가능
        hour += 12
    return time(hour % 24, minute)
