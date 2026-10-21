"""트레이스 본문 가공 — 길이 제한·본문 끄기·JSON 변환."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator
import json
import time

from pension_agent.common.observability._conf import conf


def _payload(value: Any) -> Any:
    """input·output 을 전송 가능한 꼴로 변환. `LANGFUSE_CAPTURE_CONTENT=0` 이면 길이만."""
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
    """JSON 가능 값으로 변환. 불가하면 문자열."""
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
    """ISO 8601 UTC. `datetime.UTC` 는 3.11+ 라 미사용."""
    moment = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return moment.isoformat().replace("+00:00", "Z")
