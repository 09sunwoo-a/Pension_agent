"""문제상황 → 지식 카드 — ⑥⑦⑧ 공통 후보군 산출(매칭 규칙 단일 정의)."""

from __future__ import annotations

from pension_agent.knowledge import shared_store
from pension_agent.knowledge.similarity import ngram_sim
from pension_agent.tools.customer_solution.support.reference_kb import pitch_kb, pitch_kb_module

ASSETS: list[dict] = shared_store().fields_of("asset")

# 매칭 3단: ① 카드의 세그먼트 직접 연결 ② 세그먼트 그룹 ↔ 카드 그룹 매핑 ③ n-gram 유사도(순위·폴백)

# 세그먼트 그룹 → 화법 그룹. 공통 그룹(GENERAL_PITCH_GROUP)은 모든 그룹에 자동 추가
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

# 상황 무관 공통 화법 그룹
GENERAL_PITCH_GROUP = "오프너·클로징·논거·상담 태도"

# 세그먼트 그룹 → 업무처리절차 그룹(⑧ 통화 전 확인 화면). 공통 그룹 자동 추가
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

# 세그먼트 그룹 → 관리방법론 그룹
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

# LLM 선별용 후보군 상한
MAX_OBJECTION_CANDIDATES = 12
MAX_RESOURCE_CANDIDATES = 10


def _situation_text(s: dict) -> str:
    return " ".join(x for x in (s.get("title"), s.get("condition_text")) if x)


def _card_text(c: dict) -> str:
    return " ".join(x for x in (c.get("title"), c.get("summary")) if x)


def _pitch_cards(kb, card_type: str) -> list[dict]:
    """사후관리 화법 카드(`scope` 기준, 신규유치 레거시 제외)."""
    return [c for c in kb.pitches
            if c.get("type") == card_type and c.get("scope") == "사후관리"]


def _match_situations(situations: list[dict], cards: list[dict],
                      group_map: dict[str, tuple[str, ...]], general_group: str,
                      limit: int) -> list[tuple[float, dict, dict]]:
    """(관련도, 카드, 근거 세그먼트) 목록, 관련도 내림차순. 상황 없으면 빈 목록(폴백은 호출부)."""
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
    """문제상황 화법 카드 + 관련도(대화형 원장용)."""
    kb = pitch_kb()
    if kb is None or not situations:
        return []
    return _match_situations(situations, _pitch_cards(kb, card_type),
                             SEGMENT_TO_PITCH_GROUPS, GENERAL_PITCH_GROUP, limit)


def situation_cards(situations: list[dict], card_type: str, limit: int) -> list[tuple[dict, dict]]:
    """문제상황 화법 카드."""
    return [(c, s) for _score, c, s in scored_situation_cards(situations, card_type, limit)]


def _cards_of(kind: str) -> list[dict]:
    kb = pitch_kb()
    return [c for c in kb.cards if c["_kind"] == kind] if kb else []


def scored_situation_procedures(situations: list[dict],
                                limit: int) -> list[tuple[float, dict, dict]]:
    """문제상황 업무 절차 + 관련도. 화면번호 있는 것 우선."""
    if not situations:
        return []
    ranked = _match_situations(situations, _cards_of("procedure"),
                               SEGMENT_TO_PROCEDURE_GROUPS, GENERAL_PROCEDURE_GROUP, limit * 2)
    with_screens = [t for t in ranked if t[1].get("screens")]
    return (with_screens or ranked)[:limit]


def situation_procedures(situations: list[dict], limit: int) -> list[tuple[dict, dict]]:
    """문제상황 업무 절차. 화면번호 있는 것 우선."""
    return [(c, s) for _score, c, s in scored_situation_procedures(situations, limit)]


def scored_situation_methods(situations: list[dict],
                             limit: int) -> list[tuple[float, dict, dict]]:
    """문제상황 관리 방법론 + 관련도."""
    if not situations:
        return []
    cards = [c for c in _cards_of("method") if c.get("scope") == "사후관리"]
    return _match_situations(situations, cards,
                             SEGMENT_TO_METHOD_GROUPS, GENERAL_METHOD_GROUP, limit)


def situation_methods(situations: list[dict], limit: int) -> list[tuple[dict, dict]]:
    """문제상황 관리 방법론."""
    return [(c, s) for _score, c, s in scored_situation_methods(situations, limit)]


def card_source(card: dict) -> str | None:
    """카드 출처 한 줄. 원천 문서 미특정 시 None."""
    kb_mod = pitch_kb_module
    kb = pitch_kb()
    if kb_mod is None or kb is None or not hasattr(kb_mod, "source_label"):
        return None
    return kb_mod.source_label(kb, card)
