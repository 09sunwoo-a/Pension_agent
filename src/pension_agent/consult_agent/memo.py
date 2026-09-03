"""쪽지 본문 조립 — 이번 상담 요약을 직원 본인 쪽지함으로 보낼 때의 꼴(CLAUDE.md §10 쪽지).

본문은 세 덩이다.

    퇴직연금 사후관리 에이전트의
    {성명} 고객님 ({오늘}) 상담 내용 요약입니다.

    - (LLM 이 쓴 요약 항목 — 검증을 통과한 이번 답변 그대로)

    [고객 주요 정보]
    | 항목 | 값 |   ← 코드가 원장 값으로 채운다

**누가 무엇을 쓰는지가 갈려 있다**(루트 CLAUDE.md 규칙 2). 머리말의 성명·날짜와 고객 주요 정보
표는 코드가 만든다 — 어느 고객의 며칠 상담인지, 평가금액이 얼마인지는 LLM 이 «표현»할 일이
아니라 원장이 아는 값이다. 요약 항목만 LLM 이 쓴다. 표 값은 화면과 같은 산출
(`engine.prepare` 의 `facts["customer"]`·`facts["account_state"]`·`conditions`)을 그대로 옮기고
여기서 새로 계산하지 않는다 — 화면과 다른 값을 쪽지에 남기면 안 된다(CLAUDE.md §3).

프로파일이 없거나 산출에 실패하면 표를 **붙이지 않는다** — 빈 칸을 «미확인»으로 채우면 그
문자열이 쪽지로 나간다(render._customer_header 가 스타클럽 등급을 다루는 방식과 같다).
"""

from __future__ import annotations

from pension_agent.clock import today

MEMO_TITLE = "퇴직연금 사후관리 에이전트의"
KEY_INFO_HEADER = "[고객 주요 정보]"


def _date_label() -> str:
    """상담 시점의 «오늘» — 원장 기준일(customer.AS_OF)이 아니다(두 시간축 주석 참고)."""
    d = today()
    return f"{d.year}년 {d.month}월 {d.day}일"


def _key_info(customer_id: str) -> list[tuple[str, str]]:
    """고객 주요 정보 6항목. 값은 전부 strategy_agent 산출 문자열을 옮긴 것이다.

    같은 항목을 `customer` 도구가 재료로 싣는 dict 에서 읽는다 — 브리핑 화면 상단과 같은
    문자열이라 «화면에는 3억, 쪽지에는 2.9억»이 생길 수 없다.
    """
    from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.strategy_agent import engine  # noqa: PLC0415

    profile = strategy_customer.get_profile(customer_id)
    if profile is None:
        return []
    facts = engine.prepare(profile)
    header, state = facts["customer"], facts["account_state"]
    # 성립 요건은 `코드:이름` 이다 — 코드(`tax`·`add`)는 직원에게 뜻이 없으므로 이름만 싣는다
    # (tools._cond_labels 와 같은 처리).
    reasons = [c.split(":", 1)[1] if ":" in c else c for c in facts.get("conditions") or []]
    return [
        ("연령 · 투자성향", f"{header['연령']}세 · {header['투자성향']}"),
        ("평가금액", str(header["평가금액"])),
        ("수익률(1년)", str(header["수익률"])),
        ("연금개시", f"요건 {state['연금개시요건']} · {state['연금개시']}"),
        ("세액공제 잔여한도", str(state["세액공제_잔여한도"])),
        ("관리 사유", " · ".join(reasons) if reasons else "없음"),
    ]


def compose(customer_id: str, summary: str) -> str:
    """쪽지 본문. `summary` 는 검증을 통과한 이번 답변이고 여기서 고치지 않는다."""
    name = customer_id
    rows: list[tuple[str, str]] = []
    try:
        from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
        profile = strategy_customer.get_profile(customer_id)
        if profile is not None:
            name = profile.nm
        rows = _key_info(customer_id)
    except Exception:
        rows = []
    parts = [f"{MEMO_TITLE}\n{name} 고객님 ({_date_label()}) 상담 내용 요약입니다.",
             summary.strip()]
    if rows:
        table = ["| 항목 | 값 |", "|---|---|", *(f"| {k} | {v} |" for k, v in rows)]
        parts.append(KEY_INFO_HEADER + "\n" + "\n".join(table))
    return "\n\n".join(parts)
