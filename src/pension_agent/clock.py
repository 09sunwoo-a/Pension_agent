"""«오늘»의 단일 출처.

경로가 `config.py` 하나여야 하는 것과 같은 이유다(CLAUDE.md 4번 규칙) — 오늘을 읽는 곳이
둘이면 한쪽만 고정된 채 갈라지고, 그때 화면은 여전히 그럴듯해 보인다. 실제로 이 저장소는
원장 기준일 하나로 오늘까지 세다가 "연말까지 129일"(실제 126일)·"만기 D-17"(실제 D-14)을
말했다.

여기 있는 것은 «지금 몇 시인가»뿐이다. **원장 스냅샷이 언제 찍혔는가**(`customer.AS_OF`)는
시연 데이터에 딸린 값이라 그 데이터를 아는 모듈이 갖는다.

쓰는 곳이 셋이라 공용으로 올렸다 — strategy_agent(잔여일수·경과일) · consult_agent(`date`
도구) · verify(연도 없이 말한 날짜를 어느 해로 읽을지). verify 는 두 에이전트가 함께 쓰는
아래층이라 strategy_agent 를 임포트할 수 없다.
"""

from __future__ import annotations

import os
from datetime import date

#: 오늘을 고정하는 환경변수. 형식이 틀리면 조용히 실제 날짜로 넘어가지 않고 즉시 실패한다 —
#: 오타 하나로 «고정한 줄 알았는데 안 고정된» 산출물이 나오는 것이 가장 나쁜 실패다.
TODAY_ENV = "PENSION_TODAY"


def today() -> date:
    """오늘. `PENSION_TODAY=YYYY-MM-DD` 가 있으면 그 날짜로 고정한다.

    고정 스위치가 있는 이유는 재현성이다. 테스트는 `tests/__init__.py` 가 원장 기준일로
    고정한 채 돌고(안 그러면 600건 넘는 단언이 실행일마다 흔들린다), 데모를 특정 날짜로
    얼려 보고 싶을 때도 같은 스위치를 쓴다.
    """
    pinned = os.environ.get(TODAY_ENV, "").strip()
    if not pinned:
        return date.today()
    try:
        return date.fromisoformat(pinned)
    except ValueError as exc:
        raise ValueError(f"{TODAY_ENV}={pinned!r} 는 YYYY-MM-DD 가 아닙니다") from exc
