"""WorkB 쪽지 명령 노드 — "오늘 타겟 고객 쪽지로 보내줘" 를 발송 제안으로 잇는다.

**여기서 보내지 않는다.** 이 노드가 하는 일은 «보낼 글을 만들고 승낙을 받는 것»까지이고,
실제 발송은 다음 턴의 승낙이 `act.confirm_action` 을 지날 때 일어난다 — 화면 연계가 쓰는
그 경로 그대로다(CLAUDE.md §10). 발송은 되돌릴 수 없으므로(루트 CLAUDE.md 5번) 승낙 없이
나가는 길이 없어야 한다.

━━ 고객 화면이 필요 없다 ━━
`lms_link` 는 «이 고객에게 보낼 문구»라 고객 화면을 전제하지만, 오늘의 타겟 고객 목록은
로스터 전체다. 열려 있는 고객과 무관하게 성립하므로 `_NEEDS_CUSTOMER` 부류가 아니다 —
고객 창을 안 띄운 아침에 제일 먼저 쓰는 기능이 이것이라 전제를 붙이면 쓸 수가 없다.

━━ 수신자는 직원 본인이다 ━━
코드가 정한다(`workb.employee_id`). LLM 이 대화에서 사번을 뽑아내게 두면 엉뚱한 사람에게
고객 목록이 나갈 수 있고, 그건 확인 절차로도 못 막는다 — 직원은 자기가 승낙한 게 누구 앞인지
읽지 않는다. 사번을 모르면 제안하지 않는다.

━━ 보낼 글을 그대로 보여주고 승낙받는다 ━━
무엇을 보내는지 모르고 누른 «네» 는 승낙이 아니다. 그래서 제안 턴이 본문을 그대로 싣고,
승낙 턴은 **그때 만든 그 본문**을 보낸다(다시 만들지 않는다 — 자정을 넘기면 날짜가 갈린다).
"""

from __future__ import annotations

from typing import Any

from pension_agent import workb
from pension_agent.consult_agent.state import AgentState


def workb_note(state: AgentState) -> dict[str, Any]:
    emp_no = workb.employee_id(state.get("employee_id"))
    if not emp_no:
        # 받을 사람을 모르는 채로 "보낼까요?" 를 묻지 않는다 — 승낙받을 대상이 없다.
        return {"answer": "쪽지를 받을 직원 사번을 알 수 없어요. 로그인 정보가 넘어오지 "
                          f"않았거나 {workb.EMP_NO_ENV} 가 설정되지 않았습니다.",
                "sources": []}

    note = workb.daily_targets_note()
    if not note.count:
        # 보낼 것이 없으면 제안하지 않는다. 빈 쪽지를 보낼지 묻는 것은 물을 값이 아니다.
        return {"answer": workb.EMPTY_BODY, "sources": []}

    cut = f" (본문에는 {note.shown}명까지 실립니다)" if note.truncated else ""
    action = {"kind": "workb_note",
              "label": f"오늘의 타겟 고객 {note.count}명을 본인({emp_no}) 앞으로 쪽지 발송",
              "recipients": [emp_no],
              # 승낙 턴이 다시 만들지 않도록 보낼 글을 그대로 남긴다(§10 "무엇을 하기로
              # 한 것인지는 제안한 턴이 남긴 것으로 정한다").
              "title": note.title, "body": note.body}
    return {"answer": f"{note.body}\n\n"
                      f"— 위 내용을 본인({emp_no}) 앞으로 쪽지 발송할까요?{cut} (네 / 아니오)",
            "sources": [], "pending_action": action}
