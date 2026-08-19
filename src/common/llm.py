"""공용 LLM 클라이언트 — 프로바이더 전환. 환경이 바뀌면 이 파일만 손대면 되도록 격리한다.

호출부(nodes.py·engine.py·agent.py 등)는 provider 세부(메시지 형식·헤더·텍스트 추출)를
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

available() 가 False 면 상위 계층은 규칙 기반 폴백으로 동작한다(strategy_agent 규약).
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from pathlib import Path


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

    umbrella/.env 는 세 에이전트가 공유하는 단일 설정 파일이다(common 의 부모 디렉토리).
    """
    explicit = os.getenv("LLM_DOTENV")
    if explicit:
        _load_env_file(explicit)
    _load_env_file(Path(__file__).resolve().parent.parent / ".env")


_bootstrap_env()

PROVIDER = os.getenv("LLM_PROVIDER") or ("genai" if os.getenv("LLM_BASE_URL") else "anthropic")

# ── genai (사내 플랫폼) ──
BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "")
TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

# ── anthropic (외부 테스트) ──
ANTHROPIC_MODEL = os.getenv("IRP_AGENT_MODEL", "claude-sonnet-5")

_anthropic_client = None  # 첫 호출 때 한 번만 생성


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
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


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


def generate(prompt: str, *, max_tokens: int, system: str | None = None,
             temperature: float = 0.2, x_client_user: str = "anonymous") -> str:
    """단발 생성. 응답 본문 문자열을 반환하며, 미설정/실패 시 예외를 전파한다.

    상위 계층은 available() 로 먼저 확인하거나, 예외를 잡아 규칙 기반 폴백으로 전환한다.
    """
    if not available():
        raise RuntimeError(
            "LLM 미설정 — PROVIDER=%s. genai 는 LLM_BASE_URL/LLM_API_KEY, "
            "anthropic 은 ANTHROPIC_API_KEY 를 확인하십시오." % PROVIDER
        )
    if PROVIDER == "anthropic":
        return _generate_anthropic(prompt, system, max_tokens, temperature)
    return _generate_genai(prompt, system, max_tokens, temperature, x_client_user)


async def agenerate(prompt: str, *, max_tokens: int, system: str | None = None,
                    temperature: float = 0.2, x_client_user: str = "anonymous") -> str:
    """비동기 호출. 동기 구현을 스레드로 넘겨 blocking I/O 를 이벤트 루프에서 뺀다."""
    return await asyncio.to_thread(
        generate, prompt, max_tokens=max_tokens, system=system,
        temperature=temperature, x_client_user=x_client_user,
    )
