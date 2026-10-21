"""제도·상품 확정값 — fact 도구의 검색과 근거 블록 조립.

값에는 기준시점·상태·출처 필수. «확인 필요» 팩트는 적재 단계에서 제외.
"""

from __future__ import annotations

from pension_agent.evidence import marks
from pension_agent.tools.kb_search import select
from pension_agent.knowledge.kb import origin_of
from pension_agent.state import KB

TOP_K = 3

# n-gram 채택 문턱 — 팩트는 텍스트가 길어 유사도가 낮게 나옴
MIN_SCORE = 0.10


def _score(fact: dict, question: str) -> float:
    """n-gram 관련도. 라벨 가중치 2배."""
    from pension_agent.knowledge.similarity import ngram_sim  # noqa: PLC0415

    label = ngram_sim(question, fact.get("label") or "")
    value = ngram_sim(question, fact.get("value") or "")
    return label * 2 + value


def _render(fact: dict) -> list[str]:
    lines = [f"■ {fact['label']}", "", fact.get("value") or ""]
    # 원문 표 필수 — 빠지면 표 수치 인용이 검증기에 막힘. tables 는 대조 전용(중복 렌더 금지)
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
    # 출처 필수(origin_of 가 대체 순서 처리)
    tail.append(f"출처 {origin_of(KB, fact)}")
    # 고객 안내 가능 여부는 참·거짓 모두 표시 — 거짓 누락 시 내부용이 미선언과 구별 불가
    facing = marks.facing_note(fact)
    if facing:
        tail.append(facing)
    lines.append("· " + " · ".join(tail))
    return lines


def _ngram(question: str) -> list[tuple[float, dict]]:
    """n-gram 폴백 선택."""
    ranked = sorted(
        ((_score(f, question), f) for f in KB.facts.values()),
        key=lambda x: (-x[0], x[1]["id"]),
    )
    return [(s, f) for s, f in ranked if s >= MIN_SCORE][:TOP_K]


def search(question: str) -> list[tuple[float, dict]]:
    """팩트 top-3 — LLM 선택, 0건이면 자체 n-gram(_ngram) 폴백."""
    # select 모듈 경유 호출 필수 — 이름 임포트 시 테스트의 llm_pick 교체가 안 먹힘
    hits = select.llm_pick(("fact",), question)
    return hits[:TOP_K] if hits else _ngram(question)


def render(hits: list[tuple[float, dict]]) -> str:
    """확정값 블록 — compose 재료 겸 검증 실패 시 복구 블록."""
    return "\n\n".join("\n".join(_render(f)) for _, f in hits)
