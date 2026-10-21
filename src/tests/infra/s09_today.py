"""오늘·기준일 — 두 시간축 분리 · 잔여일수 세는 법.

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)
"""

from __future__ import annotations

from tests.infra._common import check


# 날짜를 명시해 검증 — 고정 오늘만으로는 «세는 법» 오류(연말 잔여일수 ±1)를 못 잡음

import os
from datetime import date

import tests
from pension_agent.tools.customer_solution import customer as CUST

check(tests.PINNED_TODAY == CUST.AS_OF.isoformat(),
      "테스트가 고정한 오늘 = 원장 기준일(AS_OF)",
      f"{tests.PINNED_TODAY} vs {CUST.AS_OF}")

# 옛 단일 축 이름 금지 — 기준일과 오늘을 혼동하는 호출부 방지
check(not hasattr(CUST, "TODAY"),
      "옛 단일 축(customer.TODAY)이 남아 있지 않다")

# 오늘 미포함 — 12/31 당일이 0
_counts = [(date(2026, 8, 27), 126), (date(2026, 12, 31), 0), (date(2026, 12, 30), 1),
           (date(2026, 1, 1), 364), (date(2024, 8, 27), 126)]  # 2024 는 윤년
for _base, _want in _counts:
    _got = CUST.days_to_year_end(_base)
    check(_got == _want, f"연말 잔여일수 {_base} → {_want}일 (오늘 제외)", f"{_got}")

# 잔여일수 음수(지난 만기) 처리
from copy import deepcopy

from pension_agent.tools.customer_solution.engine.text import dday

check(dday(14) == "D-14" and dday(0) == "D-0", "잔여일수 표기: 오늘까지는 D-n", dday(0))
check(dday(-87) == "만기 경과 87일", "지난 만기는 «D--87» 이 아니라 «만기 경과»로 읽는다", dday(-87))
check(dday(None) == "", "만기가 없으면 표기도 없다")

_p = deepcopy(CUST.PERSONAS[0]) if CUST.PERSONAS else None
if _p is not None:
    _p.matDD = -5
    check("mat" not in CUST.conditions(_p),
          "지난 만기는 «만기 1개월 전 안내» 요건을 세우지 않는다", str(CUST.conditions(_p)))
    _p.matDD = 14
    check("mat" in CUST.conditions(_p), "다가오는 만기는 그대로 요건이 된다")

# 지난 만기를 건너뛰고 다음 만기 선택 — 맨 앞을 집으면 30일 내 만기 고객이 요건에서 누락
_mats = [{"date": "2026-01-10", "dd": -30, "type": "예금", "name": "A", "amount": 100},
         {"date": "2026-03-01", "dd": 20, "type": "GIC", "name": "B", "amount": 200}]
check(CUST.next_maturity(_mats)["date"] == "2026-03-01",
      "다음 만기는 지난 만기를 건너뛰고 잡힌다", str(CUST.next_maturity(_mats)))
# 전부 지났으면 가장 최근에 지난 것
_past = [dict(m, dd=-40) for m in _mats]
check(CUST.next_maturity(_past)["date"] == "2026-03-01",
      "전부 지났으면 가장 최근에 지난 만기를 집는다", str(CUST.next_maturity(_past)))
check(CUST.next_maturity([]) is None, "만기가 없으면 None")

# 고정 스위치 — 형식 오류 시 실제 날짜로 조용히 넘어가지 않음
_saved = os.environ.get(CUST.TODAY_ENV)
try:
    os.environ[CUST.TODAY_ENV] = "2026-11-30"
    check(CUST.today() == date(2026, 11, 30), "PENSION_TODAY 가 오늘을 고정한다", str(CUST.today()))
    check(CUST.ledger_age_days() == (date(2026, 11, 30) - CUST.AS_OF).days,
          "원장 나이 = 오늘 − 기준일")
    os.environ[CUST.TODAY_ENV] = "2026-13-99"
    try:
        CUST.today()
        _raised = False
    except ValueError:
        _raised = True
    check(_raised, "PENSION_TODAY 형식 오류는 즉시 실패한다(조용히 실제 날짜로 넘어가지 않는다)")
    os.environ[CUST.TODAY_ENV] = ""
    check(CUST.today() == date.today(), "고정하지 않으면 실제 오늘이다", str(CUST.today()))
finally:
    if _saved is None:
        os.environ.pop(CUST.TODAY_ENV, None)
    else:
        os.environ[CUST.TODAY_ENV] = _saved
