"""시황(market) · 운용 상품(lineup) 도구.

두 도구가 같은 함수(_market_like) 사용 — 종류만 다름. 기준시점·시효 표시는 notices 로 강제.
"""

from __future__ import annotations

from collections.abc import Callable
from pension_agent.tools.kb_search import kb_index
from pension_agent.knowledge import kb as KBMOD
from pension_agent.state import KB, AgentState
from pension_agent import tools as _T  # noqa: PLC0415 — 후크는 패키지 경유
from pension_agent.tools.adequacy import _adopt
from pension_agent.evidence.record import Evidence, _ev, _scope
from pension_agent.tools.kb_search.cards import advisory_mark, stale_mark


#: 후보 수 — 카드 하나가 표 통째(약 4천 자)일 수 있어 작게.
MARKET_TOP_K = 2


def _render_market(card: dict) -> str:
    # 개요 카드는 제목 = 문서명 — 중복 표기 생략
    doc_name = card.get("group") if card.get("group") != card.get("title") else None
    scope = " · ".join(x for x in (card.get("category"), doc_name) if x)
    lines = [f"■ {card['title']}" + (f"  ({scope})" if scope else "")]
    if card.get("topic"):
        lines.append(f"· 무엇을 다루나: {card['topic']}")
    lines.append(f"· 기준시점: {card['as_of']}")
    for k in card.get("key_points") or []:
        lines.append(f"· 요점: {k}")
    if card.get("content"):
        lines += ["", card["content"].strip()]
    # 시효 표시는 묶음 단위(stale_marks)
    lines.append(f"· 출처 {KBMOD.origin_of(KB, card)}")
    return "\n".join(lines)


def stale_marks(cards: list[dict]) -> list[str]:
    """카드 묶음의 시효 표시 — 같은 경고는 한 줄로 합치고 기준시점만 나열."""
    groups: dict[str, list[str]] = {}
    for c in cards:
        warn = (c.get("volatile") or "").strip()
        if not warn:
            continue
        as_of = (c.get("as_of") or "").strip()
        bucket = groups.setdefault(warn, [])
        if as_of and as_of not in bucket:
            bucket.append(as_of)
    return [f"※ {warn}" + (f" — {' · '.join(as_ofs)} 기준 표기입니다." if as_ofs else ".")
            for warn, as_ofs in groups.items()]


def _prefer_sections(hits: list[tuple[float, dict]]) -> list[tuple[float, dict]]:
    """같은 문서의 절이 함께 걸리면 개요 카드 제외(키워드가 많아 후보 자리만 차지). 절이 없으면 유지."""
    parents = {c.get("parent") for _s, c in hits if c.get("parent")}
    return [(s, c) for s, c in hits if c["id"] not in parents]


def _market_like(kind: str, label: str) -> Callable[[AgentState, str], Evidence | None]:
    """market·lineup 도구 본체. atomic 미요구(표 덤프 방지) — 오짝은 tables 선언으로 대조."""

    def run(state: AgentState, query: str) -> Evidence | None:
        hits = _adopt(state, query, _prefer_sections(
            _T.pick((kind,), query, top_k=MARKET_TOP_K * 3))[:MARKET_TOP_K], label)
        if not hits:
            return None
        return market_evidence(kind, query, hits)

    return run


#: 단위 복원 줄 머리말 — 트레이스에서 인용 허용 확장임을 표시.
PERCENT_ALLOW = "[단위 복원] 표의 백분율 열 값 — 본문에는 단위가 열 머리말에만 있다:"


def _percent_allow(cards: list[dict]) -> str:
    """백분율 선언 열 값의 두 표기(35·35%) 인용 허용 줄. 없으면 빈 문자열.

    - 셀은 숫자만, 답변은 «35%» → verify 가 다른 주장으로 보고 폐기하는 것 방지
    - 선언된 열만 확장 — verify 전역에서 % 무시 시 «건수 3»이 «3%»로 통과
    - allow 전용, 본문 미포함(중복)
    """
    seen: list[str] = []
    for card in cards:
        for table in card.get("tables") or []:
            for row in table.get("rows") or []:
                for value in row.get("percents") or []:
                    for form in (value, f"{value}%"):
                        if form not in seen:
                            seen.append(form)
    return f"{PERCENT_ALLOW} {' '.join(seen)}" if seen else ""


def market_evidence(kind: str, query: str, hits: list[tuple[float, dict]],
                    tool: str | None = None) -> Evidence | None:
    """시황·상품 카드 → 원장 항목. 검색 도구·카드 되싣기 공용."""
    cards = [c for _s, c in hits]
    # ※ 시효(묶음 단위) · ⚖ 권유 아님 고지 — 둘 다 카드 선언에서
    stale = stale_marks(cards)
    notices: list[str] = list(stale)
    scopes: list[dict] = []
    for c in cards:
        marks = [m for m in (*stale, advisory_mark(c)) if m]
        if not marks:
            continue
        notices += [m for m in marks if m not in notices]
        scopes.append(_scope(c["title"], [], marks))
    text = "\n\n".join([*(_render_market(c) for c in cards), *stale])
    pct = _percent_allow(cards)
    return _ev(tool or kind, query, text,
               kb_index.sources_of(KB, hits), notices=notices, scopes=scopes,
               allow=[text, pct] if pct else None, cards=cards)
