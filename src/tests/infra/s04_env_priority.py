"""env — 값 우선순위 · 실행 단계(ENV_PATH) · 출처 이름.

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)
"""

from __future__ import annotations

from pathlib import Path

from tests.infra._common import check


# 고정 대상:
#   · 실제 환경변수 > LLM_DOTENV 파일 > .env
#   · ENV_PATH 가 serving 이면 …_SERV, 그 외 …_TRNN
#   · 단계별 값 없으면 접미사 없는 이름으로 폴백(Gateway·사외)

import os  # noqa: E402
import tempfile  # noqa: E402

from pension_agent.common import env as _env  # noqa: E402
from pension_agent.common import llm as _llm  # noqa: E402

_ENV_KEYS = ("LLM_PROVIDER", "LLM_MODEL", "LLM_DOTENV", "PENSION_TEST_MARK", "ENV_PATH",
             "LLM_BASE_URL", "LLM_BASE_URL_TRNN", "LLM_BASE_URL_SERV", "LLM_API_KEY", "LLM_API_KEY_SERV")
_saved_profile_env = {k: os.environ.get(k) for k in _ENV_KEYS}


def _clear_env():
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


try:
    with tempfile.TemporaryDirectory() as _td:
        _root = Path(_td)
        (_root / ".env").write_text("LLM_PROVIDER=genai\nLLM_MODEL=file-model\nPENSION_TEST_MARK=shared\n",
                                    encoding="utf-8")
        _clear_env()
        _env.load(root=_root)
        check(os.environ.get("LLM_PROVIDER") == "genai" and os.environ.get("PENSION_TEST_MARK") == "shared",
              "env: src/.env 를 읽는다")

        # LLM_DOTENV 파일이 .env 보다 우선
        (_root / "other.env").write_text("LLM_MODEL=other-model\n", encoding="utf-8")
        _clear_env()
        os.environ["LLM_DOTENV"] = str(_root / "other.env")
        _env.load(root=_root)
        check(os.environ.get("LLM_MODEL") == "other-model" and os.environ.get("LLM_PROVIDER") == "genai",
              "env: LLM_DOTENV 파일이 .env 를 덮되, 없는 키는 .env 에서 온다")

        # 실제 환경변수가 최우선
        _clear_env()
        os.environ["LLM_MODEL"] = "from-shell"
        _env.load(root=_root)
        check(os.environ.get("LLM_MODEL") == "from-shell", "env: 실제 환경변수는 파일이 덮지 못한다")

        # 실행 단계(ENV_PATH) — 미지정 = 분석계(TRNN), 배포 시 Jenkins 가 serving 주입
        _clear_env()
        os.environ.update({"LLM_BASE_URL_TRNN": "https://h/trnn/m", "LLM_BASE_URL_SERV": "https://h/serv/m",
                           "LLM_API_KEY": "k-common", "LLM_API_KEY_SERV": "k-serv"})
        check(_env.suffix() == "TRNN", "env: ENV_PATH 가 없으면 분석계(TRNN)", _env.suffix())
        check(_env.staged("LLM_BASE_URL") == "https://h/trnn/m", "env: 분석계면 …/trnn/… URL 을 읽는다")
        check(_env.staged("LLM_API_KEY") == "k-common", "env: 단계별 키가 없으면 접미사 없는 키로 폴백")
        os.environ["ENV_PATH"] = "serving"
        check(_env.staged("LLM_BASE_URL") == "https://h/serv/m", "env: serving 이면 …/serv/… URL 을 읽는다")
        check(_env.staged("LLM_API_KEY") == "k-serv", "env: serving 이면 serving 키를 읽는다")
        os.environ["ENV_PATH"] = "dev"
        check(_env.suffix() == "TRNN", "env: serving 이 아닌 값(dev 등)은 전부 분석계 — 가이드의 else 분기")
        _clear_env()
        os.environ["LLM_BASE_URL"] = "https://one"
        check(_env.staged("LLM_BASE_URL") == "https://one", "env: 단계별 URL 이 없으면 하나짜리 LLM_BASE_URL(Gateway·사외)")

        # 값을 읽은 «이름»(출처) — 서빙계 URL + 접미사 없는 키(APIM 401) 짝 진단용
        _clear_env()
        os.environ.update({"LLM_BASE_URL_SERV": "https://h/serv/m", "LLM_API_KEY": "k-common",
                           "ENV_PATH": "serving"})
        check(_env.source("LLM_BASE_URL") == "LLM_BASE_URL_SERV", "env: URL 출처 이름을 말한다(단계별)")
        check(_env.source("LLM_API_KEY") == "LLM_API_KEY", "env: 키 출처 이름을 말한다(접미사 없는 폴백)")
        check(_env.source("LLM_MODEL") == "", "env: 아무 데도 없으면 출처는 빈 문자열")
        check(_llm._mismatch("LLM_BASE_URL_SERV", "LLM_API_KEY"),
              "llm: 서빙계 URL + 접미사 없는 키 = 단계 어긋남(401 invalid subscription key)")
        check(not _llm._mismatch("LLM_BASE_URL_SERV", "LLM_API_KEY_SERV"),
              "llm: 둘 다 서빙계면 어긋남이 아니다")
        check(not _llm._mismatch("LLM_BASE_URL", "LLM_API_KEY"),
              "llm: 단계 구분이 없는 설정(Gateway·사외)은 어긋남이 아니다")
        check(not _llm._mismatch("LLM_BASE_URL_SERV", ""),
              "llm: 키가 아예 없으면 어긋남이 아니라 미설정이다(available() 가 잡는다)")
        # 안내할 키 이름은 실제로 읽은 URL 변수 기준 — ENV_PATH 재조회 시 _SERV URL 에 _TRNN 키 안내
        _saved_src = _llm.BASE_URL_SRC
        try:
            _llm.BASE_URL_SRC = "LLM_BASE_URL_SERV"
            os.environ.pop("ENV_PATH", None)          # 지금 프로세스는 분석계로 보인다
            check(_llm.wanted_key_name() == "LLM_API_KEY_SERV",
                  "llm: 채울 키 이름은 URL 출처가 정한다(ENV_PATH 를 다시 읽지 않는다)",
                  _llm.wanted_key_name())
            _llm.BASE_URL_SRC = "LLM_BASE_URL"
            check(_llm.wanted_key_name() == "LLM_API_KEY",
                  "llm: 단계 구분이 없으면 접미사 없는 이름을 안내한다")
        finally:
            _llm.BASE_URL_SRC = _saved_src
finally:
    _clear_env()
    for _k, _v in _saved_profile_env.items():
        if _v is not None:
            os.environ[_k] = _v
    _env.load()
