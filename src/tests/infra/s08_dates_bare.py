"""연도 없이 말한 날짜 — «오늘 언저리» 해석.

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)
"""

from __future__ import annotations

from tests.infra._common import check

from tests.infra.s05_numbers import _judge, _numbers   # 앞 구간에서 이어받음


# 연도 없는 날짜는 오늘 ±1년으로 해석 — 3년 전 이력의 월일 차용은 차단, 정상 어법(작년 상담·내년 만기)은 허용

# 오늘 명시 — 고정 하루에 기대면 해가 바뀔 때 다른 것을 측정
_THIS_YEAR = 2026

for _label, _ledger, _bare, _want in (
    ("올해 만기",        "만기도래 2026-09-10",              "9월 10일이 만기예요.",       True),
    ("내년 만기",        "만기도래 2027-02-14",              "2월 14일이 만기예요.",       True),
    ("작년 상담",        "지난 상담 2025-11-13",             "11월 13일에 상담하셨어요.",  True),
    ("원장이 연도 없이", "다음 해 7월 1일부터 발급됩니다",     "7월 1일부터 발급돼요.",      True),
    ("3년 전 납입이력",  "2023년 납입 900만원 (2023-12-31)", "12월 31일까지 납입하세요.",  False),
    ("오래된 카드 시효", "준법감시인 심의필 2013.02.28.",     "2월 28일까지예요.",          False),
):
    _nums = _numbers(_ledger, this_year=_THIS_YEAR)
    _ok, _why = _judge(_bare, _nums, set(), set())
    check(_ok == _want, f"연도 없는 날짜 — {_label}: {_bare}", f"기대 {_want} / 실제 {_ok} {_why}")

# 연도를 밝히면 오래된 날짜도 인용 가능 — 차단은 연도 생략 시만
_old = _numbers("2023년 납입 900만원 (2023-12-31)", this_year=_THIS_YEAR)
check(_judge("2023년 12월 31일 납입이에요.", _old, set(), set())[0],
      "연도를 밝히면 오래된 날짜도 인용된다")
