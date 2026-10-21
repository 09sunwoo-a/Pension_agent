"""«오늘»의 단일 출처. 원장 기준일(`customer.AS_OF`)은 미포함 — 데이터 모듈 소유.

strategy_agent(잔여일수·경과일) · consult_agent(`date` 도구) · verify(연도 없는 날짜 해석)가 사용.
"""

from __future__ import annotations

import os
from datetime import date, datetime

#: 오늘 고정용 환경변수. 형식 오류 시 실제 날짜로 넘어가지 않고 즉시 실패.
TODAY_ENV = "PENSION_TODAY"


def today() -> date:
    """오늘. `PENSION_TODAY=YYYY-MM-DD` 있으면 그 날짜(테스트·데모 재현용)."""
    pinned = os.environ.get(TODAY_ENV, "").strip()
    if not pinned:
        return date.today()
    try:
        return date.fromisoformat(pinned)
    except ValueError as exc:
        raise ValueError(f"{TODAY_ENV}={pinned!r} 는 YYYY-MM-DD 가 아닙니다") from exc


def now() -> datetime:
    """지금 시각. 날짜는 `today()`(고정 포함), 시각은 실제 시계. 예약 쪽지 판정용."""
    return datetime.combine(today(), datetime.now().time().replace(microsecond=0))
