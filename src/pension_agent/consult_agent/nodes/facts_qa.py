"""제도·상품 확정값 — `fact` 도구(tools.py)의 검색과 근거 블록 조립.

예전에는 여기 `fact_lookup` 노드가 있었다. "세액공제 한도가 얼마야?" 를 의도 하나로
분류해 LLM 없이 값을 그대로 돌려주는 즉답 경로였고, LLM 이 죽었을 때의 대체 경로이기도
했다. 그 경로는 CLAUDE.md §11 에 따라 지웠다 — 근거가 덜 갖춰진 답변이 정상 답변처럼
보이는 것이 무응답보다 위험하고, 같은 재료를 두 경로(즉답 노드 · fact 도구)로 답하면
프롬프트·검증·표시 규약이 갈린다.

남은 것은 검색(search)과 근거 블록 조립(render)이다. 값과 함께 **기준시점·상태·출처**를
반드시 붙여 내보내는 규약은 그대로다(07_에이전트_기능정의/01 ② "근거 수치는 기준시점과 함께").

`⚠ 확인 필요` 팩트는 애초에 지식베이스에 적재되지 않는다(변환기가 보류 파일로 분리) — 확인되지
않은 값을 답할 방법 자체를 없애 둔 것이다. 적재된 것 중 `⏳ 시효 민감` 은 기준시점을 함께
말해야만 쓸 수 있으므로 답변에 표시를 남긴다.
"""

from __future__ import annotations

from pension_agent.consult_agent import marks, select
from pension_agent.consult_agent.kb import origin_of
from pension_agent.consult_agent.state import KB

TOP_K = 3

# 채택 문턱. 팩트는 라벨·본문이 길어 화법 카드보다 유사도가 낮게 나오므로 별도로 둔다.
# 이 아래는 "그 수치는 지식베이스에 없다"고 답하는 편이 맞다.
MIN_SCORE = 0.10


def _score(fact: dict, question: str) -> float:
    """질문과 팩트의 관련도. 라벨(제목)을 본문보다 무겁게 본다 — 라벨이 곧 주제어다."""
    from pension_agent.knowledge.similarity import ngram_sim  # noqa: PLC0415

    label = ngram_sim(question, fact.get("label") or "")
    value = ngram_sim(question, fact.get("value") or "")
    return label * 2 + value


def _render(fact: dict) -> list[str]:
    lines = [f"■ {fact['label']}", "", fact.get("value") or ""]
    # 원문 표. **싣지 않으면 값이 없는 카드가 된다** — 검증기는 근거 블록에 있는 수치만
    # 인용을 허용하므로, 표가 재료에 없으면 직원이 물었을 때 "자료가 없어요" 가 그대로
    # 나간다(원문에는 있는데도). 구조 선언(`tables`)은 오짝 대조용이고 렌더는 원문으로
    # 한다 — 같은 표를 두 모양으로 실으면 답변이 표를 두 번 옮긴다.
    if fact.get("content"):
        lines += ["", fact["content"].strip()]
    if fact.get("status") and fact["status"] != "확정":
        lines.append(f"⚠ 상태: {fact['status']} — 인용 시 기준시점을 반드시 함께 말하세요.")
    if fact.get("verify_points"):
        lines.append(f"· 자주 틀리는 지점: {fact['verify_points']}")
    if fact.get("screens"):
        lines.append(f"· 확인 화면: {' '.join(fact['screens'])}")
    tail = []
    if fact.get("as_of"):
        tail.append(f"기준시점 {fact['as_of']}")
    # 출처는 어떤 경우에도 밝힌다 — 원천 문서 → 원문 표기 → 추출지식 절 순으로 물러선다.
    # 값만 있고 어디서 왔는지 없는 답은 직원이 고객에게 옮길 수 없다(origin_of).
    tail.append(f"출처 {origin_of(KB, fact)}")
    # 참·거짓을 **둘 다** 싣는다. 예전에는 참일 때만 붙였고, 그래서 내부용 팩트는 선언이
    # 없는 카드와 구분되지 않은 채 재료로 들어갔다 — 작성 프롬프트의 「'내부용'으로 표시된
    # 자료는…」 규칙이 가리킬 표시가 없었다(marks.py docstring).
    facing = marks.facing_note(fact)
    if facing:
        tail.append(facing)
    lines.append("· " + " · ".join(tail))
    return lines


def _ngram(question: str) -> list[tuple[float, dict]]:
    """라벨·본문 n-gram 으로 고른 팩트. LLM 이 아무것도 못 골랐을 때의 폴백이다."""
    ranked = sorted(
        ((_score(f, question), f) for f in KB.facts.values()),
        key=lambda x: (-x[0], x[1]["id"]),
    )
    return [(s, f) for s, f in ranked if s >= MIN_SCORE][:TOP_K]


def search(question: str) -> list[tuple[float, dict]]:
    """질문에 맞는 팩트 top-3. `fact` 도구가 부른다.

    **LLM 이 먼저 고르고, 못 고르면 n-gram 으로 물러선다** — 다른 여덟 종류가 이미 그
    길이다(select.pick). 팩트만 오래도록 n-gram 하나뿐이었는데, 팩트가 카드 색인 밖에
    살아서(kinds.json `consumed: reference`) LLM 이 고를 후보가 될 수 없었기 때문이다.
    그 대가는 직원 말과 카드 말이 다를 때 통째로 0건이었다 — "연말정산 얼마나 돌려받아?"
    가 세액공제 카드(F2)를 못 찾고, "중도에 깨면 세금 얼마나 떼?" 가 중도해지 카드(F30)를
    못 찾았다. 하필 팩트는 한도·세율처럼 **숫자를 묻는** 재료라 «자료가 없어요» 가 가장
    아픈 자리다.

    n-gram 채점은 그대로 둔다(`_ngram`). 색인에 들어갔다고 `retrieve` 의 채점으로 갈아타면
    LLM 이 죽거나 아무것도 못 고른 턴의 결과가 조용히 달라진다 — 넓히기만 하고 좁히지
    않는다.
    """
    # `select.llm_pick` 을 **모듈째로** 부른다. 이름을 이 모듈로 끌어오면 호출 시점이 아니라
    # 임포트 시점에 묶여, 테스트·디버그가 LLM 선택을 끄려고 `select.llm_pick` 을 갈아끼워도
    # 여기만 진짜를 부른다 — 스텁 줄을 모듈 수만큼 늘려야 하는 자리다.
    hits = select.llm_pick(("fact",), question)
    return hits[:TOP_K] if hits else _ngram(question)


def render(hits: list[tuple[float, dict]]) -> str:
    """확정값 블록. compose 의 재료이자, 원문 스팬이 어긋났을 때의 복구 블록이다."""
    return "\n\n".join("\n".join(_render(f)) for _, f in hits)
