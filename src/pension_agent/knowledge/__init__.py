"""데이터 접근 계층 — 모든 지식·카탈로그가 여기를 통해서만 들어온다.

    kinds.json      레코드 종류 레지스트리(선언형). 검증·저작 프롬프트가 여기서 나온다
    schema.py       종류 구동 검증 + 저작 프롬프트 생성기 (CLI)
    store.py        통합 레코드 로더 (`{meta, records:[{id, kind, fields}]}` 단일 규격)
    kb.py           카드 지식베이스 — 적재·시효성·출처·검색 (두 에이전트 공용)
    similarity.py   n-gram 유사도 — 검색 채점의 기초
    checks.py       범용 무결성 검증 (ID 중복 · 깨진 참조 · 사실충돌)
    data/           공용 지식 카드 (kb_build 산출물)

`shared_store()` 는 `config.DATA_ROOTS` 전체를 한 번만 적재해 캐시한다. 예전에는
engine·support·situations 가 각자 `Store([...])` 를 만들어 같은 JSON 을 세 번 파싱했고,
루트 목록도 세 곳에 복제돼 있었다 — 한 곳만 고치면 나머지가 조용히 어긋나는 형태였다.
"""

from __future__ import annotations

from pension_agent import config
from pension_agent.knowledge.store import Store

_shared: Store | None = None


def shared_store() -> Store:
    """`config.DATA_ROOTS` 전체를 담은 공용 스토어(프로세스당 1회 적재)."""
    global _shared
    if _shared is None:
        _shared = Store(config.DATA_ROOTS)
    return _shared


__all__ = ["Store", "shared_store"]
