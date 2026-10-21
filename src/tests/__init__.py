"""회귀 테스트 패키지. 오늘을 원장 기준일(`customer.AS_OF`)로 고정.

미고정 시 잔여일수·미접촉 일수가 실행일마다 달라져 단언이 흔들림. 두 값 불일치는 `test_infra` 가 검사.
"""

from __future__ import annotations

import os

#: customer.AS_OF 와 같은 날. import 금지 — customer 임포트(PERSONAS 생성)보다 먼저 대입 필요
PINNED_TODAY = "2026-08-24"

os.environ.setdefault("PENSION_TODAY", PINNED_TODAY)
