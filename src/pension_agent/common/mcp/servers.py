"""행내 MCP 서버 카탈로그 — 어디에 붙나.

한 곳에 모아 두는 이유는 **새 기능을 붙일 때 고칠 자리를 하나로 만들기 위해서다.** 지금
쓰는 것은 WorkB 쪽지 하나지만 행내 MCP 게이트웨이에는 서버가 여럿 붙어 있고(사내 DB·뉴스·
메일·통계·공공데이터·전자공시), 그중 하나를 쓰게 될 때 고치는 것은 **이 표 한 줄과 도구
어댑터 파일 하나**다. 연결·인증(`client.py`)은 건드리지 않는다.

━━ 표에 있다고 붙지 않는다 ━━
등록하는 것은 `MCP_SERVERS` 에 적힌 서버뿐이고 기본값은 `workb` 하나다. 쓰지도 않는
서버까지 등록하면 도구 목록을 읽을 때마다 그 서버에도 접속하게 되고, 행내 감사 기록에는
«이 에이전트가 그 서버에 붙었다»가 남는다. 나중에 «누가 왜 붙었나»를 되짚을 때 그 기록이
그대로 잡음이 된다.

━━ 설정이 모자란 서버는 빼고 이유를 남긴다 ━━
`sybase` 는 경로에 접속 id 가 하나 더 붙는다(`/sybase/{client_id}:{conn_id}`). 그 값이
비어 있는 채로 켜면 `:` 로 끝나는 주소가 만들어지고, 접속 실패는 «설정이 없다»가 아니라
«서버가 없다»로 보인다. 그래서 서버마다 «무엇이 더 있어야 켜지나»(`needs`)를 선언하고,
모자라면 그 서버만 빼고 경고를 남긴다 — 넣어 두면 도구 목록 읽기가 통째로 실패해서
멀쩡한 WorkB 까지 못 쓰게 된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Server:
    """MCP 서버 한 대.

    `label` 은 `MultiServerMCPClient` 에 등록되는 이름이고 행내 예제 표기를 그대로 쓴다 —
    도구 이름이 어느 서버에서 왔는지 로그·오류 메시지에서 그 이름으로 나타난다.
    """

    key: str                        # MCP_SERVERS 에 적는 이름 · 코드가 부르는 이름
    label: str                      # MultiServerMCPClient 등록 이름(행내 예제 표기)
    path: str                       # 경로 템플릿 — {client_id} · {conn_id}
    what: str                       # 무엇을 하는 서버인가(사람이 읽는 한 줄)
    needs: tuple[str, ...] = ()     # 이 서버에만 더 필요한 설정 이름
    transport: str = "sse"


#: 게이트웨이에 붙어 있는 서버들. **여기 있다고 켜지는 것이 아니다**(위 머리말).
#: 아직 안 써 본 서버는 경로만 적어 둔 상태이고, 쓰기로 하면 도구 어댑터를 함께 만든다.
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

#: 아무것도 적지 않았을 때 켜는 서버. 지금 이 저장소가 실제로 쓰는 것은 이것 하나다.
DEFAULT: tuple[str, ...] = ("workb",)

_BY_KEY = {s.key: s for s in CATALOG}


def parse(raw: str) -> tuple[str, ...]:
    """`MCP_SERVERS` 값(쉼표 목록)을 서버 이름들로. 비어 있으면 기본값.

    모르는 이름은 **버리고 경고를 남긴다.** 오타 하나로 기동이 죽으면 안 되지만, 조용히
    버리면 «켠 줄 알았는데 안 켜진» 상태가 된다.
    """
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
    """`MultiServerMCPClient` 에 넘길 서버 목록.

    설정이 모자란 서버는 빼고 경고를 남긴다(머리말) — 넣어 두면 도구 목록 읽기가 통째로
    실패해서, 설정이 멀쩡한 서버까지 함께 못 쓰게 된다.
    """
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
