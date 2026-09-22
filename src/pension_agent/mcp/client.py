"""행내 MCP 연결 — 인증 · 접속 · 도구 호출.

행내 MCP 게이트웨이에 붙어 등록된 도구(tool)를 부른다. 지금 실제로 부르는 것은 WorkB
쪽지 하나지만(`mcp/workb.py`), 이 파일은 **어느 도구인지 모른다** — 아는 것은 「어떻게
붙나 · 어떻게 부르나 · 언제 다시 붙나」뿐이다.

    from pension_agent import mcp

    raw = await mcp.client_for("3902172").call("send_memo", {...})

━━ 아는 것과 모르는 것 ━━
**결과 판정은 도구를 아는 쪽이 한다.** 여기서는 도구가 돌려준 것을 그대로 올려보낸다 —
WorkB 는 실패를 어댑터 오류로 세우지 않고 본문에 `{"success": false, ...}` 로 담아 보내는데
(`pension_agent/note.py::parse_result`), 그런 판정을 이 파일이 갖기 시작하면 서버가
늘 때마다 여기에 분기가 하나씩 붙고 결국 아무도 전체를 못 읽는다.

━━ 행내 패키지는 임포트 시점에 들여오지 않는다 ━━
`mcp_sdk` · `langchain_mcp_adapters` 는 저장소 밖 패키지다. 모듈 머리에서 임포트하면
**그 패키지 없이는 테스트도 임포트도 안 되고**(지금 전 테스트가 LLM 키도 사내 패키지도
없이 돈다) 망분리 밖에서는 설치할 수도 없다. 그래서 실제로 붙을 때 처음 들여오고, 그
자리(`use_backend`)를 테스트가 갈아끼운다 — `note.use_sender` 와 같은 규약이다.

토큰 발급(`POST /token`)만은 표준 라이브러리(urllib)로 한다. `requests` 하나를 더 깔지
않기 위해서이고, `llm.py`·`observability.py` 가 같은 이유로 urllib 을 쓴다.

━━ 사번마다 클라이언트가 따로다 ━━
MCP-User-Key 에 사번이 들어가고 행내 감사 기록도 그 사번으로 남는다. 한 클라이언트를
여러 직원이 나눠 쓰면 누가 무엇을 했는지가 전부 한 사람 앞으로 기록된다 — 되돌릴 수 없는
행위(쪽지 발송)에서 그 기록이 유일한 추적 수단이다.

**요청 컨텍스트는 프로세스 전역이다.** `mcp_sdk.set_request_context(emp_no, auth_token)`
는 SDK 안의 전역을 바꾼다 — 한 프로세스가 여러 직원의 요청을 번갈아 처리하면 마지막에
세운 사람의 컨텍스트가 남는다. 그래서 ① 호출 **직전**에 다시 세우고 ② 세우기와 호출을
**프로세스 단위** 잠금(`_CALL_LOCK`)으로 묶는다. 예전에는 이벤트 루프 단위 잠금이었는데,
실제 발송 경로는 스레드마다 새 루프(`note.send_note_sync` → `asyncio.run`, main.py 의
to_thread 안)라 루프 단위 잠금은 아무것도 막지 못했다 — 두 직원이 동시에 «네»라고 하면
A 의 쪽지가 B 의 사번으로 나갈 수 있었다. 워커 프로세스가 여럿이면 SDK 전역도 프로세스마다
따로라 그 사이는 문제가 없다.

━━ 재시도는 «다시 불러도 되는 도구»만 ━━
발송 호출이 타임아웃으로 실패했다는 것은 «안 나갔다»가 아니라 «나갔는지 모른다»이다.
그대로 다시 부르면 쪽지가 두 통 간다. 그래서 도구를 부를 때 «다시 불러도 되는가»
(`idempotent`)를 부르는 쪽이 밝히고, 밝히지 않으면 재시도하지 않는다. **붙는 단계**
(토큰 발급·도구 목록 읽기)의 실패는 부수효과가 없으므로 언제나 다시 시도한다.

━━ 환경변수 ━━
  MCP_SERVER_URL     게이트웨이 주소. 분석계·서빙계가 갈리면 `_TRNN`·`_SERV` 로 두 벌
                     (`ENV_PATH` 가 고른다 — `pension_agent/env.py`)
  MCP_USER_ID        발급받은 클라이언트 id (tea000 …)
  MCP_SECRET_KEY     발급받은 시크릿 키
  MCP_CONN_ID        sybase 서버를 켤 때만 필요한 접속 id
  MCP_SERVERS        붙을 서버(쉼표 목록). 기본 workb
  MCP_TIMEOUT        토큰 발급 타임아웃(초). 기본 20
  MCP_AUTH_TTL_SEC   토큰·사용자키를 다시 발급하는 주기(초). 기본 600
  MCP_RETRY_ATTEMPTS 다시 불러도 되는 호출의 재시도 횟수. 기본 2
  MCP_RETRY_BACKOFF_SEC  재시도 사이 대기(초). 회차마다 두 배. 기본 1 · 0 이면 바로 다시
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pension_agent import env
from pension_agent.mcp import servers

env.load()

log = logging.getLogger(__name__)


class MCPError(RuntimeError):
    """MCP 호출 실패의 뿌리. 부르는 쪽은 이것만 잡으면 된다."""


class MCPUnavailable(MCPError):
    """붙을 수 없다 — 설정이 없거나 행내 패키지가 없다. 다시 시도해도 같다."""


class MCPCallError(MCPError):
    """붙긴 했는데 호출이 실패했다. 재시도할 수 있는 실패는 이쪽이다."""


# ─────────────────────────────────────────────────────────────
# 설정
#
# 임포트 시점 상수로 두지 않는 이유: 이 값들은 «기동할 때 있으면 좋은 것»이 아니라 실제로
# 붙는 순간에만 필요하고, 테스트·진단 스크립트가 환경변수를 바꿔 가며 확인한다. 읽는 비용은
# os.environ 조회 몇 번이다.
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Settings:
    base_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    conn_id: str = ""
    servers: tuple[str, ...] = servers.DEFAULT
    timeout: float = 20.0
    auth_ttl: float = 600.0
    retry_attempts: int = 2
    retry_backoff: float = 1.0

    #: 비어 있으면 붙을 수 없는 값들. 이름을 그대로 진단에 싣는다.
    REQUIRED = ("MCP_SERVER_URL", "MCP_USER_ID", "MCP_SECRET_KEY")

    def missing(self) -> list[str]:
        have = {"MCP_SERVER_URL": self.base_url, "MCP_USER_ID": self.client_id,
                "MCP_SECRET_KEY": self.client_secret}
        return [name for name in self.REQUIRED if not have[name]]

    @property
    def configured(self) -> bool:
        return not self.missing()


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        log.warning("%s 값을 숫자로 읽지 못했습니다 — 기본값 %s 를 씁니다", name, default)
        return default


def settings() -> Settings:
    """지금 환경변수가 말하는 설정. 단계별 이름(`_TRNN`·`_SERV`)이 있으면 그것을 읽는다."""
    return Settings(
        base_url=env.staged("MCP_SERVER_URL").strip().rstrip("/"),
        client_id=env.staged("MCP_USER_ID").strip(),
        client_secret=env.staged("MCP_SECRET_KEY").strip(),
        conn_id=env.staged("MCP_CONN_ID").strip(),
        servers=servers.parse(os.getenv("MCP_SERVERS", "")),
        timeout=_num("MCP_TIMEOUT", 20.0),
        auth_ttl=_num("MCP_AUTH_TTL_SEC", 600.0),
        retry_attempts=int(_num("MCP_RETRY_ATTEMPTS", 2)),
        retry_backoff=_num("MCP_RETRY_BACKOFF_SEC", 1.0),
    )


def configured() -> bool:
    """지금 붙을 수 있는 설정이 있나. 없으면 부르는 쪽은 «미연결»로 답한다(발송하지 않는다)."""
    return settings().configured


# ─────────────────────────────────────────────────────────────
# 행내 패키지 — 들여오는 자리를 하나로 모은다
# ─────────────────────────────────────────────────────────────

#: `mcp_sdk` 모듈과 `MultiServerMCPClient` 클래스. 앱이 직접 넣어 줄 수도 있고
#: (`use_backend`), 비어 있으면 처음 붙을 때 임포트한다.
_SDK: Any = None
_ADAPTER: Any = None

#: `mcp_sdk.install()` · `setup_system()` 은 애플리케이션당 1회다(행내 가이드 STEP 1·2).
#: 무엇으로 세웠는지를 함께 들고 있다가 자격증명이 바뀌면 다시 세운다 — 테스트가 설정을
#: 갈아끼우고 다시 부를 때 옛 자격증명이 남아 있으면 무엇을 재고 있는지 알 수 없다.
_SYSTEM: tuple[str, str] | None = None

#: 패키지가 없다는 «사실»을 기억해 둔다. 실패한 임포트는 `sys.modules` 에 남지 않아서,
#: 기억해 두지 않으면 `/health` 를 부를 때마다 모듈 검색 경로를 처음부터 다시 걷는다.
_MISSING: str | None = None


def use_backend(*, sdk: Any = None, adapter: Any = None) -> None:
    """행내 패키지를 직접 넣는다(테스트·특수 배포용).

        mcp.client.use_backend(sdk=mcp_sdk, adapter=MultiServerMCPClient)

    둘 다 None 으로 부르면 되돌린다(다음 접속 때 다시 임포트한다).
    """
    global _SDK, _ADAPTER, _SYSTEM, _MISSING
    _SDK, _ADAPTER, _SYSTEM, _MISSING = sdk, adapter, None, None


def backend() -> tuple[Any, Any]:
    """(mcp_sdk, MultiServerMCPClient). 없으면 MCPUnavailable — 무엇을 깔아야 하는지와 함께."""
    global _SDK, _ADAPTER
    if _SDK is None:
        try:
            import mcp_sdk  # noqa: PLC0415 — 저장소 밖(행내) 패키지다
        except ImportError as exc:
            raise MCPUnavailable(
                "행내 mcp_sdk 패키지가 없습니다 — 이 환경에서는 MCP 로 붙을 수 없습니다") from exc
        _SDK = mcp_sdk
    if _ADAPTER is None:
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: PLC0415
        except ImportError as exc:
            raise MCPUnavailable(
                "langchain_mcp_adapters 패키지가 없습니다 — 이 환경에서는 MCP 로 붙을 수 없습니다"
            ) from exc
        _ADAPTER = MultiServerMCPClient
    return _SDK, _ADAPTER


def unavailable() -> str:
    """행내 패키지가 없으면 그 사유, 있으면 빈 문자열. **붙지는 않는다** — 깔렸나만 본다.

    `Settings.missing()` 과 짝이다: 저쪽은 설정이 비었나, 이쪽은 패키지가 없나. 둘 다
    통과해야 쪽지가 나간다 — 어느 한쪽만으로 판단하면 «설정은 있는데 패키지가 없는»
    배포에서 등록은 성공하고 발송 시점에 죽는다(2026-09-17 pod 사고).
    """
    global _MISSING
    if _SDK is not None and _ADAPTER is not None:
        return ""
    if _MISSING is None:
        try:
            backend()
        except MCPUnavailable as exc:
            _MISSING = str(exc)
        else:
            _MISSING = ""
    return _MISSING


def _setup_system(sdk: Any, cfg: Settings) -> None:
    """SDK 활성화와 에이전트 인증 정보 등록 — 프로세스당 1회(자격증명이 바뀌면 다시)."""
    global _SYSTEM
    if _SYSTEM == (cfg.client_id, cfg.client_secret):
        return
    sdk.install()
    sdk.setup_system(client_id=cfg.client_id, client_secret=cfg.client_secret)
    _SYSTEM = (cfg.client_id, cfg.client_secret)


# ─────────────────────────────────────────────────────────────
# 인증
# ─────────────────────────────────────────────────────────────

def user_key(client_id: str, client_secret: str, emp_no: str) -> str:
    """MCP-User-Key — 「누가 이 요청을 시켰나」를 서명해 담은 헤더 값.

    서명 대상은 `timestamp + request_id + emp_no` 이고, 이 셋과 서명을 JSON 으로 묶어
    base64 한 것이 헤더 값이다(행내 규격 그대로). 타임스탬프가 들어가므로 **오래 재사용할
    수 없다** — 얼마나 유효한지는 규격을 못 받아서, 접속을 `MCP_AUTH_TTL_SEC` 마다 새로
    맺는다(`MCPClient._fresh`).
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    request_id = str(uuid.uuid4())
    signature = base64.b64encode(
        hmac.new(client_secret.encode(), f"{timestamp}{request_id}{emp_no}".encode(),
                 hashlib.sha256).digest()).decode()
    payload = {"client_id": client_id, "emp_no": emp_no, "timestamp": timestamp,
               "request_id": request_id, "signature": signature}
    return base64.b64encode(json.dumps(payload).encode()).decode()


#: 자격증명 꼴 — 값 자체를 몰라도 가릴 수 있는 것들. JWT(`eyJ….….…`) · `Bearer <토큰>` ·
#: MCP-User-Key 같은 긴 base64 덩이(사용자 키는 JSON 을 base64 한 200자 안팎이다).
_CREDENTIAL_SHAPES = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/=])"),
)
REDACTED = "[가림]"


def redact(text: str, cfg: Settings | None = None, *extra: str) -> str:
    """로그·오류 문구로 나가는 글에서 자격증명을 가린다.

    어댑터·전송 계층이 올리는 예외 문구에는 **요청 그대로**가 실려 온다 — 게이트웨이 주소
    (`/workb/{client_id}` 라 MCP_USER_ID 가 경로에 있다), `Authorization: Bearer …`,
    `MCP-User-Key` 헤더 값까지. 그 문구를 `%s` 로 그대로 찍으면 자격증명이 Grafana 에 남고,
    `MCPCallError` 에 실려 화면의 «쪽지를 보내지 못했어요. …»까지 올라간다. 값을 아는 것
    (설정의 id·시크릿, 이번 접속의 토큰)은 값으로, 모르는 것은 꼴로 가린다.
    """
    out = text or ""
    known = [v for v in ((cfg.client_id, cfg.client_secret) if cfg else ()) + tuple(extra)
             if v and len(v) >= 4]
    for value in sorted(set(known), key=len, reverse=True):
        out = out.replace(value, REDACTED)
    for shape in _CREDENTIAL_SHAPES:
        out = shape.sub(REDACTED, out)
    return out


def issue_token(cfg: Settings) -> str:
    """게이트웨이에서 JWT 를 받아 온다(`POST /token`). 실패하면 사유를 남기고 올린다.

    표준 라이브러리로 부르는 이유는 머리말에 있다. **응답 본문을 오류에 싣지 않는다** —
    이 응답에는 토큰이 들어 있고, 실패 응답이라도 그 자리에 무엇이 실릴지 규격으로 정해져
    있지 않다(로그는 Grafana 에 그대로 남는다).
    """
    body = json.dumps({"user_id": cfg.client_id, "secret_key": cfg.client_secret}).encode()
    req = urllib.request.Request(f"{cfg.base_url}/token", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise MCPCallError(f"MCP 토큰 발급이 거부됐습니다 — HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 — URLError · 타임아웃 · JSON 파손
        raise MCPCallError(f"MCP 토큰 발급에 실패했습니다 — {type(exc).__name__}") from exc
    token = (data or {}).get("jwt_token") if isinstance(data, dict) else None
    if not token:
        raise MCPCallError("MCP 토큰 발급 응답에 jwt_token 이 없습니다")
    return str(token)


# ─────────────────────────────────────────────────────────────
# 호출 잠금 — 요청 컨텍스트가 프로세스 전역이라 필요하다(머리말)
# ─────────────────────────────────────────────────────────────

#: 프로세스에 하나. SDK 전역이 프로세스에 하나이므로 잠금도 그 단위여야 한다 — 스레드마다
#: 루프가 다른 실제 경로(머리말)에서 `asyncio.Lock` 은 루프마다 따로 생겨 아무것도 막지 못했다.
_CALL_LOCK = threading.Lock()


@contextlib.asynccontextmanager
async def _locked():
    """«컨텍스트 세우기 + 호출» 한 덩이를 프로세스 전체에서 직렬화한다.

    잡는 것은 스레드로 보낸다(`to_thread`) — 이벤트 루프 안에서 `threading.Lock.acquire()` 를
    직접 부르면 잠금을 쥔 쪽이 같은 루프에서 await 를 마쳐야 풀리는데 루프가 막혀 있어
    영원히 못 푼다. 잠금은 await 구간(도구 호출)에 걸쳐 잡혀 있다 — 발송 하나가 끝날 때까지
    다른 직원의 발송은 기다린다. SDK 컨텍스트가 전역인 동안은 그것이 정확한 비용이다.
    """
    await asyncio.to_thread(_CALL_LOCK.acquire)
    try:
        yield
    finally:
        _CALL_LOCK.release()


# ─────────────────────────────────────────────────────────────
# 클라이언트
# ─────────────────────────────────────────────────────────────

@dataclass
class MCPClient:
    """직원 한 명의 MCP 연결. 사번마다 따로 만든다(머리말).

    접속·도구 목록은 처음 부를 때 만들고 `MCP_AUTH_TTL_SEC` 가 지나면 다시 맺는다.
    호출이 실패하면 접속을 버린다 — 다음 호출이 새로 붙는다.

    **이벤트 루프를 건너 재사용된다.** 대화 그래프가 동기라서 쪽지 발송은
    `note.send_note_sync` → `asyncio.run` 으로 들어오고, 그것은 호출마다 새 루프다.
    어댑터가 세션을 «호출할 때 열고 닫는» 구조라 지금은 문제가 없지만(도구 객체가 접속
    설정만 들고 있다) 어댑터 버전이 바뀌면 여기가 먼저 깨진다. 그때의 증상은 조용하지
    않다 — 첫 호출부터 예외이고, 실패하면 접속을 버리므로 다음 호출이 새로 붙는다.
    """

    emp_no: str
    config: Settings = field(default_factory=settings)

    # 어댑터 인스턴스. 도구들이 이것으로 세션을 열므로 접속이 유효한 동안 들고 있는다.
    _client: Any = field(default=None, init=False, repr=False)
    _tools: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _context: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _opened: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not (self.emp_no or "").strip():
            raise MCPUnavailable("MCP 는 사번 없이 붙을 수 없습니다 — 누구의 요청인지가 인증에 들어갑니다")
        if not self.config.configured:
            raise MCPUnavailable(
                "MCP 설정이 비어 있습니다 — " + ", ".join(self.config.missing()))

    # ── 접속 ─────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._tools is not None and self._fresh

    @property
    def _fresh(self) -> bool:
        """서명·토큰이 아직 쓸 만한가. 규격을 못 받아 시간으로만 판단한다(`user_key` 주석)."""
        return (time.monotonic() - self._opened) < self.config.auth_ttl

    def tool_names(self) -> list[str]:
        """지금 붙어 있는 도구 이름들(진단용). 안 붙었으면 빈 목록."""
        return sorted(self._tools or {})

    def drop(self) -> None:
        """접속을 버린다. 다음 호출이 새로 붙는다 — 호출이 실패한 뒤에 부른다."""
        self._client, self._tools, self._context, self._opened = None, None, {}, 0.0

    async def connect(self) -> None:
        """토큰을 받고 세션을 열어 도구 목록을 읽는다. 이미 붙어 있으면 아무것도 하지 않는다."""
        if self.connected:
            return
        cfg = self.config
        sdk, adapter = backend()
        _setup_system(sdk, cfg)
        # 토큰 발급은 막히는 호출이라 스레드로 보낸다 — 이벤트 루프에서 직접 부르면 그동안
        # 이 워커가 다른 요청을 하나도 못 받는다(main.py 가 ask() 를 to_thread 로 보내는 것과
        # 같은 이유).
        token = await asyncio.to_thread(issue_token, cfg)
        key = user_key(cfg.client_id, cfg.client_secret, self.emp_no)
        endpoints = servers.endpoints(
            cfg.servers, base_url=cfg.base_url, client_id=cfg.client_id, conn_id=cfg.conn_id,
            headers={"Authorization": f"Bearer {token}", "MCP-User-Key": key})
        if not endpoints:
            raise MCPUnavailable("붙을 MCP 서버가 없습니다 — MCP_SERVERS 설정을 확인하세요")
        self._context = {"emp_no": self.emp_no, "auth_token": token}
        # 요청 컨텍스트는 세션을 열기 **전에** 세운다(행내 가이드 STEP 3). 세우기와 열기
        # 사이에 다른 직원의 요청이 끼어들면 그 사람 컨텍스트로 열리므로 호출과 같은 잠금이다.
        async with _locked():
            self._enter()
            client = adapter(endpoints)
            tools = await client.get_tools()
        self._client = client
        self._tools = {getattr(t, "name", ""): t for t in tools}
        self._opened = time.monotonic()
        log.info("MCP 접속 · 사번 %s · 서버 %s · 도구 %d개",
                 self.emp_no, ", ".join(endpoints), len(self._tools))

    def _enter(self) -> None:
        """이 요청의 주체를 SDK 에 세운다. 호출 직전마다 다시 세운다(머리말)."""
        if self._context and _SDK is not None:
            _SDK.set_request_context(**self._context)

    async def tool(self, name: str) -> Any:
        """이름으로 도구 하나. 없으면 무엇이 있는지와 함께 올린다 — 도구 이름 오타는
        「서버가 죽었다」와 아주 다른 사건인데, 구분이 없으면 둘 다 «호출 실패»로 보인다."""
        await self.connect()
        found = (self._tools or {}).get(name)
        if found is None:
            raise MCPUnavailable(
                f"MCP 도구 '{name}' 가 없습니다 — 지금 붙은 도구: "
                f"{', '.join(sorted(self._tools or {})) or '없음'}")
        return found

    # ── 호출 ─────────────────────────────────────────────────

    async def call(self, name: str, args: dict[str, Any], *, idempotent: bool = False) -> Any:
        """도구 하나를 부르고 **결과를 그대로** 돌려준다(판정은 부르는 쪽이 한다 — 머리말).

        idempotent: 다시 불러도 되는 도구인가. 기본은 아니오다 — 발송처럼 되돌릴 수 없는
        호출은 타임아웃이 «안 나갔다»를 뜻하지 않으므로, 다시 부르면 두 통이 나간다.
        붙는 단계(토큰·도구 목록)의 실패는 부수효과가 없어 언제나 다시 시도한다.
        """
        attempts = 1 + max(0, self.config.retry_attempts)
        last: Exception | None = None
        for attempt in range(attempts):
            invoked = False
            try:
                found = await self.tool(name)
                # 컨텍스트 세우기와 호출을 한 덩이로 묶는다 — 사이에 다른 직원의 요청이
                # 끼어들면 그 사람 컨텍스트로 나간다(머리말).
                async with _locked():
                    self._enter()
                    invoked = True
                    return await found.ainvoke(args)
            except MCPUnavailable:
                raise                       # 설정·패키지·도구 이름 — 다시 시도해도 같다
            except Exception as exc:        # noqa: BLE001 — 전송·서버·어댑터 어느 쪽이든
                last = exc
                # 예외 문구는 가려서 남긴다 — 전송 계층은 요청(주소·헤더)을 문구에 그대로
                # 싣고, 그 안에 클라이언트 id·토큰·사용자 키가 있다(`redact` 머리말). drop()
                # 이 토큰을 지우기 전에 읽어 둔다.
                reason = self._redact(f"{type(exc).__name__}: {exc}")
                self.drop()
                log.warning("MCP 호출 실패 · 도구 %s · %d/%d회 · %s",
                            name, attempt + 1, attempts, reason)
                if invoked and not idempotent:
                    break                   # 나갔는지 모르는 호출을 다시 부르지 않는다
                if attempt + 1 < attempts and self.config.retry_backoff > 0:
                    await asyncio.sleep(self.config.retry_backoff * (2 ** attempt))
        # 화면까지 올라가는 문구다(note.send_note 의 detail) — 위와 같은 이유로 가린다.
        raise MCPCallError(
            f"MCP 도구 '{name}' 호출에 실패했습니다 — "
            + (self._redact(f"{type(last).__name__}: {last}") if last else "UNKNOWN")) from last

    def _redact(self, text: str) -> str:
        """이 접속이 아는 자격증명(설정의 id·시크릿 + 이번 토큰)까지 값으로 가린다."""
        return redact(text, self.config, self._context.get("auth_token", ""))


# ─────────────────────────────────────────────────────────────
# 사번별 클라이언트
# ─────────────────────────────────────────────────────────────

#: 사번 → 클라이언트. 접속을 요청마다 새로 맺지 않으려고 들고 있는다(토큰 발급 + 도구
#: 목록 읽기가 매번이면 쪽지 한 통에 왕복이 셋이다). 상한을 두는 이유는 이 프로세스가
#: 여러 직원의 요청을 받기 때문이다 — 없으면 사번 수만큼 무한정 쌓인다.
_CLIENTS: dict[str, MCPClient] = {}
MAX_CLIENTS = 64


def client_for(emp_no: str, *, config: Settings | None = None) -> MCPClient:
    """이 사번의 클라이언트. 설정이 바뀌었으면 새로 만든다."""
    cfg = config or settings()
    found = _CLIENTS.get(emp_no)
    if found is not None and found.config == cfg:
        return found
    if len(_CLIENTS) >= MAX_CLIENTS:
        # 가장 오래 전에 들어온 것부터 버린다(파이썬 dict 는 넣은 순서를 지킨다).
        _CLIENTS.pop(next(iter(_CLIENTS)), None)
    client = MCPClient(emp_no, config=cfg)
    _CLIENTS[emp_no] = client
    return client


def reset() -> None:
    """들고 있던 접속을 전부 버린다(설정을 바꿔 다시 붙을 때·테스트)."""
    for client in _CLIENTS.values():
        client.drop()
    _CLIENTS.clear()


def stats() -> dict[str, Any]:
    """지금 MCP 가 어떤 상태인가 — `/health` 가 그대로 싣는다.

    **키·토큰은 내보내지 않는다.** 설정 «여부»와 어디를 보고 있는지까지다(`/health` 의
    llm 칸과 같은 규약).

    `configured` 와 `packages` 를 나란히 싣는다 — 붙지 않는 이유가 설정인지 패키지인지
    갈려야 처방이 갈린다(`.env` 를 채우나, 이미지를 다시 마나).
    """
    cfg = settings()
    return {
        "configured": cfg.configured,
        "missing": cfg.missing(),
        "packages": not unavailable(),
        "base_url_set": bool(cfg.base_url),
        "servers": list(cfg.servers),
        "clients": len(_CLIENTS),
        "connected": sorted(emp for emp, c in _CLIENTS.items() if c.connected),
    }
