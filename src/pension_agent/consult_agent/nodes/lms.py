"""LMS 화면 연계 명령 노드 — "그 문구로 LMS 보내줘" 를 발송 화면 연계 제안으로 잇는다.

**보내지 않는다.** 에이전트는 화면을 열어줄 뿐 작업을 대신 수행하지 않으므로(CLAUDE.md §10),
이 노드가 하는 일은 발송이 아니라 **제안**이다 — 직원이 승낙하면 다음 턴에서 발송 화면
URL 을 준다(nodes/act.py). 실제로 보낼지는 직원이 그 화면에서 정한다.

'그 문구'가 정확히 무엇을 가리키는지는 추적하지 않는다. 턴 기록에 답변 원문이 남게 된
뒤로도(state.Turn 의 answer — `last_answer` 도구가 읽는다) 그대로다 — 답변 하나에 대사가
여럿일 수 있고, 어느 문장이 고객에게 나갈지를 코드가 짐작해 발송 화면에 채우면 §10 의
게이트를 짐작으로 여는 셈이다. 이번 질문 안에 인용부호로 명시된 문구가 있으면 그것을 쓰고,
없으면 문구를 명시해달라고 되묻는다.
"""

from __future__ import annotations

import re
from typing import Any

from pension_agent.consult_agent import screens
from pension_agent.consult_agent.state import KB, AgentState

_QUOTE = re.compile(r"[\"'“”‘’](.+?)[\"'“”‘’]")


def lms_link(state: AgentState) -> dict[str, Any]:
    customer_id = state.get("customer_id")
    if not customer_id:
        return {
            "answer": "지금 조회 중인 고객을 찾을 수 없어요. 고객 화면을 먼저 열어주세요.",
            "sources": [],
        }

    m = _QUOTE.search(state["question"])
    message = m.group(1).strip() if m else None
    if not message:
        return {
            "answer": '어떤 문구를 보낼지 큰따옴표로 감싸서 알려주시겠어요? (예: "..." 로 LMS 보내줘)',
            "sources": [],
        }

    found = screens.lms_screen(KB)
    if not found:
        # 발송 화면이 어디인지 지식베이스에 없으면 링크를 만들지 않는다 — 없는 화면번호로
        # 링크를 만들면 직원이 엉뚱한 화면에서 작업하게 된다(§10).
        return {
            "answer": "발송 화면번호가 지식베이스에 없어서 연계를 만들지 못했어요. "
                      "단말에서 발송 화면을 직접 열어 주세요.",
            "sources": [],
        }

    number, card = found
    action = {"kind": "lms", "label": f"{number} 발송 화면 열기", "screen": number, "card": card,
              "params": {"customer_id": customer_id}, "message": message}
    return {
        # 링크는 화면만 연다(딥링크 파라미터는 scnNo·mode 뿐 — screens.py). 문구를 "채워"
        # 준다고 말하지 않는 이유다. 문구는 연계할 때 다시 실어 준다(act.py::_link).
        "answer": f'{number} 발송 화면을 열어드릴까요? (네 / 아니오)\n'
                  f"문구는 화면이 열리면 직접 넣으시면 되고, 보낼지는 그 화면에서 정하시면 돼요.",
        "sources": [], "pending_action": action,
    }
