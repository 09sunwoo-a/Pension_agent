""".env 로딩과 실행 단계 — llm.py 와 observability.py 가 함께 쓴다.

따로 있는 이유는 하나다: observability 도 .env 값이 필요한데 llm 이 observability 를
임포트하므로 그 반대 방향은 순환이다. 파싱은 python-dotenv(플랫폼 승인 패키지)가 한다.

파일은 `src/.env` 하나다. 실제 환경변수 > `LLM_DOTENV` 로 지정한 파일 > `src/.env`
(python-dotenv 의 기본 override=False — 이미 있는 값은 덮지 않는다. 운영이 주입한 값을
저장소에 남은 파일이 뒤엎는 것이 가장 나쁜 실패다).

행내 GenAI 플랫폼은 분석계(…/trnn/…)와 서빙계(…/serv/…)의 APIM 경로가 달라 URL·키가 두
벌이고 같은 .env 에 함께 있다. 어느 것을 읽을지는 실제 환경변수 `ENV_PATH` 가 정한다 —
워크스페이스에는 없고(→ 분석계), 배포 때 Jenkins 가 `serving` 을 넣는다(→ 서빙계).
플랫폼 가이드의 분기 그대로: `ENV == "serving"` 이면 _SERV, 그 외 전부 _TRNN.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from pension_agent import config


def load(root: Path | None = None) -> None:
    """LLM_DOTENV(있으면) → src/.env. 여러 번 불러도 결과는 같다(있는 값은 덮지 않는다)."""
    if os.getenv("LLM_DOTENV"):
        load_dotenv(os.environ["LLM_DOTENV"])
    load_dotenv((root or config.SRC_ROOT) / config.DOTENV.name)


def stage() -> str:
    """ENV_PATH 값(소문자). 없으면 train — 행내 워크스페이스가 그렇다."""
    return (os.getenv("ENV_PATH") or "train").strip().lower()


def suffix() -> str:
    """serving 이면 SERV, 그 외 전부 TRNN — 가이드의 if/else 그대로."""
    return "SERV" if stage() == "serving" else "TRNN"


def staged(name: str, default: str = "") -> str:
    """`<name>_<단계>` → 없으면 `<name>_TRNN`(분석계가 기본) → 없으면 `<name>`(단계 구분이 없는 Gateway·사외)."""
    return (os.getenv(f"{name}_{suffix()}") or os.getenv(f"{name}_TRNN")
            or os.getenv(name) or default)


if __name__ == "__main__":
    # python -m pension_agent.env — «키를 넣었는데 왜 안 되나»를 한 화면에. 키 값은 찍지 않는다.
    load()
    from pension_agent import llm  # noqa: PLC0415

    print(f"설정 파일       {config.DOTENV}  ({'있음' if config.DOTENV.is_file() else '없음 — cp .env.example .env'})")
    src = f"LLM_BASE_URL_{suffix()}" if os.getenv(f"LLM_BASE_URL_{suffix()}") else "LLM_BASE_URL"
    print(f"단계(ENV_PATH)  {stage()}  — {'실제 환경변수' if os.getenv('ENV_PATH') else '없음 → 분석계'} · URL 은 {src}")
    print(f"프로바이더      {llm.PROVIDER}  · 모델 {llm._default_model_label()}")
    print(f"LLM 호출 가능   {'예' if llm.available() else '아니오 — 키·엔드포인트가 비어 있다'}")
    print(f"관측(Langfuse)  {'켜짐' if os.getenv('LANGFUSE_PUBLIC_KEY') else '꺼짐'}")
