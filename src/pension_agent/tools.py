"""도구(tool) 레지스트리 — 대화형 에이전트(consult_agent)가 부르는 부수효과 함수들.

MCP 연동 전 스텁이고, 호출 사실만 세션이력에 남긴다. MCP 도구가 준비되면 함수 **본문만**
실제 클라이언트 호출로 교체하면 된다 — 레지스트리 키와 시그니처는 그대로 유지하므로,
이 함수를 부르는 노드 쪽은 변경할 필요가 없다.

━━ 발송하지 않는다 ━━
예전에는 `send_lms` 가 "발송"이었다(스텁이지만 이름과 규약이 그랬다). 지금 에이전트는
**화면을 열어줄 뿐 작업을 대신 수행하지 않는다**(consult_agent/CLAUDE.md §10) — 문자를
보낼지는 직원이 발송 화면에서 정한다. 그래서 남은 것은 발송이 아니라 **그 화면에 문구를
채워도 되는지의 판정**이다.
"""

from __future__ import annotations

from typing import Any, Callable


from pension_agent.session_store import append_turn


def _match_asset(message: str) -> dict[str, Any] | None:
    """이 문구가 어느 안내 콘텐츠에서 온 것인지 되짚는다.

    직원은 브리핑 ⑨ 에 뜬 문구를 그대로 복사해 온다. 그 문구가 더미 콘텐츠에서 왔다면
    아래 게이트가 막아야 하는데, 게이트는 자산을 받아야 판정할 수 있다. LLM 이 문구를
    다듬었을 수 있으므로 완전일치만 보지 않고 앞부분 일치도 함께 본다.
    """
    from pension_agent.strategy_agent import support  # noqa: PLC0415 — 순환 임포트 회피

    norm = " ".join(message.split())
    for a in support.ASSETS:
        base = " ".join((a.get("lms_message") or "").split())
        if not base:
            continue
        head = base[:24]
        if norm == base or (head and (norm.startswith(head) or base.startswith(norm[:24]))):
            return a
    return None


def open_lms_screen(customer_id: str, message: str,
                    *, session_id: str = "tool-log") -> dict[str, Any]:
    """LMS 발송 화면에 문구를 채워도 되는지 판정한다. 발송은 하지 않는다.

    **더미 게이트.** 문구가 더미 콘텐츠(`dummy: true`)에서 온 것이면 거부한다. 채워
    넣으면 직원이 그 화면에서 **그대로 보낼 수 있기 때문**이다 — 지어낸 일정을 담은
    문구가 고객 문자로 나가는 것과 한 걸음 차이다.

    예전에는 발송 문구 앞에 `[더미] ` 접두를 붙여 표시했는데, 데모 산출물에 딱지가 남는 게
    싫다는 결정으로 접두를 뗐다. 접두를 그냥 떼면 보호막이 사라지므로 **표시를 지우는 대신
    게이트로 옮겼다.** 접두는 LLM 이 문구를 다시 쓰면서 지울 수 있지만, 이 판정은 못 지운다.

    실제 콘텐츠로 교체할 때는 자산의 `dummy` 를 지우면 이 게이트가 자동으로 열린다.
    """
    asset = _match_asset(message)
    if asset is not None and asset.get("dummy"):
        result = {
            "status": "blocked",
            "detail": ("더미 콘텐츠는 발송 화면에 채울 수 없습니다 — 실제 콘텐츠로 교체한 뒤"
                       "(자산의 dummy 표시 제거) 다시 시도하세요"),
            "asset_id": asset.get("id"),
            "message": message,
        }
        append_turn(customer_id, session_id, {
            "role": "tool",
            "text": f"[발송 화면 연계 차단] 더미 콘텐츠 {asset.get('id')}",
            "tool_calls": [
                {"name": "open_lms_screen", "args": {"message": message}, "result": result},
            ],
        })
        return result

    result = {"status": "ok", "detail": "발송 화면에 채울 수 있는 문구입니다", "message": message}
    append_turn(customer_id, session_id, {
        "role": "tool",
        "text": f"[발송 화면 연계] {message[:50]}",
        "tool_calls": [
            {"name": "open_lms_screen", "args": {"message": message}, "result": result},
        ],
    })
    return result


def register_consult_note(customer_id: str, note: str,
                          *, session_id: str = "tool-log") -> dict[str, Any]:
    """상담 이력 등록 — 화면 상단의 '상담 이력 등록' 버튼(REQUIREMENTS.md §14)에 대응하는 도구.

    대외 행위가 아니라 내부 기록이라 스텁이어도 실제 동작에 가깝다 — 세션 저장소에 남기면
    다음 브리핑의 '상담 이력' 섹션에 그대로 나타난다. 정식 CRM 연동이 붙으면 본문만 교체한다.
    """
    result = {"status": "stubbed", "detail": "상담 이력을 세션 기록에 남겼습니다", "note": note}
    append_turn(customer_id, session_id, {
        "role": "note", "text": note,
        "tool_calls": [{"name": "register_consult_note", "args": {"note": note}, "result": result}],
    })
    return result


TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "open_lms_screen": open_lms_screen,
    "register_consult_note": register_consult_note,
}
