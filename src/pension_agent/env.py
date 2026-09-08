""".env 로딩 — 설정 환경변수의 단일 출처. **파일은 `src/.env` 하나다.**

`export` 대신 파일로 환경변수를 관리한다. 외부 의존성(python-dotenv) 없이 표준 라이브러리만
쓴다 — 망분리 환경에 코드를 들여올 때 설치할 것이 늘지 않아야 한다.

━━ 파일 하나 ━━
행내 워크스페이스·배포 이미지·사외 개발 PC 모두 `src/.env` 한 파일이다. 환경마다 내용이
다를 뿐이다(견본 `.env.example` 에 세 환경의 구역이 있다). 예전에는 환경마다
`.env.<프로파일>` 을 두고 `PENSION_ENV` 로 골랐는데, 행내에서는 파일 하나를 워크스페이스와
이미지가 그대로 공유해야 해서 프로파일이 설 자리가 없었다 — 다른 설정을 잠깐 쓸 일은
`LLM_DOTENV=<경로>` 하나로 충분하다.

━━ 실행 단계 — 같은 파일, 다른 값 ━━
행내 GenAI 플랫폼은 분석계(trnn)와 서빙계(serv)의 APIM 경로가 달라 URL 이 두 벌이다. 둘 다
`.env` 에 두고(`LLM_BASE_URL_TRNN` · `LLM_BASE_URL_SERV`, 키도 `LLM_API_KEY_TRNN` · `_SERV`),
어느 것을 읽을지는 실제 환경변수 `ENV_PATH` 가 정한다 — 워크스페이스에는 없고(→ 분석계),
배포 때 Jenkins 가 `serving` 을 넣는다(→ 서빙계). 플랫폼 가이드의 분기 그대로:
`ENV == "serving"` 이면 SERV, 그 외 전부 TRNN. `.env` 에는 ENV_PATH 를 적지 않는다.

━━ 값의 우선순위 ━━
**이미 실제 환경에 설정된 값은 덮어쓰지 않는다**(`os.environ` 이 파일보다 우선). 운영에서
주입한 값을 저장소에 남은 파일이 조용히 뒤엎는 것이 가장 나쁜 실패다. 파일끼리는
  실제 환경변수 > `LLM_DOTENV` 로 지정한 파일 > `src/.env`

    from pension_agent import env
    env.load()               # 몇 번을 불러도 파일은 한 번만 읽는다(멱등)
    env.staged("LLM_BASE_URL")    # 단계에 맞는 값
    python -m pension_agent.env   # «지금 어느 파일·단계·프로바이더인가»
"""

from __future__ import annotations

import os
from pathlib import Path

from pension_agent import config

#: 명시 경로를 주는 환경변수. 지정하면 `src/.env` 보다 **먼저** 읽는다(먼저 읽힌 값이 이긴다).
DOTENV_ENV = "LLM_DOTENV"

#: 실행 단계를 주는 환경변수 — 행내 플랫폼 규약. `serving`(배포된 컨테이너, Jenkins 가 넣는다)
#: 이면 서빙계, 그 외(워크스페이스에는 없다)는 전부 분석계. **파일 경로가 아니다** — 한동안
#: .env 경로로 잘못 읽고 있었다. Dockerfile 의 `ARG ENV_FILE_PATH → ENV ENV_PATH` 가 이것이다.
STAGE_ENV = "ENV_PATH"
DEFAULT_STAGE = "train"

_loaded = False
_active: dict = {"files": []}


def parse_file(path: str | Path) -> dict[str, str]:
    """파일 하나를 `KEY=value` dict 로 읽는다. 없으면 빈 dict. os.environ 은 건드리지 않는다."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return {}
    out: dict[str, str] = {}
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
            out[key] = val
    return out


def load_file(path: str | Path) -> bool:
    """파일 하나를 읽어 os.environ 에 채운다. 없으면 조용히 지나간다. 읽었으면 True."""
    values = parse_file(path)
    for key, val in values.items():
        os.environ.setdefault(key, val)  # 실제 환경변수가 있으면 그것이 이긴다
    return Path(path).is_file()


def load(*, force: bool = False, root: Path | None = None) -> None:
    """LLM_DOTENV(명시 경로) → `src/.env` 순으로 읽는다.

    두 번째 호출부터는 아무것도 하지 않는다 — 같은 파일을 다시 읽어도 결과는 같지만
    (setdefault 라 멱등), 임포트가 잦은 모듈에서 매번 디스크를 치지 않게 한다.
    `root` 는 테스트용이다(임시 디렉터리의 파일로 우선순위를 검사한다).
    """
    global _loaded, _active
    if _loaded and not force:
        return
    _loaded = True
    files: list[str] = []
    explicit = os.getenv(DOTENV_ENV)
    if explicit and load_file(explicit):
        files.append(explicit)
    dotenv = (root or config.SRC_ROOT) / config.DOTENV.name
    if load_file(dotenv):
        files.append(str(dotenv))
    _active = {"files": files}


def stage() -> str:
    """지금 실행 단계. ENV_PATH 값 그대로(소문자), 비면 `train`(행내 로컬 기본)."""
    return (os.getenv(STAGE_ENV) or DEFAULT_STAGE).strip().lower() or DEFAULT_STAGE


def suffix() -> str:
    """단계 → 환경변수 접미사. 플랫폼 가이드의 분기 그대로다:
    `ENV == "serving"` 이면 서빙계(SERV), **그 외 전부** 분석계(TRNN). 개발계·검증계도 같다."""
    return "SERV" if stage() == "serving" else "TRNN"


def staged(name: str, default: str = "") -> str:
    """단계별 값 조회 — `<name>_TRNN` / `<name>_SERV` 가 있으면 그것, 없으면 `<name>`.

    단계 구분이 없는 엔드포인트(LLM Gateway·사외)는 접미사 없는 이름 하나만 둔다 — 그래서
    접미사 없는 이름이 폴백이다.
    """
    return os.getenv(f"{name}_{suffix()}") or os.getenv(name) or default


def active() -> dict:
    """읽힌 파일 목록. `load()` 전에는 빈 목록."""
    return dict(_active)


def main() -> None:
    """`python -m pension_agent.env` — 지금 어느 환경인가.

    «키를 넣었는데 왜 안 되나»의 원인은 대개 파일 위치·단계다. 읽힌 파일과 그 결과
    (단계·프로바이더·모델·키 유무)를 한 화면에 낸다. 키 값은 찍지 않는다.
    """
    load()
    from pension_agent import llm  # noqa: PLC0415 — 결과를 보여주려는 것이지 여기서 필요하진 않다

    print(f"읽은 파일       {', '.join(active()['files']) or '(없음 — src/.env 가 없다. cp .env.example .env)'}")
    # 어느 단계의 URL 을 읽었나 — 행내 .env 에는 …/trnn/… 과 …/serv/… 가 함께 있어서,
    # 값이 찍혀 있는데도 «왜 그쪽을 부르나»가 여기서 갈린다. 키 값은 찍지 않는다.
    src = f"LLM_BASE_URL_{suffix()}" if os.getenv(f"LLM_BASE_URL_{suffix()}") else "LLM_BASE_URL"
    print(f"단계(ENV_PATH)  {stage()}  — {os.getenv(STAGE_ENV) and '실제 환경변수' or '없음 → 분석계'} · URL 은 {src}")
    print(f"프로바이더      {llm.PROVIDER}  · 모델 {llm._default_model_label()}")
    print(f"LLM 호출 가능   {'예' if llm.available() else '아니오 — 키·엔드포인트가 비어 있다'}")
    print(f"관측(Langfuse)  {'켜짐' if os.getenv('LANGFUSE_PUBLIC_KEY') else '꺼짐'}")


if __name__ == "__main__":
    main()
