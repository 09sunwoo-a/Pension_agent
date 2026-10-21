"""행위 레지스트리 — 직원 승낙 뒤 코드가 실행하는 부수효과 함수(LLM 선택 없음).

고객 대상 발송은 미수행(발송 화면 문구 판정만). 행내 쪽지만 실제 발송.
스텁(`open_lms_screen`·`register_consult_note`)은 실연동 시 본문만 교체, 키·시그니처 유지.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable


from pension_agent.common.session_store import append_turn
from pension_agent.tools.customer_solution import support


def _match_asset(message: str) -> dict[str, Any] | None:
    """문구의 출처 안내 자산 조회. 열쇠는 url — 본문은 LLM 이 다시 써서 문구 대조는 빗나감."""
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
    """LMS 발송 화면 문구 채움 판정(발송 없음). dummy 자산 문구 거부 — 채우면 그대로 발송 가능."""
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
    """상담 이력 등록(스텁) — 세션 저장소 기록, 다음 브리핑 상담 이력에 반영."""
    result = {"status": "stubbed", "detail": "상담 이력을 세션 기록에 남겼습니다", "note": note}
    append_turn(customer_id, session_id, {
        "role": "note", "text": note,
        "tool_calls": [{"name": "register_consult_note", "args": {"note": note}, "result": result}],
    })
    return result


#: 쪽지 받는 사람 기본 표기(직원 본인). 타 직원은 사번 명시 시에만.
MEMO_DEFAULT_TO = "본인"

#: HTML 태그 제거용 — 미제거 시 `&amp;` 때문에 url 대조 실패, 게이트가 조용히 열림
_TAG = re.compile(r"<[^>]+>")


def _plain(markup: str) -> str:
    return html.unescape(_TAG.sub(" ", markup or ""))


def send_memo(customer_id: str, text: str, *, title: str,
              recipients: list[str] | None = None, to: str = MEMO_DEFAULT_TO,
              as_employee: str | None = None,
              session_id: str = "tool-log") -> dict[str, Any]:
    """행내 WorkB 쪽지 발송(되돌릴 수 없음 — 승낙 뒤에만 호출). 본문·제목 수정 없음.

    - `as_employee`: 발신 사번(감사 기록용). 받는 사람은 `recipients`
    - dummy 자산 문구 거부 — 본인 수신도 동일 적용
    - `not_connected`·WorkB `success: false` 는 성공 아님
    """
    from pension_agent.common import note  # noqa: PLC0415 — strategy_agent 임포트를 지연시킨다

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
        result = {**note.send_note_sync(ids, note.Note(title=title, body=text),
                                         as_employee=as_employee), "to": to}
    append_turn(customer_id, session_id, {
        "role": "tool",
        "text": f"[쪽지 발송 · {to}] {title}",
        "tool_calls": [{"name": "send_memo",
                        "args": {"to": to, "recipients": ids, "title": title, "text": text},
                        "result": result}],
    })
    return result


def _dummy_block(text: str) -> dict[str, Any] | None:
    """더미 콘텐츠가 실린 쪽지면 거부 결과, 아니면 None — 사번·이름 발송이 같은 판정을 쓴다."""
    asset = _match_asset(_plain(text))
    if asset is not None and asset.get("dummy"):
        return {"status": "blocked",
                "detail": ("더미 콘텐츠가 실린 쪽지는 보낼 수 없습니다 — 실제 콘텐츠로 교체한 뒤"
                           "(자산의 dummy 표시 제거) 다시 시도하세요"),
                "asset_id": asset.get("id")}
    return None


def send_memo_by_name(customer_id: str, text: str, *, title: str, user_name: str,
                      group_name: str = "", to: str = "", as_employee: str | None = None,
                      session_id: str = "tool-log") -> dict[str, Any]:
    """이름(·부서)으로 찾아 쪽지 발송. 1명이면 발송, 결과 `sent`·`candidates`·`not_found`·실패."""
    from pension_agent.common import note  # noqa: PLC0415

    blocked = _dummy_block(text)
    if blocked is not None:
        result: dict[str, Any] = {**blocked, "to": to, "user_name": user_name, "title": title}
    else:
        result = {**note.send_note_by_name_sync(user_name, group_name or None,
                                                note.Note(title=title, body=text),
                                                as_employee=as_employee), "to": to}
    append_turn(customer_id, session_id, {
        "role": "tool",
        "text": f"[쪽지 발송(이름) · {to}] {title}",
        "tool_calls": [{"name": "send_memo_by_name",
                        "args": {"to": to, "user_name": user_name, "group_name": group_name or None,
                                 "title": title, "text": text},
                        "result": result}],
    })
    return result


# 예약 쪽지 — 실행기만 교체 가능(`SCHEDULERS`). 실행기 미정 시 발송 금지(즉시 발송으로 대체 금지)

#: 예약 실행기 환경변수. 빈 값은 `demo`(접수 즉시 발송), `off` 는 미연결.
SCHEDULER_ENV = "PENSION_MEMO_SCHEDULER"
SCHEDULER_DEFAULT = "demo"
SCHEDULER_OFF = "off"


def _demo_scheduler(customer_id: str, text: str, *, send_at: str, **kw: Any) -> dict[str, Any]:
    """시연용 실행기 — 접수 즉시 발송. 발송 실패 시 결과 그대로 반환."""
    user_name, group_name = kw.pop("user_name", ""), kw.pop("group_name", "")
    if user_name:
        kw.pop("recipients", None)
        sent = send_memo_by_name(customer_id, text, user_name=user_name, group_name=group_name,
                                 **kw)
    else:
        sent = send_memo(customer_id, text, **kw)
    if sent.get("status") not in ("sent", "stubbed"):
        return sent
    return {**sent, "status": "scheduled", "executor": "demo", "send_at": send_at,
            "detail": "시연 실행기: 예약 접수와 동시에 즉시 발송"}


#: 예약 실행기 표(키 = `SCHEDULER_ENV` 값).
SCHEDULERS: dict[str, Callable[..., dict[str, Any]]] = {
    "demo": _demo_scheduler,
}


def scheduler_name() -> str:
    """예약 실행기 이름. `off`·표에 없는 값(오타 포함)은 ""(미연결) — 기본값으로 대체 금지."""
    import os  # noqa: PLC0415

    name = (os.getenv(SCHEDULER_ENV) or "").strip().lower() or SCHEDULER_DEFAULT
    return name if name in SCHEDULERS else ""


def schedule_memo(customer_id: str, text: str, *, send_at: str, send_label: str, title: str,
                  recipients: list[str] | None = None, to: str = MEMO_DEFAULT_TO,
                  user_name: str = "", group_name: str = "",
                  as_employee: str | None = None,
                  session_id: str = "tool-log") -> dict[str, Any]:
    """WorkB 쪽지 `send_at`(ISO) 예약. 실행기 없으면 `not_connected`, 접수 사실은 상담이력에 기록.

    이름 예약(`user_name`)은 `demo` 실행기만 지원 — 실제 백엔드 규칙 미정(동명이인 선택 불가).
    """
    name = scheduler_name()
    if not name:
        result: dict[str, Any] = {
            "status": "not_connected", "to": to, "recipients": list(recipients or []),
            "detail": f"예약 실행기가 정해지지 않았습니다({SCHEDULER_ENV})"}
    else:
        result = SCHEDULERS[name](customer_id, text, send_at=send_at, title=title,
                                  recipients=recipients, to=to, as_employee=as_employee,
                                  user_name=user_name, group_name=group_name,
                                  session_id=session_id)
    append_turn(customer_id, session_id, {
        "role": "tool",
        "text": f"[쪽지 예약 · {send_label} · {to}] {title}"
                + (" (시연 실행기: 즉시 발송)" if name == "demo" else ""),
        "tool_calls": [{"name": "schedule_memo",
                        "args": {"to": to, "recipients": list(recipients or []), "title": title,
                                 "user_name": user_name or None, "group_name": group_name or None,
                                 "send_at": send_at, "executor": name or None},
                        "result": {k: v for k, v in result.items() if k != "title"}}],
    })
    return result


ACTIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "open_lms_screen": open_lms_screen,
    "register_consult_note": register_consult_note,
    "send_memo": send_memo,
    "schedule_memo": schedule_memo,
    "send_memo_by_name": send_memo_by_name,
}
