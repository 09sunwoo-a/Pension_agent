"""전송 — 백그라운드 워커 하나

`observability.py` 에서 갈라낸 모듈 — 공개 표면은 패키지 `__init__` 이다.
"""

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

from pension_agent.observability._conf import BATCH_SIZE, FLUSH_INTERVAL, INGESTION_PATH, MAX_QUEUE, _Conf, conf
from pension_agent.observability._payload import _now


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

#: 마지막 전송 실패의 원인. 전송은 백그라운드라 예외를 호출부로 올릴 수 없어서, 진단은
#: 이 값과 아래 «첫 실패 한 줄»이 전담한다.
_LAST_ERROR: str | None = None
_warned = False


def stats() -> dict[str, int]:
    """보낸/버린/실패한 이벤트 수. 관측이 도는지 확인할 때 본다."""
    return dict(_STATS)


def last_error() -> str | None:
    """마지막 전송 실패 원인. 실패가 없었으면 None."""
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
        global _LAST_ERROR, _warned
        _STATS["failed"] += len(batch)
        detail = ""
        if isinstance(exc, urllib.error.HTTPError):
            with contextlib.suppress(Exception):
                detail = " " + exc.read().decode("utf-8", "replace")[:300]
        _LAST_ERROR = f"{type(exc).__name__}: {exc}{detail}"
        # **첫 실패는 반드시 한 줄 알린다.** 삼키는 것은 «에이전트를 세우지 않기» 위해서지
        # «아무 말도 하지 않기» 위해서가 아니다 — 조용히 실패하면 «전송이 깨졌다»와 «애초에
        # 안 켜졌다»가 화면에서 구별되지 않고, 그 상태로 대시보드만 들여다보게 된다.
        # 두 번째부터는 잠잠하다(턴마다 같은 줄이 쌓이면 그게 다시 노이즈다). 전부 보려면
        # LANGFUSE_DEBUG=1.
        if not _warned or conf().debug:
            _warned = True
            print(f"[langfuse] 전송 실패 — {_LAST_ERROR}\n"
                  f"[langfuse]   host={c.host} · 진단: python -m pension_agent.observability",
                  file=sys.stderr)


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
