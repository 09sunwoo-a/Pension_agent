"""엑셀 10_상담이력 → 더미 상담 세션(session_data) 시딩 (멱등).

브리핑·history 도구가 평소 경로로 읽도록 세션에 저장. 목업 — 실데이터 전환 시 삭제 필요.

    python -m scripts.seed_sessions           # 없는 것만
    python -m scripts.seed_sessions --force   # 다시 심기
"""

from __future__ import annotations

import json
import sys

from pension_agent.common import config, session_store

#: 시딩 세션 접두 — --force 는 이 접두 세션만 교체(실제 대화 보호).
SEED_PREFIX = "past-"

#: 과거 상담 요약의 역할(발화 아님). 표시명은 tools.py::_HISTORY_ROLE.
RECORD_ROLE = "record"


def _seed_customer(rec: dict, *, force: bool) -> int:
    """한 고객의 과거 상담 시딩. 심은 건수 반환."""
    pin = rec["id"]
    existing = {s["session_id"] for s in session_store.list_sessions(pin)}
    made = 0
    for entry in sorted(rec.get("history") or [], key=lambda h: h["상담년월일"]):
        session_id = f"{SEED_PREFIX}{entry['상담년월일']}"
        if session_id in existing and not force:
            continue
        if session_id in existing:
            _drop_session(pin, session_id)
        session_store.append_turn(
            pin, session_id,
            {   # ts = 상담일(시딩일 아님) — 순서·경과일 기준
                "ts": f"{entry['상담년월일']}T09:00:00+00:00",
                "role": RECORD_ROLE,
                "text": entry["상담이력내용"],
                "intent": None,
                "tool_calls": [],
            },
        )
        made += 1
    return made


def _drop_session(customer_id: str, session_id: str) -> None:
    """세션 하나 삭제. --force 전용(session_store 삭제 API 없음)."""
    fp = config.SESSION_DATA_DIR / f"{customer_id}.json"
    doc = json.loads(fp.read_text(encoding="utf-8"))
    doc["sessions"] = [s for s in doc["sessions"] if s["session_id"] != session_id]
    fp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def main(force: bool = False) -> None:
    if not config.CUSTOMERS_JSON.is_file():
        print("customers.json 이 없습니다 — 먼저 python -m scripts.import_customers")
        raise SystemExit(1)
    doc = json.loads(config.CUSTOMERS_JSON.read_text(encoding="utf-8"))
    made = sum(_seed_customer(rec, force=force) for rec in doc["records"])
    have = sum(1 for rec in doc["records"] if rec.get("history"))
    print(f"[seed_sessions] 과거 상담 {made}건 시딩 — 기록 보유 고객 {have}명 "
          f"({config.SESSION_DATA_DIR.relative_to(config.SRC_ROOT)})")
    if not made:
        print("            (이미 심어져 있습니다. 다시 심으려면 --force)")


if __name__ == "__main__":
    main(force="--force" in sys.argv[1:])
