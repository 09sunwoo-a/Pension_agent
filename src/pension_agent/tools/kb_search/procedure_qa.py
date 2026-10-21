"""업무 처리 절차 — procedure 도구의 검색과 근거 블록 조립.

화면번호는 atomic 으로 원문 강제(cards._procedure). 고객 안내 가능·확인 필요 표시 유지.
"""

from __future__ import annotations

from pension_agent.evidence import marks
from pension_agent.knowledge.kb import origin_of, role_texts
from pension_agent.tools.kb_search.select import pick
from pension_agent.state import KB

TOP_K = 2
# 화면번호 우선 재정렬용 후보 수
SEARCH_K = 6

_SCREEN_ASKED = ("화면", "번호", "메뉴", "경로", "어디서")


def _render(card: dict) -> list[str]:
    lines = [f"■ {card['title']}"]
    if card.get("status") == "확인 필요":
        lines.append("⚠ 자료 간 표기가 어긋나는 절차입니다 — 처리 전 원본·담당부서 확인이 필요합니다.")
    lines += ["", card.get("summary") or ""]
    if card.get("screens"):
        lines.append(f"· 화면번호: {' '.join(card['screens'])}")
    # caution 만 표시 — authoring 메모 미노출
    for caution in role_texts(card.get("cautions"), "caution"):
        lines.append(f"· {caution}")
    facing = marks.facing_note(card)
    if facing:
        lines.append(f"· {facing}")
    lines.append(f"· 출처 {origin_of(KB, card)}")
    return lines


def search(question: str) -> list[tuple[float, dict]]:
    """절차 카드 top-2."""
    hits = pick(("procedure",), question, top_k=SEARCH_K)

    # 화면·경로 질문이면 화면번호 있는 절차 우선
    if any(k in question for k in _SCREEN_ASKED):
        hits.sort(key=lambda h: (0 if h[1].get("screens") else 1, -h[0], h[1]["id"]))
    return hits[:TOP_K]


def render(hits: list[tuple[float, dict]]) -> str:
    """화면번호·처리 순서 블록 — compose 재료 겸 복구 블록."""
    return "\n\n".join("\n".join(_render(c)) for _, c in hits)
