"""상담 세션·대화이력 저장소 — 고객 단위 JSON 파일(가변 운영 데이터, kinds.json 검증 대상 아님).

대화형은 `append_turn()` 으로 쓰기만, 브리핑은 `summarize_for_briefing()` 으로 읽기만.
직원 발화 원문 포함 가능 — `.gitignore` 로 저장소 제외.
"""

from __future__ import annotations

import json
import logging
import re
# datetime.UTC 는 3.11+ — timezone.utc 사용
from datetime import datetime, timezone
from pathlib import Path

from pension_agent.common import config
from typing import Any, TypedDict

SESSION_DATA_DIR = config.SESSION_DATA_DIR


class Turn(TypedDict, total=False):
    """대화 한 턴. role: "user"|"agent"|"tool"|"correction"."""

    ts: str
    role: str
    text: str
    intent: str | None
    tool_calls: list[dict[str, Any]]


_log = logging.getLogger(__name__)

#: 파일명이 되는 고객 id 꼴. 빈 값·경로 문자(`..`·`/`) 금지.
_CUSTOMER_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def recordable(customer_id: str | None) -> bool:
    """이 고객 id 로 기록 가능 여부."""
    return bool(customer_id) and bool(_CUSTOMER_ID.match(str(customer_id)))


def _path(customer_id: str) -> Path:
    return SESSION_DATA_DIR / f"{customer_id}.json"


def _load(customer_id: str) -> dict[str, Any]:
    fp = _path(customer_id)
    if not fp.exists():
        return {"customer_id": customer_id, "sessions": []}
    return json.loads(fp.read_text(encoding="utf-8"))


def scrub_text(text: str) -> str:
    """UTF-8 로 못 쓰는 문자(짝 없는 서로게이트) 제거 — 비UTF-8 터미널 백스페이스 잔여물 대비."""
    return text.encode("utf-8", "ignore").decode("utf-8")


def _save(customer_id: str, doc: dict[str, Any]) -> None:
    SESSION_DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 서로게이트 잔여물로 쓰기가 죽지 않게 직렬화 결과에서 제거(마지막 방어선)
    data = json.dumps(doc, ensure_ascii=False, indent=2)
    _path(customer_id).write_bytes(data.encode("utf-8", "ignore"))


def append_turn(
    customer_id: str, session_id: str, turn: Turn, *, employee_id: str | None = None
) -> None:
    """세션에 턴 추가(새 session_id 면 세션 생성). 고객 id 부적합 시 경고 후 미기록."""
    if not recordable(customer_id):
        _log.warning("세션 기록 건너뜀 — 고객 id 가 비었거나 꼴이 아님(%r) · %s",
                     customer_id, (turn.get("text") or "")[:40])
        return
    doc = _load(customer_id)
    turn = dict(turn)
    turn.setdefault("ts", datetime.now(timezone.utc).isoformat())

    session = next((s for s in doc["sessions"] if s["session_id"] == session_id), None)
    if session is None:
        session = {
            "session_id": session_id,
            "started_at": turn["ts"],
            "employee_id": employee_id,
            "turns": [],
            "outcome": None,  # 상담결과 반영용 예약 필드(미사용)
        }
        doc["sessions"].append(session)
    session["turns"].append(turn)
    _save(customer_id, doc)


def drop_session(customer_id: str, session_id: str) -> bool:
    """세션 하나 삭제, 삭제 시 True. 리허설 잔여물 정리 전용(운영 경로 사용 금지)."""
    doc = _load(customer_id)
    kept = [s for s in doc["sessions"] if s["session_id"] != session_id]
    if len(kept) == len(doc["sessions"]):
        return False
    doc["sessions"] = kept
    _save(customer_id, doc)
    return True


def list_sessions(customer_id: str) -> list[dict[str, Any]]:
    return _load(customer_id)["sessions"]


def summarize_for_briefing(customer_id: str, n: int = 3) -> list[str]:
    """브리핑 «상담 이력»용 한 줄 요약(최근 n건, 최신순). 코드 조립, LLM 미사용."""
    sessions = sorted(list_sessions(customer_id), key=lambda s: s["started_at"], reverse=True)
    lines = []
    for s in sessions[:n]:
        date = s["started_at"][:10]
        turns = s["turns"]
        # 과거 상담 기록(role=record)은 원문, 에이전트 대화는 턴 수만
        record = next((t for t in turns if t.get("role") == "record"), None)
        if record:
            lines.append(f"{date} 상담 — {' '.join((record.get('text') or '').split())}")
        else:
            lines.append(f"{date} 상담 — {len(turns)}턴 진행")
    return lines
