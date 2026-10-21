"""llm — 429·5xx 재시도 · 호출 게이트(동시성·감속) · x-client-user.

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)
"""

from __future__ import annotations

from tests.infra._common import check


# 확인: 429·5xx 재시도 · Retry-After 대기 · 4xx 미재시도(진단 지연 방지)
# 호출 게이트: ② 동시성 상한 · ③ 적응형 감속(안 맞은 스레드도 대기) · x-client-user 헤더(쿼터 버킷)

import io
import json
import threading
import time as _time
import urllib.error

from pension_agent.common import llm as _llm

# llm 의 time.sleep·urlopen 을 교체, finally 에서 원복
_saved_llm = (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.time.sleep,
              _llm.urllib.request.urlopen, _llm.MIN_INTERVAL)
_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY = "genai", "http://fake", "k"
# 간격 제한 끔 — 재시도 대기와 구분(간격·동시성은 ②③ 에서 별도 검사)
_llm.MIN_INTERVAL = 0.0
_llm._next_free = 0.0
_sleeps: list[float] = []
# _llm.time 은 stdlib time 모듈 자체 — 실제 sleep 원본 확보(동시성 검사용)
_real_sleep = _llm.time.sleep
_llm.time.sleep = _sleeps.append


def _http_error(code: int, headers: dict | None = None,
                body: bytes = b"") -> urllib.error.HTTPError:
    import email.message
    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("http://fake/chat/completions", code, "err", msg,
                                  io.BytesIO(body))


class _FakeResp:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self):
        return json.dumps({"choices": [{"message": {"content": "답"}}]}).encode()


try:
    calls = {"n": 0}

    def _urlopen_429_twice(req, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(429, {"Retry-After": "1"})
        return _FakeResp()

    _llm.urllib.request.urlopen = _urlopen_429_twice
    out = _llm.generate("q")
    check(out == "답" and calls["n"] == 3, "llm: 429 두 번 뒤 재시도로 성공한다",
          f"calls={calls['n']} out={out!r}")
    # 대기는 게이트(_pace)가 수행 — monotonic 흐름 때문에 1.0 언저리
    check(len(_sleeps) == 2 and all(0.9 < w <= 1.0 for w in _sleeps),
          "llm: Retry-After 초만큼 기다린다", str(_sleeps))

    # 서버 Retry-After 는 추측 백오프 상한(30초) 미적용 — 끊으면 헛된 재시도 발생
    calls["n"], _sleeps[:] = 0, []
    _llm._next_free = 0.0

    def _urlopen_429_long(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429, {"Retry-After": "50"})
        return _FakeResp()

    _llm.urllib.request.urlopen = _urlopen_429_long
    _llm.generate("q")
    check(len(_sleeps) == 1 and 49.0 < _sleeps[0] <= 50.0,
          "llm: 서버 Retry-After 는 추측 상한(30초)에 걸리지 않고 그대로 쉰다", str(_sleeps))
    # 터무니없는 값은 MAX_RETRY_AFTER 로 절단
    _wait, _told = _llm._backoff(_http_error(429, {"Retry-After": "9999"}), 0)
    check(_told and _wait == _llm.MAX_RETRY_AFTER,
          "llm: Retry-After 가 터무니없이 크면 MAX_RETRY_AFTER 에서 끊는다", f"{_wait}")
    _wait, _told = _llm._backoff(_http_error(429), 10)
    check(not _told and _wait <= _llm.MAX_BACKOFF,
          "llm: Retry-After 가 없으면 추측 백오프이고 MAX_BACKOFF 를 넘지 않는다", f"{_wait}")

    calls["n"], _sleeps[:] = 0, []
    _llm._next_free = 0.0
    _llm.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
        _http_error(429))
    try:
        _llm.generate("q")
        _raised = None
        _raised_exc = None
    except _llm.LLMError as exc:
        _raised = str(exc)
        _raised_exc = exc
    check(_raised is not None and "429" in _raised,
          "llm: 계속 429 면 상한에서 멈추고 LLMError 로 올린다", str(_raised))
    # 호출부가 문자열 검색 없이 429 판별(status)
    check(getattr(_raised_exc, "status", None) == 429,
          "llm: LLMError 가 HTTP 상태 코드를 싣는다(status)", str(getattr(_raised_exc, "status", None)))

    # 5xx 재시도 — 두 번 실패 후 세 번째 성공
    calls["n"], _sleeps[:] = 0, []

    def _urlopen_500_twice(req, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(500)
        return _FakeResp()

    _llm.urllib.request.urlopen = _urlopen_500_twice
    out = _llm.generate("q")
    check(out == "답" and calls["n"] == 3, "llm: 500 두 번 뒤 재시도로 성공한다",
          f"calls={calls['n']} out={out!r}")

    # 계속 5xx 면 «속도 제한»이 아닌 «서버 오류»로 보고(직원 조치가 다름)
    calls["n"], _sleeps[:] = 0, []
    _llm.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
        _http_error(503))
    try:
        _llm.generate("q")
        _raised = None
    except _llm.LLMError as exc:
        _raised = str(exc)
    check(_raised is not None and "503" in _raised and "서버 오류" in _raised,
          "llm: 계속 5xx 면 상한에서 멈추고 서버 오류로 말한다", str(_raised))

    # 4xx 는 재시도 금지 — 결과 동일, 진단만 지연
    calls["n"] = 0

    def _urlopen_401(req, timeout=None):
        calls["n"] += 1
        raise _http_error(401)

    _llm.urllib.request.urlopen = _urlopen_401
    try:
        _llm.generate("q")
    except _llm.LLMError:
        pass
    check(calls["n"] == 1, "llm: 4xx(요청이 잘못된 에러)는 재시도하지 않는다", f"calls={calls['n']}")

    # 원인(경로 없음 vs 모델 없음)은 응답 본문에만 — 본문·URL 함께 전달
    _llm.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
        _http_error(404, body=b'{"error": {"code": "model_not_found"}}'))
    try:
        _llm.generate("q")
        _raised = None
    except _llm.LLMError as exc:
        _raised = str(exc)
    check(_raised is not None and "model_not_found" in _raised
          and "/chat/completions" in _raised,
          "llm: HTTP 오류는 응답 본문과 부른 URL 을 함께 올린다", str(_raised))

    # 재시도 소진 후에도 마지막 응답 본문 유지(본문은 한 번만 읽힘)
    _sleeps[:] = []
    _llm._next_free = 0.0
    _llm.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
        _http_error(429, body=b"quota exceeded for this key"))
    try:
        _llm.generate("q")
        _raised = None
    except _llm.LLMError as exc:
        _raised = str(exc)
    check(_raised is not None and "quota exceeded" in _raised,
          "llm: 재시도를 다 써도 마지막 응답 본문이 남는다", str(_raised))

    # x-client-user 헤더 실림
    _llm._next_free = 0.0
    _seen: dict = {}

    def _urlopen_capture(req, timeout=None):
        _seen.update(req.headers)
        return _FakeResp()

    _llm.urllib.request.urlopen = _urlopen_capture
    with _llm.client_user("emp-0417"):
        _llm.generate("q")
    check(_seen.get("X-client-user") == "emp-0417",
          "llm: client_user() 로 감싼 호출은 그 주체로 나간다", str(_seen.get("X-client-user")))
    _seen.clear()
    _llm.generate("q")
    check(_seen.get("X-client-user") == _llm.DEFAULT_CLIENT_USER,
          "llm: 주체를 주지 않으면 기본값으로 나간다(빈 값 금지)",
          str(_seen.get("X-client-user")))

    # 쿼터 버킷 분산 — 사번 + 임의 접미(사번은 감사 기록이라 앞에 유지)
    _saved_spread, _llm.CLIENT_USER_SPREAD = _llm.CLIENT_USER_SPREAD, 5
    try:
        check(_llm.spread_client_user("3902172") != _llm.spread_client_user("3902172"),
              "llm: 접미는 호출마다 새로 뽑는다(한 프로세스가 순차로 돌아도 나뉜다)")
        _bucket = _llm.spread_client_user("3902172")
        check(_bucket.startswith("3902172-") and len(_bucket) == len("3902172-") + 5,
              "llm: 사번은 앞에 그대로 남고 접미만 붙는다(감사 기록이 사라지지 않는다)",
              _bucket)
        # main.py 는 x_client_user 직접 전달 — 이 경로 누락 시 실서비스만 미분산
        _seen.clear()
        _llm.generate("q", x_client_user="3902172")
        check(str(_seen.get("X-client-user", "")).startswith("3902172-"),
              "llm: 호출부가 직접 준 주체에도 분산이 걸린다(API 경로가 빠지지 않는다)",
              str(_seen.get("X-client-user")))
        # 관측에도 헤더와 같은 값
        _seen.clear()
        with _llm.client_user("emp-0417"):
            _llm.generate("q")
        check(str(_seen.get("X-client-user", "")).startswith("emp-0417-"),
              "llm: client_user() 로 감싼 주체에도 분산이 걸린다",
              str(_seen.get("X-client-user")))
    finally:
        _llm.CLIENT_USER_SPREAD = _saved_spread
    check(_llm.spread_client_user("3902172") == "3902172",
          "llm: 기본은 꺼짐 — 감사 기록의 모양을 조용히 바꾸지 않는다",
          _llm.spread_client_user("3902172"))

    # ② 동시성 상한 — 동시 호출 ≤ MAX_CONCURRENCY
    _llm._next_free = 0.0
    _inflight = {"now": 0, "max": 0}
    _inflight_lock = threading.Lock()

    def _urlopen_slow(req, timeout=None):
        with _inflight_lock:
            _inflight["now"] += 1
            _inflight["max"] = max(_inflight["max"], _inflight["now"])
        _real_sleep(0.02)         # 실제 sleep — 겹칠 틈 확보(time.sleep 은 스텁)
        with _inflight_lock:
            _inflight["now"] -= 1
        return _FakeResp()

    _llm.urllib.request.urlopen = _urlopen_slow
    _threads = [threading.Thread(target=_llm.generate, args=("q",)) for _ in range(8)]
    for t in _threads:
        t.start()
    for t in _threads:
        t.join()
    check(_inflight["max"] <= _llm.MAX_CONCURRENCY,
          f"llm: 동시 호출이 상한({_llm.MAX_CONCURRENCY})을 넘지 않는다",
          f"관측 최대 {_inflight['max']}")

    # ③ 적응형 감속 — 맞은 스레드만이 아니라 게이트 전체 지연
    _llm._next_free = 0.0
    _llm._slow_down(5.0)
    _pushed = _llm._next_free - _time.monotonic()
    check(4.0 < _pushed <= 5.0,
          "llm: 429·5xx 를 맞으면 프로세스 전체의 다음 호출 시각이 밀린다", f"{_pushed:.2f}초")
    _llm._next_free = 0.0
finally:
    (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.time.sleep,
     _llm.urllib.request.urlopen, _llm.MIN_INTERVAL) = _saved_llm
    _llm._next_free = 0.0
