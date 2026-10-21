"""Langfuse 설정 — 첫 사용 시 한 번 읽음(임포트 시점 아님 — `.env` 적재 순서 무관하게)."""

from __future__ import annotations

import base64
import os
import threading

from pension_agent.common import env


#: Langfuse Cloud. 자체 호스팅이면 LANGFUSE_HOST 로 덮어씀.
DEFAULT_HOST = "https://cloud.langfuse.com"
INGESTION_PATH = "/api/public/ingestion"

#: 큐 상한. 넘치면 새 이벤트 버림(호출부 차단 금지).
MAX_QUEUE = 2000
#: 배치당 이벤트 수.
BATCH_SIZE = 50
#: 배치 미충족 시 대기 시간(초). 지나면 있는 것만 전송.
FLUSH_INTERVAL = 1.0


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
    """관측 전송 가능 여부. 키 없음·스위치 꺼짐이면 False."""
    return conf().enabled


def reset() -> None:
    """설정 재적재(테스트용)."""
    global _CONF
    with _CONF_LOCK:
        _CONF = None
