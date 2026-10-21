"""행내 MCP 연동 — 에이전트가 행내 시스템을 부르는 유일한 통로.

    from pension_agent import mcp

    mcp.install()                      # 앱 시작 시 1회(main.py · app.py)
    mcp.stats()                        # 연결 상태 (/health)
    await mcp.client_for(emp_no).call("send_memo", {...})

    servers.py   서버 주소 표 (새 서버: 한 줄 추가)
    client.py    연결·호출·재연결 (기능 추가 시 수정 없음)
    workb.py 등  서버별 도구·인자 이름 어댑터 (결과 판정은 도구를 아는 쪽 — 쪽지는 note.parse_result)

- MCP 설정·행내 패키지 중 하나라도 없으면 미설치 → 쪽지는 «미연결» 응답(성공 위장 금지)
- 행내 패키지는 임포트 시점 미로딩(테스트는 패키지 없이 실행)
"""

from __future__ import annotations

import logging

from pension_agent.common.mcp.client import (  # noqa: F401 — 공개 표면
    NOISY_LOGGERS,
    MCPCallError,
    MCPClient,
    MCPError,
    MCPUnavailable,
    Settings,
    client_for,
    configured,
    quiet_loggers,
    reset,
    settings,
    stats,
    unavailable,
    use_backend,
)

log = logging.getLogger(__name__)

__all__ = [
    "NOISY_LOGGERS", "MCPCallError", "MCPClient", "MCPError", "MCPUnavailable", "Settings",
    "client_for", "configured", "install", "quiet_loggers", "reset", "settings", "stats",
    "unavailable", "use_backend",
]


def install() -> bool:
    """행내 MCP 어댑터 등록(앱 시작 시 1회). 성공 시 True."""
    from pension_agent.common.mcp import workb  # noqa: PLC0415 — note→customer_solution 임포트 비용 회피

    return workb.install()
