"""업무 처리 절차 — `procedure` 도구(tools.py)의 검색과 근거 블록 조립.

facts_qa 와 같은 이유로 즉답 노드를 지웠다(§11 · 그 파일 주석). 남은 것은 검색과 근거
블록 조립이다.

답의 알맹이가 **화면번호와 처리 순서**라, 그 값은 원문 그대로 나가야 한다 — 문장을 다듬는
과정에서 한 글자만 틀려도 직원이 없는 화면을 찾는다. 그 집행은 도구가 선언한 `atomic`
스팬으로 코드가 한다(tools.py::_procedure).

원문의 ▶(고객 안내 가능)·⚠(자료 간 상충·확인 필요) 표시를 그대로 옮겨 답변에 남긴다 —
"고객에게 그대로 말해도 되는가"와 "이 절차는 아직 확정이 아닌가"는 직원이 반드시 알아야 하는
단서이고, 화면번호만 뽑아 주면 그 단서가 사라진다.
"""

from __future__ import annotations

from pension_agent.consult_agent import marks
from pension_agent.consult_agent.kb import origin_of, role_texts
from pension_agent.consult_agent.select import pick
from pension_agent.consult_agent.state import KB

TOP_K = 2
# 재정렬용으로 넉넉히 뽑는다 — n-gram 은 '무엇에 대한 절차인가'는 잘 맞히지만
# '화면번호를 묻는다'는 요구는 모른다.
SEARCH_K = 6

_SCREEN_ASKED = ("화면", "번호", "메뉴", "경로", "어디서")


def _render(card: dict) -> list[str]:
    lines = [f"■ {card['title']}"]
    if card.get("status") == "확인 필요":
        lines.append("⚠ 자료 간 표기가 어긋나는 절차입니다 — 처리 전 원본·담당부서 확인이 필요합니다.")
    lines += ["", card.get("summary") or ""]
    if card.get("screens"):
        lines.append(f"· 화면번호: {' '.join(card['screens'])}")
    # 역할이 caution 인 주의만 보여준다 — ⚠ 유의 블록의 본체는 저작 검증 메모(authoring)라
    # 직원에게 띄우지 않는다(역할 선언은 build_kb + config 예외표).
    for caution in role_texts(card.get("cautions"), "caution"):
        lines.append(f"· {caution}")
    # 참·거짓을 둘 다 싣는다 — 팩트 재료와 같은 이유다(facts_qa._render · marks.py).
    facing = marks.facing_note(card)
    if facing:
        lines.append(f"· {facing}")
    lines.append(f"· 출처 {origin_of(KB, card)}")
    return lines


def search(question: str) -> list[tuple[float, dict]]:
    """절차 카드 top-2. `procedure` 도구가 부른다."""
    hits = pick(("procedure",), question, top_k=SEARCH_K)

    # 화면·경로를 물었으면 화면번호가 실린 절차를 앞세운다. 관련도는 비슷한데 한쪽에만 답이
    # 들어 있는 경우가 실제로 나온다 — "디폴트옵션 변경 화면번호" 에 화면번호 없는 항목이 먼저 왔다.
    if any(k in question for k in _SCREEN_ASKED):
        hits.sort(key=lambda h: (0 if h[1].get("screens") else 1, -h[0], h[1]["id"]))
    return hits[:TOP_K]


def render(hits: list[tuple[float, dict]]) -> str:
    """화면번호·처리 순서 블록. compose 의 재료이자, 원문 스팬이 어긋났을 때의 복구 블록이다."""
    return "\n\n".join("\n".join(_render(c)) for _, c in hits)
