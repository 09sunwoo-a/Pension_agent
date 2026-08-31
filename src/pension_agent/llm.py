"""공용 LLM 클라이언트 — 프로바이더 전환. 환경이 바뀌면 이 파일만 손대면 되도록 격리한다.

호출부(pitch.py·engine.py·agent.py 등)는 provider 세부(메시지 형식·헤더·텍스트 추출)를
몰라도 되도록 generate()/agenerate() 만 쓴다.

━━ 프로바이더 ━━
  genai      사내 GenAI 플랫폼 (OpenAI 호환 vLLM). base_url + kb-key + x-client-user.
             표준 라이브러리(urllib)만 사용 — 망분리 환경에서 추가 의존성이 필요 없다.
  anthropic  외부 테스트용 Anthropic SDK (claude-sonnet-5). anthropic 패키지가 있어야 하며
             api.anthropic.com 에 접근 가능한 환경(사외)에서만 쓴다.

선택 규칙: LLM_PROVIDER 가 있으면 그 값을, 없으면 LLM_BASE_URL 유무로 자동 판별
           (내부로 코드를 들여오면 LLM_BASE_URL 이 잡혀 자동으로 genai 가 된다).

━━ 환경변수 ━━
  LLM_PROVIDER      "genai" | "anthropic" (미지정 시 자동 판별)
  LLM_BASE_URL      genai 엔드포인트 (/v1 등 경로 접미사 없이 호스트까지)
  LLM_API_KEY       genai 인증 키 (Authorization Bearer + kb-key 헤더에 동일 사용)
  LLM_MODEL         모델 슬러그. 비우면 게이트웨이 기본 라우팅
  LLM_TIMEOUT       초. 기본 60
  ANTHROPIC_API_KEY anthropic 프로바이더용 (테스트 경로)
  IRP_AGENT_MODEL   anthropic 모델. 기본 claude-sonnet-5

available() 가 False 면 strategy_agent 는 규칙 기반 폴백으로 동작한다(그쪽 규약).
consult_agent 는 폴백하지 않는다 — LLM 이 없으면 답을 만들지 않고 그렇게 말한다
(consult_agent/CLAUDE.md §11). 그래서 호출 실패는 전부 `LLMError` 한 종류로 올라간다:
도구·노드가 `except Exception` 으로 삼켜서 "지식베이스에 자료가 없다"로 둔갑하는 것을
막으려면, 삼키면 안 되는 예외가 다른 예외와 구분돼야 한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from pension_agent import config


# ─────────────────────────────────────────────────────────────
# .env 로딩 — export 대신 파일로 환경변수를 관리한다.
#   외부 의존성(python-dotenv) 없이 표준 라이브러리만 쓴다(망분리 대비).
#   이미 실제 환경에 설정된 값은 덮어쓰지 않는다(os.environ 이 .env 보다 우선).
# ─────────────────────────────────────────────────────────────

def _load_env_file(path: str | Path) -> None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)  # 실제 환경변수가 있으면 그것이 이긴다


def _bootstrap_env() -> None:
    """LLM_DOTENV(명시 경로) → umbrella 루트 .env 순으로 읽는다.

    config.DOTENV(= src/.env) 는 두 에이전트가 공유하는 단일 설정 파일이다.
    """
    explicit = os.getenv("LLM_DOTENV")
    if explicit:
        _load_env_file(explicit)
    _load_env_file(config.DOTENV)


_bootstrap_env()

PROVIDER = os.getenv("LLM_PROVIDER") or ("genai" if os.getenv("LLM_BASE_URL") else "anthropic")

#: max_tokens 를 넘기지 않은 호출의 기본치. 브리핑 문장 한 편 분량.
DEFAULT_MAX_TOKENS = 900

# ── genai (사내 플랫폼) ──
BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "")
TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
#: 429 재시도 횟수(첫 호출 포함). anthropic SDK 는 자체 재시도가 있어 genai 경로만 쓴다.
RETRY_ATTEMPTS = int(os.getenv("LLM_RETRY_ATTEMPTS", "3"))

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
    return bool(BASE_URL and API_KEY)


def _generate_genai(prompt: str, system: str | None, max_tokens: int,
                    temperature: float, x_client_user: str) -> str:
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
    # 429(Too Many Requests)만 스스로 재시도한다 — 게이트웨이의 속도 제한이라 잠깐 쉬면
    # 풀리는 에러인데, 이 코드는 몰아서 부르는 자리가 많다(브리핑 1회 = 11연쇄 호출,
    # app.py 기동 시 9명 선생성, 대화 한 턴 4~7회 + compose·되묻기 동시 호출). 행내에서
    # 실제로 429 로 턴이 통째로 죽었다. 서버가 Retry-After 를 주면 그 값(상한 30초)을,
    # 없으면 2·4초 백오프를 쓴다. 그 밖의 HTTP 에러는 재시도하지 않는다 — 401·500 은
    # 기다려도 안 풀리고, 같은 요청을 반복하면 진단만 늦어진다.
    last: urllib.error.HTTPError | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            last = exc
            if attempt == RETRY_ATTEMPTS - 1:
                break
            retry_after = (exc.headers.get("Retry-After") or "").strip()
            try:
                wait = min(float(retry_after), 30.0)
            except ValueError:
                wait = 2.0 * (attempt + 1)
            time.sleep(wait)
    raise LLMError(
        f"HTTP 429 Too Many Requests — {RETRY_ATTEMPTS}회 시도 후에도 속도 제한. "
        "호출 간격을 두거나 게이트웨이 쿼터를 확인하십시오.") from last


def _generate_anthropic(prompt: str, system: str | None, max_tokens: int,
                        temperature: float) -> str:
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
    return "".join(b.text for b in msg.content if b.type == "text")


def generate(prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS, system: str | None = None,
             temperature: float = 0.2, x_client_user: str = "anonymous") -> str:
    """단발 생성. 응답 본문 문자열을 반환하며, 실패는 전부 `LLMError` 로 올린다.

    프로바이더별 예외(urllib 의 HTTPError·socket.timeout, anthropic SDK 의 APIError,
    응답 스키마가 어긋났을 때의 KeyError …)를 한 종류로 모으는 이유는 호출부가 "삼켜도
    되는 예외"와 "삼키면 안 되는 예외"를 구분할 수 있어야 하기 때문이다(LLMError 주석).
    원인 문자열은 그대로 보존한다 — 진단이 화면에서 끝나야 한다.
    """
    system = system or None   # "" 은 시스템 메시지 없음으로 본다(프로바이더가 빈 문자열을 싫어한다)
    if not available():
        raise LLMError(
            "LLM 미설정 — PROVIDER=%s. genai 는 LLM_BASE_URL/LLM_API_KEY, "
            "anthropic 은 ANTHROPIC_API_KEY 를 확인하십시오." % PROVIDER
        )
    try:
        if PROVIDER == "anthropic":
            return _generate_anthropic(prompt, system, max_tokens, temperature)
        return _generate_genai(prompt, system, max_tokens, temperature, x_client_user)
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc


async def agenerate(prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS, system: str | None = None,
                    temperature: float = 0.2, x_client_user: str = "anonymous") -> str:
    """비동기 호출. 동기 구현을 스레드로 넘겨 blocking I/O 를 이벤트 루프에서 뺀다."""
    return await asyncio.to_thread(
        generate, prompt, max_tokens=max_tokens, system=system,
        temperature=temperature, x_client_user=x_client_user,
    )
