"""문제상황에 걸린 참고자료 도구(playbook) — 화면 ⑥⑦⑧ 과 같은 후보군.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from pension_agent.consult_agent import kb as KBMOD
from pension_agent.consult_agent.nodes import pitch as PITCHMOD, procedure_qa
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.llm import LLMError
from pension_agent.consult_agent.tools.adequacy import _adopt
from pension_agent.consult_agent.tools.base import Evidence, _ev
from pension_agent.consult_agent.tools.cards import _method_decls, _procedure_decls, _render_method


# 문제상황에 걸린 화법 — 화면 ⑥⑦⑧ 과 같은 후보군
# ─────────────────────────────────────────────────────────────

#: 한 번에 올리는 후보 상한. 상담 중에 읽을 수 있는 분량을 넘기면 아무도 안 읽는다
#: (guard.PER_COND 와 같은 이유의 상한이다).
PLAYBOOK_TOP_K = 2

#: 갈래 — 화면 ⑥⑦⑧ 이 쓰는 후보 축 그대로다. pitch 갈래의 세 card_type(proposal ⑥ ·
#: objection ⑦ · guide ⑧)과, ⑧ 이 화법과 번갈아 싣는 방법론·절차(`resource.py`
#: `_situation_resources`). 여기서 축을 새로 정하지 않는다 — 정하면 화면과 갈린다.
PLAYBOOK_LANES = ("pitch", "procedure", "method")

_PLAYBOOK_TYPES = ("proposal", "objection", "guide")


def playbook_hits(state: AgentState, *, lanes: tuple[str, ...] | None = None,
                   exclude: set[str] | None = None,
                   top_k: int = PLAYBOOK_TOP_K) -> list[tuple[float, dict]]:
    """이 고객의 문제상황에 걸린 화법 후보. 도구(`_situation`)와 제안 판정(`act`)이 함께 쓴다.

    **매칭을 여기서 만들지 않는다.** 고객 상태 → 화법 연결은 strategy_agent 가 화면 ⑥⑦⑧
    을 위해 이미 갖고 있고(3단: 카드의 `segments` → 세그먼트·화법 그룹 매핑 → n-gram,
    `docs/REQUIREMENTS.md` §2), 그 함수를 그대로 부른다. 대화형이 자기 매칭을 만들면 같은
    질문에 화면과 다른 카드를 말하게 된다(CLAUDE.md §3 — 같은 재료를 두 경로로 구현하지
    않는다).

    **금지 상속도 같은 이유로 공짜다.** `problem_situations()` 가 세그먼트의 `exclusions`
    선언을 판정하므로(연금수령 개시 계좌에서 납입·세액공제 세그먼트 seg.13·15·16 이 빠진다),
    여기서 따로 막지 않아도 화면이 막은 것은 후보에 애초에 없다 — 따로 막으면 그것이 두 번째
    판정 경로가 되어 언젠가 화면과 갈린다.

    카드 본체는 **대화형 KB**(`state.KB`)에서 id 로 다시 찾는다. strategy_agent 쪽은 KB 를
    따로 적재하는 별도 인스턴스라, 그쪽 카드를 그대로 쓰면 시효성 판정이 노드마다 갈릴 수
    있다(state.py 가 KB 를 한 번만 적재하는 이유와 같다).

    슬롯(«방금 나온 상황»)으로 후보를 좁힌다 — 화면은 고객 상태만 보고 상담 **전에** 고르지만
    여기는 상담 **중**이라, 직원이 방금 말한 상황까지 볼 수 있다. 이것이 화면과 다른 일을
    하는 유일한 근거다. 슬롯 분해가 실패하면(LLM) 좁히지 않고 상태만으로 고른다.

    `lanes` 는 어느 축의 후보를 볼지다(PLAYBOOK_LANES 부분집합). 제안 판정(`act`)은 이번
    턴이 다룬 갈래만 넘긴다 — 절차를 물은 턴에 화법을 제안하면 §3 「묻지 않은 값」의 제안
    버전이 된다. 도구로 직접 불릴 때는 전 갈래를 본다(None) — 그때는 질문 자체가 이
    재료를 향한 것이라 갈래를 좁힐 근거가 질문에 없다.
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return []
    from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.strategy_agent.situations import problem_situations  # noqa: PLC0415
    from pension_agent.strategy_agent.support import matching  # noqa: PLC0415
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
        # 슬롯은 후보를 **좁히는** 보조 정보다. 없으면 고객 상태만으로 고르면 되고, LLM 이
        # 정말 죽었다면 뒤이은 작성이 같은 이유로 실패해 턴이 §11 로 끝난다.
        slots = {}

    # 갈래마다 넉넉히 뽑아 슬롯으로 거른 뒤 합쳐서 상위 K 를 고른다 — 갈래별로 K 를
    # 나눠 가지면 이 고객에게 걸린 것이 한 갈래에 몰려 있을 때 그 갈래가 잘린다.
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
    best: dict[str, tuple[float, dict]] = {}
    for score, card, _seg in scored:
        local = by_id.get(card["id"])
        if local is None or local["id"] in skip:
            continue
        # 슬롯 스코프는 화법 카드의 축이다(tags.stage·customer_type). 절차·방법론 카드에는
        # 그 축이 없어서 여기 걸면 슬롯이 잡힌 턴마다 전부 탈락한다 — 화법에만 건다.
        if local["_kind"] == "pitch" and not KBMOD.matches_scope(
                local, customer_type=slots.get("customer_type"), stage=slots.get("stage")):
            continue
        if local["id"] not in best or score > best[local["id"]][0]:
            best[local["id"]] = (score, local)
    return sorted(best.values(), key=lambda x: (-x[0], x[1]["id"]))[:top_k]


def _playbook(state: AgentState, query: str) -> Evidence | None:
    """이 고객 상태에 걸린 화법·반론·방법론·절차 참고자료 — 화면 ⑥⑦⑧ 과 같은 후보군에서.

    `customer` 도구가 싣는 것은 화면이 **이미 고른** 2건이고, 여기는 그 **후보군 전체**에서
    이번 대화 상황에 맞는 것을 고른다. 후보군이 같으므로 화면이 자른 것을 꺼내와도 화면과
    어긋나지 않는다 — 다른 매칭이 만든 카드가 아니기 때문이다.

    **후보군 전체를 원장에 싣지 않는다.** ⑦⑧ 후보군은 카드 id·발췌가 통째로 들어와 인용
    허용 수치를 몇 배로 불리고, 그러면 무관한 카드의 숫자가 아무 주장에나 근거를 대준다
    (`_POOL_KEYS` 주석의 사고 — 22개가 110개가 되면서 오답이 통과했다). 여기서는 고른
    것만 올린다.
    """
    hits = playbook_hits(state, exclude=cited_cards(state))
    hits = _adopt(state, query, hits, "고객 상태에 걸린 자료")
    if not hits:
        return None
    return playbook_evidence(query, hits)


def playbook_evidence(query: str, hits: list[tuple[float, dict]]) -> Evidence | None:
    """(관련도, 카드) 목록 → playbook 원장 항목. 도구와 승낙 턴(`act._show_playbook`)이
    함께 쓴다.

    후보에는 종류가 섞인다(화법·방법론·절차 — 화면 ⑧ 이 세 갈래를 번갈아 싣는 것과 같은
    축이다). **종류마다 렌더러와 선언을 그 종류의 도구 것 그대로 쓴다** — 화법 렌더러
    (`kb.build_context`)에 절차 카드를 태우면 두 가지가 깨진다: ① `cautions` 를 역할(role)
    구분 없이 뿌려 저작 메모(authoring)가 직원에게 노출되고(§12 지워진 gap 17 이 고친
    실패의 재발), ② 화면번호가 `atomic` 강제를 받지 않아 LLM 이 옮겨 적다 틀려도 아무도
    못 잡는다.
    """
    if not hits:
        return None
    pitch_hits = [(sc, c) for sc, c in hits if c["_kind"] == "pitch"]
    proc_hits = [(sc, c) for sc, c in hits if c["_kind"] == "procedure"]
    method_hits = [(sc, c) for sc, c in hits if c["_kind"] == "method"]

    blocks: list[str] = []
    atomic: list[str] = []
    notices: list[str] = []
    scopes: list[dict] = []
    if pitch_hits:
        blocks.append(KBMOD.build_context(KB, pitch_hits))
    if proc_hits:
        blocks.append(procedure_qa.render(proc_hits))
        p_atomic, p_notices, p_scopes = _procedure_decls([c for _sc, c in proc_hits])
        atomic += p_atomic
        notices += p_notices
        scopes += p_scopes
    if method_hits:
        blocks.append("\n\n".join(_render_method(c) for _sc, c in method_hits))
        m_notices, m_scopes = _method_decls([c for _sc, c in method_hits])
        notices += m_notices
        scopes += m_scopes
    return _ev("playbook", query, "\n\n".join(blocks), KBMOD.sources_of(KB, hits),
               atomic=atomic, notices=notices, scopes=scopes or None,
               cards=[c for _sc, c in hits])


def cited_cards(state: AgentState) -> set[str]:
    """이번 턴 원장이 이미 들고 있는 카드 id. 같은 카드를 두 번 싣지 않기 위해서다 —
    `customer` 도구가 화면의 2건을 이미 실었다면 그건 여기서 다시 꺼낼 것이 아니다."""
    return {s["id"] for e in (state.get("evidence") or []) for s in e["sources"] if s.get("id")}
