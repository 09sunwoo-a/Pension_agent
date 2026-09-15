"""본문 가공 — 자를 것은 자르고, 끌 수 있으면 끈다

`observability.py` 에서 갈라낸 모듈 — 공개 표면은 패키지 `__init__` 이다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator
import json
import time

from pension_agent.observability._conf import conf


# ─────────────────────────────────────────────────────────────
# 본문 가공 — 자를 것은 자르고, 끌 수 있으면 끈다
# ─────────────────────────────────────────────────────────────

def _payload(value: Any) -> Any:
    """input·output 을 보낼 수 있는 모양으로 만든다.

    `LANGFUSE_CAPTURE_CONTENT=0` 이면 본문 대신 길이만 남긴다 — 개인정보를 외부로
    내보내지 않으면서 «호출은 있었다 · 얼마나 길었다»는 볼 수 있게 하는 자리다.
    """
    if value is None:
        return None
    c = conf()
    if not c.capture_content:
        return {"omitted": True, "chars": len(_text(value))}
    return _clip(_jsonable(value), c.max_chars)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(
        _jsonable(value), ensure_ascii=False, default=str)


def _jsonable(value: Any) -> Any:
    """JSON 으로 실을 수 있는 값으로 낮춘다. 못 낮추는 것은 문자열로 떨어뜨린다."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"\n…({len(value) - limit}자 생략)"
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, limit) for v in value]
    return value


def _put_if(target: dict, key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _now() -> str:
    return _iso(time.time())


def _iso(ts: float | None) -> str:
    """Langfuse 가 받는 ISO 8601 UTC. `datetime.UTC` 는 3.11+ 라 쓰지 않는다."""
    moment = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return moment.isoformat().replace("+00:00", "Z")
