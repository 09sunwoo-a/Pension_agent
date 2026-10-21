"""공용 LLM 클라이언트 — 프로바이더 격리. 호출부는 generate()/agenerate() 만 사용.

프로바이더(LLM_PROVIDER, 미지정 시 LLM_BASE_URL → genai, GEMINI_API_KEY → gemma, 그 외 anthropic):
  genai      사내 GenAI 플랫폼(OpenAI 호환 vLLM). urllib 만 사용
  gemma      사외 사전점검용 Google generativelanguage Gemma. urllib 만 사용
  anthropic  사외 테스트용 Anthropic SDK

- 설정은 `src/.env`(env.py). 진단: python -m pension_agent.common.env
- 모든 호출은 성공·실패 모두 `observability.record_generation()` 기록
- 실패는 전부 `LLMError` — 호출부가 삼켜 «자료 없음»으로 둔갑시키지 않게 구분

환경변수:
  LLM_PROVIDER      "genai" | "gemma" | "anthropic" (미지정 시 자동 판별)
  ENV_PATH          실행 단계(배포 시 Jenkins 가 serving 주입, 수동 설정 금지). 없으면 분석계
  LLM_BASE_URL_TRNN / _SERV
                    행내 GenAI 플랫폼 URL 두 벌(…/trnn/… · …/serv/…). ENV_PATH 로 고른다
  LLM_BASE_URL      단계 구분이 없을 때의 하나짜리(Gateway·사외). 단계별 값이 없으면 이것
  LLM_API_KEY       인증 키 (Authorization Bearer + kb-key 헤더에 동일 사용).
                    단계마다 다르면 LLM_API_KEY_TRNN / _SERV 으로 갈라 둘 수 있다
  LLM_MODEL         모델 슬러그. 비우면 게이트웨이 기본 라우팅
  LLM_TIMEOUT       초. 기본 60
  LLM_CLIENT_USER   x-client-user 기본값. 호출부가 실제 사용자를 주면 그것이 이긴다
  LLM_CLIENT_USER_SPREAD  쿼터 버킷 분산용 사번 접미 길이. 0(기본)이면 미부착
  LLM_PII_SCRUB     개인정보 꼴 마스킹. 기본 켜짐(0 으로 끔)
  LLM_RETRY_ATTEMPTS  429·5xx 재시도 횟수(첫 호출 포함). 기본 5
  LLM_MAX_CONCURRENCY  동시에 나가는 호출 수 상한. 기본 2
  LLM_MIN_INTERVAL_SEC  호출 사이 최소 간격(초). 기본 0.2
  LLM_COOLDOWN      429·5xx 후 프로세스 전체 감속 기준값(초). 기본 2(Retry-After 우선)
  GEMINI_API_KEY    gemma 프로바이더용 (Google AI Studio 발급 키)
  GEMMA_MODEL       gemma 모델 ID. 기본 gemma-4-31b-it
  GEMMA_THINKING_LEVEL  thinkingConfig.thinkingLevel. 기본 MINIMAL
  ANTHROPIC_API_KEY anthropic 프로바이더용 (테스트 경로)
  IRP_AGENT_MODEL   anthropic 모델. 기본 claude-sonnet-5
"""

from __future__ import annotations

import asyncio
import json
import re
import logging
import os
import random
import string
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pension_agent.common import env, observability, privacy

# 모듈 상수보다 먼저 .env 적재
env.load()

# genai — URL·키는 실행 단계(ENV_PATH: train | serving)별로 선택
STAGE = env.stage()
BASE_URL = env.staged("LLM_BASE_URL").rstrip("/")
API_KEY = env.staged("LLM_API_KEY")
#: URL·키의 출처 변수 이름(값 아님 — /health·401 안내용).
BASE_URL_SRC = env.source("LLM_BASE_URL")
API_KEY_SRC = env.source("LLM_API_KEY")


def key_stage_mismatch() -> bool:
    """URL 은 단계별 이름, 키는 접미사 없는 폴백에서 왔는지(→ APIM 401 원인)."""
    return _mismatch(BASE_URL_SRC, API_KEY_SRC)


def _mismatch(url_src: str, key_src: str) -> bool:
    """출처 이름 두 개로 판정(테스트용 — 모듈 상수는 임포트 시 고정)."""
    return bool(_stage_suffix(url_src) and key_src and not _stage_suffix(key_src))


def _stage_suffix(src: str) -> str:
    """출처 이름의 단계 접미(`_TRNN`·`_SERV`). 없으면 빈 문자열."""
    for tail in ("_TRNN", "_SERV"):
        if src.endswith(tail):
            return tail
    return ""


def wanted_key_name() -> str:
    """현재 URL 과 같은 단계의 키 변수 이름. 실제로 읽은 URL 변수 기준(`env.suffix()` 미사용)."""
    return f"LLM_API_KEY{_stage_suffix(BASE_URL_SRC)}"


def _auth_hint() -> str:
    """401·403 용 설정 진단 문구. 키 값 미포함."""
    where = f"URL={BASE_URL_SRC or '(없음)'} · 키={API_KEY_SRC or '(없음)'} · 단계={STAGE}"
    if key_stage_mismatch():
        return (f"설정: {where} — **키가 단계와 어긋난다.** {wanted_key_name()} 에 이 단계의 "
                f"키를 넣으십시오(지금은 접미사 없는 {API_KEY_SRC} 가 실려 나갔습니다).")
    return (f"설정: {where} — 게이트웨이가 키를 거부했습니다. 이 엔드포인트에 발급된 키가 "
            f"{API_KEY_SRC or 'LLM_API_KEY'} 에 들어 있는지 확인하십시오. "
            f"**한 달 동안 호출이 없으면 구독 키가 폐기된다**(2026-09-16 행내 실측 — 서빙계가 그렇게 끊겼다). "
            f"오래 안 쓴 단계라면 콘솔에서 재발급받는 것이 먼저입니다. 그 밖의 원인: 다른 단계의 키·앞뒤 공백.")


PROVIDER = os.getenv("LLM_PROVIDER") or (
    "genai" if BASE_URL
    else "gemma" if os.getenv("GEMINI_API_KEY")
    else "anthropic"
)

#: max_tokens 기본값.
DEFAULT_MAX_TOKENS = 900

#: 모델 슬러그. 비우면 payload 에서 `model` 키 제외(기본).
#: LLM Gateway(LiteLLM)는 필수(라우팅 키), 내부 GenAI 플랫폼은 URL 이 곧 모델 — 넣으면 404.
MODEL = os.getenv("LLM_MODEL", "")
TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
#: 429·5xx 재시도 횟수(첫 호출 포함). genai·gemma 전용(anthropic SDK 는 자체 재시도).
RETRY_ATTEMPTS = int(os.getenv("LLM_RETRY_ATTEMPTS", "5"))

# 개인정보 마스킹 — 게이트웨이 필터 400(재시도 불가) 회피. 진단 시 LLM_PII_SCRUB=0

#: 개인정보 꼴 마스킹 여부. 기본 켜짐.
PII_SCRUB = (os.getenv("LLM_PII_SCRUB", "1") or "1").strip().lower() not in ("0", "false", "no")


def _scrub(prompt: str, system: str | None) -> tuple[str, str | None, list[str]]:
    """프롬프트·시스템 프롬프트 마스킹, 걸린 룰 이름 반환. 로그에는 룰 이름만."""
    if not PII_SCRUB:
        return prompt, system, []
    prompt, hit = privacy.mask(prompt)
    if system:
        system, hit_system = privacy.mask(system)
        hit += [r for r in hit_system if r not in hit]
    if hit:
        observability.step("llm", "개인정보 가림", masked=", ".join(hit), level=logging.WARNING)
    return prompt, system, hit


# 호출 게이트(429 예방, genai·gemma 전용): ① 동시성 상한 ② 최소 간격
# ③ 429·5xx 시 프로세스 전체 감속 — 맞은 스레드만 쉬면 나머지가 틈을 메움

#: 동시에 나가는 호출 수 상한. 1 이면 완전 직렬.
MAX_CONCURRENCY = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "2")))
#: 호출 사이 최소 간격(초). 0 이면 제한 없음.
MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL_SEC", "0.2") or 0)
#: Retry-After 없을 때 지수 백오프 기준값(초).
COOLDOWN = float(os.getenv("LLM_COOLDOWN", "2"))
#: 추정 백오프 상한(초).
MAX_BACKOFF = 30.0
#: 서버 Retry-After 상한(초). 추정 상한과 별도 — 짧게 끊으면 재시도 횟수만 소모.
MAX_RETRY_AFTER = 120.0

_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENCY)
_PACE_LOCK = threading.Lock()
#: 다음 호출 가능 시각(time.monotonic). 감속은 이 값을 뒤로 밂.
_next_free = 0.0


def _pace() -> None:
    """최소 간격·감속만큼 대기. 시각 예약은 락 안, sleep 은 락 밖."""
    global _next_free
    with _PACE_LOCK:
        now = time.monotonic()
        start = max(now, _next_free)
        _next_free = start + MIN_INTERVAL
    wait = start - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _slow_down(seconds: float) -> None:
    """지금부터 seconds 동안 모든 호출 정지."""
    global _next_free
    with _PACE_LOCK:
        _next_free = max(_next_free, time.monotonic() + seconds)


@contextmanager
def _gate() -> Iterator[None]:
    """동시성 상한 + 최소 간격(HTTP 호출 1회 단위)."""
    with _SLOTS:
        _pace()
        yield


# 호출 주체(x-client-user) — 감사 기록·쿼터 버킷. 진입점이 ContextVar 로 턴 전체에 설정

#: 호출 주체 미지정 시 기본값.
DEFAULT_CLIENT_USER = os.getenv("LLM_CLIENT_USER", "pension-agent")

_CLIENT_USER: ContextVar[str] = ContextVar("llm_client_user", default="")


@contextmanager
def client_user(name: str | None) -> Iterator[None]:
    """블록 안 LLM 호출의 x-client-user 설정. name 이 비면 변경 없음."""
    if not name:
        yield
        return
    token = _CLIENT_USER.set(name)
    try:
        yield
    finally:
        _CLIENT_USER.reset(token)


def current_client_user() -> str:
    """현재 x-client-user. ContextVar > 환경변수 기본값."""
    return _CLIENT_USER.get() or DEFAULT_CLIENT_USER


# 쿼터 버킷 분산 — 사번 뒤 임의 접미(사번은 감사용으로 유지). 기본 꺼짐

#: 사번 뒤 임의 접미 길이. 0 이면 미부착(기본).
CLIENT_USER_SPREAD = max(0, int(os.getenv("LLM_CLIENT_USER_SPREAD", "0") or 0))
#: 접미 문자 집합(하이픈으로 연결).
_SPREAD_ALPHABET = string.ascii_letters + string.digits


def spread_client_user(base: str) -> str:
    """접미를 붙인 x-client-user(호출마다 새로 생성). 꺼져 있으면 base 그대로."""
    if CLIENT_USER_SPREAD <= 0 or not base:
        return base
    suffix = "".join(random.choice(_SPREAD_ALPHABET) for _ in range(CLIENT_USER_SPREAD))
    return f"{base}-{suffix}"

# gemma (사외 사전점검)
GEMMA_MODEL = os.getenv("GEMMA_MODEL", "gemma-4-31b-it")
GEMMA_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMMA_MODEL}:generateContent"
)
#: MINIMAL 필수 — 기본값이면 thought 가 maxOutputTokens 를 소진해 빈 답. thinkingBudget=0 은 거부됨.
GEMMA_THINKING_LEVEL = os.getenv("GEMMA_THINKING_LEVEL", "MINIMAL")

# anthropic (사외 테스트)
ANTHROPIC_MODEL = os.getenv("IRP_AGENT_MODEL", "claude-sonnet-5")

_anthropic_client = None


class LLMError(RuntimeError):
    """LLM 호출 실패(미설정·인증·타임아웃·프로바이더 오류). 호출부에서 삼키기 금지.

    status: HTTP 상태 코드(없으면 None).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def available() -> bool:
    """LLM 호출 가능 여부."""
    if PROVIDER == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    if PROVIDER == "gemma":
        return bool(os.getenv("GEMINI_API_KEY"))
    return bool(BASE_URL and API_KEY)


def _generate_genai(prompt: str, system: str | None, max_tokens: int,
                    temperature: float, x_client_user: str) -> tuple[str, dict]:
    """OpenAI 호환 /chat/completions 호출(urllib)."""
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    payload: dict = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if MODEL:
        payload["model"] = MODEL

    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            # APIM 구독 키 헤더는 kb-key(Ocp-Apim-Subscription-Key 미인식)
            "kb-key": API_KEY,
            "x-client-user": x_client_user,
        },
        method="POST",
    )
    try:
        body = _post_json(req)
    except LLMError as exc:
        # 401·403 에 어느 변수의 키가 나갔는지 덧붙임
        if exc.status in (401, 403):
            raise LLMError(f"{exc}\n{_auth_hint()}", status=exc.status) from exc
        raise
    usage = body.get("usage") or {}
    return body["choices"][0]["message"]["content"], {
        "model": body.get("model") or MODEL or "(gateway-default)",
        "usage": _usage(usage.get("prompt_tokens"), usage.get("completion_tokens"),
                        usage.get("total_tokens")),
    }


def _usage(prompt_tokens: Any, completion_tokens: Any, total_tokens: Any = None) -> dict | None:
    """토큰 사용량 → Langfuse 꼴. 없으면 None."""
    if prompt_tokens is None and completion_tokens is None:
        return None
    used = {"unit": "TOKENS"}
    if prompt_tokens is not None:
        used["input"] = int(prompt_tokens)
    if completion_tokens is not None:
        used["output"] = int(completion_tokens)
    if total_tokens is not None:
        used["total"] = int(total_tokens)
    elif "input" in used and "output" in used:
        used["total"] = used["input"] + used["output"]
    return used


def _post_json(req: urllib.request.Request) -> dict:
    """urlopen + JSON 파싱(genai·gemma 공용). 429·5xx 재시도, 그 외 4xx 는 즉시 LLMError.

    - 대기는 직접 sleep 하지 않고 `_slow_down()` → 다음 `_gate()` 에서(이중 대기 방지, 전 스레드 공유)
    - 최종 실패 문구에 원인(429 / 5xx) 구분
    """
    last: urllib.error.HTTPError | None = None
    last_body = ""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with _gate(), urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = _error_body(exc)
            if not _retryable(exc.code):
                # 4xx 는 응답 본문이 진단의 전부 — 예외 문장은 여러 단계에서 잘리므로 전문을 로그에 별도 기록
                if body:
                    logging.getLogger("agent").error(
                        "LLM %s %s — 응답 전문 ↓\n%s\n요청 URL: %s",
                        exc.code, exc.reason, _full_error_body(exc, body), req.full_url)
                raise LLMError(
                    f"HTTP {exc.code} {exc.reason} — {req.full_url}"
                    + (f"\n응답: {body}" if body else ""), status=exc.code) from exc
            last, last_body = exc, body
            if attempt == RETRY_ATTEMPTS - 1:
                break
            wait, told = _backoff(exc, attempt)
            # 대기값 출처(서버/추정) 기록 — 상한 조정 근거
            observability.step("llm", retries=f"{attempt + 1}/{RETRY_ATTEMPTS}", status=exc.code,
                               wait=f"{wait:.1f}초", source="서버" if told else "추정",
                               level=logging.WARNING)
            _slow_down(wait)
    code = last.code if last else 0
    detail = ("속도 제한. 호출 간격을 두거나 쿼터를 확인하십시오." if code == 429
              else "서버 오류. 잠시 후 다시 시도하십시오(요청이 잘못된 것이 아닙니다).")
    raise LLMError(
        f"HTTP {code} — {RETRY_ATTEMPTS}회 시도 후에도 실패. {detail} "
        f"(동시 {MAX_CONCURRENCY} · 간격 {MIN_INTERVAL}초 — LLM_MAX_CONCURRENCY 를 낮추거나 "
        f"LLM_MIN_INTERVAL_SEC 를 늘립니다.)"
        + (f"\n응답: {last_body}" if last_body else ""), status=code or None) from last


#: 예외 문장에 싣는 오류 본문 상한(화면·단계 로그로 흘러감).
ERROR_BODY_LIMIT = 400

#: 진단 로그용 오류 전문 상한(HTML 오류 페이지 대비).
FULL_ERROR_BODY_LIMIT = 4000


def _full_error_body(exc: urllib.error.HTTPError, already: str) -> str:
    """진단 로그용 응답 전문(`_error_body` 가 보관한 원문, 없으면 잘린 것)."""
    raw = getattr(exc, "_full_body", None) or already
    return raw[:FULL_ERROR_BODY_LIMIT]


def _error_body(exc: urllib.error.HTTPError) -> str:
    """오류 응답 본문 한 줄(잘림). 못 읽으면 빈 문자열. 스트림이라 한 번만 읽힘."""
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — 원래 오류를 가리지 않게
        return ""
    # 전문은 예외에 보관(`_full_error_body`)
    exc._full_body = raw  # noqa: SLF001
    text = " ".join(raw.split())
    return text[:ERROR_BODY_LIMIT] + ("…" if len(text) > ERROR_BODY_LIMIT else "")


def _retryable(code: int) -> bool:
    """재시도 대상 여부(429·5xx)."""
    return code == 429 or 500 <= code < 600


def _backoff(exc: urllib.error.HTTPError, attempt: int) -> tuple[float, bool]:
    """(대기 초, 서버 제공 여부). Retry-After 우선, 없으면 지수 백오프 + 지터(동시 재시도 분산)."""
    retry_after = (exc.headers.get("Retry-After") or "").strip() if exc.headers else ""
    try:
        return min(float(retry_after), MAX_RETRY_AFTER), True
    except ValueError:
        pass
    return min(COOLDOWN * (2 ** attempt) * (0.5 + random.random()), MAX_BACKOFF), False


def _generate_gemma(prompt: str, system: str | None, max_tokens: int,
                    temperature: float) -> tuple[str, dict]:
    """generativelanguage :generateContent 호출(urllib). systemInstruction 거부 — 프롬프트 앞에 접합."""
    text = f"{system}\n\n{prompt}" if system else prompt
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
            "thinkingConfig": {"thinkingLevel": GEMMA_THINKING_LEVEL},
        },
    }
    req = urllib.request.Request(
        GEMMA_ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": os.environ["GEMINI_API_KEY"].strip(),
        },
        method="POST",
    )
    payload = _post_json(req)
    candidates = payload.get("candidates") or []
    if not candidates:
        raise LLMError(f"gemma 응답에 candidates 가 없습니다: {payload}")
    parts = candidates[0].get("content", {}).get("parts", [])
    # thought 파트 제외
    text_out = "".join(
        p.get("text", "") for p in parts
        if isinstance(p, dict) and "text" in p and not p.get("thought")
    ).strip()
    meta = payload.get("usageMetadata") or {}
    return text_out, {
        "model": GEMMA_MODEL,
        "usage": _usage(meta.get("promptTokenCount"), meta.get("candidatesTokenCount"),
                        meta.get("totalTokenCount")),
    }


def _generate_anthropic(prompt: str, system: str | None, max_tokens: int,
                        temperature: float) -> tuple[str, dict]:
    """Anthropic SDK 호출(테스트 경로, 지연 임포트)."""
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic  # noqa: PLC0415 — genai 전용 환경엔 미설치일 수 있음

        _anthropic_client = Anthropic()
    kwargs: dict = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system is not None:
        kwargs["system"] = system
    msg = _anthropic_client.messages.create(**kwargs)
    used = getattr(msg, "usage", None)
    return "".join(b.text for b in msg.content if b.type == "text"), {
        "model": getattr(msg, "model", None) or ANTHROPIC_MODEL,
        "usage": _usage(getattr(used, "input_tokens", None),
                        getattr(used, "output_tokens", None)),
    }


def generate(prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS, system: str | None = None,
             temperature: float = 0.2, x_client_user: str = "",
             name: str = "llm.generate") -> str:
    """단발 생성. 응답 문자열 반환, 모든 실패는 `LLMError`(원인 문자열 보존).

    name: 관측용 호출 이름. x_client_user: 비우면 client_user 컨텍스트.
    """
    system = system or None   # "" 은 시스템 메시지 없음
    # 버킷 분산은 여기 한 곳에서 — main.py 는 x_client_user 를 직접 넘김
    x_client_user = spread_client_user(x_client_user or current_client_user())
    if not available():
        raise LLMError(
            "LLM 미설정 — PROVIDER=%s. genai 는 LLM_BASE_URL/LLM_API_KEY, "
            "gemma 는 GEMINI_API_KEY, anthropic 은 ANTHROPIC_API_KEY 를 확인하십시오. "
            "어느 .env 가 읽혔는지는 python -m pension_agent.common.env 로 봅니다."
            % PROVIDER
        )
    # 마스킹은 여기 한 곳에서(프로바이더 공통)
    prompt, system, _masked = _scrub(prompt, system)
    started = time.time()
    try:
        if PROVIDER == "anthropic":
            text, meta = _generate_anthropic(prompt, system, max_tokens, temperature)
        elif PROVIDER == "gemma":
            text, meta = _generate_gemma(prompt, system, max_tokens, temperature)
        else:
            text, meta = _generate_genai(prompt, system, max_tokens, temperature, x_client_user)
    except Exception as exc:
        _observe(name, started, prompt, system, max_tokens, temperature, x_client_user,
                 meta={}, output=None, error=f"{type(exc).__name__}: {exc}")
        if isinstance(exc, LLMError):
            raise
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc
    _observe(name, started, prompt, system, max_tokens, temperature, x_client_user,
             meta=meta, output=text, error=None)
    return text


def _observe(name: str, started: float, prompt: str, system: str | None, max_tokens: int,
             temperature: float, x_client_user: str, *, meta: dict, output: str | None,
             error: str | None) -> None:
    """호출 한 건 관측 기록 + `prompt_chars` 점수(입력 크기 분포용).

    system 이 있으면 채팅 메시지 꼴로 기록(Langfuse 가 역할별 렌더).
    """
    observability.score("prompt_chars", prompt_chars(prompt, system), comment=name)
    # 호출별 로그는 DEBUG, INFO 는 턴 끝 합계만
    observability.tally_llm(prompt_chars(prompt, system))
    observability.step("llm", name, chars=prompt_chars(prompt, system),
                       elapsed=f"{time.time() - started:.1f}초", error=error,
                       level=logging.DEBUG)
    observability.record_generation(
        name,
        model=meta.get("model") or _default_model_label(),
        input=([{"role": "system", "content": system},
                {"role": "user", "content": prompt}] if system else prompt),
        output=output,
        usage=meta.get("usage"),
        start=started,
        end=time.time(),
        parameters={"max_tokens": max_tokens, "temperature": temperature},
        metadata={"provider": PROVIDER, "x_client_user": x_client_user,
                  "prompt_chars": prompt_chars(prompt, system)},
        error=error,
    )


# 응답에서 JSON 추출(코드블록·전후 설명 허용). 못 찾으면 «없음» — 호출부가 규격 밖 응답으로 처리

def json_object(text: str) -> dict | None:
    """첫 JSON 객체. 없음·파손·비객체면 None."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        val = json.loads(m.group())
    except ValueError:
        return None
    return val if isinstance(val, dict) else None


def json_list(text: str) -> list:
    """JSON 배열. 없음·파손·비배열이면 빈 목록."""
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        val = json.loads(m.group())
    except ValueError:
        return []
    return val if isinstance(val, list) else []


def prompt_chars(prompt: str, system: str | None) -> int:
    """입력 글자 수(시스템 프롬프트 포함)."""
    return len(prompt or "") + len(system or "")


def _default_model_label() -> str:
    """응답에 모델명이 없을 때의 표기."""
    if PROVIDER == "anthropic":
        return ANTHROPIC_MODEL
    if PROVIDER == "gemma":
        return GEMMA_MODEL
    return MODEL or "(gateway-default)"


async def agenerate(prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS, system: str | None = None,
                    temperature: float = 0.2, x_client_user: str = "",
                    name: str = "llm.agenerate") -> str:
    """비동기 호출 — `generate` 를 스레드로(컨텍스트 복사로 트레이스 유지)."""
    return await asyncio.to_thread(
        generate, prompt, max_tokens=max_tokens, system=system,
        temperature=temperature, x_client_user=x_client_user, name=name,
    )
