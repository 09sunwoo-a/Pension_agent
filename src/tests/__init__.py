"""회귀 테스트 패키지.

**오늘을 고정한 채 돈다.** `pension_agent.strategy_agent.customer.today()` 는 기본이 실제
날짜라, 고정하지 않으면 만기 잔여일수·미접촉 일수·연말까지 남은 일수가 실행일마다 달라지고
600건 넘는 단언이 «어제는 통과했는데 오늘은 실패»로 흔들린다. 고정값은 원장 스냅샷 기준일
(`customer.AS_OF`)과 같은 날로 둔다 — 시연 데이터가 그날 찍힌 값이므로 그 위에서 세운
기존 단언(만기 D-17, 미접촉 88일 …)이 그대로 성립한다.

두 값이 갈리면 `tests/test_infra.py` 가 잡는다. 날짜 산술 자체는 특정 날짜를 명시해
검증한다(같은 파일 「오늘·기준일」 절) — 고정해 둔 하루로만 보면 세는 법의 오류를 못 잡는다.
"""

from __future__ import annotations

import os

#: customer.AS_OF 와 같은 날. 여기서 import 해 오지 않는 이유는 이 대입이 그 모듈보다
#: **먼저** 일어나야 하기 때문이다(customer 는 임포트 시점에 PERSONAS 를 만든다).
PINNED_TODAY = "2026-08-24"

TODAY_ENV = "PENSION_TODAY"     # pension_agent.clock.TODAY_ENV — 위와 같은 이유로 복사한다

#: **이 고정을 채운 게 우리인가.** 밖에서 준 값(특정 날짜로 얼려 보려는 사람·`app.py`)은
#: 존중하므로, 되돌려도 되는 것은 우리가 채운 경우뿐이다. `unpin_today()` 가 이것을 본다.
PINNED_BY_TESTS = not os.environ.get(TODAY_ENV, "").strip()

if PINNED_BY_TESTS:
    os.environ[TODAY_ENV] = PINNED_TODAY


def unpin_today() -> None:
    """이 패키지가 채운 «오늘» 고정을 되돌린다 — **리허설·디버그 CLI 전용**이다.

    고정은 **회귀 테스트의 요건**이지 이 패키지를 지나는 모든 실행의 요건이 아니다.
    그런데 리허설 러너(`tests.debug.reps`)와 디버그 CLI(`tests.debug`)가 이 패키지 안에
    살아서, 임포트만으로 위 고정을 물려받았다 — 실제 날짜로 도는 줄 알고 돌린 사람이
    원장 기준일 기준의 경과일을 지어낸 수치로 의심한 실측이 있다(2026-09-07).
    리허설은 «상담 시점의 오늘»을 보는 자리이므로 실제 날짜로 돈다.

    **`pension_agent` 를 임포트하기 전에 불러야 한다.** `customer` 는 임포트 시점에
    PERSONAS 를 만들면서 잔여일수·경과일을 그때의 «오늘»로 계산한다 — 그 뒤에 환경변수를
    바꿔도 그 값들은 이미 굳었다.

    밖에서 준 값은 건드리지 않는다(`PINNED_BY_TESTS`) — 특정 날짜로 얼려 보려고
    `PENSION_TODAY=…` 를 붙여 돌리는 것이 그대로 살아 있어야 한다.
    """
    if PINNED_BY_TESTS:
        os.environ.pop(TODAY_ENV, None)
