"""WorkB(행내 직원용 작업툴) MCP 도구 어댑터 — 지금은 쪽지 발송 하나.

**같은 이름의 두 층이 있다.** 무엇을 보낼지(본문·제목·수신자 검증·결과 판정)는
`pension_agent/workb.py` 가 이미 갖고 있고, 여기는 그 아래 층이다 — **어떤 도구를 어떤
인자 이름으로 부르면 그 쪽지가 나가는가.** 층을 가른 이유는 위층이 MCP 를 몰라야 하기
때문이다: 그래야 테스트가 행내 패키지 없이 본문·마스킹·상한·결과 판정을 전부 검사할 수
있고, 발송 경로가 바뀌어도(다른 전송 수단, 스텁) 위층이 그대로 남는다.

    from pension_agent import mcp
    mcp.install()          # 앱 시작 시 1회 — 위층에 이 어댑터를 등록한다

━━ 새 WorkB 기능을 붙일 때 ━━
`send_memo` 옆에 함수 하나를 더한다 — `client.call("<도구 이름>", {...})` 을 부르고 결과를
그대로 돌려주면 된다. 연결·인증·재시도는 `client.py` 가 이미 한다. 다른 서버(사내 DB·
메일 등)의 기능이면 `servers.CATALOG` 에서 그 서버를 켜고 파일을 하나 새로 만든다 —
서버 하나에 어댑터 파일 하나가 규칙이다.

━━ 쓰지 않기로 한 도구 ━━
WorkB 에는 이름·부서로 사람을 찾아 보내는 도구(`search_emp_and_send_memo`)도 있지만
쓰지 않는다. 동명이인이 갈리는 자리를 검색에 맡기면 엉뚱한 사람의 받은편지함에 고객
정보가 남고, 그건 확인 절차로도 못 막는다(`consult_agent/CLAUDE.md` §10). 수신자는
직원이 적은 사번이거나 본인이다.
"""

from __future__ import annotations

import logging
from typing import Any

from pension_agent import workb as notes          # 위층 — 본문·수신자 검증·결과 판정
from pension_agent.mcp import client as mcp_client

log = logging.getLogger(__name__)

#: WorkB 쪽지 발송 도구의 이름과 인자 이름. 대문자는 행내 규격 그대로다.
TOOL = "send_memo"
ARG_RECIPIENT, ARG_TITLE, ARG_BODY = "RECIPIENT", "TITLE", "BODY"


async def send_memo(recipients: list[str], title: str, body: str,
                    *, emp_no: str | None = None) -> Any:
    """쪽지 한 통을 MCP 로 내보내고 **서버 응답을 그대로** 돌려준다.

    `pension_agent/workb.py::Sender` 의 모양 그대로라 `use_sender` 에 그대로 등록된다 —
    성공·실패 판정은 위층의 `parse_result` 가 한다(WorkB 는 실패를 어댑터 오류로 세우지
    않고 본문에 담아 보낸다).

    ━━ 행위 주체는 사번이다 ━━
    MCP-User-Key 에 사번이 들어가고 행내 감사 기록도 그 사번으로 남는다. 여기서 정하는
    것은 **누가 보내는가**이지 누가 받는가가 아니다 — 받는 사람은 위층이 이미 정했다
    (기본은 본인, 타인은 직원이 사번을 적었을 때만).

    그 값은 로그인 사번 → 발송을 감싼 블록이 세운 주체(`workb.acting`) → `WORKB_EMP_NO`
    환경변수 순으로 정해진다. 앞의 둘은 진입점이 받은 사번이 대화 상태(`employee_id`)를
    거쳐 여기까지 내려온 것이다(`graph.ask` → `nodes/act.py` → `tools.send_memo` →
    `workb.send_note`). **환경변수까지 떨어졌으면 경고를 남긴다** — 여러 직원이 쓰는
    배포에서 그 상태는 «전부 한 사람 이름으로 나간다»는 뜻이고, 화면에는 아무 표시도
    나지 않아 로그가 유일한 신호다.

    ━━ 재시도하지 않는다 ━━
    `idempotent=False` 다. 발송 호출이 타임아웃으로 실패했다는 것은 «안 나갔다»가 아니라
    «나갔는지 모른다»이고, 그대로 다시 부르면 같은 쪽지가 두 통 간다. 붙는 단계의 실패는
    `client.call` 이 알아서 다시 시도한다(부수효과가 없다).
    """
    ids = notes.validate_recipients(recipients)   # 문자열 하나를 리스트 대신 넘기는 것을 막는다
    sender = notes.employee_id(emp_no)
    if not sender:
        raise mcp_client.MCPUnavailable(
            "쪽지를 보낼 직원 사번이 없습니다 — 로그인 사번이 넘어오지 않았고 "
            f"{notes.EMP_NO_ENV} 환경변수도 비어 있습니다")
    if not ((emp_no or "").strip() or notes.acting_employee()):
        log.warning("쪽지를 %s 사번으로 보냅니다 — 로그인 사번이 넘어오지 않아 %s 환경변수로 "
                    "떨어졌습니다(여러 직원이 쓰는 배포면 전부 이 사번으로 나갑니다)",
                    sender, notes.EMP_NO_ENV)
    return await mcp_client.client_for(sender).call(
        TOOL, {ARG_RECIPIENT: ids, ARG_TITLE: title, ARG_BODY: body}, idempotent=False)


def install() -> bool:
    """위층(`pension_agent/workb.py`)의 발송 함수로 이 어댑터를 등록한다.

    설정이 없으면 **등록하지 않고 거짓을 돌려준다** — 등록해 두면 발송 시도가 예외로
    끝나는데, 등록하지 않으면 위층이 «미연결»로 답하고 본문만 만든다. 둘의 차이는 화면에
    뜨는 문구이고, 아직 붙이지 않은 환경(사외 개발 PC·테스트)에서는 뒤엣것이 맞다.
    """
    if not mcp_client.configured():
        log.info("MCP 미설정 — 쪽지 발송을 붙이지 않습니다(본문만 생성): %s",
                 ", ".join(mcp_client.settings().missing()))
        return False
    notes.use_sender(send_memo)
    log.info("MCP 쪽지 발송을 붙였습니다 — 도구 %s", TOOL)
    return True
