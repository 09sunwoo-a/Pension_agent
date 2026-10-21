"""Langfuse 전송 — 백그라운드 워커 하나."""

from __future__ import annotations

import atexit
import contextlib
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from pension_agent.common.observability._conf import BATCH_SIZE, FLUSH_INTERVAL, INGESTION_PATH, MAX_QUEUE, _Conf, conf
from pension_agent.common.observability._payload import _now


_QUEUE: queue.Queue[dict] = queue.Queue(maxsize=MAX_QUEUE)
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()

#: 큐 투입 후 전송 미완료 이벤트 수(flush() 대기 기준).
_pending = 0
_IDLE = threading.Condition()

_STATS = {"queued": 0, "sent": 0, "dropped": 0, "failed": 0}

#: 마지막 전송 실패 원인(백그라운드라 예외 대신 이 값으로 진단).
_LAST_ERROR: str | None = None
_warned = False


def stats() -> dict[str, int]:
    """보낸/버린/실패한 이벤트 수."""
    return dict(_STATS)


def last_error() -> str | None:
    """마지막 전송 실패 원인. 없으면 None."""
    return _LAST_ERROR


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
    """큐를 배치로 전송. 미충족이어도 FLUSH_INTERVAL 경과 시 전송."""
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
        global _LAST_ERROR, _warned
        _STATS["failed"] += len(batch)
        detail = ""
        if isinstance(exc, urllib.error.HTTPError):
            with contextlib.suppress(Exception):
                detail = " " + exc.read().decode("utf-8", "replace")[:300]
        _LAST_ERROR = f"{type(exc).__name__}: {exc}{detail}"
        # 첫 실패만 한 줄 출력(조용한 실패 방지). 전부 보려면 LANGFUSE_DEBUG=1
        if not _warned or conf().debug:
            _warned = True
            print(f"[langfuse] 전송 실패 — {_LAST_ERROR}\n"
                  f"[langfuse]   host={c.host} · 진단: python -m pension_agent.common.observability",
                  file=sys.stderr)


def _batch_metadata(c: _Conf) -> dict:
    meta = {"sdk_name": "pension_agent", "sdk_version": "stdlib-1",
            "environment": c.environment}
    if c.release:
        meta["release"] = c.release
    return meta


def flush(timeout: float = 5.0) -> bool:
    """큐가 빌 때까지 대기(atexit·테스트). 시간 내 완료 여부 반환, 예외 없음."""
    with _IDLE:
        return _IDLE.wait_for(lambda: _pending == 0, timeout=timeout)


def _debug(message: str) -> None:
    if conf().debug:
        print(f"[langfuse] {message}", file=sys.stderr)
