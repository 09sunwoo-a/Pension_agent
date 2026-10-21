"""LMS 화면 연계 노드 — «그 문구로 LMS 보내줘»를 발송 화면 연계 제안으로 연결. 발송 안 함.

문구는 질문 안 인용부호로 명시된 것만 사용. 없으면 되물음(답변 속 문장 추측 금지).
"""

from __future__ import annotations

import re
from typing import Any

from pension_agent.actions import screens
from pension_agent.state import KB, AgentState

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
        # 화면번호 미등록 시 링크 미생성 — 엉뚱한 화면 방지
        return {
            "answer": "발송 화면번호가 지식베이스에 없어서 연계를 만들지 못했어요. "
                      "단말에서 발송 화면을 직접 열어 주세요.",
            "sources": [],
        }

    number, card = found
    action = {"kind": "lms", "label": f"{number} 발송 화면 열기", "screen": number, "card": card,
              "params": {"customer_id": customer_id}, "message": message}
    return {
        # 딥링크는 scnNo·mode 뿐 — 문구를 «채워» 준다고 말하지 않음
        "answer": f'{number} 발송 화면을 열어드릴까요? (네 / 아니오)\n'
                  f"문구는 화면이 열리면 직접 넣으시면 되고, 보낼지는 그 화면에서 정하시면 돼요.",
        "sources": [], "pending_action": action,
    }
