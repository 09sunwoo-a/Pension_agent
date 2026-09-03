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

**요약에서는 항목 줄만 취한다.** 형태 요구(`ANSWER_SHAPES["transcript"]`)가 「- 물은 것 — 안내
요지」 줄만 쓰라고 시키는데, 실 LLM 은 그 위에 도입 문장을 두 줄 얹었다(2026-09-03 박정호 M1 —
「쪽지 발송 여부는 시스템이 답변 뒤에 따로 안내해요」·「이번 상담에서 오간 내용은 아래와
같아요」. 앞 줄은 지시 문장을 그대로 옮겨 적은 것이다). 프롬프트 지시만으로는 막히지 않는다는
실측이므로, 형태에 맞는 줄(`_ITEM`)만 코드가 취한다 — 톤을 판정하는 것이 아니라 형태 요구와
같은 규칙을 코드가 한 번 더 집행하는 것이다. 항목 줄이 하나도 없으면(「기록 없음」 한 줄 답변)
요약 전체를 그대로 쓴다 — 지어내지도 비우지도 않는다.
"""

from __future__ import annotations

import re

from pension_agent.clock import today

MEMO_TITLE = "퇴직연금 사후관리 에이전트의"
KEY_INFO_HEADER = "[고객 주요 정보]"

#: 화면에서 쪽지 본문을 감싸는 코드블록 펜스(act.offer). 본문의 일부가 아니라 화면 장치다 —
#: 보내는 본문(pending_action.text)에는 없고, 세션 기록에서 재료를 만들 때는 뗀다(tools._transcript).
FENCE = "```"

#: 요약의 «항목 줄» — 「- 」·「• 」·「* 」·「1. 」·「① 」로 시작하는 줄.
_ITEM = re.compile(r"^\s*(?:[-•*]|\d+[.)]|[①-⑳])\s+")


def items_of(summary: str) -> str:
    """요약에서 항목 줄만. 하나도 없으면 요약 전체(모듈 docstring 마지막 문단)."""
    lines = [ln.rstrip() for ln in summary.strip().splitlines() if _ITEM.match(ln)]
    return "\n".join(lines) if lines else summary.strip()


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
    """쪽지 본문. `summary` 는 검증을 통과한 이번 답변이고, 그중 항목 줄만 싣는다(items_of) —
    줄 안의 내용은 고치지 않는다."""
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
             items_of(summary)]
    if rows:
        table = ["| 항목 | 값 |", "|---|---|", *(f"| {k} | {v} |" for k, v in rows)]
        parts.append(KEY_INFO_HEADER + "\n" + "\n".join(table))
    return "\n\n".join(parts)
