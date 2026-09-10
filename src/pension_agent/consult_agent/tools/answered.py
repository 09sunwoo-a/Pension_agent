"""이전 답변 도구(last_answer) — 이 에이전트가 이번 상담에서 한 답변을 **다시 쓰는 재료**로 싣는다.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.

━━ 왜 있나 ━━
「고객에게 할 말 좀 더 짧게 줄여줘」·「그 중 두 번째 상품 더 설명해줘」·「아까 수수료 설명한
거 요약해줘」는 새 질문이 아니라 **이전 답변을 가리키는 요청**이다. 그 재료는 지식베이스가
아니라 이미 나간 답변 그 자체인데, 대화 맥락(`format_history`)은 직원 질문만 싣고 답변
원문을 들지 않는다(state.Turn) — 그래서 이 재료가 없던 동안 그 요청은 답할 길이 없었다.
실측(2026-09-10): 「증권사는 ETF 종류가 많던데요」 화법을 답한 다음 턴의 「고객에게 해야 할
말 좀 더 짧게 줄여줘」가 **브리핑 수정(correction)** 으로 분류돼, 화법과 무관한 AI브리핑
문장(「만기 예정 예금이 있어 …」)을 고쳐 «이렇게 반영할게요»로 끝났다. 직원이 가리킨 것은
화면의 문장이 아니라 방금 받은 답변이었다.

━━ 왜 도구인가 ━━
답변 원문을 프롬프트의 대화 맥락에 그냥 실으면 LLM 이 그 수치를 되받고, 그 수치는 이번 턴
원장 밖이라 §6 이 답을 통째로 버린다(`plan._repeated_materials` 머리말). **원장에 실으면**
그 문제가 없다 — 이 도구가 돌려주는 텍스트가 곧 인용 허용 집합이 되어, 직전 답변에 있던
193종·400개 같은 수치를 줄인 답변이 그대로 옮겨도 통과한다. `transcript` 가 이번 세션의
답변 원문을 원장에 싣는 것과 같은 규약이다. 그쪽과 갈리는 축은 **범위와 용도**다 —
transcript 는 세션 전체를 요약하려고 싣고(고객 화면이 열려 있어야 한다), 이쪽은 답변
하나를 다시 쓰려고 싣는다(고객 화면과 무관하다 — 답변 원문은 턴 기록에 있다).

━━ 어느 답변인가 — LLM 이 번호를 고르고 코드가 한 턴만 확정한다 ━━
「처음에 말한 수수료 표」처럼 몇 턴 전 답변을 가리킬 수 있다. 어느 턴을 뜻하는지는 해석이라
계획 LLM 이 하고(대화 맥락의 `[3]` 번호를 `query` 에 적는다), 코드는 그 번호 하나만 꺼낸다.
번호가 없거나 범위 밖이거나 그 턴에 답변이 없으면(되묻기 턴) **직전 답변**이다. **어떤
경우에도 한 턴만** 싣는다 — 여러 답변을 한꺼번에 실으면 인용 허용 집합이 대화 전체가 되어
6턴 전 답변의 수치가 이번 주장의 근거로 통과한다(§3 「후보군 전체를 원장에 싣지 않는다」의
사고 — 허용 수치가 22개에서 110개가 되자 오답이 통과했다). 재료 첫 줄에 어느 질문의 답인지
찍히므로, 번호를 잘못 골라도 답변에서 드러난다.

━━ «자세히» — 그 답변의 근거 카드를 id 로 되싣는다 ━━
답변 원문만으로는 원문에 없던 세부를 쓸 수 없다. 「자세히 설명해줘」·「왜 그렇게 말해야 해」
에는 그 턴이 실제로 근거로 썼던 카드(턴 기록의 `sources`, 역할 «근거»)를 **id 로 다시 꺼내**
함께 싣는다 — 재검색이 아니라서 다른 카드가 섞이지 않는다. 카드는 종류별 도구의 렌더러·
선언 그대로 싣는다(`combine.evidence_from_cards` — 절차의 화면번호 강제·주의 표시가 그대로
붙는다). 계획 LLM 이 `query` 에 «근거»를 적을 때만이다 — 요약·줄이기에는 답변 원문이면 된다.

━━ 재료의 경계 ━━
- 그 답변(과 그 근거 카드)에 **있던 것**만 실린다. 없던 것을 더해 달라는 요청은 계획이 그
  재료의 도구를 함께 부른다(PLAN_PROMPT).
- 「하지 말 것」 가드(역할 «주의»)는 잇지 않는다 — 이번 턴에도 코드가 고객 상태를 읽어 다시
  붙인다(compose). 재료 성격 표시(§7)는 잇는다.
- 화면 장치는 뗀다: 답변 끝의 제안 문구·쪽지 펜스·「── 참고한 자료」 블록.
"""

from __future__ import annotations

import re

from pension_agent.consult_agent.state import KB, AgentState, Turn, numbered_history
from pension_agent.consult_agent.tools.base import Evidence, _ev
from pension_agent.consult_agent.tools.history import _strip_devices
from pension_agent.consult_agent.tools.ledger import GROUND

#: 출처가 하나도 남지 않은 답변(메타 안내·연계 URL 등)을 재료로 실을 때의 출처 표기.
#: 지어내지 않는다 — «이번 상담에서 이 에이전트가 한 답변»이라는 사실 하나다.
SELF_SOURCE = {"id": "turn.last_answer", "title": "이전 답변",
               "doc": "이번 상담에서 에이전트가 한 답변", "score": None, "page": None}

#: 재료 블록 머리말. compose 가 «새 검색 결과»로 읽지 않게 성격을 첫 줄에 적고, **어느 턴의
#: 답인지**를 번호와 질문으로 적는다 — 번호를 잘못 골랐으면 여기서 드러난다.
HEADER = ("■ 이전 답변 원문 [{no}] — 직원 질문 «{question}» 에 이 에이전트가 한 답변이다 "
          "(다시 쓰는 재료 · 검색 결과가 아니다)")

#: 되실은 근거 카드 블록의 머리말.
CARDS_HEADER = "■ 위 답변이 근거로 썼던 자료 (그 턴의 출처를 그대로 되실은 것 · 재검색 아님)"

#: 계획 LLM 이 `query` 에 적는 턴 번호 꼴 — 대화 맥락의 `[3]` 그대로. 맨 앞의 맨숫자도
#: 받는다. 문장 가운데 숫자("300만원 답변")는 번호로 읽지 않는다 — 금액이 턴 번호가 된다.
_TURN_REF = re.compile(r"^\s*\[?\s*(\d{1,2})\s*\]?(?=\D|$)|\[(\d{1,2})\]")

#: `query` 에 이 말이 있으면 근거 카드를 함께 싣는다.
_WITH_CARDS = "근거"


def last_answered(history: list[Turn] | None) -> Turn | None:
    """답변 원문이 남아 있는 가장 최근 턴. 없으면 None.

    되묻기로 끝난 턴에는 답변이 없다(graph.ask 가 비워 둔다) — 그 앞 턴의 답이 «직전 답변»이다.
    `tools.usable` 도 이 판정으로 도구를 카탈로그에 올릴지 정한다 — 재료가 없는 턴에 도구를
    보여주면 계획이 한 바퀴를 버린다.
    """
    for turn in reversed(history or []):
        if ((turn or {}).get("answer") or "").strip():
            return turn
    return None


def referenced_turn(history: list[Turn] | None, query: str) -> tuple[int, Turn] | None:
    """`query` 가 가리키는 (대화 맥락 번호, 턴). 번호가 없거나 범위 밖이거나 그 턴에 답변이
    없으면 직전 답변으로 떨어진다 — **한 턴만** 돌려준다(모듈 머리말)."""
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
    """기록된 답변에서 화면 장치를 뗀 본문 — 제안 문구·쪽지 펜스(`_strip_devices`)와
    「── 참고한 자료」 블록. 표시 블록은 `marks` 로 다시 붙는다."""
    from pension_agent.consult_agent.nodes import plan as P  # noqa: PLC0415 — 순환 회피

    text = _strip_devices(answer)
    head, sep, _tail = text.partition("\n\n" + P.MATERIAL_MARKS + "\n")
    return (head if sep else text).strip()


def _grounds(turn: Turn) -> list[dict]:
    """그 답변이 «근거»로 세운 출처(역할 «주의»는 뺀다 — 모듈 머리말)."""
    return [{k: v for k, v in s.items() if k != "role"}
            for s in (turn.get("sources") or []) if s.get("role", GROUND) == GROUND]


def _recall_cards(turn: Turn, query: str) -> Evidence | None:
    """그 답변의 근거 카드를 id 로 되실은 원장 항목. 카드가 지식베이스에 없으면(고객 원장·
    상담 기록·오늘 날짜처럼 카드가 아닌 출처) 그 출처는 건너뛴다 — 지어내지 않는다."""
    from pension_agent.consult_agent.tools.combine import evidence_from_cards  # noqa: PLC0415

    hits = [(float(s.get("score") or 0.0), card) for s in _grounds(turn)
            if (card := KB.cards_by_id.get(s.get("id") or ""))]
    return evidence_from_cards("last_answer", query, hits) if hits else None


def _last_answer(state: AgentState, query: str) -> Evidence | None:
    """이전 답변 원문(+ 요청하면 그 근거 카드). 턴 기록에서 온 재료라 검색이 아니고, 고객
    화면과 무관하다."""
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
                # 답변 본문은 그대로 허용 텍스트이고, 카드의 허용 텍스트는 카드 쪽 것을 잇는다.
                allow=[text, *(cards["allow"] if cards else [])],
                scopes=cards["notice_scopes"] if cards else None,
                meta={"turn": no, "question": turn.get("question") or "",
                      "with_cards": cards is not None})
    if found is None:
        return None
    # 표시는 재료에 걸린다(§7) — 같은 재료를 다시 쓴 답변에도 같은 표시가 붙는다. 관계 선언
    # (값–조건 쌍)은 되실은 카드에서 온다 — 답변 본문에는 선언이 없다.
    marks = list(turn.get("marks") or [])
    if cards is not None:
        marks += [m for m in cards["marks"] if m not in marks]
        found["related"] = list(cards["related"])
    found["marks"] = marks
    return found
