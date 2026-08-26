"""엑셀 10_상담이력 → 더미 상담 세션(session_data) 시딩 (멱등).

목업 9케이스에는 "과거에 이런 상담을 했다"는 기록이 딸려 있다(원본 xlsx 10_상담이력,
데이터사전: "과거 상담 의사·불만·관심사항을 LLM이 현재 솔루션과 화법에 함께 반영").
시연에서 그 상태를 재현하려면 상담 기록이 **실제로 저장소에 있어야** 한다 — 그래야
브리핑 §14 와 대화형 history 도구가 평소 경로 그대로 읽는다.

**왜 별도 재료로 두지 않고 세션에 심나.** 직원이 "지난번에 무슨 얘기 했지" 로 묻는 것은
한 사건이다. 원장용 경로와 세션용 경로를 따로 두면 도구가 둘이 되고, 어느 쪽을 부를지
LLM 이 정하게 되어 한쪽만 불린 턴은 나머지가 없는 것이 된다. 읽는 곳을 하나로 두려고
기록 쪽을 맞춘다 — 실서비스에서도 CRM 상담 기록이 같은 자리에 적재될 자리다.

주의: 여기서 만드는 것은 **목업**이다. `session_data/` 에는 원래 직원 발화 원문 같은
개인정보에 준하는 내용이 쌓이므로, 실데이터 전환 시 이 시딩 결과를 지우고 저장소에서도
제외해야 한다(`.gitignore`).

실행 (src/ 에서):

    python -m scripts.seed_sessions           # 없는 것만 심는다
    python -m scripts.seed_sessions --force   # 이미 있어도 다시 심는다
"""

from __future__ import annotations

import json
import sys

from pension_agent import config, session_store

#: 시딩으로 만든 세션임을 식별하는 접두. 이 접두가 붙은 세션만 --force 로 갈아엎는다 —
#: 사람이 실제로 나눈 대화까지 지우면 안 된다.
SEED_PREFIX = "past-"

#: 과거 상담 기록의 역할. 직원·에이전트의 «발화»가 아니라 상담 결과 «요약»이라 따로 둔다
#: (consult_agent/tools.py::_HISTORY_ROLE 이 이 값을 사람이 읽는 이름으로 옮긴다).
RECORD_ROLE = "record"


def _seed_customer(rec: dict, *, force: bool) -> int:
    """한 고객의 과거 상담을 세션으로 심는다. 심은 건수를 돌려준다."""
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
            {   # 상담일을 그대로 타임스탬프로 쓴다 — 심은 날짜가 아니라 상담한 날짜여야
                # "지난 상담" 순서와 경과일이 맞는다.
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
    """세션 하나를 지운다. --force 재시딩에서만 쓴다(session_store 는 삭제 API 가 없다)."""
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
