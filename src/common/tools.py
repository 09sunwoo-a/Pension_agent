"""도구(tool) 레지스트리 — 대화형 에이전트(consult_agent)가 부르는 부수효과 함수들.

지금은 send_lms 하나뿐이고, 실제 발송 없이 스텁으로 응답하며 호출 사실만 세션이력에 남긴다
(LMS MCP 연동 전). MCP 도구가 준비되면 함수 **본문만** 실제 클라이언트 호출로 교체하면
된다 — 레지스트리 키와 시그니처는 그대로 유지하므로, 이 함수를 부르는 라우터·노드 쪽은
변경할 필요가 없다. 이것이 "나중에 갈아끼우기 쉬운" 확장 지점의 핵심이다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.session_store import append_turn  # noqa: E402


def send_lms(customer_id: str, message: str, phone: str | None = None,
             *, session_id: str = "tool-log") -> dict[str, Any]:
    """LMS 발송 — MCP 연동 전 스텁. 실제 발송은 하지 않고 호출 사실만 세션이력에 기록한다."""
    result = {
        "status": "stubbed",
        "detail": "LMS 발송 MCP 연동 전 — 실제 발송 없음",
        "message": message,
        "phone": phone,
    }
    append_turn(customer_id, session_id, {
        "role": "tool",
        "text": f"[send_lms 호출] {message[:50]}",
        "tool_calls": [
            {"name": "send_lms", "args": {"message": message, "phone": phone}, "result": result},
        ],
    })
    return result


TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "send_lms": send_lms,
}
