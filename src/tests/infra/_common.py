"""공용 인프라 테스트 공용 — 결과 누적(check) · 테스트 세션 id 정리.

`tests/test_infra.py` 가 sNN_*.py 를 번호 순서로 임포트 후 결과 출력.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent.common import session_store
from pension_agent.actions import actions

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))


#: 이 스위트가 만드는 세션 고객 id — 정리 대상은 이것만
_TEST_CUSTOMERS = ("TEST01", "TEST02", "NO_SUCH_CUSTOMER")


def _clean_session_data() -> None:
    """이 테스트가 만든 세션 파일만 삭제 — 디렉터리째 지우면 시연 픽스처 소실."""
    for customer_id in _TEST_CUSTOMERS:
        (session_store.SESSION_DATA_DIR / f"{customer_id}.json").unlink(missing_ok=True)


