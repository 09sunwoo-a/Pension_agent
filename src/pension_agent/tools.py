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
    아래 게이트가 막아야 하는데, 게이트는 자산을 받아야 판정할 수 있다.

    **되짚는 열쇠는 안내 링크(url)다.** 문구의 가운데 본문은 고객마다 LLM 이 다시 쓰지만
    (agent._write_lms_messages) 링크는 코드가 조립하는 골격에 있어 바뀌지 않는다
    (support/outreach.py::lms_frame). 예전에는 자산의 고정 문구와 앞부분을 대조했는데,
    문구가 고객별 생성으로 바뀌면서 그 대조는 **LLM 이 문장을 다듬을수록 빗나갔다** —
    게이트가 조용히 열리는 방향의 실패다. 링크가 없는 옛 자산을 위해 문구 대조도 남긴다.
    """
    from pension_agent.strategy_agent import support  # noqa: PLC0415 — 순환 임포트 회피

    norm = " ".join(message.split())
    for a in support.ASSETS:
        url = (a.get("url") or "").strip()
        if url and url in norm:
            return a
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


#: 쪽지의 기본 수신인. 직원이 받는 사람을 말하지 않으면 **본인 쪽지함**이다 — 상담 요약을
#: 남기려는 요청이지 누군가에게 전달하려는 요청이 아니기 때문이다. 다른 직원에게 보내는
#: 것은 수신인 지정이 단말 연동 규격에 들어올 때 정한다(지금은 받지 않는다).
MEMO_DEFAULT_TO = "본인"


def send_memo(customer_id: str, text: str, *, to: str = MEMO_DEFAULT_TO,
              session_id: str = "tool-log") -> dict[str, Any]:
    """행내 쪽지 발송 — 상담 요약을 직원 본인의 쪽지함으로 보낸다. MCP 연동 전 스텁.

    **대외 행위가 아니다.** 받는 사람이 직원 자신(행내 메신저)이라 고객에게 나가는 것이
    없고, `register_consult_note` 와 같은 «내부 기록» 부류다 — 그래서 에이전트가 화면을
    열어 주는 데서 멈추지 않고 보내는 것까지 한다. 그래도 보내고 나면 되돌릴 수 없으므로
    (루트 CLAUDE.md 규칙 5) 부르는 쪽은 직원이 요약을 읽고 승낙한 뒤에만 부른다
    (`consult_agent/nodes/act.py::confirm_action`) — 요약 문장은 LLM 이 쓴 것이라 직원이
    보기 전에 나가면 안 된다.

    문구는 여기서 만들지도 고치지도 않는다. 받은 텍스트를 그대로 보낸다 — 검증을 통과한
    답변이 곧 쪽지 본문이고, 여기서 다듬으면 화면에 보여 준 것과 다른 쪽지가 나간다.
    """
    result = {"status": "stubbed",
              "detail": f"쪽지를 보냈습니다(받는 사람: {to}) — 연동 전이라 세션 기록에만 남깁니다",
              "to": to, "text": text}
    append_turn(customer_id, session_id, {
        "role": "tool",
        "text": f"[쪽지 발송 · {to}] {' '.join(text.split())[:50]}",
        "tool_calls": [{"name": "send_memo", "args": {"to": to, "text": text}, "result": result}],
    })
    return result


TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "open_lms_screen": open_lms_screen,
    "register_consult_note": register_consult_note,
    "send_memo": send_memo,
}
