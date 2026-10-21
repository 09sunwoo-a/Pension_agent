"""화법 도구(pitch). 대사라 atomic·notices 없음 — 원문 강제 시 대사가 인용문이 됨."""

from __future__ import annotations

from pension_agent.tools.kb_search import kb_index
from pension_agent.tools.kb_search import pitch_slots as PITCHMOD
from pension_agent.state import KB, AgentState
from pension_agent import tools as _T  # noqa: PLC0415 — 후크는 패키지 경유
from pension_agent.tools.adequacy import _adopt
from pension_agent.evidence.record import Evidence, _ev


#: 화법 카드 후보 수.
PITCH_TOP_K = 3


def _pitch(state: AgentState, query: str) -> Evidence | None:
    """화법 카드 선택 — LLM → 슬롯 좁힌 n-gram → 슬롯 없는 n-gram."""
    hits = _T.llm_pick(("pitch",), query)[:PITCH_TOP_K]
    slots: dict = {}
    if not hits:
        # 슬롯은 n-gram 폴백 전용 — 폴백 진입 시에만 LLM 호출
        slots = PITCHMOD.extract_slots(state)
        hits = _T.retrieve(KB, top_k=PITCH_TOP_K, kinds=["pitch"], utterance=query, **slots)
    if not hits:
        hits = _T.retrieve(KB, top_k=PITCH_TOP_K, kinds=["pitch"], utterance=query)
    hits = _adopt(state, query, hits, "상담 화법")
    if not hits:
        return None
    # slots 는 compose 의 '파악된 상황' 줄 재료
    return _ev("pitch", query, kb_index.build_context(KB, hits), kb_index.sources_of(KB, hits),
               cards=[c for _s, c in hits], meta={"slots": slots})
