"""관리 대상 목록의 마크다운 표 — 선정·정렬 없이 원장 값만 표시."""

from __future__ import annotations

import html
import re

from pension_agent.common import note
from pension_agent.tools.customer_solution.customer import TAX_CREDIT_CAP_WON, Profile
from pension_agent.tools.customer_solution.target_list import Target


def _cell(value: str) -> str:
    """셀 안의 줄바꿈·마크다운 구분자·HTML 이 표 구조를 바꾸지 않도록 처리."""
    text = html.escape(" ".join(value.split()), quote=False)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`").replace("*", "\\*")


def _pin(profile: Profile) -> str:
    """원장 PIN 만 표시. 부점 DB 의 내부 키(부점:이름)는 PIN 으로 쓰지 않음."""
    if not re.fullmatch(r"\d{6}-\d{7}", profile.id):
        return "미확인"
    return note.display_customer_id(profile.id)


def render(targets: list[Target]) -> str:
    """선정 순서·모든 사유를 유지한 표 하나. PIN 은 기존 쪽지의 마스킹 규칙 적용.
    잔액과 1년 수익률 · 공제 잔여한도와 소진율은 한 칸에 괄호로 둔다(2026-10-23 사용자 결정 — 열 수를 줄인다)."""
    if not targets:
        return note.EMPTY_BODY
    lines = [
        "| 순서 | 고객 PIN | 고객(나이·등급) | 투자성향 | IRP 잔액(1년 수익률) | 공제 잔여한도(소진율) | 관리 사유 |",
        "|---:|---|---|---|---:|---:|---|",
    ]
    for i, target in enumerate(targets, 1):
        p = target.profile
        customer = " · ".join([p.nm, f"{p.ag}세"] + ([p.club_grade] if p.club_grade else []))
        used = (1 - p.room * 10_000 / TAX_CREDIT_CAP_WON) * 100
        reasons = [note.cond_text(target, cond) for cond in target.conds]
        if p.pension_savings:
            reasons.append("연금저축 보유")
        cells = [str(i), _pin(p), customer, p.rk, f"{note.won(p.bal)} ({p.ret:+.1f}%)",
                 f"{note.won(p.room * 10_000)} (소진 {used:.0f}%)",
                 " / ".join(reasons)]
        lines.append("| " + " | ".join(_cell(value) for value in cells) + " |")
    return "\n".join(lines)
