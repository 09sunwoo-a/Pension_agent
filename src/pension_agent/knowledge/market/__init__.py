"""시황·금리 소스 — 읽기 계약 `current()` 하나.

데모 금리표(`rates_demo.json`) 자리표시자, 반환값에 `dummy: True`. 실피드 연결 시 `current()` 본문만 교체.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEMO = Path(__file__).resolve().parent / "rates_demo.json"
_cache: dict[str, Any] | None = None


def current() -> dict[str, Any]:
    """인용 가능한 시황·금리 — `{as_of, dummy, rates}`."""
    global _cache
    if _cache is None:
        raw = json.loads(_DEMO.read_text(encoding="utf-8"))
        _cache = {
            "as_of": raw["meta"].get("as_of"),
            "dummy": bool(raw["meta"].get("dummy")),
            "rates": raw["rates"],
        }
    return _cache


def rate(key: str) -> float | None:
    """금리 값 하나. 없으면 None."""
    r = current()["rates"].get(key)
    return None if r is None else float(r["value"])
