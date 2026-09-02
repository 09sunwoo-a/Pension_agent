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

os.environ.setdefault("PENSION_TODAY", PINNED_TODAY)

#: **저장된 브리핑을 쓰지 않는다.** 회귀 검사는 브리핑을 «만드는 경로»를 검증하는데,
#: 미리 만들어 커밋해 둔 산출물(strategy_agent/briefings.json — scripts/build_briefings.py)이
#: 캐시를 채우면 검사가 그 파일을 읽고 통과한다. 그러면 생성 로직이 깨져도 초록이 뜬다.
#: 지금은 저장본의 기준일이 달라 어차피 키가 안 맞지만, «어쩌다 안 맞아서» 안전한 것과
#: «끄기로 정해서» 안전한 것은 다르다 — 기준일이 같아지는 날 조용히 무너진다.
os.environ.setdefault("PENSION_NO_PREBUILT", "1")
