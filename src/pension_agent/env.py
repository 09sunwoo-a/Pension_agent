""".env 로딩 — 설정 환경변수의 단일 출처.

`export` 대신 파일 하나(`src/.env`)로 환경변수를 관리한다. 외부 의존성(python-dotenv)
없이 표준 라이브러리만 쓴다 — 망분리 환경에 코드를 들여올 때 설치할 것이 늘지 않아야 한다.

**이미 실제 환경에 설정된 값은 덮어쓰지 않는다**(`os.environ` 이 `.env` 보다 우선).
운영에서 주입한 값을 저장소에 남은 파일이 조용히 뒤엎는 것이 가장 나쁜 실패다.

원래 이 코드는 `llm.py` 안에 있었다. LLM 설정만 읽던 동안은 거기가 맞았지만, 관측
설정(`observability.py` 의 LANGFUSE_*)도 같은 파일에서 와야 하므로 아래층으로 내렸다.
두 모듈이 각자 `.env` 를 파싱하면 «어느 쪽이 먼저 임포트됐는가»에 따라 설정이 갈린다.

    from pension_agent import env
    env.load()      # 몇 번을 불러도 파일은 한 번만 읽는다(멱등)
"""

from __future__ import annotations

import os
from pathlib import Path

from pension_agent import config

#: 명시 경로를 주는 환경변수. 지정하면 `config.DOTENV` 보다 **먼저** 읽는다
#: (먼저 읽힌 값이 이긴다 — setdefault).
DOTENV_ENV = "LLM_DOTENV"

_loaded = False


def load_file(path: str | Path) -> None:
    """파일 하나를 읽어 os.environ 에 채운다. 없으면 조용히 지나간다."""
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


def load(*, force: bool = False) -> None:
    """LLM_DOTENV(명시 경로) → `config.DOTENV`(= src/.env) 순으로 읽는다.

    두 번째 호출부터는 아무것도 하지 않는다 — 같은 파일을 다시 읽어도 결과는 같지만
    (setdefault 라 멱등), 임포트가 잦은 모듈에서 매번 디스크를 치지 않게 한다.
    """
    global _loaded
    if _loaded and not force:
        return
    _loaded = True
    explicit = os.getenv(DOTENV_ENV)
    if explicit:
        load_file(explicit)
    load_file(config.DOTENV)
