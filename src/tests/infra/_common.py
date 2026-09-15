"""공용 인프라 테스트가 함께 쓰는 것 — 결과 누적(check) · 이 스위트가 만드는 세션 id 와 그 정리.

검사 구간은 tests/infra/sNN_*.py 에 번호 순서대로 있고, `tests/test_infra.py` 가 그 순서로
임포트해 돌린 뒤 여기 쌓인 결과를 출력한다. 순서가 뜻이 있다 — 앞 구간이 갈아끼운 것을
뒤 구간이 이어받거나 되돌리는 자리가 있다.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent import session_store, tools

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))


#: 이 파일이 만드는 세션 고객 id. 정리는 **이것만** 지운다.
_TEST_CUSTOMERS = ("TEST01", "TEST02", "NO_SUCH_CUSTOMER")


def _clean_session_data() -> None:
    """이 테스트가 만든 세션 파일만 지운다.

    예전에는 디렉터리를 통째로 rmtree 했다. session_data 가 실행 중에만 생기는 임시
    데이터일 때는 맞았지만, 지금은 시연 픽스처(과거 상담 기록 — scripts/seed_sessions.py)가
    거기 함께 산다 — 테스트 한 번 돌리면 그 픽스처가 통째로 날아갔고, 다음 시연에서
    "지난 상담 없음"이 되는데 아무도 그 인과를 짚지 못한다.
    """
    for customer_id in _TEST_CUSTOMERS:
        (session_store.SESSION_DATA_DIR / f"{customer_id}.json").unlink(missing_ok=True)


