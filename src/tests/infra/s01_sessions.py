"""session_store — 주인 없는 기록은 남기지 않는다

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from tests.infra._common import _clean_session_data, check, session_store


# ─────────────────────────────────────────────────────────────
# session_store — 주인 없는 기록은 남기지 않는다
import logging as _lg0  # noqa: E402
_skip_records: list = []


class _SkipCapture(_lg0.Handler):
    def emit(self, record):
        _skip_records.append(record)


_ss_log = _lg0.getLogger("pension_agent.session_store")
_ss_log.addHandler(_SkipCapture())
_ss_log.setLevel(_lg0.INFO)
try:
    for bad in ("", "../x", "a/b", None):
        session_store.append_turn(bad, "sess-x", {"role": "tool", "text": "연계"})
    check(not (session_store.SESSION_DATA_DIR / ".json").exists()
          and not (session_store.SESSION_DATA_DIR / "..").with_suffix(".json").exists(),
          "session_store: 고객 id 가 비면 session_data/.json 을 만들지 않는다")
    check(len(_skip_records) == 4 and all(r.levelno == _lg0.WARNING for r in _skip_records)
          and "건너뜀" in _skip_records[0].getMessage(),
          "session_store: 건너뛴 기록은 WARNING 으로 남는다", str([r.getMessage() for r in _skip_records][:1]))
    check(session_store.recordable("198734-1205842") and not session_store.recordable("a b"),
          "session_store: recordable 이 고객 id 꼴을 가른다")
finally:
    _ss_log.handlers.clear()

# session_store — 왕복 테스트
# ─────────────────────────────────────────────────────────────

_clean_session_data()
session_store.append_turn("TEST01", "sess-a", {"role": "user", "text": "질문1"}, employee_id="E01")
session_store.append_turn("TEST01", "sess-a", {"role": "agent", "text": "답변1"})
session_store.append_turn("TEST01", "sess-b", {"role": "user", "text": "질문2"})

sessions = session_store.list_sessions("TEST01")
check(len(sessions) == 2, "session_store: 세션 2개(sess-a·sess-b) 기록됨", str(len(sessions)))
sess_a = next(s for s in sessions if s["session_id"] == "sess-a")
check(len(sess_a["turns"]) == 2, "session_store: sess-a 에 턴 2개", str(len(sess_a["turns"])))
check(sess_a["employee_id"] == "E01", "session_store: employee_id 보존")
check(all("ts" in t for t in sess_a["turns"]), "session_store: 각 턴에 ts 자동 부여")

summary = session_store.summarize_for_briefing("TEST01")
check(len(summary) == 2, "session_store: summarize_for_briefing 최신순 2건", str(summary))

empty_summary = session_store.summarize_for_briefing("NO_SUCH_CUSTOMER")
check(empty_summary == [], "session_store: 없는 고객은 빈 목록(에러 아님)")

# UTF-8 로 쓸 수 없는 문자가 섞여도 기록은 남는다 — 행내 터미널(로케일 비 UTF-8)에서
# 백스페이스가 한글 한 글자 중 1바이트만 지우면 남은 2바이트가 surrogateescape 로 들어와
# `input()` 은 성공하고 파일 쓰기에서 죽었다(2026-09-08 실측 — 답변까지 만든 턴이 통째로).
_broken = "이 고객 왜 타겟".encode("utf-8")[:-1].decode("utf-8", "surrogateescape") + " 이야?"
check(session_store.scrub_text(_broken) == "이 고객 왜 타 이야?",
      "session_store.scrub_text: 반쪽 바이트(서로게이트)를 지운다", repr(session_store.scrub_text(_broken)))
try:
    session_store.append_turn("TEST01", "sess-c", {"role": "user", "text": _broken})
    _saved_ok = True
except UnicodeEncodeError:
    _saved_ok = False
check(_saved_ok, "session_store: 서로게이트가 섞인 텍스트로도 저장이 죽지 않는다")
_sess_c = next(s for s in session_store.list_sessions("TEST01") if s["session_id"] == "sess-c")
check(_sess_c["turns"][0]["text"] == "이 고객 왜 타 이야?",
      "session_store: 저장 뒤 다시 읽힌다(깨진 바이트만 빠진다)", repr(_sess_c["turns"][0]["text"]))

_clean_session_data()
