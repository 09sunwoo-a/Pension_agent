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


#: 적합성 판정 응답의 토큰 상한. keep 배열 + 갈래 한 축이 들어가는 JSON 객체 한 줄.
#: 예전에는 id 배열뿐이라 200 이었다 — 갈래를 함께 적게 되면서 닫는 괄호 전에 잘릴 여지가
#: 생겼고, 잘린 JSON 은 통째로 버려져 **후보가 전멸한다**.
ADEQUACY_MAX_TOKENS = 400

#: 갈래 한 축의 최소 선택지 수. 하나뿐이면 갈래가 아니다(되묻기의 MIN_OPTIONS 와 같은 값이고
#: 같은 이유다 — 갈래를 보여주지 못하는 갈래 표시는 아무것도 정해주지 않는다).
MIN_BRANCH_OPTIONS = 2


def _branches(raw: object) -> list[dict]:
    """게이트 응답의 `branches` 를 규격에 맞는 것만 남겨 정규화한다.

    싣는 것은 **축 이름과 선택지 라벨뿐**이다. 카드 id 를 싣지 않는 이유는 갈래 후보를
    빼지 않고 남기기 때문이다(ADEQUACY_PROMPT 머리말) — 재료는 이미 원장에 있으므로
    여기서 다시 가리킬 것이 없고, 판정 프롬프트가 필요로 하는 것은 「무엇으로 갈리나」다.
    """
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        axis = str(item.get("axis") or "").strip()
        options = [str(o).strip() for o in (item.get("options") or [])
                   if isinstance(o, str) and str(o).strip()]
        if axis and len(options) >= MIN_BRANCH_OPTIONS:
            out.append({"axis": axis, "options": options})
    return out


def record_branches(state: AgentState, found: list[dict]) -> None:
    """게이트가 표시한 갈래를 이번 턴의 상태에 쌓는다. 축 이름으로 중복을 걷는다.

    **왜 상태를 여기서 건드리나** — 갈래를 아는 곳(게이트)과 그것을 쓰는 곳(되묻기 판정)
    사이에 노드 경계가 있다. 원장(evidence)에 실으면 될 것 같지만 이건 근거가 아니라
    «후보가 어떻게 갈렸나»의 기록이라, 근거로 실으면 답변 재료가 되고 compose 가 그 문구를
    인용할 수 있게 된다. 전달은 `plan_step` 이 자기 반환값으로 명시적으로 한다 — 여기서는
    같은 노드 안의 리스트에 쌓기만 한다(그래프 상태 전파를 in-place 변경에 기대지 않는다).
    """
    if not found:
        return
    bucket = state.setdefault("branches", [])
    seen = {b["axis"] for b in bucket}
    for b in found:
        if b["axis"] not in seen:
            seen.add(b["axis"])
            bucket.append(b)


def _adequacy_verdict(raw: str) -> tuple[set[str], list[dict]]:
    """게이트 응답을 (남길 id, 갈래) 로 읽는다.

    **객체와 배열을 둘 다 읽는다.** 규격은 객체(`{"keep": …, "branches": …}`)인데, 갈래를
    적기 전의 규격이 배열(`["proc.020"]`)이었고 작은 모델은 그 꼴로 돌아가는 일이 있다.
    배열로 오면 «갈래는 못 봤지만 채택은 했다»로 읽는다 — 규격을 못 맞췄다고 후보를
    전멸시키면, 갈래를 적게 한 변경이 **맞는 답을 지우는** 쪽으로 작동한다(§6 이 가장
    경계하는 자리다). 아무것도 못 읽으면 지금까지와 같이 빈 채택이다.
    """
    obj = re.search(r"\{.*\}", raw, re.S)
    if obj:
        try:
            val = json.loads(obj.group())
        except ValueError:
            val = None
        if isinstance(val, dict):
            keep = val.get("keep")
            return ({x for x in keep if isinstance(x, str)} if isinstance(keep, list) else set(),
                    _branches(val.get("branches")))
    arr = re.search(r"\[.*\]", raw, re.S)
    if arr:
        try:
            val = json.loads(arr.group())
        except ValueError:
            val = None
        if isinstance(val, list):
            return {x for x in val if isinstance(x, str)}, []
    return set(), []


def fits_question(question: str, hits: list[tuple[float, dict]],
                  kind: str = "지식", history: list[dict] | None = None,
                  query: str | None = None,
                  sink: list[dict] | None = None) -> list[tuple[float, dict]]:
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

    **`sink` 는 갈래를 받아 가는 자리다.** 후보 목록 전체를 보는 곳이 여기뿐이라 「이 둘은
    조건이 갈려 답이 달라지는 관계」를 알 수 있는 것도 여기뿐인데, 반환값은 남길 후보라
    그 사실을 실을 데가 없었다(ADEQUACY_PROMPT 머리말 「갈래를 여기서 적는 이유」).
    갈래로 표시된 후보는 **빼지 않는다** — 빼면 되묻기가 성립할 재료가 사라진다.
    """
    progress.emit("찾은 자료가 질문에 맞는지 확인하고 있어요")
    cards = "\n".join(_headline(c) for _, c in hits)
    raw = _T.generate(ADEQUACY_PROMPT.format(question=question, cards=cards, kind=kind,
                                          query=query or question,
                                          history_block=format_history(history)),
                   max_tokens=ADEQUACY_MAX_TOKENS, name="consult.adequacy")
    keep, found = _adequacy_verdict(raw)
    if sink is not None:
        sink.extend(found)
    return [(score, card) for score, card in hits if card.get("id") in keep]


def _adopt(state: AgentState, query: str, hits: list[tuple[float, dict]],
           kind: str) -> list[tuple[float, dict]]:
    """채택할 후보만 남겨 돌려준다. 0건이면 게이트를 돌리지 않는다(부를 이유가 없다).

    직원 질문과 이번 질의를 **둘 다** 넘긴다. 예전에는 `question or query` 로 하나만
    넘겨서, 계획이 무엇을 찾는 중인지가 게이트에 안 보였다.

    게이트가 표시한 갈래는 상태에 쌓아 되묻기 판정으로 넘긴다(`record_branches`).
    `tools.run` 이 질의를 바꿔 한 번 더 부르면 게이트도 두 번 도는데, 축 이름으로 중복이
    걷히므로 같은 갈래가 두 줄로 서지 않는다.
    """
    if not hits:
        return []
    sink: list[dict] = []
    kept = _T.fits_question(state.get("question") or query, hits, kind,
                            history=state.get("history"), query=query, sink=sink)
    record_branches(state, sink)
    return kept
