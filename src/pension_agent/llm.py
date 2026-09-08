"""공용 LLM 클라이언트 — 프로바이더 전환. 환경이 바뀌면 이 파일만 손대면 되도록 격리한다.

호출부(pitch.py·engine.py·agent.py 등)는 provider 세부(메시지 형식·헤더·텍스트 추출)를
몰라도 되도록 generate()/agenerate() 만 쓴다.

━━ 프로바이더 ━━
  genai      사내 GenAI 플랫폼 (OpenAI 호환 vLLM). base_url + kb-key + x-client-user.
             표준 라이브러리(urllib)만 사용 — 망분리 환경에서 추가 의존성이 필요 없다.
  gemma      외부 사전점검용 Google generativelanguage API 의 Gemma. 사내 플랫폼이
             서빙하는 것과 같은 계열 모델이라, 내부 이관 전에 "gemma 로도 답이
             잘 나오는가"를 사외에서 확인하는 경로다. 표준 라이브러리만 사용.
  anthropic  외부 테스트용 Anthropic SDK (claude-sonnet-5). anthropic 패키지가 있어야 하며
             api.anthropic.com 에 접근 가능한 환경(사외)에서만 쓴다.

선택 규칙: LLM_PROVIDER 가 있으면 그 값을, 없으면 자동 판별 —
           LLM_BASE_URL 이 있으면 genai (내부로 코드를 들여오면 자동으로 이쪽),
           없고 GEMINI_API_KEY 가 있으면 gemma, 둘 다 없으면 anthropic.

━━ 실행 환경(프로파일) ━━
환경이 셋이다 — 행내 GenAI 플랫폼(genai) · 행내 LLM Gateway(genai 경로, 값만 다름) ·
로컬(anthropic). 환경마다 `src/.env.<이름>`
한 파일이고 `env.py` 가 고른다(PENSION_ENV, 또는 파일이 하나뿐이면 그것). 어느 환경이
잡혔는지는 `python -m pension_agent.env` 가 보여준다. 이 파일은 그 결과(환경변수)만 읽는다.

━━ 환경변수 ━━
  LLM_PROVIDER      "genai" | "gemma" | "anthropic" (미지정 시 자동 판별)
  ENV_PATH          실행 단계 train | serving. 비면 train(행내 로컬 기본). 플랫폼 규약
  LLM_BASE_URL_TRAIN / _SERVING
                    행내 GenAI 플랫폼 URL 두 벌(…/trnn/… · …/serv/…). ENV_PATH 로 고른다
  LLM_BASE_URL      단계 구분이 없을 때의 하나짜리(Gateway·사외). 단계별 값이 없으면 이것
  LLM_API_KEY       인증 키 (Authorization Bearer + kb-key 헤더에 동일 사용).
                    단계마다 다르면 LLM_API_KEY_TRAIN / _SERVING 으로 갈라 둘 수 있다
  LLM_MODEL         모델 슬러그. 비우면 게이트웨이 기본 라우팅
  LLM_TIMEOUT       초. 기본 60
  LLM_CLIENT_USER   x-client-user 기본값. 호출부가 실제 사용자를 주면 그것이 이긴다
  LLM_RETRY_ATTEMPTS  429·5xx 재시도 횟수(첫 호출 포함). 기본 5
  LLM_MAX_CONCURRENCY  동시에 나가는 호출 수 상한. 기본 2
  LLM_MIN_INTERVAL_SEC  호출 사이 최소 간격(초). 기본 0.2
  LLM_COOLDOWN      429·5xx 를 맞은 뒤 프로세스 전체가 쉬는 시간의 기준값(초). 기본 2
  GEMINI_API_KEY    gemma 프로바이더용 (Google AI Studio 발급 키)
  GEMMA_MODEL       gemma 모델 ID. 기본 gemma-4-31b-it
  GEMMA_THINKING_LEVEL  thinkingConfig.thinkingLevel. 기본 MINIMAL (아래 상수 주석 참고)
  ANTHROPIC_API_KEY anthropic 프로바이더용 (테스트 경로)
  IRP_AGENT_MODEL   anthropic 모델. 기본 claude-sonnet-5

━━ 관측 ━━
모든 호출은 성공·실패 양쪽 다 `observability.record_generation()` 으로 한 건씩 남는다
(Langfuse). 키가 없으면 통째로 꺼지고, 켜져 있어도 전송은 백그라운드라 호출을 늦추지
않는다 — 자세한 것은 `observability.py`.

available() 가 False 면 strategy_agent 는 규칙 기반 폴백으로 동작한다(그쪽 규약).
consult_agent 는 폴백하지 않는다 — LLM 이 없으면 답을 만들지 않고 그렇게 말한다
(consult_agent/CLAUDE.md §11). 그래서 호출 실패는 전부 `LLMError` 한 종류로 올라간다:
도구·노드가 `except Exception` 으로 삼켜서 "지식베이스에 자료가 없다"로 둔갑하는 것을
막으려면, 삼키면 안 되는 예외가 다른 예외와 구분돼야 한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pension_agent import env, observability

# .env(= src/.env)를 먼저 읽는다 — 아래 모듈 상수가 그 값으로 정해진다.
# 파싱은 env.py 가 한다(관측 설정도 같은 파일에서 와야 하므로 아래층으로 내렸다).
env.load()

# ── genai (사내 플랫폼) — 값은 실행 단계(ENV_PATH: train | serving)에 따라 고른다 ──
# 행내 .env 하나에 URL 이 두 벌 있다(…/trnn/… 과 …/serv/…). 워크스페이스는 train,
# 배포 컨테이너는 플랫폼이 serving 을 넣어 준다. 어느 것을 읽었는지는 /health 와
# `python -m pension_agent.env` 가 보여준다(STAGE).
STAGE = env.stage()
BASE_URL = env.staged("LLM_BASE_URL").rstrip("/")
API_KEY = env.staged("LLM_API_KEY")

PROVIDER = os.getenv("LLM_PROVIDER") or (
    "genai" if BASE_URL
    else "gemma" if os.getenv("GEMINI_API_KEY")
    else "anthropic"
)

#: max_tokens 를 넘기지 않은 호출의 기본치. 브리핑 문장 한 편 분량.
DEFAULT_MAX_TOKENS = 900

#: 모델 슬러그. **비우면 payload 에서 `model` 키를 아예 뺀다** — 그것이 기본이다.
#:
#: 규격 문서 셋이 여기서 갈린다. SKILL.md 는 LLM_MODEL 을 「필수」로 적고 예시 슬러그
#: (claude-sonnet-4-6)까지 주는데, genai-platform.md 는 「생략이 기본값 — 게이트웨이가
#: 라우팅한다」고 적고 코드 예제에서 model 을 주석 처리해 둔다. 어긋난 것이 아니라
#: **엔드포인트가 모델을 고르는 방식이 둘**이기 때문이다:
#:
#:   LLM Gateway(LiteLLM)  엔드포인트 하나에 여러 모델이 붙어 있다 → body 의 model 이
#:                         라우팅 키다. 채워야 한다(.env.gateway.example 이 그 경우).
#:   내부 GenAI 플랫폼      URL 경로가 곧 모델이다(…/trnn/gemma-4 · …/serv/gemma-4) → body 에
#:                         model 을 함께 실으면 **404** 다(2026-09-08 행내 실측).
#:
#: 그래서 이 값의 정답은 «플랫폼별»이고, 코드는 둘 다 받는다 — 판단은 .env 가 한다.
#: 콘솔이 모델 이름을 알려주더라도 그것은 «무엇이 서빙되는지»의 표시이지 body 에 실을
#: 값이라는 뜻은 아니다. 그 혼동이 행내 첫 연결을 404 로 막았다.
MODEL = os.getenv("LLM_MODEL", "")
TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
#: 429·5xx 재시도 횟수(첫 호출 포함). anthropic SDK 는 자체 재시도가 있어 genai·gemma 경로만 쓴다.
RETRY_ATTEMPTS = int(os.getenv("LLM_RETRY_ATTEMPTS", "5"))

# ─────────────────────────────────────────────────────────────
# 호출 게이트 — 429 는 «재시도»가 아니라 «덜 몰아치기»로 막는다
#
# 이 코드는 몰아서 부르는 자리가 구조적으로 많다: 브리핑 1건 = 9~11 연쇄 호출, 대화 한 턴
# = 계획 루프 최대 4바퀴 + compose·되묻기 «동시» 호출(nodes/answer.py 의 ThreadPoolExecutor).
# 행내 게이트웨이에서도, 무료 쿼터의 generativelanguage 에서도 이 버스트로 턴이 죽었다.
#
# 재시도만으로는 못 막는다 — 재시도는 이미 맞은 뒤의 대응이고, 여러 스레드가 동시에 맞으면
# 같이 재시도해서 다시 같이 맞는다. 그래서 세 겹을 둔다:
#   ① 동시성 상한  — 한 프로세스에서 동시에 나가는 호출 수를 세마포어로 묶는다
#   ② 최소 간격    — 호출 사이를 벌려 초당 요청 수를 눌러 둔다
#   ③ 적응형 감속  — 누가 429·5xx 를 맞으면 **프로세스 전체**가 그만큼 쉰다. 맞은 스레드만
#                    쉬면 나머지가 그 사이를 메워 서버가 느끼는 압력이 안 준다
#
# 게이트는 `_post_json` 이 감싸는 genai·gemma 경로에만 선다. anthropic 은 SDK 가 자체
# 재시도·백오프를 갖고 있고 행내에서는 쓰지 않는 경로다(RETRY_ATTEMPTS 주석과 같은 이유).
# ─────────────────────────────────────────────────────────────

#: 동시에 나가는 호출 수 상한. 1 이면 완전 직렬.
MAX_CONCURRENCY = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "2")))
#: 호출 사이 최소 간격(초). 0 이면 간격 제한 없음(테스트가 이렇게 끈다).
#: 기본을 0 에서 0.2 로 올린 근거는 행내 실측이다 — 게이트웨이가 이 버스트에 429 를 냈다.
#: 브리핑 11연쇄에 +2.2초라 감당할 수 있는 값이고, 모자라면 .env 에서 올린다.
MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL_SEC", "0.2") or 0)
#: 서버가 Retry-After 를 안 줄 때 쓰는 지수 백오프의 기준값(초).
COOLDOWN = float(os.getenv("LLM_COOLDOWN", "2"))
#: 한 번에 쉬는 최대 시간(초). Retry-After 가 터무니없이 커도 여기서 끊는다.
MAX_BACKOFF = 30.0

_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENCY)
_PACE_LOCK = threading.Lock()
#: 다음 호출이 나갈 수 있는 가장 이른 시각(time.monotonic 기준). 감속은 이 값을 민다.
_next_free = 0.0

_log = logging.getLogger(__name__)


def _pace() -> None:
    """최소 간격·감속이 요구하는 만큼 기다린 뒤 돌아온다. 대기 시각은 락 안에서 «예약»
    하고 잠은 락 밖에서 잔다 — 락을 쥔 채 자면 뒤따르는 스레드가 예약조차 못 해서
    간격이 벌어지는 게 아니라 줄줄이 밀린다."""
    global _next_free
    with _PACE_LOCK:
        now = time.monotonic()
        start = max(now, _next_free)
        _next_free = start + MIN_INTERVAL
    wait = start - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _slow_down(seconds: float) -> None:
    """지금부터 seconds 동안 **모든** 호출을 세운다(위 ③). 맞은 스레드만이 아니라 아직 안
    맞은 스레드도 같이 쉬어야 서버가 느끼는 압력이 실제로 준다."""
    global _next_free
    with _PACE_LOCK:
        _next_free = max(_next_free, time.monotonic() + seconds)


@contextmanager
def _gate() -> Iterator[None]:
    """동시성 상한 + 최소 간격. HTTP 호출 한 번(재시도 한 바퀴)을 감싼다."""
    with _SLOTS:
        _pace()
        yield


# ─────────────────────────────────────────────────────────────
# 호출 주체(x-client-user)
#
# 플랫폼 규격은 이 값을 «input_value JSON 에서 추출»하라고 못박는다. 감사 기록이자
# 게이트웨이의 쿼터 버킷이라, 전부 한 값("anonymous")으로 나가면 모든 호출이 한 버킷에
# 몰린다 — 429 를 스스로 부르는 설정이다. 관측(Langfuse) 메타데이터에도 같은 값이 실린다.
#
# 전달은 ContextVar 로 한다. 노드·도구 수십 곳의 시그니처에 인자를 하나씩 꿰는 대신,
# 진입점(main.py·graph.ask)이 턴 전체를 감싸면 그 안의 모든 호출이 따라간다.
# consult_agent 의 진행 표시(progress.py)가 같은 방식이고, nodes/answer.py 의 스레드는
# 이미 contextvars.copy_context() 로 컨텍스트를 복사하므로 스레드 경계도 넘는다.
# ─────────────────────────────────────────────────────────────

#: 호출부가 아무것도 주지 않았을 때 쓰는 값. 사람이 아니라 «이 배치/화면»이라는 표시다.
DEFAULT_CLIENT_USER = os.getenv("LLM_CLIENT_USER", "pension-agent")

_CLIENT_USER: ContextVar[str] = ContextVar("llm_client_user", default="")


@contextmanager
def client_user(name: str | None) -> Iterator[None]:
    """이 블록 안의 모든 LLM 호출을 name 이 부른 것으로 기록한다.

    name 이 비면 아무것도 바꾸지 않는다 — 바깥에서 이미 정해진 주체가 있으면 그것이
    남고, 없으면 DEFAULT_CLIENT_USER 로 떨어진다.
    """
    if not name:
        yield
        return
    token = _CLIENT_USER.set(name)
    try:
        yield
    finally:
        _CLIENT_USER.reset(token)


def current_client_user() -> str:
    """지금 유효한 x-client-user. 명시 인자 > ContextVar > 환경변수 기본값."""
    return _CLIENT_USER.get() or DEFAULT_CLIENT_USER

# ── gemma (외부 사전점검) ──
GEMMA_MODEL = os.getenv("GEMMA_MODEL", "gemma-4-31b-it")
GEMMA_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMMA_MODEL}:generateContent"
)
#: gemma-4 는 thinking 모델이라 기본 설정으로는 hidden reasoning 이 maxOutputTokens 를
#: 전부 삼켜 답변이 빈 채로 잘린다(실측: 300 중 297 이 thought). MINIMAL 로 눌러야
#: 호출부가 준 max_tokens 가 답변 분량으로 쓰인다. thinkingBudget=0 은 이 모델이 거부한다.
GEMMA_THINKING_LEVEL = os.getenv("GEMMA_THINKING_LEVEL", "MINIMAL")

# ── anthropic (외부 테스트) ──
ANTHROPIC_MODEL = os.getenv("IRP_AGENT_MODEL", "claude-sonnet-5")

_anthropic_client = None  # 첫 호출 때 한 번만 생성


class LLMError(RuntimeError):
    """LLM 호출이 깨졌다 — 미설정·인증 실패·타임아웃·프로바이더 오류.

    이 예외만 따로 있는 이유는 **삼켜지면 안 되기 때문**이다. consult_agent 는 도구
    하나가 죽어도 루프를 계속하려고 `except Exception` 을 여러 겹 두고 있는데, 거기에
    LLM 장애가 같이 걸리면 "찾아봤는데 재료가 없다"는 답으로 나간다 — 있는 자료를
    없다고 말하는 셈이다(CLAUDE.md §11). 호출부는 이 예외를 재던지고, 턴은 'LLM 연결이
    안 되어 있다'는 한 가지 안내로 끝난다.
    """


def available() -> bool:
    """LLM 호출 가능 여부. 미설정 시 상위 계층이 폴백 경로를 선택한다."""
    if PROVIDER == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    if PROVIDER == "gemma":
        return bool(os.getenv("GEMINI_API_KEY"))
    return bool(BASE_URL and API_KEY)


def _generate_genai(prompt: str, system: str | None, max_tokens: int,
                    temperature: float, x_client_user: str) -> tuple[str, dict]:
    """OpenAI 호환 /chat/completions 를 표준 라이브러리로 호출한다."""
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
            "kb-key": API_KEY,              # 사내 플랫폼 인증
            "x-client-user": x_client_user,  # 호출 주체 식별(감사/쿼터)
        },
        method="POST",
    )
    body = _post_json(req)
    usage = body.get("usage") or {}
    return body["choices"][0]["message"]["content"], {
        "model": body.get("model") or MODEL or "(gateway-default)",
        "usage": _usage(usage.get("prompt_tokens"), usage.get("completion_tokens"),
                        usage.get("total_tokens")),
    }


def _usage(prompt_tokens: Any, completion_tokens: Any, total_tokens: Any = None) -> dict | None:
    """토큰 사용량을 Langfuse 가 읽는 모양으로 맞춘다. 프로바이더가 안 주면 None."""
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
    """urlopen + JSON 파싱. genai·gemma 경로가 함께 쓴다.

    **429 와 5xx 를 재시도한다.** 둘 다 «서버 사정이라 잠깐 쉬면 풀리는» 에러이고, 이 코드는
    몰아서 부르는 자리가 많다(브리핑 1회 = 11연쇄 호출, app.py 기동 시 고객 선생성, 대화
    한 턴 4~7회 + compose·판정 동시 호출). 행내 게이트웨이(genai)에서도, 무료 쿼터의
    generativelanguage(gemma)에서도 실제로 429 로 턴이 통째로 죽었다.

    5xx 는 예전에 재시도 대상이 아니었다 — 「401·500 은 기다려도 안 풀린다」는 전제였는데,
    **실측이 그 전제를 뒤집었다**(2026-09-07 · gemma-4-31b-it): 리허설 대본 한 블록의 11턴
    중 10턴이 `HTTP 500` 으로 죽었고, 같은 프롬프트를 그대로 다시 던지면 200 으로 통과했다.
    그 상태에서는 실 LLM 리허설이 «에이전트가 무엇을 답하나»가 아니라 «오늘 엔드포인트가
    살아 있나»를 재게 된다. 401·403·404 처럼 **요청이 잘못된** 에러는 그대로 올린다 —
    같은 요청을 반복해도 결과가 같고, 반복하면 진단만 늦어진다.

    재시도해도 계속 실패하면 `LLMError` 다. 그 예외를 삼켜 "자료가 없다"로 답하지 않는 것은
    호출부의 규약이고(consult_agent/CLAUDE.md §11), 여기서는 **원인을 문장에 남기는 것**까지
    한다 — 429 와 5xx 는 직원이 할 일이 다르다(기다린다 / 잠시 후 다시 시도한다).

    기다림은 **여기서 자지 않는다.** `_slow_down()` 으로 게이트의 «다음 호출 가능 시각»만
    밀어 두면 다음 바퀴의 `_gate()` 가 그만큼 재운다. 이렇게 해야 대기가 한 곳에서만
    일어나고(이중 대기 없음), 같은 대기를 **다른 스레드도 함께** 받는다 — 맞은 스레드만
    쉬면 나머지가 그 틈을 메워 서버가 느끼는 압력이 안 준다(게이트 ③).
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
                # 요청이 잘못된 에러는 **무엇이** 잘못됐는지가 전부다. 예전에는 이 예외를
                # 그대로 올려 `HTTPError: HTTP Error 404: Not Found` 한 줄만 남았는데,
                # 404 는 «경로가 없다»와 «그런 모델이 없다»가 같은 코드로 온다 — 응답
                # 본문에만 갈려 있고 그 본문을 버리고 있었다. 진단이 화면에서 끝나야 한다.
                raise LLMError(
                    f"HTTP {exc.code} {exc.reason} — {req.full_url}"
                    + (f"\n응답: {body}" if body else "")) from exc
            last, last_body = exc, body
            if attempt == RETRY_ATTEMPTS - 1:
                break
            wait = _backoff(exc, attempt)
            _log.warning("LLM %s — %.1f초 감속 후 재시도 (%d/%d)",
                         exc.code, wait, attempt + 1, RETRY_ATTEMPTS)
            _slow_down(wait)
    code = last.code if last else 0
    detail = ("속도 제한. 호출 간격을 두거나 쿼터를 확인하십시오." if code == 429
              else "서버 오류. 잠시 후 다시 시도하십시오(요청이 잘못된 것이 아닙니다).")
    raise LLMError(
        f"HTTP {code} — {RETRY_ATTEMPTS}회 시도 후에도 실패. {detail} "
        f"(동시 {MAX_CONCURRENCY} · 간격 {MIN_INTERVAL}초 — LLM_MAX_CONCURRENCY 를 낮추거나 "
        f"LLM_MIN_INTERVAL_SEC 를 늘립니다.)"
        + (f"\n응답: {last_body}" if last_body else "")) from last


#: 오류 본문을 이만큼만 싣는다. 게이트웨이가 HTML 오류 페이지를 통째로 주기도 한다.
ERROR_BODY_LIMIT = 400


def _error_body(exc: urllib.error.HTTPError) -> str:
    """오류 응답 본문 한 줄. 못 읽으면 빈 문자열 — 진단을 돕자고 다른 예외를 내지 않는다.

    본문은 **한 번만** 읽을 수 있다(스트림). 재시도 경로와 최종 예외가 같은 것을 봐야 하므로
    잡는 자리에서 바로 읽어 문자열로 들고 다닌다.
    """
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — 본문을 못 읽는 것이 원래 오류를 가리면 안 된다
        return ""
    text = " ".join(raw.split())
    return text[:ERROR_BODY_LIMIT] + ("…" if len(text) > ERROR_BODY_LIMIT else "")


def _retryable(code: int) -> bool:
    """다시 던지면 결과가 달라질 수 있는 에러인가. 429 와 5xx 만 그렇다."""
    return code == 429 or 500 <= code < 600


def _backoff(exc: urllib.error.HTTPError, attempt: int) -> float:
    """다음 시도까지 프로세스 전체가 쉴 초.

    서버가 Retry-After 를 주면 **그 값을 그대로** 쓴다(상한 MAX_BACKOFF) — 서버가 아는
    회복 시각을 우리가 추측으로 덮을 이유가 없다. 없으면 지수 백오프에 지터를 섞는다.
    지터가 필요한 이유: 동시에 맞은 스레드들이 같은 시간을 기다리면 같은 순간에 한꺼번에
    다시 몰려가 또 같이 맞는다.
    """
    retry_after = (exc.headers.get("Retry-After") or "").strip() if exc.headers else ""
    try:
        return min(float(retry_after), MAX_BACKOFF)
    except ValueError:
        pass
    return min(COOLDOWN * (2 ** attempt) * (0.5 + random.random()), MAX_BACKOFF)


def _generate_gemma(prompt: str, system: str | None, max_tokens: int,
                    temperature: float) -> tuple[str, dict]:
    """Google generativelanguage :generateContent 를 표준 라이브러리로 호출한다.

    Gemma 모델은 이 API 에서 systemInstruction 을 받지 않으므로(요청이 거부된다)
    시스템 프롬프트는 사용자 프롬프트 앞에 이어 붙인다 — 사내 vLLM 서빙으로 넘어가면
    _generate_genai 가 system 메시지로 제대로 실어 보내니, 이 접합은 이 경로에만 있다.
    """
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
    # thought=True 인 hidden reasoning 파트는 답변이 아니다 — 제외한다.
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
    """Anthropic SDK 호출 (테스트 경로). 패키지는 이 분기에서만 lazy import 한다."""
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
    """단발 생성. 응답 본문 문자열을 반환하며, 실패는 전부 `LLMError` 로 올린다.

    프로바이더별 예외(urllib 의 HTTPError·socket.timeout, anthropic SDK 의 APIError,
    응답 스키마가 어긋났을 때의 KeyError …)를 한 종류로 모으는 이유는 호출부가 "삼켜도
    되는 예외"와 "삼키면 안 되는 예외"를 구분할 수 있어야 하기 때문이다(LLMError 주석).
    원인 문자열은 그대로 보존한다 — 진단이 화면에서 끝나야 한다.

    name: Langfuse 대시보드에 뜨는 이 호출의 이름(예: "briefing.talking_scripts").
    브리핑 한 건이 11연쇄, 대화 한 턴이 4~7회라 이름이 없으면 어느 호출이 어느 단계인지
    구분되지 않는다. 호출부가 자기 단계 이름을 준다 — 관측이 꺼져 있으면 쓰이지 않는다.

    x_client_user: 비우면 이 턴의 주체(client_user 컨텍스트)로 채운다. 진입점이 턴 전체를
    감싸므로 호출부는 대개 주지 않는다.
    """
    system = system or None   # "" 은 시스템 메시지 없음으로 본다(프로바이더가 빈 문자열을 싫어한다)
    # 헤더에도 관측 메타데이터에도 같은 값이 실려야 한다 — 여기서 한 번만 정한다.
    x_client_user = x_client_user or current_client_user()
    if not available():
        raise LLMError(
            "LLM 미설정 — PROVIDER=%s. genai 는 LLM_BASE_URL/LLM_API_KEY, "
            "gemma 는 GEMINI_API_KEY, anthropic 은 ANTHROPIC_API_KEY 를 확인하십시오. "
            "어느 .env 가 읽혔는지는 python -m pension_agent.env 로 봅니다."
            % PROVIDER
        )
    started = time.time()
    try:
        if PROVIDER == "anthropic":
            text, meta = _generate_anthropic(prompt, system, max_tokens, temperature)
        elif PROVIDER == "gemma":
            text, meta = _generate_gemma(prompt, system, max_tokens, temperature)
        else:
            text, meta = _generate_genai(prompt, system, max_tokens, temperature, x_client_user)
    except Exception as exc:
        # 실패도 남긴다 — 대시보드에 «호출이 아예 없었다» 와 «호출이 깨졌다» 가 같은
        # 모양으로 보이면 장애를 되짚을 수 없다.
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
    """호출 한 건을 관측에 남긴다. 관측이 꺼져 있으면 즉시 돌아온다(observability).

    **system 이 있는 호출은 채팅 메시지 꼴로 싣는다.** Langfuse 는 `[{role, content}, …]`
    를 대화로 알아보고 역할별로 갈라 렌더하지만, 그 밖의 dict 는 JSON 한 덩어리로
    직렬화해 보여준다 — `\\n`·`\\"` 가 이스케이프된 채 한 칸에 들어차서 프롬프트를 읽을
    수 없다. 대시보드에서 되짚으라고 남기는 기록이니 읽히는 꼴이 요건이다.
    system 이 없는 호출은 문자열 그대로 둔다(그쪽은 이미 본문으로 렌더된다).
    """
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
        metadata={"provider": PROVIDER, "x_client_user": x_client_user},
        error=error,
    )


def _default_model_label() -> str:
    """응답이 모델명을 안 주거나(게이트웨이 기본 라우팅) 호출이 깨졌을 때 쓸 표기."""
    if PROVIDER == "anthropic":
        return ANTHROPIC_MODEL
    if PROVIDER == "gemma":
        return GEMMA_MODEL
    return MODEL or "(gateway-default)"


async def agenerate(prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS, system: str | None = None,
                    temperature: float = 0.2, x_client_user: str = "",
                    name: str = "llm.agenerate") -> str:
    """비동기 호출. 동기 구현을 스레드로 넘겨 blocking I/O 를 이벤트 루프에서 뺀다.

    `asyncio.to_thread` 는 현재 컨텍스트를 복사해 넘기므로 트레이스 묶음(ContextVar)도
    그대로 따라간다 — 이 경로로 부른 호출도 같은 트레이스 아래 붙는다.
    """
    return await asyncio.to_thread(
        generate, prompt, max_tokens=max_tokens, system=system,
        temperature=temperature, x_client_user=x_client_user, name=name,
    )
