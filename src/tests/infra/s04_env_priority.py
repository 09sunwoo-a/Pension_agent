"""env — 값의 우선순위 · 실행 단계 (env.py 머리말)

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from tests.infra._common import check


# ─────────────────────────────────────────────────────────────
# env — 값의 우선순위 · 실행 단계 (env.py 머리말)
#
# 파일은 src/.env 하나다. 고정하는 것:
#   · 실제 환경변수 > LLM_DOTENV 로 지정한 파일 > .env — 운영이 주입한 값을 파일이 뒤엎지 않는다
#   · ENV_PATH 가 serving 이면 …_SERV, 그 외 전부 …_TRNN — 플랫폼 가이드의 분기 그대로
#   · 단계별 값이 없으면 접미사 없는 이름이 폴백(Gateway·사외)
# ─────────────────────────────────────────────────────────────

import os  # noqa: E402
import tempfile  # noqa: E402

from pension_agent import env as _env  # noqa: E402

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

        # LLM_DOTENV 로 지정한 파일이 .env 보다 앞선다 (다른 설정을 잠깐 쓸 때)
        (_root / "other.env").write_text("LLM_MODEL=other-model\n", encoding="utf-8")
        _clear_env()
        os.environ["LLM_DOTENV"] = str(_root / "other.env")
        _env.load(root=_root)
        check(os.environ.get("LLM_MODEL") == "other-model" and os.environ.get("LLM_PROVIDER") == "genai",
              "env: LLM_DOTENV 파일이 .env 를 덮되, 없는 키는 .env 에서 온다")

        # 실제 환경변수는 어느 파일도 덮지 못한다
        _clear_env()
        os.environ["LLM_MODEL"] = "from-shell"
        _env.load(root=_root)
        check(os.environ.get("LLM_MODEL") == "from-shell", "env: 실제 환경변수는 파일이 덮지 못한다")

        # 실행 단계(ENV_PATH) — 행내 .env 하나에 URL 이 두 벌(…/trnn/… · …/serv/…) 있고
        # 어느 것을 읽을지는 이 변수가 정한다. 워크스페이스에는 없으니 분석계, 배포 때는
        # Jenkins 가 실제 환경변수로 serving 을 넣는다. 파일 경로로 잘못 읽던 때가 있었다.
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
finally:
    _clear_env()
    for _k, _v in _saved_profile_env.items():
        if _v is not None:
            os.environ[_k] = _v
    _env.load()
