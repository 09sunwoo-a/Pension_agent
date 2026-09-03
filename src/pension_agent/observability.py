"""Langfuse 관측(트레이싱) — LLM 호출을 대시보드에서 되짚을 수 있게 남긴다.

무엇을 위한 것인가. 이 저장소는 한 번의 브리핑에 LLM 을 11번, 대화 한 턴에 4~7번
부른다. 답이 이상하게 나왔을 때 **어느 호출이 무엇을 받고 무엇을 뱉었는지**를 화면
로그로 되짚는 것은 사실상 불가능하다. Langfuse 는 그 호출들을 트레이스 하나(= 브리핑
한 건 · 대화 한 턴) 아래 묶어 보여준다.

    from pension_agent import observability as obs

    with obs.trace("consult.turn", input=question, session_id=sid) as tr:
        ...                       # 이 안에서 난 llm.generate 는 전부 이 트레이스 밑에 붙는다
        tr.update(output=answer)

━━ 설계 ━━

**표준 라이브러리만 쓴다.** langfuse SDK 를 넣지 않는 이유는 사내 genai 경로가
urllib 만 쓰는 것과 같다(`llm.py`) — 망분리 환경으로 코드를 들여올 때 설치할 것이
늘지 않아야 한다. Langfuse 의 수집 API(`POST /api/public/ingestion`)는 Basic 인증 +
JSON 배치 한 종류라 SDK 없이 부를 수 있다.

**에이전트를 절대 세우지 않는다.** 관측은 부산물이지 기능이 아니다. 그래서

  - 전송은 백그라운드 워커 스레드가 한다. 호출부는 큐에 넣고 즉시 돌아온다.
  - 큐가 차면 **새 이벤트를 버린다**(호출부를 막지 않는다). 버린 수는 `stats()` 에 남는다.
  - 전송 실패·설정 오류·직렬화 실패는 전부 이 모듈 안에서 삼킨다. 밖으로 예외가 나가지
    않는다 — 관측이 죽어서 상담이 죽는 일은 없어야 한다.

**키가 없으면 통째로 꺼진다.** `enabled()` 가 False 면 `trace()` 는 아무것도 하지 않는
핸들을 주고 `record_generation()` 은 즉시 돌아온다. 테스트·시연은 키 없이 그대로 돈다.

━━ 환경변수 ━━
  LANGFUSE_PUBLIC_KEY   pk-lf-... (없으면 관측 꺼짐)
  LANGFUSE_SECRET_KEY   sk-lf-... (없으면 관측 꺼짐)
  LANGFUSE_HOST         기본 https://cloud.langfuse.com. 자체 호스팅이면 그 주소
  LANGFUSE_ENABLED      0/false 로 두면 키가 있어도 끈다
  LANGFUSE_RELEASE      버전 태그(선택). 배포본 구분용
  LANGFUSE_ENVIRONMENT  기본 demo. 시연/스테이징/운영 구분용
  LANGFUSE_CAPTURE_CONTENT  기본 1. 0 이면 프롬프트·응답 본문을 보내지 않고 길이만 남긴다
  LANGFUSE_MAX_CHARS    본문 한 건의 상한. 기본 20000자. 넘으면 잘라 «…(N자 생략)» 표시
  LANGFUSE_TIMEOUT      전송 타임아웃 초. 기본 10

**개인정보 주의.** 프롬프트에는 고객 원장(이름·잔액·상품)이 실려 있다. 지금 고객은
시연용 목업이라 그대로 보내지만(루트 CLAUDE.md), 실데이터로 바꾸는 순간 이 값이 외부
SaaS 로 나간다. 그때 정해야 할 것은 `docs/PRODUCTION_RISKS.md` §9 에 적혀 있다 —
자체 호스팅으로 돌리거나 `LANGFUSE_CAPTURE_CONTENT=0` 으로 본문을 끊는다.
"""

from __future__ import annotations

import atexit
import base64
import contextlib
import contextvars
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

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


# ─────────────────────────────────────────────────────────────
# 트레이스 컨텍스트 — 한 턴/한 브리핑 아래 호출들을 묶는다
# ─────────────────────────────────────────────────────────────

#: 현재 트레이스 id. ContextVar 라 스레드에 자동으로 따라가지 않는다 — 스레드를 띄우는
#: 자리(answer.py 의 compose 병렬 작성)는 이미 `contextvars.copy_context()` 로 넘기고
#: 있어서 그대로 따라간다. asyncio.to_thread(llm.agenerate)도 컨텍스트를 복사한다.
_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "langfuse_trace_id", default=None)


class Trace:
    """트레이스 하나. `trace()` 가 만들어 준다 — 직접 만들지 않는다."""

    __slots__ = ("id", "name", "_fields", "_live")

    def __init__(self, trace_id: str, name: str, live: bool) -> None:
        self.id = trace_id
        self.name = name
        self._fields: dict[str, Any] = {}
        self._live = live

    def update(self, **fields: Any) -> None:
        """트레이스에 얹을 값(output·metadata·tags·user_id …). 닫힐 때 함께 보낸다."""
        if self._live:
            self._fields.update(fields)


#: 관측이 꺼져 있을 때 돌려주는 핸들. update() 가 아무것도 하지 않는다.
_NULL_TRACE = Trace("", "", live=False)


@contextlib.contextmanager
def trace(name: str, *, input: Any = None, user_id: str | None = None,
          session_id: str | None = None, metadata: dict | None = None,
          tags: list[str] | None = None) -> Iterator[Trace]:
    """트레이스 하나를 연다. 이 블록 안의 LLM 호출은 전부 여기에 묶인다.

    블록에서 예외가 나면 트레이스에 level=ERROR 와 예외 문구를 남기고 예외는 그대로
    올려보낸다 — 관측은 흐름을 바꾸지 않는다.
    """
    if not enabled():
        yield _NULL_TRACE
        return

    handle = Trace(uuid.uuid4().hex, name, live=True)
    body: dict[str, Any] = {"id": handle.id, "name": name, "timestamp": _now()}
    _put_if(body, "release", conf().release or None)
    _put_if(body, "input", _payload(input))
    _put_if(body, "userId", user_id)
    _put_if(body, "sessionId", session_id)
    _put_if(body, "metadata", metadata)
    _put_if(body, "tags", tags)
    _emit("trace-create", body)   # 시작 시점에 한 번 — 도중에 프로세스가 죽어도 흔적이 남는다

    token = _TRACE_ID.set(handle.id)
    started = time.time()
    try:
        yield handle
    except BaseException as exc:                        # noqa: BLE001 — 기록만 하고 되던진다
        handle.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        _TRACE_ID.reset(token)
        closing: dict[str, Any] = {"id": handle.id, "name": name, "timestamp": _now()}
        fields = dict(handle._fields)
        meta = dict(fields.pop("metadata", None) or {})
        meta["latency_ms"] = int((time.time() - started) * 1000)
        for key in ("level", "status_message"):
            if key in fields:
                meta[key] = fields.pop(key)
        _put_if(closing, "output", _payload(fields.pop("output", None)))
        _put_if(closing, "userId", fields.pop("user_id", None))
        _put_if(closing, "sessionId", fields.pop("session_id", None))
        _put_if(closing, "tags", fields.pop("tags", None))
        meta.update(fields)                              # 남은 것은 전부 메타데이터로
        closing["metadata"] = meta
        _emit("trace-create", closing)                   # 같은 id — Langfuse 가 병합한다


def current_trace_id() -> str | None:
    """지금 열려 있는 트레이스 id. 없으면 None."""
    return _TRACE_ID.get()


# ─────────────────────────────────────────────────────────────
# 관측 기록 — LLM 호출 한 건
# ─────────────────────────────────────────────────────────────

def record_generation(name: str, *, model: str | None = None, input: Any = None,
                      output: Any = None, usage: dict | None = None,
                      start: float | None = None, end: float | None = None,
                      parameters: dict | None = None, metadata: dict | None = None,
                      error: str | None = None) -> None:
    """LLM 호출 한 건을 남긴다. `llm.generate()` 가 성공·실패 양쪽에서 부른다.

    start·end 는 `time.time()` 값이다. 트레이스가 열려 있지 않으면 이 호출만 담은
    트레이스를 하나 만들어 붙인다 — 스크립트에서 직접 부른 호출도 잃지 않는다.
    """
    if not enabled():
        return
    try:
        trace_id = _TRACE_ID.get()
        if trace_id is None:
            trace_id = uuid.uuid4().hex
            _emit("trace-create", {"id": trace_id, "name": name, "timestamp": _now()})
        body: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "traceId": trace_id,
            "name": name,
            "startTime": _iso(start),
            "endTime": _iso(end),
        }
        _put_if(body, "model", model)
        _put_if(body, "modelParameters", parameters)
        _put_if(body, "input", _payload(input))
        _put_if(body, "output", _payload(output))
        _put_if(body, "usage", usage)
        _put_if(body, "metadata", metadata)
        if error:
            body["level"] = "ERROR"
            body["statusMessage"] = error
        _emit("generation-create", body)
    except Exception as exc:                              # noqa: BLE001 — 관측은 흐름을 막지 않는다
        _debug(f"record_generation 실패: {type(exc).__name__}: {exc}")


# ─────────────────────────────────────────────────────────────
# 본문 가공 — 자를 것은 자르고, 끌 수 있으면 끈다
# ─────────────────────────────────────────────────────────────

def _payload(value: Any) -> Any:
    """input·output 을 보낼 수 있는 모양으로 만든다.

    `LANGFUSE_CAPTURE_CONTENT=0` 이면 본문 대신 길이만 남긴다 — 개인정보를 외부로
    내보내지 않으면서 «호출은 있었다 · 얼마나 길었다»는 볼 수 있게 하는 자리다.
    """
    if value is None:
        return None
    c = conf()
    if not c.capture_content:
        return {"omitted": True, "chars": len(_text(value))}
    return _clip(_jsonable(value), c.max_chars)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(
        _jsonable(value), ensure_ascii=False, default=str)


def _jsonable(value: Any) -> Any:
    """JSON 으로 실을 수 있는 값으로 낮춘다. 못 낮추는 것은 문자열로 떨어뜨린다."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"\n…({len(value) - limit}자 생략)"
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, limit) for v in value]
    return value


def _put_if(target: dict, key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _now() -> str:
    return _iso(time.time())


def _iso(ts: float | None) -> str:
    """Langfuse 가 받는 ISO 8601 UTC. `datetime.UTC` 는 3.11+ 라 쓰지 않는다."""
    moment = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return moment.isoformat().replace("+00:00", "Z")


# ─────────────────────────────────────────────────────────────
# 전송 — 백그라운드 워커 하나
# ─────────────────────────────────────────────────────────────

_QUEUE: queue.Queue[dict] = queue.Queue(maxsize=MAX_QUEUE)
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()

#: 큐에 들어갔지만 아직 전송이 끝나지 않은 이벤트 수. flush() 가 이걸 보고 기다린다.
_pending = 0
_IDLE = threading.Condition()

_STATS = {"queued": 0, "sent": 0, "dropped": 0, "failed": 0}


def stats() -> dict[str, int]:
    """보낸/버린/실패한 이벤트 수. 관측이 도는지 확인할 때 본다."""
    return dict(_STATS)


def _emit(event_type: str, body: dict) -> None:
    global _pending
    event = {"id": uuid.uuid4().hex, "type": event_type,
             "timestamp": _now(), "body": body}
    _ensure_worker()
    try:
        with _IDLE:
            _pending += 1
        _QUEUE.put_nowait(event)
        _STATS["queued"] += 1
    except queue.Full:
        with _IDLE:
            _pending -= 1
            _IDLE.notify_all()
        _STATS["dropped"] += 1
        _debug("큐가 가득 차 관측 이벤트를 버렸습니다")


def _ensure_worker() -> None:
    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_run, name="langfuse", daemon=True)
        _WORKER.start()
        atexit.register(flush)


def _run() -> None:
    """큐를 배치로 묶어 보낸다. 배치가 덜 차도 FLUSH_INTERVAL 이 지나면 보낸다."""
    global _pending
    while True:
        batch = [_QUEUE.get()]
        deadline = time.monotonic() + FLUSH_INTERVAL
        while len(batch) < BATCH_SIZE:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(_QUEUE.get(timeout=remaining))
            except queue.Empty:
                break
        try:
            _send(batch)
        finally:
            with _IDLE:
                _pending -= len(batch)
                _IDLE.notify_all()


def _send(batch: list[dict]) -> None:
    c = conf()
    try:
        req = urllib.request.Request(
            f"{c.host}{INGESTION_PATH}",
            data=json.dumps({"batch": batch, "metadata": _batch_metadata(c)},
                            ensure_ascii=False, default=str).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {c.auth}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=c.timeout) as resp:
            resp.read()
        _STATS["sent"] += len(batch)
    except Exception as exc:                              # noqa: BLE001 — 관측 실패는 삼킨다
        _STATS["failed"] += len(batch)
        detail = ""
        if isinstance(exc, urllib.error.HTTPError):
            with contextlib.suppress(Exception):
                detail = exc.read().decode("utf-8", "replace")[:500]
        _debug(f"전송 실패: {type(exc).__name__}: {exc} {detail}")


def _batch_metadata(c: _Conf) -> dict:
    meta = {"sdk_name": "pension_agent", "sdk_version": "stdlib-1",
            "environment": c.environment}
    if c.release:
        meta["release"] = c.release
    return meta


def flush(timeout: float = 5.0) -> bool:
    """큐가 빌 때까지 기다린다. 프로세스 종료 직전(atexit)과 테스트가 부른다.

    반환값은 «시간 안에 다 보냈는가». 못 보냈다고 예외를 던지지는 않는다.
    """
    with _IDLE:
        return _IDLE.wait_for(lambda: _pending == 0, timeout=timeout)


def _debug(message: str) -> None:
    if conf().debug:
        print(f"[langfuse] {message}", file=sys.stderr)
