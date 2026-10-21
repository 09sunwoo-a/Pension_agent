"""대화 맥락(history) 보관 — 프로세스 메모리, `(x_client_user, session_id)` 키.

게이트웨이 응답에 `history` 를 돌려줄 자리가 없어 진입점(main.py)이 턴마다 맡기고 되찾음.
영구 상담이력(`session_store`)과 별개 · 재기동 시 소멸 · 다중 컨테이너 미공유.
호출자가 `message_hists` 에 Turn 형식을 보내면 그것이 우선.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

from pension_agent.state import Turn

#: 마지막 접근 후 만료 시간(시연 기준 임의값).
TTL_SEC = 2 * 60 * 60
#: 세션 수 상한. 초과 시 LRU 폐기.
MAX_SESSIONS = 500

_lock = threading.Lock()
#: (x_client_user, session_id) → (마지막 접근 시각, history)
_store: "OrderedDict[tuple[str, str], tuple[float, list[Turn]]]" = OrderedDict()


def _key(x_client_user: str | None, session_id: str | None) -> tuple[str, str]:
    return (str(x_client_user or ""), str(session_id or "default"))


def _evict(now: float) -> None:
    """만료·상한 초과분 폐기. 잠금 안에서 호출."""
    expired = [k for k, (seen, _) in _store.items() if now - seen > TTL_SEC]
    for k in expired:
        del _store[k]
    while len(_store) > MAX_SESSIONS:
        _store.popitem(last=False)


def get(x_client_user: str | None, session_id: str | None) -> list[Turn] | None:
    """이 세션의 마지막 history. 없거나 만료됐으면 None."""
    now = time.monotonic()
    with _lock:
        _evict(now)
        item = _store.get(_key(x_client_user, session_id))
        if item is None:
            return None
        _store.move_to_end(_key(x_client_user, session_id))
        _store[_key(x_client_user, session_id)] = (now, item[1])
        return list(item[1])


def put(x_client_user: str | None, session_id: str | None, history: list[Turn] | None) -> None:
    """턴 종료 후 history 보관. 빈 목록이면 세션 삭제."""
    now = time.monotonic()
    with _lock:
        key = _key(x_client_user, session_id)
        if not history:
            _store.pop(key, None)
        else:
            _store[key] = (now, list(history))
            _store.move_to_end(key)
        _evict(now)


def drop(x_client_user: str | None, session_id: str | None) -> bool:
    with _lock:
        return _store.pop(_key(x_client_user, session_id), None) is not None


def clear() -> None:
    with _lock:
        _store.clear()


def stats() -> dict[str, Any]:
    """/health 용 세션 수·설정."""
    now = time.monotonic()
    with _lock:
        _evict(now)
        return {"sessions": len(_store), "ttl_sec": TTL_SEC, "max_sessions": MAX_SESSIONS}
