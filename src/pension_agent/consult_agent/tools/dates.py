"""오늘 날짜·기한 도구(date) — 코드가 오늘을 재료로 싣는다.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from pension_agent.consult_agent.state import AgentState
from pension_agent.consult_agent.tools.base import Evidence, _ev


# ─────────────────────────────────────────────────────────────
#
# 작성 규약(COMPOSE_SYSTEM 1번)이 «재료에 없는 값은 재료 안 값에서 계산해서 만들어내지도
# 않는다(날짜·차액·비율 전부)»다. 옳은 규약이다 — 그런데 그 결과 **오늘이 며칠인지가 어디에도
# 재료로 없었다.** 그래서 세액공제처럼 연말이 마감인 이야기에서 "며칠 남았다"를 말할 수가
# 없었고, 말하면 원장 밖 수치라 verify 가 잘라냈다.
#
# 답은 «LLM 이 오늘을 알게 하는 것»이 아니다. LLM 의 오늘 감각은 학습 시점이지 실행 시점이
# 아니라, 알게 두면 조용히 몇 달 틀린 날짜를 말한다. 답은 **코드가 오늘을 재료로 싣는 것**
# 이다 — 다른 도구가 지식베이스에서 근거를 길어오는 것과 정확히 같은 자리다.
#
# 세는 법을 둘 다 싣는다. "연말까지 126일"과 "오늘 포함 127일"은 같은 날에 대해 둘 다
# 참이라, 하나만 던지면 어느 쪽인지 몰라 하루짜리 오안내가 된다.
# ─────────────────────────────────────────────────────────────

_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def _date(state: AgentState, query: str) -> Evidence | None:
    """오늘 날짜와 기한까지 남은 일수. 검색이 아니라 시스템 시계에서 온 재료다.

    고객 화면이 열려 있으면 **원장 스냅샷 기준일**도 함께 싣는다. 잔액·수익률은 그날 찍힌
    값이고 잔여일수는 오늘 기준이라, 둘이 며칠 벌어져 있는지를 재료가 말해주지 않으면
    "만기 D-14 인데 왜 잔액은 사흘 전 값이냐"에 답할 수 없다.
    """
    from pension_agent.strategy_agent import customer as CUST  # noqa: PLC0415

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
    # atomic·notices 를 비워 둔다. 여기 있는 것은 값+조건이 붙은 원문 스팬이 아니라 계산값
    # 하나하나라, 문장을 통째로 요구하면 답변이 날짜 덤프가 된다. 수치 집합 검사만 걸린다.
    #
    # 그래도 **틀린 날짜는 걸린다.** 검증기가 날짜를 연·월·일 토큰으로 흩지 않고 통짜
    # 정규형으로 대조하기 때문이다(verify.py 의 _DATE_KO 주석). 그 전에는 원장 어딘가에
    # 2026 이 있다는 이유로 2026년의 아무 달이나 통과했다. 여기 실리는 재료의 형태
    # 요구(ANSWER_SHAPES["date"] — 남은 일수만 쓰고 기준 날짜를 빼지 않는다)와 작성 규약
    # 1번(재료 밖 날짜 계산 금지)이 그 위에 겹쳐 있다.
    return _ev("date", query, "\n".join(lines),
               [{"id": "system.date", "title": f"오늘 날짜 ({now.isoformat()})",
                 "doc": "시스템 시계 — 에이전트 실행 시점", "score": None, "page": None}])
