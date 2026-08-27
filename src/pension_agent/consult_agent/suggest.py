"""추천 질문 칩 — 고객 화면을 열었을 때 에이전트가 먼저 내미는 질문.

직원은 "이 고객과 지난 상담이 있었다"는 사실 자체를 모르면 물어볼 생각도 못 한다.
그래서 기록이 **있는 고객에 한해** 그 사실(날짜·경과일)을 담은 질문을 칩으로 띄운다 —
읽는 순간 정보 전달이 끝나고, 누르면 계획 루프가 history 도구로 깊이 들어간다.

문구는 전부 **코드가 조립한다**(고정 템플릿 + 계산값). LLM 이 칩을 쓰면 기록에 없는
내용이 질문에 실려 들어오고, 직원이 누르는 순간 그 문장이 원장의 출발점이 된다.
템플릿에는 지어낼 자리가 없다 — 날짜와 경과일은 세션 저장소·customer.TODAY 에서 온
계산값이다.

기록이 없으면 빈 리스트다. 항상 뜨는 칩은 배경이 되어 아무도 읽지 않는다
(nodes/act.py 가 매 턴 제안을 하지 않는 것과 같은 이유). 고객마다 다르게 뜨는 것
자체가 "상황을 읽고 있다"는 표시다.
"""

from __future__ import annotations

from datetime import date


def history_chips(customer_id: str | None) -> list[str]:
    """고객의 과거 상담(record)이 있으면 그 기반 추천 질문을 돌려준다. 없으면 [].

    최신 record 세션 하나만 본다 — 칩은 입구이지 목록이 아니고, 깊이는 history 도구가
    담당한다. 에이전트와 나눈 대화 세션(user/agent)은 세지 않는다: 방금 한 대화를
    "지난 상담"이라 부르면 직원이 오독한다.
    """
    if not customer_id:
        return []
    from pension_agent import session_store  # noqa: PLC0415
    try:
        sessions = session_store.list_sessions(customer_id)
    except Exception:
        return []

    latest: date | None = None
    for s in sessions:
        if not any(t.get("role") == "record" for t in (s.get("turns") or [])):
            continue
        try:
            d = date.fromisoformat((s.get("started_at") or "")[:10])
        except ValueError:
            continue
        if latest is None or d > latest:
            latest = d
    if latest is None:
        return []

    # 기준일은 브리핑과 같은 축(customer.TODAY)이어야 경과일이 화면의 다른 수치와 어긋나지
    # 않는다 — date.today() 를 쓰면 같은 시드가 실행일마다 다른 경과일을 말한다.
    from pension_agent.strategy_agent.customer import TODAY  # noqa: PLC0415
    elapsed = (TODAY - latest).days
    when = f"{latest.month}/{latest.day}"
    return [
        f"지난 상담({when} · {elapsed}일 전)에서 무슨 얘기 했지?",
        "지난 상담 내용 참고해서 오늘 뭐라고 말하면 좋을까?",
    ]
