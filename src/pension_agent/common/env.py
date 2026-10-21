""".env 로딩과 실행 단계 — llm.py·observability 공용(llm→observability 순환 회피용 분리).

우선순위: 실제 환경변수 > `LLM_DOTENV` 파일 > `src/.env` (override=False — 운영 주입값 보호).
`ENV_PATH == "serving"` 이면 _SERV(서빙계), 그 외 _TRNN(분석계) 키·URL 사용.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from pension_agent.common import config


def load(root: Path | None = None) -> None:
    """LLM_DOTENV(있으면) → src/.env 적재. 멱등(기존 값 미덮어씀)."""
    if os.getenv("LLM_DOTENV"):
        load_dotenv(os.environ["LLM_DOTENV"])
    load_dotenv((root or config.SRC_ROOT) / config.DOTENV.name)


def stage() -> str:
    """ENV_PATH 값(소문자). 없으면 train(행내 워크스페이스)."""
    return (os.getenv("ENV_PATH") or "train").strip().lower()


def suffix() -> str:
    """serving 이면 SERV, 그 외 TRNN."""
    return "SERV" if stage() == "serving" else "TRNN"


def staged(name: str, default: str = "") -> str:
    """`<name>_TRNN|_SERV` 우선, 없으면 `<name>`(단계 구분 없는 Gateway·사외용)."""
    return os.getenv(f"{name}_{suffix()}") or os.getenv(name) or default


def source(name: str) -> str:
    """`staged(name)` 값의 출처 이름(값 아님). 없으면 빈 문자열.

    URL·키가 각자 폴백해 «서빙계 URL + 접미사 없는 키» 짝이 생기면 APIM 401 — 설정 표시용.
    """
    staged_name = f"{name}_{suffix()}"
    if os.getenv(staged_name):
        return staged_name
    return name if os.getenv(name) else ""


if __name__ == "__main__":
    # 설정 진단 출력. 키 값 출력 금지
    load()
    from pension_agent.common import llm  # noqa: PLC0415

    print(f"설정 파일       {config.DOTENV}  ({'있음' if config.DOTENV.is_file() else '없음 — cp .env.example .env'})")
    url_src, key_src = source("LLM_BASE_URL"), source("LLM_API_KEY")
    print(f"단계(ENV_PATH)  {stage()}  — {'실제 환경변수' if os.getenv('ENV_PATH') else '없음 → 분석계'}")
    print(f"URL · 키 출처   {url_src or '(없음)'} · {key_src or '(없음)'}")
    if llm.key_stage_mismatch():
        print(f"  ⚠ 단계가 어긋난다 — URL 은 {url_src}, 키는 접미사 없는 {key_src} 다. "
              f"게이트웨이가 단계마다 다른 키를 요구하면 401(invalid subscription key) 이 난다. "
              f"{llm.wanted_key_name()} 에 그 단계의 키를 넣는다.")
    print(f"프로바이더      {llm.PROVIDER}  · 모델 {llm._default_model_label()}")
    print(f"LLM 호출 가능   {'예' if llm.available() else '아니오 — 키·엔드포인트가 비어 있다'}")
    print(f"관측(Langfuse)  {'켜짐' if os.getenv('LANGFUSE_PUBLIC_KEY') else '꺼짐'}")
