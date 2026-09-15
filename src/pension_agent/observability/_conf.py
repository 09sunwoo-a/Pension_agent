"""설정 — 첫 사용 시점에 한 번 읽는다

`observability.py` 에서 갈라낸 모듈 — 공개 표면은 패키지 `__init__` 이다.
"""

from __future__ import annotations

import base64
import os
import threading

from pension_agent import env


#: Langfuse Cloud. 자체 호스팅이면 LANGFUSE_HOST 로 덮는다.
DEFAULT_HOST = "https://cloud.langfuse.com"
INGESTION_PATH = "/api/public/ingestion"

#: 큐 상한. 넘치면 새 이벤트를 버린다 — 호출부를 막느니 관측을 잃는다.
MAX_QUEUE = 2000
#: 한 번에 보내는 이벤트 수. Langfuse 수집 API 는 배치를 받는다.
BATCH_SIZE = 50
#: 배치가 덜 찼을 때 기다리는 시간(초). 이 시간이 지나면 있는 것만 보낸다.
FLUSH_INTERVAL = 1.0


# ─────────────────────────────────────────────────────────────
# 설정 — 첫 사용 시점에 한 번 읽는다
#
# 임포트 시점에 읽지 않는 이유: `.env` 를 언제 읽었는지에 설정이 좌우되면 안 된다.
# 모듈이 임포트되는 순서는 호출자가 정하지만, 첫 트레이스는 항상 그 뒤다.
# ─────────────────────────────────────────────────────────────

class _Conf:
    __slots__ = ("enabled", "host", "auth", "release", "environment",
                 "capture_content", "max_chars", "timeout", "debug")

    def __init__(self) -> None:
        env.load()
        public = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        secret = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        switch = os.getenv("LANGFUSE_ENABLED", "").strip().lower()
        self.enabled = bool(public and secret) and switch not in ("0", "false", "no", "off")
        self.host = (os.getenv("LANGFUSE_HOST") or DEFAULT_HOST).rstrip("/")
        self.auth = base64.b64encode(f"{public}:{secret}".encode()).decode() if public else ""
        self.release = os.getenv("LANGFUSE_RELEASE", "").strip()
        self.environment = os.getenv("LANGFUSE_ENVIRONMENT", "demo").strip() or "demo"
        self.capture_content = _flag("LANGFUSE_CAPTURE_CONTENT", True)
        self.max_chars = _int("LANGFUSE_MAX_CHARS", 20000)
        self.timeout = _int("LANGFUSE_TIMEOUT", 10)
        self.debug = _flag("LANGFUSE_DEBUG", False)


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


_CONF: _Conf | None = None
_CONF_LOCK = threading.Lock()


def conf() -> _Conf:
    global _CONF
    if _CONF is None:
        with _CONF_LOCK:
            if _CONF is None:
                _CONF = _Conf()
    return _CONF


def enabled() -> bool:
    """관측을 보낼 수 있는가. 키가 없거나 스위치가 꺼져 있으면 False."""
    return conf().enabled


def reset() -> None:
    """설정을 다시 읽게 한다. 환경변수를 바꿔 가며 도는 테스트용."""
    global _CONF
    with _CONF_LOCK:
        _CONF = None
