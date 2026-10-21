"""market — 시황·금리 소스.

바깥에는 읽기 계약 `current()` 하나만 노출한다(README 참고). 지금은 **데모 금리표**
(`rates_demo.json`)를 그대로 돌려주는 자리표시자 구현이고, 반환값에 `dummy: True` 가
붙어 있다 — 이 값으로 채워진 문장은 전부 더미로 집계된다(tools/demo_status.py).

실제 피드가 붙으면 `current()` 본문만 교체한다. 호출부(화법 금리 슬롯·조건 판정)는
`dummy` 플래그를 보고 동작을 바꾸지 않는다 — 표시·집계에만 쓴다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEMO = Path(__file__).resolve().parent / "rates_demo.json"
_cache: dict[str, Any] | None = None


def current() -> dict[str, Any]:
    """지금 인용 가능한 시황·금리. 키는 rates_demo.json 의 `rates` 를 따른다.

    반환 형태::

        {"as_of": "2026-08", "dummy": True,
         "rates": {"cash_idle": {"label": ..., "value": 2.42, "unit": "%"}, ...}}
    """
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
    """금리 값 하나. 없으면 None — 지어내지 않는다."""
    r = current()["rates"].get(key)
    return None if r is None else float(r["value"])
