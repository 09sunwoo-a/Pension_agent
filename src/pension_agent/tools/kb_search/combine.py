"""여러 종류의 카드 묶음 → 원장 항목 하나(playbook · last_answer 용).

종류별 빌더를 그대로 사용 — 한 렌더러로 합치면 저작 메모 노출·atomic 강제 누락.
"""

from __future__ import annotations

from collections.abc import Callable

from pension_agent.tools.kb_search import kb_index
from pension_agent.state import KB
from pension_agent.evidence.record import Evidence, _ev
from pension_agent.tools.kb_search.cards import (
    channel_evidence, fact_evidence, fieldtip_evidence, method_evidence, procedure_evidence,
    screen_evidence, segment_evidence,
)
from pension_agent.tools.kb_search.market import market_evidence

Hits = list[tuple[float, dict]]


def _pitch_evidence(query: str, hits: Hits, tool: str) -> Evidence | None:
    return _ev(tool, query, kb_index.build_context(KB, hits), kb_index.sources_of(KB, hits),
               cards=[c for _s, c in hits])


#: 종류 → 원장 항목 빌더. 순서 = 블록 순서(화법 먼저). 없는 종류는 예외.
_BUILDERS: dict[str, Callable[..., Evidence | None]] = {
    "pitch": _pitch_evidence,
    "fact": fact_evidence,
    "procedure": procedure_evidence,
    "screen": screen_evidence,
    "channel": channel_evidence,
    "segment": None,      # customer_id 가 필요해 아래에서 따로 부른다
    "method": method_evidence,
    "fieldtip": fieldtip_evidence,
    "market": lambda q, h, tool: market_evidence("market", q, h, tool=tool),
    "lineup": lambda q, h, tool: market_evidence("lineup", q, h, tool=tool),
}


def evidence_from_cards(tool: str, query: str, hits: Hits, *,
                        customer_id: str | None = None) -> Evidence | None:
    """(관련도, 카드) 목록 → 종류별 빌더 결과를 합친 원장 항목. 빌더 없는 종류는 ValueError."""
    if not hits:
        return None
    unknown = sorted({c["_kind"] for _s, c in hits} - set(_BUILDERS))
    if unknown:
        raise ValueError(f"원장 항목을 만들 렌더러가 없는 카드 종류: {unknown}")

    parts: list[Evidence] = []
    for kind, build in _BUILDERS.items():
        group = [(s, c) for s, c in hits if c["_kind"] == kind]
        if not group:
            continue
        part = (segment_evidence(query, group, customer_id, tool=tool) if kind == "segment"
                else build(query, group, tool))
        if part is not None:
            parts.append(part)
    if not parts:
        return None

    notices: list[str] = []
    for p in parts:
        notices += [n for n in p["notices"] if n not in notices]
    sources: list[dict] = []
    seen: set[str] = set()
    for p in parts:
        for s in p["sources"]:
            if s["id"] not in seen:
                seen.add(s["id"])
                sources.append(s)
    scopes = [sc for p in parts for sc in p["notice_scopes"]]
    return _ev(tool, query, "\n\n".join(p["text"] for p in parts), sources,
               atomic=[a for p in parts for a in p["atomic"]], notices=notices,
               allow=[a for p in parts for a in p["allow"]],
               scopes=scopes or None, cards=[c for _s, c in hits])
