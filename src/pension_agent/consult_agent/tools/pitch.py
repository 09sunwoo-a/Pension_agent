"""화법 도구(pitch) — atomic·notices 가 비어 있는 도구.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from pension_agent.consult_agent import kb as KBMOD
from pension_agent.consult_agent.nodes import pitch as PITCHMOD
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.consult_agent import tools as _T  # noqa: PLC0415 — 후크는 패키지를 거쳐 부른다(머리말)
from pension_agent.consult_agent.tools.adequacy import _adopt
from pension_agent.consult_agent.tools.base import Evidence, _ev


# ─────────────────────────────────────────────────────────────
# 화법 — atomic·notices 가 비어 있는 도구. 특별하지 않다.
#
# atomic·notices 가 비어 있는 이유는 화법이 **고객에게 말할 문장**이기 때문이다. 원문
# 스팬을 그대로 박으면 대사가 아니라 인용문이 되고, 화법의 쓸모가 사라진다. 화법이
# 기대는 보호는 위 적합성 게이트인데, 이제 그건 모든 도구가 함께 쓴다.
# ─────────────────────────────────────────────────────────────

#: 화법 카드 후보 수. 예전 pitch.TOP_K 와 같다.
PITCH_TOP_K = 3


def _pitch(state: AgentState, query: str) -> Evidence | None:
    """화법 카드 3단 선택 — 예전 그래프의 llm_select → retrieve → broaden 을 그대로 옮긴 것.

    ① LLM 이 버킷→카드로 고른다.
    ② 못 고르면 n-gram 으로 찾는다. 이때는 슬롯(고객유형·거절유형·단계)으로 후보를 좁힌다.
    ③ 그래도 0건이면 슬롯을 다 풀고 다시 찾는다(예전 broaden 1·2차를 한 번에 합친 것 —
       단계를 나눠 두 번 돌려도 결국 전부 푸는 것이 마지막이었고, 중간 단계가 건진 사례가
       회귀 스위트에 없었다).
    """
    hits = _T.llm_pick(("pitch",), query)[:PITCH_TOP_K]
    slots: dict = {}
    if not hits:
        # 슬롯 분해는 **여기서** 한다. 예전에는 계획 루프 앞의 노드가 모든 턴에 대해
        # 미리 뽑았는데, 화법을 부르지도 않는 턴("이 고객 예금 잔액 얼마지")에서 LLM
        # 호출 한 번이 통째로 낭비됐다. n-gram 폴백에만 쓰이므로 폴백에 들어올 때 뽑는다.
        slots = PITCHMOD.extract_slots(state)
        hits = _T.retrieve(KB, top_k=PITCH_TOP_K, kinds=["pitch"], utterance=query, **slots)
    if not hits:
        hits = _T.retrieve(KB, top_k=PITCH_TOP_K, kinds=["pitch"], utterance=query)
    hits = _adopt(state, query, hits, "상담 화법")
    if not hits:
        return None
    # 슬롯을 원장에 남긴다 — compose 의 '파악된 상황' 한 줄이 이걸 읽는다. 화법을 안 부른
    # 턴에는 그 줄이 아예 붙지 않는다(있지도 않은 상담 상황을 상상하게 두지 않는다).
    return _ev("pitch", query, KBMOD.build_context(KB, hits), KBMOD.sources_of(KB, hits),
               cards=[c for _s, c in hits], meta={"slots": slots})
