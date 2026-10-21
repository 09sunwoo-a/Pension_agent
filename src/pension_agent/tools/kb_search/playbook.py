"""고객 문제상황별 참고자료 도구(playbook) — 화면 ⑥⑦⑧ 과 같은 후보군."""

from __future__ import annotations

from pension_agent.knowledge import kb as KBMOD
from pension_agent.tools.kb_search import pitch_slots as PITCHMOD
from pension_agent.state import KB, AgentState
from pension_agent.common.llm import LLMError
from pension_agent.tools.adequacy import _adopt
from pension_agent.evidence.record import Evidence


#: 한 번에 올리는 후보 상한(상담 중 읽을 분량).
PLAYBOOK_TOP_K = 2

#: 후보 갈래 — 화면 ⑥⑦⑧ 축과 동일하게 유지(별도 정의 시 화면과 불일치).
PLAYBOOK_LANES = ("pitch", "procedure", "method")

_PLAYBOOK_TYPES = ("proposal", "objection", "guide")


def playbook_hits(state: AgentState, *, lanes: tuple[str, ...] | None = None,
                   exclude: set[str] | None = None,
                   top_k: int = PLAYBOOK_TOP_K) -> list[tuple[float, dict]]:
    """playbook_ranked 에서 문제상황 제외."""
    return [(score, card) for score, card, _sit in
            playbook_ranked(state, lanes=lanes, exclude=exclude, top_k=top_k)]


def playbook_ranked(state: AgentState, *, lanes: tuple[str, ...] | None = None,
                    exclude: set[str] | None = None,
                    top_k: int = PLAYBOOK_TOP_K) -> list[tuple[float, dict, dict]]:
    """고객 문제상황별 후보 — (관련도, 카드, 그 카드를 고른 문제상황).

    - 매칭은 화면 ⑥⑦⑧ 함수 재사용(별도 매칭·금지 판정 금지 — 화면과 불일치)
    - 문제상황 반환: 제안 문구의 사유용(요건 목록으로 대신하면 제목·내용 불일치)
    - 카드 본체는 대화형 KB 에서 id 로 재조회
    - 화법 카드는 슬롯으로 축소(슬롯 실패 시 상태만으로)
    - lanes: 볼 갈래(None 이면 전부)
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return []
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution.situations import problem_situations  # noqa: PLC0415
    from pension_agent.tools.customer_solution.support import matching  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return []
        situations = problem_situations(profile, strategy_customer.conditions(profile))
    except Exception:
        return []
    if not situations:
        return []

    try:
        slots = PITCHMOD.extract_slots(state)
    except LLMError:
        slots = {}

    # 갈래별 K 배분 금지 — 한 갈래에 몰린 후보가 잘림
    scored: list[tuple[float, dict, dict]] = []
    for lane in (lanes or PLAYBOOK_LANES):
        if lane == "pitch":
            for card_type in _PLAYBOOK_TYPES:
                scored += matching.scored_situation_cards(situations, card_type, top_k * 3)
        elif lane == "procedure":
            scored += matching.scored_situation_procedures(situations, top_k * 3)
        elif lane == "method":
            scored += matching.scored_situation_methods(situations, top_k * 3)

    by_id = {c["id"]: c for c in KB.cards}
    skip = set(exclude or ())
    best: dict[str, tuple[float, dict, dict]] = {}
    for score, card, seg in scored:
        local = by_id.get(card["id"])
        if local is None or local["id"] in skip:
            continue
        # 슬롯 축은 화법 카드 전용 — 절차·방법론에 걸면 전부 탈락
        if local["_kind"] == "pitch" and not KBMOD.matches_scope(
                local, customer_type=slots.get("customer_type"), stage=slots.get("stage")):
            continue
        # 중복 카드는 최고 점수 문제상황 유지
        if local["id"] not in best or score > best[local["id"]][0]:
            best[local["id"]] = (score, local, seg or {})
    return sorted(best.values(), key=lambda x: (-x[0], x[1]["id"]))[:top_k]


def _playbook(state: AgentState, query: str) -> Evidence | None:
    """고객 상태별 참고자료 — 원장엔 고른 카드만(후보군 전체 적재 시 인용 허용 수치 과다)."""
    hits = playbook_hits(state, exclude=cited_cards(state))
    hits = _adopt(state, query, hits, "고객 상태에 걸린 자료")
    if not hits:
        return None
    return playbook_evidence(query, hits)


def playbook_evidence(query: str, hits: list[tuple[float, dict]]) -> Evidence | None:
    """(관련도, 카드) 목록 → playbook 원장 항목(종류별 빌더). 도구·승낙 턴 공용."""
    from pension_agent.tools.kb_search.combine import evidence_from_cards  # noqa: PLC0415
    return evidence_from_cards("playbook", query, hits)


def cited_cards(state: AgentState) -> set[str]:
    """이번 턴 원장에 이미 있는 카드 id(중복 적재 방지)."""
    return {s["id"] for e in (state.get("evidence") or []) for s in e["sources"] if s.get("id")}
