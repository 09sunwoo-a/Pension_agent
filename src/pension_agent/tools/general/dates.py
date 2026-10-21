"""오늘 날짜·기한 도구(date) — 시스템 시계 값을 재료로 적재.

재료 밖 날짜 계산 금지 규약 때문에 «며칠 남았다»는 이 재료가 있어야만 답 가능.
"""

from __future__ import annotations

from pension_agent.state import AgentState
from pension_agent.evidence.record import Evidence, _ev


_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def _date(state: AgentState, query: str) -> Evidence | None:
    """오늘 날짜·연말 잔여일(오늘 제외·포함 둘 다 — 하나만 주면 하루 오안내).

    고객 화면이 열려 있으면 원장 기준일과 그 차이도 포함.
    """
    from pension_agent.tools.customer_solution import customer as CUST  # noqa: PLC0415

    now = CUST.today()
    left = CUST.days_to_year_end(now)
    lines = [
        "■ 오늘 날짜와 기한 (시스템 시계 — 검색 결과가 아니다)",
        f"· 오늘: {now.year}년 {now.month}월 {now.day}일 ({_WEEKDAYS[now.weekday()]}) / {now.isoformat()}",
        f"· 올해: {now.year}년 (세액공제 등 «올해»는 {now.year}년 1월 1일~12월 31일)",
        f"· 연말({now.year}년 12월 31일)까지: {left}일 남음 — 오늘을 세지 않은 값이고, "
        f"오늘부터 12월 31일까지를 세면 {left + 1}일이다",
    ]
    if state.get("customer_id"):
        age = CUST.ledger_age_days()
        lines.append(
            f"· 고객 계좌 원장 기준일: {CUST.AS_OF.isoformat()} — 오늘 기준 {age}일 전 스냅샷이다. "
            "잔액·수익률·납입액은 그날 값이고, 만기까지 며칠·마지막 접촉 이후 며칠 같은 "
            "잔여일수·경과일은 오늘 기준으로 다시 센 값이다")
    # atomic 없음(날짜 덤프 방지) — 틀린 날짜는 verify 의 날짜 정규형 대조가 잡음
    return _ev("date", query, "\n".join(lines),
               [{"id": "system.date", "title": f"오늘 날짜 ({now.isoformat()})",
                 "doc": "시스템 시계 — 에이전트 실행 시점", "score": None, "page": None}])
