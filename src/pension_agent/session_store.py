"""상담 세션/대화이력 저장소 — consult_agent 와 strategy_agent 가 공유하는 운영 데이터.

`data/*.json`(저작된 지식 — 검증·버전관리 대상)과는 성격이 다르다. 여기 담기는 것은 직원이
에이전트와 나눈 대화 원문처럼 매 상담마다 바뀌는 가변 운영 데이터라 `kinds.json`에 등록하지
않고(`common.schema`의 검증 대상이 아님), 별도 디렉터리 `common/session_data/`에 고객 단위
파일로 저장한다. 직원 발화 원문 등 개인정보에 준하는 내용이 들어갈 수 있어 `.gitignore`로
저장소에서 제외한다.

consult_agent 는 매 턴 `append_turn()`으로 기록만 하고, strategy_agent 는 브리핑 화면의
"상담 이력"(REQUIREMENTS.md §14)을 위해 `summarize_for_briefing()`으로 읽기만 한다 — 어느 쪽도
서로의 소유 데이터에 쓰지 않는다("코드=사실" 경계를 대화이력에도 그대로 적용).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pension_agent import config
from typing import Any, TypedDict

SESSION_DATA_DIR = config.SESSION_DATA_DIR


class Turn(TypedDict, total=False):
    """대화 한 턴. role: "user"|"agent"|"tool"|"correction"."""

    ts: str
    role: str
    text: str
    intent: str | None
    tool_calls: list[dict[str, Any]]


def _path(customer_id: str) -> Path:
    return SESSION_DATA_DIR / f"{customer_id}.json"


def _load(customer_id: str) -> dict[str, Any]:
    fp = _path(customer_id)
    if not fp.exists():
        return {"customer_id": customer_id, "sessions": []}
    return json.loads(fp.read_text(encoding="utf-8"))


def _save(customer_id: str, doc: dict[str, Any]) -> None:
    SESSION_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path(customer_id).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def append_turn(
    customer_id: str, session_id: str, turn: Turn, *, employee_id: str | None = None
) -> None:
    """세션에 턴 하나를 추가한다. session_id 가 처음 보는 값이면 세션을 새로 연다."""
    doc = _load(customer_id)
    turn = dict(turn)
    turn.setdefault("ts", datetime.now(UTC).isoformat())

    session = next((s for s in doc["sessions"] if s["session_id"] == session_id), None)
    if session is None:
        session = {
            "session_id": session_id,
            "started_at": turn["ts"],
            "employee_id": employee_id,
            "turns": [],
            "outcome": None,  # REQUIREMENTS.md §19(상담결과 반영)를 위한 예약 필드. 이번엔 채우지 않는다.
        }
        doc["sessions"].append(session)
    session["turns"].append(turn)
    _save(customer_id, doc)


def list_sessions(customer_id: str) -> list[dict[str, Any]]:
    return _load(customer_id)["sessions"]


def summarize_for_briefing(customer_id: str, n: int = 3) -> list[str]:
    """AI브리핑 §14 "상담 이력"에 노출할 한 줄 요약 목록(최근 n건, 최신순).

    코드로 조립한 문자열이지 LLM 생성이 아니다 — 브리핑에 노출되는 모든 문장은 수치·사실을
    코드가 정한다는 원칙(§15-17)을 대화이력에도 동일하게 적용한다.
    """
    sessions = sorted(list_sessions(customer_id), key=lambda s: s["started_at"], reverse=True)
    lines = []
    for s in sessions[:n]:
        date = s["started_at"][:10]
        turns = s["turns"]
        # 과거 상담 기록(role=record)은 «무슨 얘기를 했는지» 자체가 내용이므로 그대로 싣는다.
        # 에이전트와 나눈 대화는 발화가 길고 여러 턴이라 건수로만 요약한다 — 화면 §14 는
        # 한 줄짜리 목록이고, 원문이 필요하면 대화형 history 도구가 발췌를 싣는다.
        record = next((t for t in turns if t.get("role") == "record"), None)
        if record:
            lines.append(f"{date} 상담 — {' '.join((record.get('text') or '').split())}")
        else:
            lines.append(f"{date} 상담 — {len(turns)}턴 진행")
    return lines
