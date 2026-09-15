"""오늘·기준일 — 시간축 두 개가 섞이지 않는가

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from tests.infra._common import check


# ─────────────────────────────────────────────────────────────
# 오늘·기준일 — 시간축 두 개가 섞이지 않는가
#
# 여기만 **날짜를 명시해** 검증한다. 나머지 테스트는 tests/__init__.py 가 오늘을 원장
# 기준일로 고정한 채 돌기 때문에, 고정해 둔 그 하루로만 보면 «세는 법»의 오류를 못 잡는다
# (연말 잔여일수를 하루 더 세거나 덜 세는 것이 정확히 그런 오류다 — 화면에서는 129 도
# 126 도 똑같이 그럴듯해 보인다).
# ─────────────────────────────────────────────────────────────

import os
from datetime import date

import tests
from pension_agent.strategy_agent import customer as CUST

check(tests.PINNED_TODAY == CUST.AS_OF.isoformat(),
      "테스트가 고정한 오늘 = 원장 기준일(AS_OF)",
      f"{tests.PINNED_TODAY} vs {CUST.AS_OF}")

# 두 축이 이름부터 갈려 있어야 한다. 옛 이름이 살아 있으면 «원장 기준일»과 «오늘»을
# 같은 것으로 아는 호출부가 조용히 남는다.
check(not hasattr(CUST, "TODAY"),
      "옛 단일 축(customer.TODAY)이 남아 있지 않다")

# 세는 법 — 오늘은 세지 않는다. 12/31 당일이 0 이어야 «오늘 포함 +1» 이 성립한다.
_counts = [(date(2026, 8, 27), 126), (date(2026, 12, 31), 0), (date(2026, 12, 30), 1),
           (date(2026, 1, 1), 364), (date(2024, 8, 27), 126)]  # 2024 는 윤년
for _base, _want in _counts:
    _got = CUST.days_to_year_end(_base)
    check(_got == _want, f"연말 잔여일수 {_base} → {_want}일 (오늘 제외)", f"{_got}")

# 오늘이 움직이면 잔여일수는 음수가 될 수 있다. 그때 화면·요건이 무너지지 않는가 —
# 오늘이 고정돼 있던 동안에는 시연 데이터의 만기가 전부 미래라 한 번도 안 나던 경우다.
from copy import deepcopy

from pension_agent.strategy_agent.engine.text import dday

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

# 지난 만기 뒤의 «진짜 다음 만기»가 가려지지 않는가 — 목록 맨 앞을 그냥 집으면 30일 안에
# 만기가 오는 고객이 요건에서 통째로 빠진다.
_mats = [{"date": "2026-01-10", "dd": -30, "type": "예금", "name": "A", "amount": 100},
         {"date": "2026-03-01", "dd": 20, "type": "GIC", "name": "B", "amount": 200}]
check(CUST.next_maturity(_mats)["date"] == "2026-03-01",
      "다음 만기는 지난 만기를 건너뛰고 잡힌다", str(CUST.next_maturity(_mats)))
# 전부 지났으면 가장 최근에 지난 것 — 제일 오래 전 것을 집으면 화면이 엉뚱한 만기를 말한다.
_past = [dict(m, dd=-40) for m in _mats]
check(CUST.next_maturity(_past)["date"] == "2026-03-01",
      "전부 지났으면 가장 최근에 지난 만기를 집는다", str(CUST.next_maturity(_past)))
check(CUST.next_maturity([]) is None, "만기가 없으면 None")

# 고정 스위치. 형식이 틀리면 조용히 실제 날짜로 넘어가지 않는다.
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
