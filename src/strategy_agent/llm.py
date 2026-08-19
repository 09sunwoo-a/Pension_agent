"""LLM 클라이언트 — 공용 모듈(common.llm)로 위임하는 얇은 shim.

구현·프로바이더 전환·.env 로딩은 common/llm.py 한 곳에 있다. 여기서는 기존 호출부
(agent.py 의 `llm.generate(prompt, system=...)`)가 넘겨온 인자 규약을 그대로 유지하도록
기본값(system=""·max_tokens=900)만 감싸 공용 generate 로 넘긴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common import llm as _common  # noqa: E402

available = _common.available  # 미설정 시 상위 계층이 규칙 폴백으로 전환한다


def generate(prompt: str, *, system: str = "", max_tokens: int = 900,
             temperature: float = 0.2) -> str:
    """기존 strategy 호출 규약 보존 — 빈 system 은 시스템 메시지 없음으로 처리한다."""
    return _common.generate(
        prompt,
        system=system or None,   # "" → None: 공용 구현이 시스템 메시지를 붙이지 않음
        max_tokens=max_tokens,
        temperature=temperature,
    )
