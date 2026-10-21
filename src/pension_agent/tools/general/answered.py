"""이전 답변 도구(last_answer) — 이번 상담에서 한 답변 하나를 다시 쓰는 재료로 적재.

- 원장에 실어야 그 답변의 수치 인용이 허용됨(대화 맥락엔 답변 원문 미포함)
- 턴 선택: query 의 [번호], 없거나 무효면 직전 답변. 항상 한 턴만(여러 턴이면 인용 허용 과다)
- query 에 «근거»가 있으면 그 턴의 근거 카드를 id 로 재적재(재검색 아님)
- 가드(주의 출처)는 미승계, 재료 성격 표시는 승계. 화면 장치(제안 문구·펜스·참고자료 블록) 제거
"""

from __future__ import annotations

import re

from pension_agent.evidence import marks
from pension_agent.state import KB, AgentState, Turn, numbered_history
from pension_agent.evidence.record import Evidence, _ev
from pension_agent.tools.general.history import _strip_devices
from pension_agent.evidence.ledger import GROUND

#: 근거 출처가 없는 답변용 출처 표기.
SELF_SOURCE = {"id": "turn.last_answer", "title": "이전 답변",
               "doc": "이번 상담에서 에이전트가 한 답변", "score": None, "page": None}

#: 재료 블록 머리말 — 검색 결과 아님과 턴 번호·질문 명시(잘못 고른 번호가 드러남).
HEADER = ("■ 이전 답변 원문 [{no}] — 직원 질문 «{question}» 에 이 에이전트가 한 답변이다 "
          "(다시 쓰는 재료 · 검색 결과가 아니다)")

#: 되실은 근거 카드 블록의 머리말.
CARDS_HEADER = "■ 위 답변이 근거로 썼던 자료 (그 턴의 출처를 그대로 되실은 것 · 재검색 아님)"

#: query 의 턴 번호 — `[3]` 또는 맨 앞 숫자만(문장 중간 숫자는 금액일 수 있어 제외).
_TURN_REF = re.compile(r"^\s*\[?\s*(\d{1,2})\s*\]?(?=\D|$)|\[(\d{1,2})\]")

#: query 에 있으면 근거 카드 함께 적재.
_WITH_CARDS = "근거"


def last_answered(history: list[Turn] | None) -> Turn | None:
    """답변이 있는 가장 최근 턴(되묻기 턴 건너뜀). 없으면 None. tools.usable 도 사용."""
    for turn in reversed(history or []):
        if ((turn or {}).get("answer") or "").strip():
            return turn
    return None


def referenced_turn(history: list[Turn] | None, query: str) -> tuple[int, Turn] | None:
    """query 가 가리키는 (번호, 턴). 무효 번호면 직전 답변. 한 턴만 반환."""
    numbered = numbered_history(history)
    m = _TURN_REF.search(query or "")
    if m:
        no = int(m.group(1) or m.group(2))
        for i, turn in numbered:
            if i == no and ((turn or {}).get("answer") or "").strip():
                return i, turn
    for i, turn in reversed(numbered):
        if ((turn or {}).get("answer") or "").strip():
            return i, turn
    return None


def _body(answer: str) -> str:
    """화면 장치·「참고한 자료」 블록을 뗀 답변 본문."""
    text = _strip_devices(answer)
    head, sep, _tail = text.partition("\n\n" + marks.MATERIAL_MARKS + "\n")
    return (head if sep else text).strip()


def _grounds(turn: Turn) -> list[dict]:
    """그 답변의 근거 출처(주의 제외)."""
    return [{k: v for k, v in s.items() if k != "role"}
            for s in (turn.get("sources") or []) if s.get("role", GROUND) == GROUND]


def _recall_cards(turn: Turn, query: str) -> Evidence | None:
    """근거 카드를 id 로 재적재한 원장 항목. 카드가 아닌 출처는 건너뜀."""
    from pension_agent.tools.kb_search.combine import evidence_from_cards  # noqa: PLC0415

    hits = [(float(s.get("score") or 0.0), card) for s in _grounds(turn)
            if (card := KB.cards_by_id.get(s.get("id") or ""))]
    return evidence_from_cards("last_answer", query, hits) if hits else None


def _last_answer(state: AgentState, query: str) -> Evidence | None:
    """이전 답변 원문(+요청 시 근거 카드). 고객 화면 무관."""
    picked = referenced_turn(state.get("history"), query)
    if picked is None:
        return None
    no, turn = picked
    body = _body(turn.get("answer") or "")
    if not body:
        return None
    question = " ".join((turn.get("question") or "").split())
    text = HEADER.format(no=no, question=question) + "\n" + body
    sources = _grounds(turn) or [dict(SELF_SOURCE)]
    cards = _recall_cards(turn, query) if _WITH_CARDS in (query or "") else None
    if cards is not None:
        text += "\n\n" + CARDS_HEADER + "\n" + cards["text"]
    found = _ev("last_answer", query, text, sources,
                atomic=cards["atomic"] if cards else None,
                notices=cards["notices"] if cards else None,
                allow=[text, *(cards["allow"] if cards else [])],
                scopes=cards["notice_scopes"] if cards else None,
                meta={"turn": no, "question": turn.get("question") or "",
                      "with_cards": cards is not None})
    if found is None:
        return None
    # 표시는 원래 답변에서 승계, 관계 선언은 재적재 카드에서만
    marks = list(turn.get("marks") or [])
    if cards is not None:
        marks += [m for m in cards["marks"] if m not in marks]
        found["related"] = list(cards["related"])
    found["marks"] = marks
    return found
