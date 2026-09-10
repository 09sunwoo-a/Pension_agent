"""행내 MCP 연동 — 에이전트가 행내 시스템을 부르는 유일한 통로.

    from pension_agent import mcp

    mcp.install()                      # 앱 시작 시 1회(main.py · app.py)
    mcp.stats()                        # 지금 무엇이 붙어 있나 — /health 가 싣는다
    await mcp.client_for(emp_no).call("send_memo", {...})

━━ 세 겹으로 나눠 둔다 ━━
지금 실제로 쓰는 기능은 WorkB 쪽지 하나지만, 행내 MCP 게이트웨이에는 서버가 여럿 붙어
있다. 나중에 그중 하나를 쓰게 될 때 **무엇을 고치게 되는지**가 이 나눔의 이유다.

| 겹 | 무엇을 아나 | 새 기능을 붙일 때 |
|---|---|---|
| `servers.py` | 어느 주소에 어떤 서버가 있나 | 표에 한 줄 (이미 있으면 `MCP_SERVERS` 에 이름만) |
| `client.py`  | 어떻게 붙나 · 어떻게 부르나 · 언제 다시 붙나 | **고치지 않는다** |
| `workb.py` 등 | 그 서버의 도구 이름과 인자 이름, 결과를 누가 판정하나 | 함수 하나(같은 서버) 또는 파일 하나(새 서버) |

도구가 무엇을 돌려주는지는 `client.py` 가 모른다 — 결과 판정은 그 도구를 아는 쪽이 한다.
쪽지의 판정은 이미 `pension_agent/workb.py::parse_result` 가 갖고 있다.

━━ 설정이 없으면 아무것도 하지 않는다 ━━
`install()` 은 MCP 설정(`MCP_SERVER_URL`·`MCP_USER_ID`·`MCP_SECRET_KEY`)이 없으면 붙이지
않고 거짓을 돌려준다. 그러면 쪽지는 **보내지 않고 «미연결»로 답한다**(본문만 만든다) —
붙지 않은 것을 조용히 성공처럼 끝내는 경로는 두지 않는다(루트 CLAUDE.md 규칙 5).
행내 패키지(`mcp_sdk`·`langchain_mcp_adapters`)도 저장소 밖이라 임포트 시점에 들여오지
않는다 — 테스트는 그 패키지 없이 그대로 돈다.
"""

from __future__ import annotations

import logging

from pension_agent.mcp.client import (  # noqa: F401 — 공개 표면
    MCPCallError,
    MCPClient,
    MCPError,
    MCPUnavailable,
    Settings,
    client_for,
    configured,
    reset,
    settings,
    stats,
    use_backend,
)

log = logging.getLogger(__name__)

__all__ = [
    "MCPCallError", "MCPClient", "MCPError", "MCPUnavailable", "Settings",
    "client_for", "configured", "install", "reset", "settings", "stats", "use_backend",
]


def install() -> bool:
    """행내 MCP 를 이 프로세스에 붙인다(앱 시작 시 1회). 붙였으면 참.

    지금 붙이는 것은 WorkB 쪽지 발송 하나다 — 서버가 늘면 여기에 어댑터의 `install()` 을
    한 줄씩 더한다(진입점은 이 함수 하나만 부른다).

    어댑터 임포트는 **함수 안에서** 한다. 어댑터가 `pension_agent/workb.py` 를 들여오고
    그것이 다시 `strategy_agent` 를 통째로 끌고 오기 때문이다 — 이 패키지를 설정 확인용
    으로만 임포트한 자리(`/health`)까지 그 비용을 물면 안 된다.
    """
    from pension_agent.mcp import workb  # noqa: PLC0415 — 위 주석

    return workb.install()
