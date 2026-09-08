"""답변을 **텍스트 한 덩어리**로 펴는 곳 — 답변 + 출처 블록.

글자만 낼 수 있는 화면이 둘이다: 운영 CLI(`__main__.py`)와 행내 플랫폼 API(`main.py`).
둘이 각자 조립하면 한쪽만 «주의» 갈래를 빠뜨리거나 한쪽만 «근거: 없음»을 안 적는다 —
**같은 질문에 같은 답인데 근거가 다르게 보이는 것은 근거가 없는 것보다 나쁘다.**

출처 «한 건»의 표기(문서명·id·관련도·↗URL)는 여기서 정하지 않는다. `tools.source_lines`
하나가 정하고 디버그 실행기($CAD·$CADR)도 같은 것을 쓴다 — 이 파일은 그 위에서 **갈래와
머리말**만 얹는다. Streamlit(app.py)은 매체가 달라 별도로 렌더한다(마크다운 링크).
"""

from __future__ import annotations

from pension_agent.consult_agent.tools import source_lines

#: 답이 나온 재료 / 표현을 제한한 재료. 한 목록에 섞으면 질문과 무관한 고객 상태 가드가
#: 답의 근거처럼 보인다(nodes/plan.py::_sources 주석).
GROUND_HEADER = "─ 근거"
CAUTION_HEADER = "─ 이 고객 상담에서 지켜야 할 것 (근거 카드)"


def sources_block(sources: list[dict] | None) -> str:
    """출처 전체. 앞에 빈 줄을 두고 시작한다(답변 본문과 붙지 않게).

    근거가 하나도 없으면 «없음»이라고 **적는다.** 블록을 통째로 빼면 "근거 없이 답했다"와
    "근거를 못 실었다"가 화면에서 같아 보인다.
    """
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
