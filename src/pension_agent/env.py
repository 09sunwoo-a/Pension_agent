""".env 로딩 — 설정 환경변수의 단일 출처. 실행 환경(프로파일)별로 파일을 갈아 끼운다.

`export` 대신 파일로 환경변수를 관리한다. 외부 의존성(python-dotenv) 없이 표준 라이브러리만
쓴다 — 망분리 환경에 코드를 들여올 때 설치할 것이 늘지 않아야 한다.

━━ 파일 셋 ━━
LLM 을 쓸 수 있는 환경이 셋이고(행내 genai · 로컬 anthropic · aiden 의 OpenAI 호환 Sonnet), 환경마다
프로바이더·엔드포인트·키가 다르다. 한 파일(`src/.env`)에 세 벌을 넣고 주석을 바꿔 가며
쓰면 어느 줄이 살아 있는지 보이지 않는다. 그래서 **환경마다 파일 하나**다.

    src/.env            공통 — 어느 환경에서나 같은 값(관측 스위치·PENSION_TODAY 등)과
                        기본 프로파일 이름(`PENSION_ENV=local`)
    src/.env.bank       행내  — genai 게이트웨이 (LLM_BASE_URL · LLM_API_KEY · LLM_MODEL)
    src/.env.local      로컬  — anthropic (ANTHROPIC_API_KEY)
    src/.env.aiden      aiden — OpenAI 호환 게이트웨이의 Sonnet (genai 와 같은 키 셋, 값만 다름)

전부 gitignore 다(비밀). 저장소에는 `*.example` 만 있다 — 복사해서 채운다.

━━ 어느 파일을 읽나 ━━
프로파일은 이 순서로 정한다. 먼저 걸리는 것이 이긴다.
  ① 실제 환경변수 `PENSION_ENV`           — 한 번만 바꿔 돌릴 때(`PENSION_ENV=aiden python -m …`)
  ② `src/.env` 안의 `PENSION_ENV=` 줄      — 이 머신의 기본값을 고정해 둘 때
  ③ `src/.env.<이름>` 이 **딱 하나만** 있으면 그것 — 행내 머신에는 `.env.bank` 만 두면 끝
  ④ 없음                                  — 프로파일 파일 없이 `.env` 와 실제 환경변수만

셋 이상의 프로파일 파일이 있는데 ①②가 없으면 고르지 않는다(어느 것인지 짐작하지 않는다).

━━ 값의 우선순위 ━━
**이미 실제 환경에 설정된 값은 덮어쓰지 않는다**(`os.environ` 이 파일보다 우선). 운영에서
주입한 값을 저장소에 남은 파일이 조용히 뒤엎는 것이 가장 나쁜 실패다. 파일끼리는
  실제 환경변수 > `LLM_DOTENV` 로 지정한 파일 > `.env.<프로파일>` > `.env`
— 프로파일 파일이 공통 파일을 덮는다. 같은 키를 두 파일에 두면 프로파일 쪽이 산다.

    from pension_agent import env
    env.load()               # 몇 번을 불러도 파일은 한 번만 읽는다(멱등)
    env.active()             # 지금 어느 프로파일·어느 파일이 읽혔나
    python -m pension_agent.env   # 같은 것을 터미널에 — «지금 어느 환경인가»
"""

from __future__ import annotations

import os
from pathlib import Path

from pension_agent import config

#: 명시 경로를 주는 환경변수. 지정하면 프로파일·공통 파일보다 **먼저** 읽는다
#: (먼저 읽힌 값이 이긴다 — setdefault).
DOTENV_ENV = "LLM_DOTENV"

#: 프로파일 이름을 주는 환경변수. 실제 환경변수로도, `.env` 안의 한 줄로도 줄 수 있다.
PROFILE_ENV = "PENSION_ENV"

#: 알려진 프로파일. 여기 없는 이름도 `.env.<이름>` 이 있으면 읽는다 — 목록은 안내용이다.
PROFILES = ("bank", "local", "aiden")

_loaded = False
_active: dict = {"profile": None, "how": "미적재", "files": []}


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


def profile_file(name: str, root: Path | None = None) -> Path:
    """프로파일 이름 → 파일 경로 (`src/.env.<이름>`)."""
    return (root or config.SRC_ROOT) / f".env.{name}"


def profile_files(root: Path | None = None) -> list[Path]:
    """존재하는 프로파일 파일 전부. `*.example` 은 저장소에 있는 견본이라 세지 않는다."""
    base = root or config.SRC_ROOT
    return sorted(p for p in base.glob(".env.*")
                  if p.is_file() and not p.name.endswith(".example"))


def detect_profile(root: Path | None = None) -> tuple[str | None, str]:
    """(프로파일 이름, 어떻게 정해졌나). 머리말의 ①~④ 순서다."""
    dotenv = (root or config.SRC_ROOT) / config.DOTENV.name
    name = os.environ.get(PROFILE_ENV, "").strip()
    if name:
        return name, f"환경변수 {PROFILE_ENV}"
    name = parse_file(dotenv).get(PROFILE_ENV, "").strip()
    if name:
        return name, f"{dotenv.name} 의 {PROFILE_ENV}="
    found = profile_files(root)
    if len(found) == 1:
        return found[0].name[len(".env."):], f"프로파일 파일이 {found[0].name} 하나뿐"
    if found:
        return None, (f"프로파일 파일이 여럿({', '.join(p.name for p in found)}) — "
                      f"{PROFILE_ENV} 로 골라야 한다")
    return None, "프로파일 파일 없음"


def load(*, force: bool = False, root: Path | None = None) -> None:
    """LLM_DOTENV(명시 경로) → `.env.<프로파일>` → `.env`(공통) 순으로 읽는다.

    두 번째 호출부터는 아무것도 하지 않는다 — 같은 파일을 다시 읽어도 결과는 같지만
    (setdefault 라 멱등), 임포트가 잦은 모듈에서 매번 디스크를 치지 않게 한다.
    `root` 는 테스트용이다(임시 디렉터리의 파일 셋으로 선택 규칙을 검사한다).
    """
    global _loaded, _active
    if _loaded and not force:
        return
    _loaded = True
    base = root or config.SRC_ROOT
    files: list[str] = []
    explicit = os.getenv(DOTENV_ENV)
    if explicit and load_file(explicit):
        files.append(explicit)
    name, how = detect_profile(base)
    if name:
        pf = profile_file(name, base)
        if load_file(pf):
            files.append(str(pf))
        else:
            how += f" — 그런데 {pf.name} 파일이 없다"
        os.environ.setdefault(PROFILE_ENV, name)   # 코드가 «지금 어느 환경인가»를 물을 수 있게
    dotenv = base / config.DOTENV.name
    if load_file(dotenv):
        files.append(str(dotenv))
    _active = {"profile": name, "how": how, "files": files}


def active() -> dict:
    """지금 적용된 프로파일과 읽힌 파일. `load()` 전에는 «미적재»."""
    return dict(_active)


def main() -> None:
    """`python -m pension_agent.env` — 지금 어느 환경인가.

    «키를 넣었는데 왜 안 되나»의 원인은 대개 파일 위치·프로파일 선택이다. 읽힌 파일과
    그 결과(프로바이더·모델·키 유무)를 한 화면에 낸다. 키 값은 찍지 않는다.
    """
    load()
    from pension_agent import llm  # noqa: PLC0415 — 결과를 보여주려는 것이지 여기서 필요하진 않다

    a = active()
    print(f"프로파일        {a['profile'] or '(없음)'}  — {a['how']}")
    print(f"읽은 파일       {', '.join(a['files']) or '(없음)'}")
    have = [p.name for p in profile_files()]
    print(f"있는 프로파일   {', '.join(have) or '(없음)'}   ← 견본: "
          + ", ".join(f".env.{n}.example" for n in PROFILES))
    print(f"프로바이더      {llm.PROVIDER}  · 모델 {llm._default_model_label()}")
    print(f"LLM 호출 가능   {'예' if llm.available() else '아니오 — 키·엔드포인트가 비어 있다'}")
    print(f"관측(Langfuse)  {'켜짐' if os.getenv('LANGFUSE_PUBLIC_KEY') else '꺼짐'}")


if __name__ == "__main__":
    main()
