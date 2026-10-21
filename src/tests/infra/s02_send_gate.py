"""actions — 발송 화면 연계 게이트(발송 미수행, 세션이력에만 기록).

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)
"""

from __future__ import annotations

from tests.infra._common import _clean_session_data, actions, check, session_store


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
# 쪽지는 예외 — 직원 본인에게 가는 내부 기록, 승낙 뒤에만 호출(act.confirm_action)
check("send_memo" in actions.ACTIONS,
      "actions.ACTIONS: send_memo 등록됨 (직원 본인 쪽지 — 대외 발송이 아니다)")

_clean_session_data()
