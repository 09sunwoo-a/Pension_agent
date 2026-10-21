"""행내 MCP 서버 카탈로그 — 서버별 주소 경로.

- 등록은 `MCP_SERVERS` 에 적힌 서버만(기본 workb). 미사용 서버 접속은 행내 감사 기록 잡음
- 서버별 추가 설정(`needs`)이 비면 그 서버만 제외 — 포함 시 도구 목록 읽기 전체 실패
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Server:
    """MCP 서버 한 대."""

    key: str                        # MCP_SERVERS 에 적는 이름 · 코드가 부르는 이름
    label: str                      # MultiServerMCPClient 등록 이름(행내 예제 표기)
    path: str                       # 경로 템플릿 — {client_id} · {conn_id}
    what: str                       # 서버 설명 한 줄
    needs: tuple[str, ...] = ()     # 이 서버에만 더 필요한 설정 이름
    transport: str = "sse"


#: 게이트웨이 서버 목록. 등록 여부는 MCP_SERVERS 가 결정.
CATALOG: tuple[Server, ...] = (
    Server("workb", "workb-mcp-server", "/workb/{client_id}",
           "행내 직원용 작업툴 — 쪽지 발송(send_memo)"),
    Server("sybase", "sybase-mcp-server", "/sybase/{client_id}:{conn_id}",
           "사내 DB 조회(SybaseIQ) — sybaseiq_query", needs=("MCP_CONN_ID",)),
    Server("news", "news-mcp-server", "/search-news/{client_id}", "뉴스 검색"),
    Server("mail", "mail-mcp-server", "/mail/{client_id}", "메일 발송"),
    Server("kosis", "kosis-mcp-server", "/kosis/{client_id}", "국가통계포털(KOSIS)"),
    Server("opendata", "opendata-mcp-server", "/opendata/{client_id}", "공공데이터포털"),
    Server("dart", "dart-mcp-server", "/opendart/{client_id}", "전자공시(OpenDART)"),
)

#: MCP_SERVERS 미지정 시 기본 서버.
DEFAULT: tuple[str, ...] = ("workb",)

_BY_KEY = {s.key: s for s in CATALOG}


def parse(raw: str) -> tuple[str, ...]:
    """`MCP_SERVERS`(쉼표 목록) → 서버 이름들. 비면 기본값. 모르는 이름은 경고 후 제외."""
    names = tuple(n.strip() for n in (raw or "").split(",") if n.strip())
    if not names:
        return DEFAULT
    known = tuple(n for n in names if n in _BY_KEY)
    unknown = [n for n in names if n not in _BY_KEY]
    if unknown:
        log.warning("MCP_SERVERS 에 모르는 서버 이름이 있습니다(무시) — %s · 아는 이름: %s",
                    ", ".join(unknown), ", ".join(_BY_KEY))
    return known


def endpoints(names: tuple[str, ...], *, base_url: str, client_id: str,
              conn_id: str = "", headers: dict[str, str] | None = None,
              ) -> dict[str, dict[str, Any]]:
    """`MultiServerMCPClient` 용 서버 목록. 설정 부족 서버는 경고 후 제외."""
    values = {"MCP_CONN_ID": conn_id}
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        server = _BY_KEY.get(name)
        if server is None:
            continue
        missing = [need for need in server.needs if not values.get(need)]
        if missing:
            log.warning("MCP 서버 %s 를 건너뜁니다 — 설정이 비어 있습니다: %s",
                        name, ", ".join(missing))
            continue
        path = server.path.format(client_id=client_id, conn_id=conn_id)
        out[server.label] = {
            "url": f"{base_url}{path}",
            "transport": server.transport,
            "headers": dict(headers or {}),
        }
    return out
