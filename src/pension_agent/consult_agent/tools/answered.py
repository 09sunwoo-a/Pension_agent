"""직전 답변 도구(last_answer) — 이 에이전트가 방금 한 답변을 **다시 쓰는 재료**로 싣는다.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.

━━ 왜 있나 ━━
「고객에게 할 말 좀 더 짧게 줄여줘」·「방금 그거 더 쉽게 다시 써줘」·「고객 대사만 뽑아줘」는
새 질문이 아니라 **직전 답변을 고쳐 달라는 요청**이다. 그 재료는 지식베이스가 아니라 방금
나간 답변 그 자체인데, 대화 맥락(`format_history`)은 직원 질문만 싣고 답변 원문을 들지
않는다(state.Turn) — 그래서 이 재료가 없던 동안 그 요청은 답할 길이 없었다. 실측
(2026-09-10): 「증권사는 ETF 종류가 많던데요」 화법을 답한 다음 턴의 「고객에게 해야 할 말
좀 더 짧게 줄여줘」가 **브리핑 수정(correction)** 으로 분류돼, 화법과 무관한 AI브리핑 문장
(「만기 예정 예금이 있어 …」)을 고쳐 «이렇게 반영할게요»로 끝났다. 직원이 가리킨 것은
화면의 문장이 아니라 방금 받은 답변이었다.

━━ 왜 도구인가 ━━
답변 원문을 프롬프트의 대화 맥락에 그냥 실으면 LLM 이 그 수치를 되받고, 그 수치는 이번 턴
원장 밖이라 §6 이 답을 통째로 버린다(`plan._repeated_materials` 머리말). **원장에 실으면**
그 문제가 없다 — 이 도구가 돌려주는 텍스트가 곧 인용 허용 집합이 되어, 직전 답변에 있던
193종·400개 같은 수치를 줄인 답변이 그대로 옮겨도 통과한다. `transcript` 가 이번 세션의
답변 원문을 원장에 싣는 것과 같은 규약이다. 그쪽과 갈리는 축은 **범위와 용도**다 —
transcript 는 세션 전체를 요약하려고 싣고(고객 화면이 열려 있어야 한다), 이쪽은 직전 답변
하나를 다시 쓰려고 싣는다(고객 화면과 무관하다 — 답변 원문은 턴 기록에 있다).

━━ 재료의 경계 ━━
- 직전 답변에 **있던 것**만 실린다. 「~도 넣어서 다시 써줘」처럼 없던 것을 더해 달라는
  요청은 계획이 그 재료의 도구를 함께 부른다(PLAN_PROMPT).
- 출처는 직전 답변이 근거로 세웠던 카드(역할 «근거»)를 그대로 잇는다 — 다시 쓴 답변의
  근거는 원래 답변의 근거와 같다. 「하지 말 것」 가드(역할 «주의»)는 잇지 않는다 — 그건
  이번 턴에도 코드가 고객 상태를 읽어 다시 붙인다(compose).
- 재료 성격 표시(§7)도 잇는다 — 표시는 재료에 걸리는 것이라, 같은 재료를 줄여 썼다고
  사라지지 않는다.
- 화면 장치는 뗀다: 답변 끝의 제안 문구(«연계해드릴까요? (네 / 아니오)»)·쪽지 펜스·
  「── 참고한 자료」 블록. 표시 블록은 위처럼 `marks` 로 다시 붙으므로 본문에서 뺀다.
"""

from __future__ import annotations

from pension_agent.consult_agent.state import AgentState, Turn
from pension_agent.consult_agent.tools.base import Evidence, _ev
from pension_agent.consult_agent.tools.history import _strip_devices
from pension_agent.consult_agent.tools.ledger import GROUND

#: 출처가 하나도 남지 않은 직전 답변(메타 안내·연계 URL 등)을 재료로 실을 때의 출처 표기.
#: 지어내지 않는다 — «이번 상담에서 이 에이전트가 한 답변»이라는 사실 하나다.
SELF_SOURCE = {"id": "turn.last_answer", "title": "직전 답변",
               "doc": "이번 상담에서 에이전트가 직전에 한 답변", "score": None, "page": None}

#: 재료 블록 머리말. compose 가 «새 검색 결과»로 읽지 않게 성격을 첫 줄에 적는다.
HEADER = "■ 직전 답변 원문 — 직원 질문 «{question}» 에 이 에이전트가 방금 한 답변이다 (다시 쓰는 재료 · 검색 결과가 아니다)"


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


def _body(answer: str) -> str:
    """기록된 답변에서 화면 장치를 뗀 본문 — 제안 문구·쪽지 펜스(`_strip_devices`)와
    「── 참고한 자료」 블록. 표시 블록은 `marks` 로 다시 붙는다."""
    from pension_agent.consult_agent.nodes import plan as P  # noqa: PLC0415 — 순환 회피

    text = _strip_devices(answer)
    head, sep, _tail = text.partition("\n\n" + P.MATERIAL_MARKS + "\n")
    return (head if sep else text).strip()


def _last_answer(state: AgentState, query: str) -> Evidence | None:
    """직전 답변 원문. 턴 기록에서 온 재료라 검색이 아니고, 고객 화면과 무관하다."""
    turn = last_answered(state.get("history"))
    if turn is None:
        return None
    body = _body(turn.get("answer") or "")
    if not body:
        return None
    # 직전 답변이 «근거»로 세운 카드만 잇는다. «주의»(고객 상태 가드)는 이번 턴에도 코드가
    # 다시 붙이므로 여기서 이으면 같은 카드가 두 역할로 선다.
    sources = [{k: v for k, v in s.items() if k != "role"}
               for s in (turn.get("sources") or []) if s.get("role", GROUND) == GROUND]
    found = _ev("last_answer", query,
                HEADER.format(question=" ".join((turn.get("question") or "").split())) + "\n" + body,
                sources or [dict(SELF_SOURCE)],
                meta={"question": turn.get("question") or ""})
    if found is not None:
        # 표시는 재료에 걸린다(§7) — 같은 재료를 다시 쓴 답변에도 같은 표시가 붙는다.
        found["marks"] = list(turn.get("marks") or [])
    return found
