"""actions — 발송 화면 연계 게이트 (발송하지 않는다, 세션이력에만 기록)

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from tests.infra._common import _clean_session_data, actions, check, session_store


# ─────────────────────────────────────────────────────────────
# actions — 발송 화면 연계 게이트 (발송하지 않는다, 세션이력에만 기록)
# ─────────────────────────────────────────────────────────────

result = actions.open_lms_screen("TEST02", "테스트 문구입니다")
check(result["status"] == "ok", "actions.open_lms_screen: 더미가 아니면 통과", str(result))
check(result["message"] == "테스트 문구입니다", "actions.open_lms_screen: 요청 문구 그대로 반환")

logged = session_store.list_sessions("TEST02")
check(len(logged) == 1 and logged[0]["turns"][0]["role"] == "tool",
      "actions.open_lms_screen: 호출 사실이 세션이력에 tool 턴으로 기록됨")
check("open_lms_screen" in actions.ACTIONS,
      "actions.ACTIONS: open_lms_screen 등록됨")
check("send_lms" not in actions.ACTIONS,
      "actions.ACTIONS: 발송 수행 도구는 남아 있지 않음 (§10 — 화면만 연다)")
# 쪽지는 예외다 — 고객이 아니라 직원 본인에게 가는 행내 메모라 대외 행위가 아니고,
# register_consult_note 와 같은 «내부 기록» 부류다. 승낙 뒤에만 불린다(act.confirm_action).
check("send_memo" in actions.ACTIONS,
      "actions.ACTIONS: send_memo 등록됨 (직원 본인 쪽지 — 대외 발송이 아니다)")

_clean_session_data()
