"""행내 MCP 연결 — 인증 · 접속 · 도구 호출. 도구별 결과 판정은 미포함(어댑터 몫).

    from pension_agent import mcp

    raw = await mcp.client_for("3902172").call("send_memo", {...})

- 행내 패키지(`mcp_sdk`·`langchain_mcp_adapters`)는 접속 시 지연 임포트(`use_backend` 로 교체 가능)
- 토큰 발급은 urllib(추가 의존성 회피)
- 사번별 클라이언트 — MCP-User-Key·감사 기록이 사번 단위
- SDK 요청 컨텍스트는 프로세스 전역 — 호출 직전 재설정 + 프로세스 단위 잠금(`_CALL_LOCK`)
- 재시도는 `idempotent=True` 호출만. 접속 단계 실패는 항상 재시도

환경변수:
  MCP_SERVER_URL     게이트웨이 주소. `_TRNN`·`_SERV` 두 벌 가능(env.py)
  MCP_USER_ID        발급받은 클라이언트 id (tea000 …)
  MCP_SECRET_KEY     발급받은 시크릿 키
  MCP_CONN_ID        sybase 서버를 켤 때만 필요한 접속 id
  MCP_SERVERS        붙을 서버(쉼표 목록). 기본 workb
  MCP_TIMEOUT        토큰 발급 타임아웃(초). 기본 20
  MCP_AUTH_TTL_SEC   토큰·사용자키를 다시 발급하는 주기(초). 기본 600
  MCP_RETRY_ATTEMPTS 다시 불러도 되는 호출의 재시도 횟수. 기본 2
  MCP_RETRY_BACKOFF_SEC  재시도 대기(초), 회차마다 2배. 기본 1 · 0 이면 즉시
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

from pension_agent.common import env
from pension_agent.common.mcp import servers

env.load()

log = logging.getLogger(__name__)


class MCPError(RuntimeError):
    """MCP 실패 최상위 예외."""


class MCPUnavailable(MCPError):
    """접속 불가(설정·패키지 없음). 재시도 무의미."""


class MCPCallError(MCPError):
    """접속 후 호출 실패. 재시도 가능 부류."""


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

    #: 필수 설정 이름(진단 출력용).
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
    """환경변수 기반 설정(호출 시마다 읽음). 단계별 이름(`_TRNN`·`_SERV`) 우선."""
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
    """필수 설정 존재 여부. 없으면 호출부는 «미연결» 응답."""
    return settings().configured


#: `mcp_sdk` 모듈·`MultiServerMCPClient` 클래스. 비면 첫 접속 시 임포트.
_SDK: Any = None
_ADAPTER: Any = None

#: `install()`·`setup_system()` 에 쓴 자격증명. 바뀌면 재설정(앱당 1회 규격).
_SYSTEM: tuple[str, str] | None = None

#: 패키지 부재 사유 캐시(실패 임포트는 sys.modules 에 안 남아 매번 재탐색됨).
_MISSING: str | None = None


def use_backend(*, sdk: Any = None, adapter: Any = None) -> None:
    """행내 패키지 직접 주입(테스트용). 둘 다 None 이면 초기화."""
    global _SDK, _ADAPTER, _SYSTEM, _MISSING
    _SDK, _ADAPTER, _SYSTEM, _MISSING = sdk, adapter, None, None


def backend() -> tuple[Any, Any]:
    """(mcp_sdk, MultiServerMCPClient). 없으면 MCPUnavailable."""
    global _SDK, _ADAPTER
    if _SDK is None:
        try:
            import mcp_sdk  # noqa: PLC0415 — 저장소 밖(행내) 패키지
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
    """행내 패키지 부재 사유, 있으면 빈 문자열(접속 안 함). `Settings.missing()` 과 함께 확인."""
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


#: INFO 를 끄는 외부 로거(client_id·user_key 노출 방지). 자식 로거도 명시 — SDK install() 이
#: 자기 로거 레벨을 재설정하므로. 기동 시(main.py)와 install() 직후 두 번 적용.
NOISY_LOGGERS = ("httpx", "httpcore", "mcp", "langchain_mcp_adapters",
                 "mcp_sdk", "mcp_sdk.audit", "mcp_sdk.hooks", "mcp_sdk.identity")


def quiet_loggers() -> None:
    """`NOISY_LOGGERS` 의 INFO 끄기. 멱등."""
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _setup_system(sdk: Any, cfg: Settings) -> None:
    """SDK 활성화·인증 정보 등록. 프로세스당 1회(자격증명 변경 시 재실행)."""
    global _SYSTEM
    if _SYSTEM == (cfg.client_id, cfg.client_secret):
        return
    sdk.install()
    # install() 이 자기 로거를 INFO 로 재설정 — 다시 내림
    quiet_loggers()
    sdk.setup_system(client_id=cfg.client_id, client_secret=cfg.client_secret)
    _SYSTEM = (cfg.client_id, cfg.client_secret)


def user_key(client_id: str, client_secret: str, emp_no: str) -> str:
    """MCP-User-Key 헤더 값(행내 규격: timestamp+request_id+emp_no HMAC 서명, JSON→base64).

    유효기간 규격 미확보 — `MCP_AUTH_TTL_SEC` 마다 재접속으로 대응.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    request_id = str(uuid.uuid4())
    signature = base64.b64encode(
        hmac.new(client_secret.encode(), f"{timestamp}{request_id}{emp_no}".encode(),
                 hashlib.sha256).digest()).decode()
    payload = {"client_id": client_id, "emp_no": emp_no, "timestamp": timestamp,
               "request_id": request_id, "signature": signature}
    return base64.b64encode(json.dumps(payload).encode()).decode()


#: 값 없이 꼴로 가리는 자격증명 — Bearer 토큰 · JWT · 긴 base64(MCP-User-Key).
_CREDENTIAL_SHAPES = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/=])"),
)
REDACTED = "[가림]"


def redact(text: str, cfg: Settings | None = None, *extra: str) -> str:
    """로그·오류 문구의 자격증명 마스킹. 아는 값(id·시크릿·토큰)은 값으로, 나머지는 꼴로.

    예외 문구에 요청 주소(client_id 포함)·헤더가 그대로 실려 화면까지 올라감.
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
    """게이트웨이 JWT 발급(`POST /token`). 응답 본문은 오류 문구에 미포함(토큰 노출 방지)."""
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


#: 프로세스 단위 잠금. 발송은 스레드마다 새 루프라 `asyncio.Lock` 으로는 직렬화 불가.
_CALL_LOCK = threading.Lock()


@contextlib.asynccontextmanager
async def _locked():
    """«컨텍스트 설정 + 호출»을 프로세스 전체에서 직렬화.

    acquire 는 to_thread 로 — 루프 안 직접 호출 시 같은 루프의 보유자가 못 풀어 교착.
    """
    await asyncio.to_thread(_CALL_LOCK.acquire)
    try:
        yield
    finally:
        _CALL_LOCK.release()


@dataclass
class MCPClient:
    """직원 한 명(사번)의 MCP 연결. 첫 호출 시 접속, TTL 경과·호출 실패 시 재접속.

    이벤트 루프를 건너 재사용됨 — 어댑터가 호출마다 세션을 여는 구조라 성립(버전 변경 시 주의).
    """

    emp_no: str
    config: Settings = field(default_factory=settings)

    # 어댑터 인스턴스(도구가 이것으로 세션을 엶)
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

    @property
    def connected(self) -> bool:
        return self._tools is not None and self._fresh

    @property
    def _fresh(self) -> bool:
        """서명·토큰 유효 여부(시간 기준)."""
        return (time.monotonic() - self._opened) < self.config.auth_ttl

    def tool_names(self) -> list[str]:
        """접속된 도구 이름들(진단용)."""
        return sorted(self._tools or {})

    def drop(self) -> None:
        """접속 폐기(호출 실패 후). 다음 호출이 재접속."""
        self._client, self._tools, self._context, self._opened = None, None, {}, 0.0

    async def connect(self) -> None:
        """토큰 발급·세션 열기·도구 목록 적재. 이미 접속 중이면 무시."""
        if self.connected:
            return
        cfg = self.config
        sdk, adapter = backend()
        _setup_system(sdk, cfg)
        # 블로킹 호출 — 루프 차단 방지
        token = await asyncio.to_thread(issue_token, cfg)
        key = user_key(cfg.client_id, cfg.client_secret, self.emp_no)
        endpoints = servers.endpoints(
            cfg.servers, base_url=cfg.base_url, client_id=cfg.client_id, conn_id=cfg.conn_id,
            headers={"Authorization": f"Bearer {token}", "MCP-User-Key": key})
        if not endpoints:
            raise MCPUnavailable("붙을 MCP 서버가 없습니다 — MCP_SERVERS 설정을 확인하세요")
        self._context = {"emp_no": self.emp_no, "auth_token": token}
        # 컨텍스트는 세션 열기 전에 설정(행내 가이드 STEP 3) — 끼어들기 방지로 같은 잠금
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
        """요청 주체를 SDK 에 설정(호출 직전마다)."""
        if self._context and _SDK is not None:
            _SDK.set_request_context(**self._context)

    async def tool(self, name: str) -> Any:
        """이름으로 도구 조회. 없으면 접속된 도구 목록과 함께 MCPUnavailable."""
        await self.connect()
        found = (self._tools or {}).get(name)
        if found is None:
            raise MCPUnavailable(
                f"MCP 도구 '{name}' 가 없습니다 — 지금 붙은 도구: "
                f"{', '.join(sorted(self._tools or {})) or '없음'}")
        return found

    async def call(self, name: str, args: dict[str, Any], *, idempotent: bool = False) -> Any:
        """도구 호출, 결과 그대로 반환. idempotent=False(기본)면 호출 후 실패 시 재시도 금지."""
        attempts = 1 + max(0, self.config.retry_attempts)
        last: Exception | None = None
        for attempt in range(attempts):
            invoked = False
            try:
                found = await self.tool(name)
                async with _locked():
                    self._enter()
                    invoked = True
                    return await found.ainvoke(args)
            except MCPUnavailable:
                raise                       # 재시도 무의미
            except Exception as exc:        # noqa: BLE001 — 전송·서버·어댑터 어느 쪽이든
                last = exc
                # 마스킹은 drop() 전에 — drop() 이 토큰을 지움
                reason = self._redact(f"{type(exc).__name__}: {exc}")
                self.drop()
                log.warning("MCP 호출 실패 · 도구 %s · %d/%d회 · %s",
                            name, attempt + 1, attempts, reason)
                if invoked and not idempotent:
                    break                   # 발송 여부 불명 — 중복 발송 방지
                if attempt + 1 < attempts and self.config.retry_backoff > 0:
                    await asyncio.sleep(self.config.retry_backoff * (2 ** attempt))
        # 화면까지 올라가는 문구 — 마스킹
        raise MCPCallError(
            f"MCP 도구 '{name}' 호출에 실패했습니다 — "
            + (self._redact(f"{type(last).__name__}: {last}") if last else "UNKNOWN")) from last

    def _redact(self, text: str) -> str:
        """이 접속의 자격증명(id·시크릿·토큰)까지 마스킹."""
        return redact(text, self.config, self._context.get("auth_token", ""))


#: 사번 → 클라이언트 캐시(재접속 왕복 절감). 상한 MAX_CLIENTS.
_CLIENTS: dict[str, MCPClient] = {}
MAX_CLIENTS = 64


def client_for(emp_no: str, *, config: Settings | None = None) -> MCPClient:
    """사번별 클라이언트. 설정 변경 시 새로 생성."""
    cfg = config or settings()
    found = _CLIENTS.get(emp_no)
    if found is not None and found.config == cfg:
        return found
    if len(_CLIENTS) >= MAX_CLIENTS:
        # 가장 오래된 것부터 제거(dict 삽입 순서)
        _CLIENTS.pop(next(iter(_CLIENTS)), None)
    client = MCPClient(emp_no, config=cfg)
    _CLIENTS[emp_no] = client
    return client


def reset() -> None:
    """접속 전부 폐기(설정 변경·테스트)."""
    for client in _CLIENTS.values():
        client.drop()
    _CLIENTS.clear()


def stats() -> dict[str, Any]:
    """MCP 상태(`/health` 용). 키·토큰 미포함. 설정(configured)·패키지(packages) 별도 표기."""
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
