"""답변 + 출처 블록을 텍스트 한 덩어리로 조립 — CLI·main.py 공용.

출처 한 건 표기는 `tools.source_lines` 담당, 여기는 갈래와 머리말만.
"""

from __future__ import annotations

from pension_agent.tools import source_lines

#: 근거 / 주의 갈래 분리 — 섞으면 고객 상태 가드가 답의 근거처럼 보임
GROUND_HEADER = "─ 근거"
CAUTION_HEADER = "─ 이 고객 상담에서 지켜야 할 것 (근거 카드)"


def sources_block(sources: list[dict] | None) -> str:
    """출처 블록(앞 빈 줄 포함). 근거 없으면 «없음» 명시 — 생략 시 «못 실음»과 구분 불가."""
    items = sources or []
    ground = [s for s in items if s.get("role", "근거") == "근거"]
    caution = [s for s in items if s.get("role") == "주의"]

    lines = ["", GROUND_HEADER + ("" if ground else ": 없음")]
    for s in ground:
        lines += source_lines(s)
    if caution:
        lines += ["", CAUTION_HEADER]
        for s in caution:
            lines += source_lines(s)
    return "\n".join(lines)
