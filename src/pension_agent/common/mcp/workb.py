"""WorkB(행내 직원용 작업툴) MCP 도구 어댑터 — 쪽지 발송(사번) · 이름으로 찾아 보내기.

본문·수신자 검증·결과 판정은 위층(note.py) 소유 — 여기는 도구·인자 이름만(위층은 MCP 무지).
`search_emp_and_send_memo`: 1명이면 즉시 발송, 여러 명이면 후보 목록, 0명이면 결과 없음.
"""

from __future__ import annotations

import logging
from typing import Any

from pension_agent.common import note
from pension_agent.common.mcp import client as mcp_client

log = logging.getLogger(__name__)

#: 쪽지 발송 도구·인자 이름(대문자는 행내 규격).
TOOL = "send_memo"
ARG_RECIPIENT, ARG_TITLE, ARG_BODY = "RECIPIENT", "TITLE", "BODY"


async def send_memo(recipients: list[str], title: str, body: str,
                    *, emp_no: str | None = None) -> Any:
    """쪽지 한 통 발송, 서버 응답 그대로 반환(판정은 note.parse_result).

    - 보내는 사번: 로그인 사번 → `note.acting` → `WORKB_EMP_NO`. 환경변수 폴백 시 경고 로그
    - 재시도 금지(`idempotent=False`) — 타임아웃은 «나갔는지 모름», 재호출 시 중복 발송
    """
    ids = note.validate_recipients(recipients)   # 문자열 단독 전달 차단
    sender = note.employee_id(emp_no)
    if not sender:
        raise mcp_client.MCPUnavailable(
            "쪽지를 보낼 직원 사번이 없습니다 — 로그인 사번이 넘어오지 않았고 "
            f"{note.EMP_NO_ENV} 환경변수도 비어 있습니다")
    if not ((emp_no or "").strip() or note.acting_employee()):
        log.warning("쪽지를 %s 사번으로 보냅니다 — 로그인 사번이 넘어오지 않아 %s 환경변수로 "
                    "떨어졌습니다(여러 직원이 쓰는 배포면 전부 이 사번으로 나갑니다)",
                    sender, note.EMP_NO_ENV)
    return await mcp_client.client_for(sender).call(
        TOOL, {ARG_RECIPIENT: ids, ARG_TITLE: title, ARG_BODY: body}, idempotent=False)


#: 이름 발송 도구·인자 이름. 소문자(명세 예시의 대문자는 서버가 거부).
#: 호출 시 도구가 밝힌 인자 이름(`_declared`)에 대소문자를 맞춤.
NAME_TOOL = "search_emp_and_send_memo"
ARG_NAME, ARG_GROUP, ARG_NAME_TITLE, ARG_NAME_BODY = "user_name", "group_name", "title", "body"


async def _declared(client: Any, tool: str, wanted: dict[str, Any]) -> dict[str, Any]:
    """`wanted` 키를 도구가 밝힌 인자 이름의 대소문자로 변환. 못 읽으면 그대로."""
    try:
        found = await client.tool(tool)
        declared = list((getattr(found, "args", None) or {}).keys())
    except Exception:                       # noqa: BLE001 — 이름 맞추기가 발송을 막지 않는다
        declared = []
    by_lower = {k.lower(): k for k in declared}
    return {by_lower.get(k.lower(), k): v for k, v in wanted.items()}


async def search_emp_and_send_memo(user_name: str, group_name: str | None, title: str,
                                   body: str, *, emp_no: str | None = None) -> Any:
    """이름(과 부서)으로 찾아 발송 — 1명이면 즉시 발송. 판정은 note.parse_name_result, 재시도 금지."""
    sender = note.employee_id(emp_no)
    if not sender:
        raise mcp_client.MCPUnavailable(
            "쪽지를 보낼 직원 사번이 없습니다 — 로그인 사번이 넘어오지 않았고 "
            f"{note.EMP_NO_ENV} 환경변수도 비어 있습니다")
    args: dict[str, Any] = {ARG_NAME: user_name, ARG_NAME_TITLE: title, ARG_NAME_BODY: body}
    if group_name:
        args[ARG_GROUP] = group_name
    client = mcp_client.client_for(sender)
    return await client.call(NAME_TOOL, await _declared(client, NAME_TOOL, args),
                             idempotent=False)


def install() -> bool:
    """note 에 발송 어댑터 등록. 설정·행내 패키지 중 하나라도 없으면 미등록(«미연결») 후 False.

    패키지 확인은 설정 확인 뒤 — `unavailable()` 이 패키지 임포트를 시도하므로.
    """
    if not mcp_client.configured():
        log.info("MCP 미설정 — 쪽지 발송을 붙이지 않습니다(본문만 생성): %s",
                 ", ".join(mcp_client.settings().missing()))
        return False
    reason = mcp_client.unavailable()
    if reason:
        log.warning("MCP 설정은 갖춰졌는데 행내 패키지가 없습니다 — 쪽지 발송을 붙이지 "
                    "않습니다(본문만 생성): %s. 배포 이미지라면 requirements.txt 의 "
                    "python-mcp-sdk · langchain-mcp-adapters 가 설치됐는지 봅니다", reason)
        return False
    note.use_sender(send_memo)
    note.use_name_sender(search_emp_and_send_memo)
    log.info("MCP 쪽지 발송을 붙였습니다 — 도구 %s · %s", TOOL, NAME_TOOL)
    return True
