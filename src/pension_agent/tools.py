"""도구(tool) 레지스트리 — 대화형 에이전트(consult_agent)가 부르는 부수효과 함수들.

`open_lms_screen`·`register_consult_note` 는 아직 스텁이고 호출 사실만 세션이력에 남긴다.
단말·CRM 연동이 붙으면 함수 **본문만** 교체한다 — 레지스트리 키와 시그니처는 그대로라
부르는 노드 쪽은 바뀌지 않는다. `send_memo` 는 스텁이 아니다(아래).

━━ 고객에게는 발송하지 않는다 ━━
예전에는 `send_lms` 가 "발송"이었다(스텁이지만 이름과 규약이 그랬다). 지금 에이전트는
**고객에게 나가는 것은 화면을 열어줄 뿐 대신 수행하지 않는다**(consult_agent/CLAUDE.md §10)
— 문자를 보낼지는 직원이 발송 화면에서 정한다. 그래서 남은 것은 발송이 아니라 **그 화면에
문구를 채워도 되는지의 판정**이다.

**행내 쪽지(`send_memo`)만 예외이고, 그것이 예외인 이유가 경계 그 자체다** — 받는 사람이
고객이 아니라 행원이라 대외 행위가 아니다. 그래도 보내고 나면 되돌릴 수 없으므로(루트
규칙 5) 승낙 없이 나가는 길이 없고, 더미 게이트도 여기 그대로 있다.
"""

from __future__ import annotations

import html
import re
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


#: 쪽지의 받는 사람을 화면에 밝히는 기본 표기. 기본은 **직원 본인**이다 — 상담을 정리해
#: 남기려는 요청이지 누군가에게 전달하려는 요청이 아니기 때문이다. 다른 직원에게 보내는
#: 것은 직원이 **사번을 적었을 때만**이다(`consult_agent/nodes/act.py::employee_no`).
MEMO_DEFAULT_TO = "본인"

#: 쪽지 본문에서 안내 링크를 되짚기 위해 걷어내는 것 — HTML 태그와 실체참조. 본문이 HTML 이
#: 된 뒤로 링크의 `&` 가 `&amp;` 로 적히는데, 그대로 대조하면 게이트가 **조용히 열린다**.
_TAG = re.compile(r"<[^>]+>")


def _plain(markup: str) -> str:
    return html.unescape(_TAG.sub(" ", markup or ""))


def send_memo(customer_id: str, text: str, *, title: str,
              recipients: list[str] | None = None, to: str = MEMO_DEFAULT_TO,
              session_id: str = "tool-log") -> dict[str, Any]:
    """행내 WorkB 쪽지 발송. 본문·제목은 여기서 만들지도 고치지도 않는다.

    **되돌릴 수 없는 행위다**(루트 CLAUDE.md 규칙 5). 부르는 쪽은 직원이 초안을 읽고
    승낙한 뒤에만 부른다(`consult_agent/nodes/act.py::confirm_action`) — 본문은 LLM 이 쓴
    글이라 직원이 보기 전에 나가면 무엇이 나갔는지 아무도 모른다.

    ━━ 더미 게이트 ━━
    본문이 아직 실제 콘텐츠로 확정되지 않은 안내 문구(`dummy: true`)에서 왔으면 거부한다.
    `open_lms_screen` 과 같은 판정이고, 쪽지에도 필요한 이유는 **받는 사람이 다른 직원일 수
    있기 때문**이다 — 지어낸 일정이 적힌 쪽지를 받은 행원이 그것을 고객에게 옮기는 것은 한
    걸음 차이다. 본인에게 보내는 쪽지에도 같게 적용한다: 갈래를 나누면 그 분기가 곧 구멍이 된다.

    ━━ 「판정 못 함」을 성공으로 접지 않는다 ━━
    WorkB 는 실패를 `isError` 로 세우지 않고 본문에 `{"success": false, ...}` 로 담아
    보낸다(`workb.parse_result`). 그래서 어댑터가 성공이라고 한 것만 보고 보고하면 거부당한
    호출이 «발송 완료»로 화면에 뜬다. 클라이언트가 아직 주입되지 않았으면 `not_connected`
    이고, 그것도 성공이 아니다 — 부르는 쪽이 「보냈어요」라고 말하지 않는다.
    """
    from pension_agent import workb  # noqa: PLC0415 — strategy_agent 임포트를 지연시킨다

    ids = [r for r in (recipients or []) if r]
    asset = _match_asset(_plain(text))
    if asset is not None and asset.get("dummy"):
        result: dict[str, Any] = {
            "status": "blocked",
            "detail": ("더미 콘텐츠가 실린 쪽지는 보낼 수 없습니다 — 실제 콘텐츠로 교체한 뒤"
                       "(자산의 dummy 표시 제거) 다시 시도하세요"),
            "asset_id": asset.get("id"), "to": to, "recipients": ids, "title": title,
        }
    elif not ids:
        result = {"status": "failed", "detail": "받는 사람 사번이 없습니다",
                  "to": to, "recipients": ids, "title": title}
    else:
        result = {**workb.send_note_sync(ids, workb.Note(title=title, body=text)), "to": to}
    append_turn(customer_id, session_id, {
        "role": "tool",
        "text": f"[쪽지 발송 · {to}] {title}",
        "tool_calls": [{"name": "send_memo",
                        "args": {"to": to, "recipients": ids, "title": title, "text": text},
                        "result": result}],
    })
    return result


TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "open_lms_screen": open_lms_screen,
    "register_consult_note": register_consult_note,
    "send_memo": send_memo,
}
