"""LMS 발송 명령 노드 — "그 문구로 LMS 보내줘" 같은 대화형 명령을 common.tools.send_lms 로
연결한다.

지금은 '그 문구'가 정확히 무엇을 가리키는지 정교하게 추적하지 않는다 — history 는 답변
원문을 들고 있지 않기 때문에(프롬프트 비용 억제, router.py 의 Turn 참고) 이전 턴에서 실제로
보여준 문구를 복원할 수 없다. 이번 질문 안에 인용부호로 명시된 문구가 있으면 그것을 보내고,
없으면 직원에게 문구를 명시해달라고 되묻는다 — 실제 MCP 연동 전까지는 이 정도로 충분하고,
답변 원문까지 세션이력에 남기게 되면(§14 와는 별개의 결정) 더 정교해질 수 있다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.tools import send_lms  # noqa: E402

from router import AgentState

_QUOTE = re.compile(r"[\"'“”‘’](.+?)[\"'“”‘’]")


def lms_send(state: AgentState) -> dict[str, Any]:
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

    result = send_lms(customer_id, message)
    return {"answer": f"{result['detail']} (문구: {message})", "sources": []}
