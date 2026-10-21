"""진행 표시 — 답변 생성 중 «지금 하는 일»을 화면에 흘림.

- 답변 토큰 스트리밍 금지 — 검증 게이트에서 통째로 폐기될 수 있음
- 문구는 코드만 정함(LLM 문구 없음). 실제로 시작한 일 직전에만 emit
- 상태·history 미기록(ContextVar 전달). 콜백 없으면 no-op, 콜백 예외는 삼킴
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

#: 이번 턴의 진행 콜백. graph.ask() 가 열고 닫음.
_CALLBACK: ContextVar[Callable[[str], None] | None] = ContextVar(
    "consult_progress_callback", default=None)


@contextmanager
def reporting(callback: Callable[[str], None] | None) -> Iterator[None]:
    """블록 안의 emit 을 callback 으로 전달. None 이면 no-op."""
    token = _CALLBACK.set(callback)
    try:
        yield
    finally:
        _CALLBACK.reset(token)


def object_of(word: str) -> str:
    """목적격 조사 부착(받침 유무로 «을»/«를»). 한글이 아니면 «을(를)»."""
    if not word:
        return word
    code = ord(word[-1])
    if 0xAC00 <= code <= 0xD7A3:
        return word + ("을" if (code - 0xAC00) % 28 else "를")
    return word + "을(를)"


def emit(text: str) -> None:
    """진행 한 줄. 콜백 없으면 무시, 콜백 예외는 삼킴."""
    cb = _CALLBACK.get()
    if cb is None:
        return
    try:
        cb(text)
    except Exception:  # noqa: BLE001 — 표시 실패가 답변 실패가 되면 안 된다
        pass
