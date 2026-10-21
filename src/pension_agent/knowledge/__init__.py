"""데이터 접근 계층 — 지식·카탈로그 적재의 단일 입구.

    kinds.json      레코드 종류 레지스트리(선언형)
    schema.py       종류 구동 검증 + 저작 프롬프트 생성 (CLI)
    store.py        통합 레코드 로더
    kb.py           카드 지식베이스 — 적재·시효성·출처·검색
    similarity.py   n-gram 유사도
    checks.py       범용 무결성 검증 (ID 중복 · 깨진 참조 · 사실충돌)
    data/           공용 지식 카드 (kb_build 산출물)
"""

from __future__ import annotations

from pension_agent.common import config
from pension_agent.knowledge.store import Store

_shared: Store | None = None


def shared_store() -> Store:
    """`config.DATA_ROOTS` 전체를 담은 공용 스토어(프로세스당 1회 적재)."""
    global _shared
    if _shared is None:
        _shared = Store(config.DATA_ROOTS)
    return _shared


__all__ = ["Store", "shared_store"]
