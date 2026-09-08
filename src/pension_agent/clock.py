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


def stamp() -> dict[str, object]:
    """**이 산출이 어느 «오늘»로 만들어졌는가.** 출구가 산출과 함께 돌려주는 값이다.

    이 함수가 있는 이유는 위 머리말의 「읽는 곳이 둘이면 갈라진다」의 마지막 한 칸이다.
    산출물(브리핑 화면·대화 답변)에는 잔여일수·경과일이 잔뜩 실려 나가는데 **그것이 어느
    날 기준인지는 어디에도 안 실려 있었다.** 그러면 화면을 그리는 쪽이 제 시계를 읽는
    수밖에 없고, 그 순간 오늘을 읽는 곳이 둘이 된다 — 에이전트 프로세스에 `PENSION_TODAY`
    가 걸려 있으면 화면 상단 날짜와 답변 속 D-day 가 갈리고, 그 화면은 여전히 그럴듯하다.

    `pinned` 를 함께 싣는다. 날짜만 주면 「이 날짜가 맞나」를 사람이 판단해야 하는데,
    `pinned` 는 **누가 고정해 뒀다**는 사실 자체를 말한다. 실측(2026-09-07): 리허설
    러너가 `tests/__init__.py` 의 고정값으로 도는 줄 모르고 읽은 사람이 원장 값을 «지어낸
    수치»로 의심했다 — 값은 맞았고 화면에 없던 것은 «어느 오늘로 센 값인가» 하나였다.

    쓰는 곳은 산출의 **출구**다(`consult_agent.graph.ask` · `strategy_agent.agent.propose`).
    답변 재료(`facts`)에는 넣지 않는다 — 거기 넣으면 LLM 이 매번 날짜를 인용하게 되고,
    그 자리는 이미 `date` 도구가 맡고 있다(계획이 필요할 때만 부른다).
    """
    return {"today": today().isoformat(), "pinned": bool(os.environ.get(TODAY_ENV, "").strip())}
