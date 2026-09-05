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
환경이 셋이다 — 행내(genai) · 로컬(anthropic) · aiden(OpenAI 호환 게이트웨이의 Sonnet, genai
경로). 환경마다 `src/.env.<이름>`
한 파일이고 `env.py` 가 고른다(PENSION_ENV, 또는 파일이 하나뿐이면 그것). 어느 환경이
잡혔는지는 `python -m pension_agent.env` 가 보여준다. 이 파일은 그 결과(환경변수)만 읽는다.

━━ 환경변수 ━━
  LLM_PROVIDER      "genai" | "gemma" | "anthropic" (미지정 시 자동 판별)
  LLM_BASE_URL      genai 엔드포인트 (/v1 등 경로 접미사 없이 호스트까지)
  LLM_API_KEY       genai 인증 키 (Authorization Bearer + kb-key 헤더에 동일 사용)
  LLM_MODEL         모델 슬러그. 비우면 게이트웨이 기본 라우팅
  LLM_TIMEOUT       초. 기본 60
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
import os
import time
import urllib.error
import urllib.request
from typing import Any

from pension_agent import env, observability

# .env(= src/.env)를 먼저 읽는다 — 아래 모듈 상수가 그 값으로 정해진다.
# 파싱은 env.py 가 한다(관측 설정도 같은 파일에서 와야 하므로 아래층으로 내렸다).
env.load()

PROVIDER = os.getenv("LLM_PROVIDER") or (
    "genai" if os.getenv("LLM_BASE_URL")
    else "gemma" if os.getenv("GEMINI_API_KEY")
    else "anthropic"
)

#: max_tokens 를 넘기지 않은 호출의 기본치. 브리핑 문장 한 편 분량.
DEFAULT_MAX_TOKENS = 900

# ── genai (사내 플랫폼) ──
BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "")
TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
#: 429 재시도 횟수(첫 호출 포함). anthropic SDK 는 자체 재시도가 있어 genai·gemma 경로만 쓴다.
RETRY_ATTEMPTS = int(os.getenv("LLM_RETRY_ATTEMPTS", "3"))

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

    429(Too Many Requests)만 스스로 재시도한다 — 속도 제한이라 잠깐 쉬면 풀리는
    에러인데, 이 코드는 몰아서 부르는 자리가 많다(브리핑 1회 = 11연쇄 호출,
    app.py 기동 시 고객 선생성, 대화 한 턴 4~7회 + compose·되묻기 동시 호출). 행내
    게이트웨이(genai)에서도, 무료 쿼터의 generativelanguage(gemma)에서도 실제로 429 로
    턴이 통째로 죽었다. 서버가 Retry-After 를 주면 그 값(상한 30초)을, 없으면 2·4초
    백오프를 쓴다. 그 밖의 HTTP 에러는 재시도하지 않는다 — 401·500 은 기다려도 안
    풀리고, 같은 요청을 반복하면 진단만 늦어진다.
    """
    last: urllib.error.HTTPError | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
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
        "호출 간격을 두거나 쿼터를 확인하십시오.") from last


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
             temperature: float = 0.2, x_client_user: str = "anonymous",
             name: str = "llm.generate") -> str:
    """단발 생성. 응답 본문 문자열을 반환하며, 실패는 전부 `LLMError` 로 올린다.

    프로바이더별 예외(urllib 의 HTTPError·socket.timeout, anthropic SDK 의 APIError,
    응답 스키마가 어긋났을 때의 KeyError …)를 한 종류로 모으는 이유는 호출부가 "삼켜도
    되는 예외"와 "삼키면 안 되는 예외"를 구분할 수 있어야 하기 때문이다(LLMError 주석).
    원인 문자열은 그대로 보존한다 — 진단이 화면에서 끝나야 한다.

    name: Langfuse 대시보드에 뜨는 이 호출의 이름(예: "briefing.talking_scripts").
    브리핑 한 건이 11연쇄, 대화 한 턴이 4~7회라 이름이 없으면 어느 호출이 어느 단계인지
    구분되지 않는다. 호출부가 자기 단계 이름을 준다 — 관측이 꺼져 있으면 쓰이지 않는다.
    """
    system = system or None   # "" 은 시스템 메시지 없음으로 본다(프로바이더가 빈 문자열을 싫어한다)
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
                    temperature: float = 0.2, x_client_user: str = "anonymous",
                    name: str = "llm.agenerate") -> str:
    """비동기 호출. 동기 구현을 스레드로 넘겨 blocking I/O 를 이벤트 루프에서 뺀다.

    `asyncio.to_thread` 는 현재 컨텍스트를 복사해 넘기므로 트레이스 묶음(ContextVar)도
    그대로 따라간다 — 이 경로로 부른 호출도 같은 트레이스 아래 붙는다.
    """
    return await asyncio.to_thread(
        generate, prompt, max_tokens=max_tokens, system=system,
        temperature=temperature, x_client_user=x_client_user, name=name,
    )
