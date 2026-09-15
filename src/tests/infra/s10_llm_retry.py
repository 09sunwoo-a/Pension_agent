"""llm — 429(속도 제한)·5xx(서버 오류) 재시도

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from tests.infra._common import check


# ─────────────────────────────────────────────────────────────
# llm — 429(속도 제한)·5xx(서버 오류) 재시도
#
# 행내 게이트웨이가 몰린 호출(브리핑 11연쇄·앱 기동 선생성)에 429 를 냈고, 재시도가
# 없어서 턴이 통째로 죽었다. 5xx 는 예전에 재시도 대상이 아니었는데(「기다려도 안
# 풀린다」는 전제였다) 실측이 그 전제를 뒤집었다 — gemma 리허설 한 블록의 11턴 중
# 10턴이 500 으로 죽었고 같은 프롬프트가 재시도에서 200 이었다.
#
# 재시도가 실제로 도는지, Retry-After 를 기다리는지, **요청이 잘못된 에러(4xx)는
# 재시도하지 않는지**를 본다. 마지막 것이 요건인 이유는 401·404 를 반복해 던지면
# 결과는 그대로인 채 진단만 늦어지기 때문이다.
#
# 재시도는 이미 맞은 뒤의 처방이라, 걸리지 않게 하는 처방이 함께 있다(llm 「호출 게이트」).
# 그쪽도 여기서 본다:
#   ② 동시성      — 동시에 나가는 호출이 MAX_CONCURRENCY 를 넘지 않는가.
#   ③ 적응형 감속 — 한 스레드가 맞으면 **아직 안 맞은 스레드도** 쉬는가. 맞은 쪽만 쉬면
#                   나머지가 그 틈을 메워 서버가 느끼는 압력이 안 준다.
# 그리고 x-client-user 가 실제로 헤더에 실리는가 — 전부 한 값으로 나가면 쿼터가 한 버킷에
# 몰려 429 를 자초한다.
# ─────────────────────────────────────────────────────────────

import io
import json
import threading
import time as _time
import urllib.error

from pension_agent import llm as _llm

# llm 은 stdlib 의 time.sleep·urllib.request.urlopen 을 모듈 속성으로 부르므로 그 자리를
# 갈아끼우고 finally 에서 원복한다(다른 검사가 진짜 sleep·urlopen 을 쓸 수 있게).
_saved_llm = (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.time.sleep,
              _llm.urllib.request.urlopen, _llm.MIN_INTERVAL)
_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY = "genai", "http://fake", "k"
# 간격 제한은 재시도 검사 동안 끈다 — 재시도 대기와 섞이면 무엇 때문에 잤는지 갈리지
# 않는다. 간격·동시성 자체는 아래 ②③ 에서 따로 본다.
_llm.MIN_INTERVAL = 0.0
_llm._next_free = 0.0
_sleeps: list[float] = []
# _llm.time 은 stdlib time 모듈 그 자체다 — 여기에 스텁을 꽂으면 이 파일의 time.sleep 도
# 같이 바뀐다. 진짜로 재워야 하는 곳(동시성 검사)을 위해 원본을 잡아 둔다.
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
    # 잠은 게이트(_pace)가 잔다 — 재시도 루프는 «다음 호출 가능 시각»만 민다. 실제
    # monotonic 이 흐르므로 정확히 1.0 이 아니라 그 언저리다(스텁 sleep 은 시간을
    # 흘려보내지 않으니 오차는 테스트가 도는 시간뿐이다).
    check(len(_sleeps) == 2 and all(0.9 < w <= 1.0 for w in _sleeps),
          "llm: Retry-After 초만큼 기다린다", str(_sleeps))

    # 서버가 준 Retry-After 는 **추측 백오프의 상한(30초)에 걸리지 않는다.** 행내 실측
    # (2026-09-08): 50초를 30초에서 끊자 다음 시도가 같은 429 를 맞고 20초를 더 쉬었다 —
    # 쉬는 시간은 같은데 재시도 횟수 하나가 헛되이 나갔다.
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
    # 그래도 터무니없는 값은 끊는다 — 상한은 서버 값 전용(MAX_RETRY_AFTER)이다.
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
    # 호출부가 «속도 제한»을 문자열 검색 없이 가르는 자리 — prebuild_briefings 가 429 면 멈춘다.
    check(getattr(_raised_exc, "status", None) == 429,
          "llm: LLMError 가 HTTP 상태 코드를 싣는다(status)", str(getattr(_raised_exc, "status", None)))

    # 5xx 는 재시도한다 — 두 번 죽고 세 번째에 살아나는 서버를 흉내 낸다.
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

    # 계속 5xx 면 상한에서 멈추고, 원인이 «속도 제한»이 아니라 «서버 오류»로 나간다 —
    # 직원이 할 일이 다르다(쿼터를 본다 / 잠시 후 다시 시도한다).
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

    # 요청이 잘못된 에러는 재시도하지 않는다 — 반복해도 결과가 같고 진단만 늦어진다.
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

    # 무엇이 잘못됐는지는 **응답 본문에만** 있다. 404 는 「경로가 없다」와 「그런 모델이
    # 없다」가 같은 코드로 오는데, 예전에는 본문을 버려서 `HTTP Error 404: Not Found`
    # 한 줄만 남았다 — 행내 첫 연결에서 이 한 줄로는 어느 쪽인지 갈리지 않았다.
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

    # 재시도를 다 쓴 경우에도 마지막 본문이 남는다(본문은 한 번만 읽을 수 있다).
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

    # x-client-user — 호출부가 준 주체가 실제 헤더로 나가는가.
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

    # 쿼터 버킷 분산 — x-client-user 가 게이트웨이의 쿼터 버킷이라, 사번 하나로 몰아서
    # 부르면 그 버킷이 바닥난다(STG 분당 10회). 사번 뒤에 임의 접미를 붙여 나누되
    # **사번은 앞에 그대로 남는다** — 이 값은 버킷이면서 감사 기록이라 누가 불렀는지가
    # 사라지면 안 된다.
    _saved_spread, _llm.CLIENT_USER_SPREAD = _llm.CLIENT_USER_SPREAD, 5
    try:
        check(_llm.spread_client_user("3902172") != _llm.spread_client_user("3902172"),
              "llm: 접미는 호출마다 새로 뽑는다(한 프로세스가 순차로 돌아도 나뉜다)")
        _bucket = _llm.spread_client_user("3902172")
        check(_bucket.startswith("3902172-") and len(_bucket) == len("3902172-") + 5,
              "llm: 사번은 앞에 그대로 남고 접미만 붙는다(감사 기록이 사라지지 않는다)",
              _bucket)
        # main.py 는 x_client_user 를 직접 넘긴다 — 그 경로가 빠지면 실서비스만 안 나뉜다.
        _seen.clear()
        _llm.generate("q", x_client_user="3902172")
        check(str(_seen.get("X-client-user", "")).startswith("3902172-"),
              "llm: 호출부가 직접 준 주체에도 분산이 걸린다(API 경로가 빠지지 않는다)",
              str(_seen.get("X-client-user")))
        # 관측에도 헤더와 **같은** 값이 실려야 한다 — 갈리면 대시보드에서 되짚을 수 없다.
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

    # ② 동시성 상한 — 동시에 열려 있는 호출이 MAX_CONCURRENCY 를 넘지 않는가.
    _llm._next_free = 0.0
    _inflight = {"now": 0, "max": 0}
    _inflight_lock = threading.Lock()

    def _urlopen_slow(req, timeout=None):
        with _inflight_lock:
            _inflight["now"] += 1
            _inflight["max"] = max(_inflight["max"], _inflight["now"])
        _real_sleep(0.02)         # 진짜 sleep — 겹칠 틈을 만든다(time.sleep 은 스텁)
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

    # ③ 적응형 감속 — 맞은 스레드가 아니라 **게이트 전체**가 밀리는가.
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
