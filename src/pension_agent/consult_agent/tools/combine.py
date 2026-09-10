"""카드 묶음 → 원장 항목 하나 — **종류마다 그 종류의 도구가 쓰는 렌더러·선언을 그대로** 쓴다.

한 원장 항목에 여러 종류의 카드가 섞이는 자리가 둘이다.
  · `playbook` — 화면 ⑥⑦⑧ 후보군(화법·절차·방법론)에서 고른 것
  · `last_answer` — 직전 답변이 근거로 썼던 카드를 id 로 되싣는 것(어느 종류든 올 수 있다)

종류를 무시하고 한 렌더러에 태우면 두 가지가 깨진다: `cautions` 가 역할 구분 없이 뿌려져
저작 메모가 직원에게 노출되고(§12 지워진 gap 17 의 재발), 화면번호·값 스팬이 `atomic`
강제를 받지 않아 LLM 이 옮겨 적다 틀려도 아무도 못 잡는다. 그래서 종류별 도구가 검색
뒤에 하는 일(`fact_evidence`·`procedure_evidence` …)을 그대로 부르고 결과만 합친다 —
선언이 두 곳에 있으면 한쪽만 빠지는 날이 온다.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from collections.abc import Callable

from pension_agent.consult_agent import kb as KBMOD
from pension_agent.consult_agent.state import KB
from pension_agent.consult_agent.tools.base import Evidence, _ev
from pension_agent.consult_agent.tools.cards import (
    channel_evidence, fact_evidence, fieldtip_evidence, method_evidence, procedure_evidence,
    screen_evidence, segment_evidence,
)
from pension_agent.consult_agent.tools.market import market_evidence

Hits = list[tuple[float, dict]]


def _pitch_evidence(query: str, hits: Hits, tool: str) -> Evidence | None:
    return _ev(tool, query, KBMOD.build_context(KB, hits), KBMOD.sources_of(KB, hits),
               cards=[c for _s, c in hits])


#: 종류 → 그 종류의 원장 항목을 만드는 함수. **순서가 곧 블록 순서다**(화법이 먼저 —
#: 직원이 상담 중에 읽는 것은 할 말이고 나머지는 그 근거다). 여기 없는 종류는 실리지 않는다 —
#: 렌더러 없이 싣는 경로를 두면 위 머리말의 사고가 그 종류로 되살아난다.
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
    """(관련도, 카드) 목록 → `tool` 이름의 원장 항목 하나. 종류마다 그 종류의 빌더를 부르고
    본문·스팬·표시·출처를 합친다. 렌더러가 없는 종류는 조용히 빠지지 않고 예외다 —
    «카드는 실렸는데 선언이 안 실린» 항목이 원장에 서면 안 된다."""
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
