"""근거 대조용 지식베이스(`knowledge.kb`) 지연 적재 — ⑥~⑨ 공용."""

from __future__ import annotations

from pension_agent.knowledge import kb as pitch_kb_module  # noqa: F401 — 재노출


def load_reference_kb():
    """근거 대조용 지식베이스. 적재 실패 시 None(근거 없이 진행)."""
    try:
        return pitch_kb_module.load_kb()
    except Exception:
        return None


_PITCH_KB = None
_PITCH_KB_LOADED = False


def pitch_kb():
    """근거 대조용 지식베이스. 첫 호출 시 적재·캐싱(임포트 시 적재 금지 — 전체 카드 시효성 판정 비용)."""
    global _PITCH_KB, _PITCH_KB_LOADED
    if not _PITCH_KB_LOADED:
        _PITCH_KB = load_reference_kb()
        _PITCH_KB_LOADED = True
    return _PITCH_KB
