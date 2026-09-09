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
import logging
import re
# datetime.UTC 는 Python 3.11에서 생긴 별칭이다 — 3.10 로컬에서 임포트가 죽어
# 상담 세션 저장이 통째로 못 올라왔다. timezone.utc 는 같은 객체다.
from datetime import datetime, timezone
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


_log = logging.getLogger(__name__)

#: 파일 이름이 되는 고객 id 의 꼴. 비어 있거나 경로 문자가 섞이면 기록하지 않는다 —
#: 빈 값은 `session_data/.json` 이라는 주인 없는 파일을 만들었고(2026-09-09 실측),
#: `..`·`/` 는 저장 디렉터리 밖을 가리킨다.
_CUSTOMER_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def recordable(customer_id: str | None) -> bool:
    """이 고객 id 로 기록을 남길 수 있는가. 아니면 부르는 쪽이 건너뛴다."""
    return bool(customer_id) and bool(_CUSTOMER_ID.match(str(customer_id)))


def _path(customer_id: str) -> Path:
    return SESSION_DATA_DIR / f"{customer_id}.json"


def _load(customer_id: str) -> dict[str, Any]:
    fp = _path(customer_id)
    if not fp.exists():
        return {"customer_id": customer_id, "sessions": []}
    return json.loads(fp.read_text(encoding="utf-8"))


def scrub_text(text: str) -> str:
    """UTF-8 로 쓸 수 없는 문자(짝 없는 서로게이트)를 지운다.

    터미널 로케일이 UTF-8 이 아니면 readline 이 백스페이스에 한글 한 글자(3바이트) 중
    1바이트만 지우고, Python 은 남은 2바이트를 `surrogateescape` 로 받아 `input()` 은
    성공한다. 그 문자열은 프롬프트에도 실리고 여기까지 와서 **파일을 쓸 때** 죽는다 —
    답변은 이미 만들어진 뒤라 턴 전체가 사라진다(2026-09-08 행내 실측: 「이 고객 왜 타겟
    이야?」 뒤 `UnicodeEncodeError: surrogates not allowed`, 위치 261~262 = 질문 텍스트).
    바꿔 넣지 않고 지우는 이유는 그 바이트가 «지우려던 글자»의 잔여물이기 때문이다.
    """
    return text.encode("utf-8", "ignore").decode("utf-8")


def _save(customer_id: str, doc: dict[str, Any]) -> None:
    SESSION_DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 어떤 문자열이 들어와도 기록은 남아야 한다 — 입력 경계(REPL)가 이미 걸러 주지만
    # 이 파일이 마지막 방어선이다(scrub_text 주석). 필드를 가리지 않도록 직렬화 결과에
    # 바로 건다. 되돌릴 수 없게 지우는 것은 UTF-8 로 쓸 수 없는 문자뿐이다.
    data = json.dumps(doc, ensure_ascii=False, indent=2)
    _path(customer_id).write_bytes(data.encode("utf-8", "ignore"))


def append_turn(
    customer_id: str, session_id: str, turn: Turn, *, employee_id: str | None = None
) -> None:
    """세션에 턴 하나를 추가한다. session_id 가 처음 보는 값이면 세션을 새로 연다.

    고객 id 가 없거나 꼴이 아니면 **기록하지 않는다** — 주인 없는 파일을 만드는 것보다
    기록이 빠지는 편이 낫고, 그 사실은 경고로 남긴다(고객 화면 없이 연계가 실행된 경로다).
    """
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
            "outcome": None,  # REQUIREMENTS.md §19(상담결과 반영)를 위한 예약 필드. 이번엔 채우지 않는다.
        }
        doc["sessions"].append(session)
    session["turns"].append(turn)
    _save(customer_id, doc)


def drop_session(customer_id: str, session_id: str) -> bool:
    """세션 하나를 저장소에서 지운다. 지운 것이 있으면 True.

    **리허설이 자기 잔여물을 걷어내기 위한 자리다**(tests/debug/runner.py). 실 LLM
    리허설은 운영 진입점을 그대로 부르므로 턴마다 기록을 남기는데(§2), 그 기록이
    시연용 시드 세션과 **같은 파일에 섞인다** — 저장소에 커밋되는 픽스처라 리허설을
    한 번 돌 때마다 추적 파일이 더러워지고, 그대로 커밋되면 다음 시연에서 T5
    「지난번엔 무슨 얘기 했지?」가 리허설의 오류 문장을 «지난 상담»으로 읽는다.

    운영 경로는 이 함수를 부르지 않는다 — 상담 기록은 지우는 것이 아니다.
    """
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
