"""문제상황 → 지식 카드 — ⑥⑦⑧ 이 함께 쓰는 후보군 산출.

"이 고객은 어떤 관리 대상인가"(situations)에서 출발해 그 상황에 쓰는 화법·자료를 고른다.
세 섹션이 같은 매칭 규칙을 쓰므로 여기 한 번만 둔다 — 섹션마다 복제하면 세그먼트 그룹
매핑이 곧 갈린다.
"""

from __future__ import annotations

from pension_agent.knowledge import shared_store
from pension_agent.knowledge.similarity import ngram_sim
from pension_agent.strategy_agent.support.kb import pitch_kb, pitch_kb_module

ASSETS: list[dict] = shared_store().fields_of("asset")


# ─────────────────────────────────────────────────────────────
# 문제상황 → 지식 카드 (⑥⑦⑧ 공통 후보군)
#
# "이 고객은 어떤 관리 대상인가"(situations)에서 출발해 그 상황에 쓰는 화법·자료를 고른다.
# 연결은 3단으로 내려간다. 위 단계에서 후보가 나오면 아래는 보충으로만 쓴다.
#   1) 카드에 세그먼트가 직접 걸려 있으면 그것 — 사람이 확정한 연결이라 가장 정확하다.
#   2) 세그먼트 그룹 ↔ 화법 그룹 매핑 — 06/01 과 06/03 이 각자 인덱스에 그룹을 갖고 있어,
#      "이탈 위험 세그먼트에는 이탈 반론·방어 화법" 처럼 축끼리 맞물린다.
#   3) 세그먼트 이름·조건문과의 n-gram 유사도 — 그룹 안에서 순위를 매기고, 그룹 매핑이
#      비어 있을 때의 폴백이 된다.
# 1)은 아직 데이터에 없다(세그먼트별 카드 확정은 사람 검토가 필요한 작업). 그래서 지금은
# 2)+3)이 실제로 후보를 만든다 — 연결이 저작되면 자동으로 1)이 우선한다.
# ─────────────────────────────────────────────────────────────

# 06/01 세그먼트 인덱스 9그룹 → 06/03 화법 인덱스 8그룹.
#
# 오프너·클로징·논거·상담 태도(GENERAL_PITCH_GROUP)는 여기 적지 않는다 — 특정 상황 전용이
# 아니라 어느 상담에나 쓰이는 것이라, 아래에서 모든 그룹에 공통으로 더한다.
SEGMENT_TO_PITCH_GROUPS: dict[str, tuple[str, ...]] = {
    "이탈 위험·방어": ("이탈방어·계약이전", "반론 1", "반론 2"),
    "운용 상태·리밸런싱": ("제안 1", "반론 3"),
    "수익률·관리 공백": ("반론 2", "제안 1"),
    "납입·세액공제": ("제안 2", "반론 4"),
    "자금 유입 이벤트": ("제안 2",),
    "연금개시·수령": ("제안 2", "반론 4"),
    "연령·투자성향·등급·행동": ("제안 1",),
    "컴플라이언스·제외 조건": ("제안 1",),
}

# 상황을 가리지 않고 쓰이는 화법 그룹(오프너·클로징·논거·상담 태도). ⑧ '상담에 참고하세요' 의
# 주 재료다 — 특정 제안이 아니라 상담을 어떻게 여닫고 무엇을 논거로 쓰는지에 대한 노하우다.
GENERAL_PITCH_GROUP = "오프너·클로징·논거·상담 태도"

# 06/01 세그먼트 그룹 → 06/05 업무처리절차 그룹. ⑧ 에 '통화 전 무슨 화면을 보는가'를 얹기 위한
# 매핑이다(07_에이전트_기능정의/01 ① 7요소 중 "확인할 화면"). 조회·진단 경로는 어느 상담이든
# 앞단에 오므로 아래에서 공통으로 더한다.
SEGMENT_TO_PROCEDURE_GROUPS: dict[str, tuple[str, ...]] = {
    "이탈 위험·방어": ("계약이전 처리", "고객 접촉·발송 처리"),
    "운용 상태·리밸런싱": ("운용지시·상품변경 처리",),
    "수익률·관리 공백": ("운용지시·상품변경 처리", "고객 접촉·발송 처리"),
    "납입·세액공제": ("퇴직금·과세이연 처리",),
    "자금 유입 이벤트": ("퇴직금·과세이연 처리",),
    "연금개시·수령": ("연금개시·지급 처리",),
    "연령·투자성향·등급·행동": ("고객 접촉·발송 처리",),
    "컴플라이언스·제외 조건": ("컴플라이언스",),
}

GENERAL_PROCEDURE_GROUP = "조회·진단 경로"

# 06/01 세그먼트 그룹 → 06/02 IRP관리방법론 그룹. 방법론은 '무엇을 제안할지의 판단 규칙'이라
# ⑧ 에서 화법·절차와 함께 상담 재료가 된다.
SEGMENT_TO_METHOD_GROUPS: dict[str, tuple[str, ...]] = {
    "이탈 위험·방어": ("관리 사이클·이탈방어",),
    "운용 상태·리밸런싱": ("리밸런싱 실행", "자산배분·상품선택"),
    "수익률·관리 공백": ("자산배분·상품선택", "관리 사이클·이탈방어"),
    "납입·세액공제": ("자금 유입 설계",),
    "자금 유입 이벤트": ("자금 유입 설계",),
    "연금개시·수령": ("인출·연금 수령 설계",),
    "연령·투자성향·등급·행동": ("자산배분·상품선택",),
    "컴플라이언스·제외 조건": ("리밸런싱 실행",),
}

GENERAL_METHOD_GROUP = "계좌 진단"

# 후보군 상한. LLM 선별(agent._select)이 고르기 좋은 크기로 자른다 — 넓히면 선별 품질이 떨어지고,
# 좁히면 규칙이 이미 골라버려 선별의 의미가 없어진다.
MAX_OBJECTION_CANDIDATES = 12
MAX_RESOURCE_CANDIDATES = 10


def _situation_text(s: dict) -> str:
    return " ".join(x for x in (s.get("title"), s.get("condition_text")) if x)


def _card_text(c: dict) -> str:
    return " ".join(x for x in (c.get("title"), c.get("summary")) if x)


def _pitch_cards(kb, card_type: str) -> list[dict]:
    """신규 지식베이스(06/03 변환분)의 사후관리 화법 카드.

    레거시 카드(신규유치 챕터)는 제외한다 — 사후관리 화면에 유치 화법이 섞이면 상담 맥락이 어긋난다.
    구분은 `scope` 필드의 유무로 한다(신규 변환분만 가진다).
    """
    return [c for c in kb.pitches
            if c.get("type") == card_type and c.get("scope") == "사후관리"]


def _match_situations(situations: list[dict], cards: list[dict],
                      group_map: dict[str, tuple[str, ...]], general_group: str,
                      limit: int) -> list[tuple[float, dict, dict]]:
    """카드를 문제상황에 붙여 (관련도, 카드, 근거 세그먼트) 로 돌려준다. 관련도 내림차순.

    상황이 없으면 빈 목록이다 — 이 고객에게 해당하는 관리 사유가 없다는 뜻이므로, 아무 카드나
    채워 넣지 않는다(그 판단은 호출부의 폴백 몫).

    관련도를 **버리지 않고 함께 돌려준다.** 화면 ⑥⑦⑧ 은 정해진 개수를 채우면 그만이라
    필요가 없었지만, 대화형은 같은 후보를 근거 원장에 올리면서 "이게 얼마나 걸린 것인지"를
    함께 밝혀야 한다(consult_agent/CLAUDE.md §3 — 검색으로 온 재료는 관련도를 싣는다).
    개수만 쓰는 쪽은 `situation_cards()` 처럼 앞을 버리면 된다.
    """
    scored: dict[str, tuple[float, dict, dict]] = {}
    for s in situations:
        allowed = group_map.get(s.get("group") or "", ()) + (general_group,)
        text = _situation_text(s)
        for c in cards:
            linked = s["id"] in (c.get("segments") or [])
            in_group = any((c.get("group") or "").startswith(g) for g in allowed)
            if not linked and not in_group:
                continue
            score = ngram_sim(text, _card_text(c)) + (1.0 if linked else 0.0)
            best = scored.get(c["id"])
            if best is None or score > best[0]:
                scored[c["id"]] = (score, c, s)

    ranked = sorted(scored.values(), key=lambda x: (-x[0], x[1]["id"]))
    return ranked[:limit]


def scored_situation_cards(situations: list[dict], card_type: str,
                           limit: int) -> list[tuple[float, dict, dict]]:
    """문제상황에 쓸 화법 카드 — 관련도까지. 대화형이 근거 원장에 올릴 때 쓴다."""
    kb = pitch_kb()
    if kb is None or not situations:
        return []
    return _match_situations(situations, _pitch_cards(kb, card_type),
                             SEGMENT_TO_PITCH_GROUPS, GENERAL_PITCH_GROUP, limit)


def situation_cards(situations: list[dict], card_type: str, limit: int) -> list[tuple[dict, dict]]:
    """문제상황에 쓸 화법 카드(06/03 변환분)."""
    return [(c, s) for _score, c, s in scored_situation_cards(situations, card_type, limit)]


def _cards_of(kind: str) -> list[dict]:
    kb = pitch_kb()
    return [c for c in kb.cards if c["_kind"] == kind] if kb else []


def scored_situation_procedures(situations: list[dict],
                                limit: int) -> list[tuple[float, dict, dict]]:
    """문제상황에 쓸 업무 처리 절차 — 관련도까지. 화면번호가 있는 것을 먼저 본다."""
    if not situations:
        return []
    ranked = _match_situations(situations, _cards_of("procedure"),
                               SEGMENT_TO_PROCEDURE_GROUPS, GENERAL_PROCEDURE_GROUP, limit * 2)
    with_screens = [t for t in ranked if t[1].get("screens")]
    return (with_screens or ranked)[:limit]


def situation_procedures(situations: list[dict], limit: int) -> list[tuple[dict, dict]]:
    """문제상황에 쓸 업무 처리 절차(06/05 변환분). 화면번호가 있는 것을 먼저 본다."""
    return [(c, s) for _score, c, s in scored_situation_procedures(situations, limit)]


def scored_situation_methods(situations: list[dict],
                             limit: int) -> list[tuple[float, dict, dict]]:
    """문제상황에 쓸 관리 방법론 — 관련도까지."""
    if not situations:
        return []
    cards = [c for c in _cards_of("method") if c.get("scope") == "사후관리"]
    return _match_situations(situations, cards,
                             SEGMENT_TO_METHOD_GROUPS, GENERAL_METHOD_GROUP, limit)


def situation_methods(situations: list[dict], limit: int) -> list[tuple[dict, dict]]:
    """문제상황에 쓸 관리 방법론(06/02 변환분) — '이런 상황이면 이렇게 판단·제안한다'."""
    return [(c, s) for _score, c, s in scored_situation_methods(situations, limit)]


def card_source(card: dict) -> str | None:
    """카드의 출처 한 줄. 원천 문서를 특정하지 못하면 표시하지 않는다."""
    kb_mod = pitch_kb_module
    kb = pitch_kb()
    if kb_mod is None or kb is None or not hasattr(kb_mod, "source_label"):
        return None
    return kb_mod.source_label(kb, card)
