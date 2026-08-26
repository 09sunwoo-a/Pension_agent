"""근거 대조용 지식베이스 — consult_agent 의 카드를 읽어오는 창구.

⑥~⑨ 는 전부 지식 카드를 재료로 쓴다. 그 적재를 여기 한 곳에서만 하고, 첫 사용 시점까지
미룬다 — 지식 카드 전체를 읽어 시효성 판정까지 돌리므로, prepare() 를 부르지 않는
소비부(예: 스키마 검증 CLI)가 그 값을 지불할 이유가 없다.
"""

from __future__ import annotations

from pension_agent.consult_agent import kb as pitch_kb_module  # noqa: F401 — 재노출


# ─────────────────────────────────────────────────────────────
# 지식베이스 적재 (consult_agent/kb.py — 지연 로딩)
# ─────────────────────────────────────────────────────────────

def load_reference_kb():
    """근거 대조용 지식베이스. 적재에 실패하면 None 을 반환한다(근거 없이 진행)."""
    try:
        return pitch_kb_module.load_kb()
    except Exception:
        return None


_PITCH_KB = None
_PITCH_KB_LOADED = False


def pitch_kb():
    """근거 대조용 지식베이스. 첫 호출 때만 적재하고 캐싱한다.

    모듈 임포트 시점이 아니라 첫 사용 시점에 적재하는 이유는 비용이다 — 지식 카드 전체를
    읽어 시효성 판정까지 돌리므로, prepare() 를 부르지 않는 소비부(예: 스키마 검증 CLI)가
    이 값을 지불할 이유가 없다.
    """
    global _PITCH_KB, _PITCH_KB_LOADED
    if not _PITCH_KB_LOADED:
        _PITCH_KB = load_reference_kb()
        _PITCH_KB_LOADED = True
    return _PITCH_KB
