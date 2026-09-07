"""적합성 게이트 — 고른 근거가 질문에 답이 되는가(fits_question · _adopt). 모든 검색 도구가 채택 직전에 거친다.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

import json
import re
from pension_agent.consult_agent import progress
from pension_agent.consult_agent.prompts import ADEQUACY_PROMPT
from pension_agent.consult_agent.state import AgentState, format_history
from pension_agent.consult_agent import tools as _T  # noqa: PLC0415 — 후크는 패키지를 거쳐 부른다(머리말)


# ─────────────────────────────────────────────────────────────
# 적합성 게이트 — 고른 근거가 질문에 답이 되는가 (CLAUDE.md §5)
#
# **재료 종류를 가리지 않는다.** 예전에는 화법에만 있었다. 이유는 "나머지 도구는 코드가
# 만든 텍스트를 그대로 내보내므로 카드가 어긋나면 직원이 읽고 바로 안다"였는데, 계획
# 루프가 들어오면서 그 전제가 깨졌다 — 값·절차도 이제 LLM 이 풀어 쓰고, 어긋난 카드로
# 쓴 문장은 화법과 똑같이 그럴듯하다. 게다가 §6 의 점검은 전부 "틀린 것을 막는" 검사라
# 적절성을 보증하지 못한다(주제어만 겹친 카드로 쓴 답도 수치는 원장 안에 있다).
#
# 게이트가 도는 자리는 **채택 직전**이고, 판정은 **카드 하나씩**이다. 처음에는 후보 묶음
# 전체를 한 번에 YES/NO 로 물었는데, 그러면 옆에 있는 후보 하나가 빗나갔다는 이유로 맞는
# 카드까지 함께 버려진다 — "디폴트옵션 변경 화면번호"에 맞는 절차 카드가 있는데도 "근거를
# 찾지 못했다"고 답하던 자리다. §5 가 말하는 것도 묶음이 아니라 "그 근거를 버린다"이다.
#
# 남은 후보가 0건이면 그 도구는 근거를 못 내놓은 것이 되고(None), 원장이 비면 compose 가
# 정직하게 '없음'으로 답한다 — 틀린 답을 주느니 없다고 하는 편이 낫다(§5).
# ─────────────────────────────────────────────────────────────

def _headline(card: dict) -> str:
    """후보 한 줄. 종류마다 필드 이름이 다르므로 있는 것 중 앞에서부터 고른다."""
    title = card.get("title") or card.get("label") or card.get("id")
    detail = next((str(card[k]) for k in
                   ("value", "condition_text", "summary", "situation", "action", "content")
                   if card.get(k)), "")
    points = "; ".join(card.get("key_points") or [])[:80]
    tail = (detail or points).replace("\n", " ")[:80]
    return f"- [{card.get('id')}] {title}" + (f" · {tail}" if tail else "")


#: 적합성 판정 응답의 토큰 상한. id 몇 개짜리 JSON 배열 한 줄.
ADEQUACY_MAX_TOKENS = 200


def fits_question(question: str, hits: list[tuple[float, dict]],
                  kind: str = "지식", history: list[dict] | None = None,
                  query: str | None = None) -> list[tuple[float, dict]]:
    """질문의 '실제 의도'에 답이 되는 후보만 남긴다(오답 차단). 순서·점수는 그대로 둔다.

    LLM 이 없는 id 를 지어내도 실재 후보와 대조해 걸러낸다 — select.llm_pick 과 같은
    안전장치다. LLM 이 죽으면 예외를 그대로 올린다: 게이트를 못 돌린 턴이 게이트 없이
    답을 만들면 §11 이 막으려는 상태가 된다.

    **이전 대화를 함께 넘긴다.** 후속 질문("1번꺼"·"타행에서요")은 그 말만으로는 어떤
    후보와도 맞지 않아서, 맥락 없이 판정하면 제대로 찾아온 카드까지 전부 탈락한다 —
    계획·작성 프롬프트에 히스토리를 실을 때(§12 지워진 gap 1) 이 프롬프트만 빠져 있었다.

    **계획이 이번에 무엇을 찾는지도 함께 넘긴다**(`query`). 없으면 직원 질문을 그대로
    쓴다. 왜 필요한지는 ADEQUACY_PROMPT 머리말에 적어뒀다 — 고객 특정 질문에서 일반
    자료가 전멸하던 자리다.
    """
    progress.emit("찾은 자료가 질문에 맞는지 확인하고 있어요")
    cards = "\n".join(_headline(c) for _, c in hits)
    raw = _T.generate(ADEQUACY_PROMPT.format(question=question, cards=cards, kind=kind,
                                          query=query or question,
                                          history_block=format_history(history)),
                   max_tokens=ADEQUACY_MAX_TOKENS, name="consult.adequacy")
    m = re.search(r"\[.*\]", raw, re.S)
    try:
        kept = json.loads(m.group()) if m else []
    except ValueError:
        kept = []
    keep = {x for x in kept if isinstance(x, str)} if isinstance(kept, list) else set()
    return [(score, card) for score, card in hits if card.get("id") in keep]


def _adopt(state: AgentState, query: str, hits: list[tuple[float, dict]],
           kind: str) -> list[tuple[float, dict]]:
    """채택할 후보만 남겨 돌려준다. 0건이면 게이트를 돌리지 않는다(부를 이유가 없다).

    직원 질문과 이번 질의를 **둘 다** 넘긴다. 예전에는 `question or query` 로 하나만
    넘겨서, 계획이 무엇을 찾는 중인지가 게이트에 안 보였다.
    """
    if not hits:
        return []
    return _T.fits_question(state.get("question") or query, hits, kind,
                         history=state.get("history"), query=query)
