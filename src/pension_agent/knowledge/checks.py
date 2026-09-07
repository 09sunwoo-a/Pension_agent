"""범용 무결성 검증 — 종류·도메인과 무관한 세 가지.

ID 중복 · 깨진 참조 · 사실충돌. `schema.py` 의 종류별 검증기, `knowledge/kb.py`,
`strategy_agent/engine` 의 validate 가 공통으로 부른다.
"""

from __future__ import annotations

from collections.abc import Iterable


def check_duplicate_ids(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    errors: list[str] = []
    for i in ids:
        if i in seen:
            errors.append(f"[중복ID] {i}")
        seen.add(i)
    return errors


def check_broken_refs(refs: Iterable[str], valid_ids: set[str], *, owner: str = "") -> list[str]:
    """참조한 id 가 실재하지 않으면 오류로 보고한다."""
    prefix = f"{owner} → " if owner else ""
    return [f"[깨진참조] {prefix}{r}" for r in refs if r not in valid_ids]


def check_fact_conflicts(facts: Iterable[tuple[str, str]]) -> list[str]:
    """(label, value) 들에서 같은 label 에 서로 다른 value 가 있으면 보고한다.

    문서가 늘어날 때 가장 위험한 케이스 — 세법·금리 개정이 일부 문서에만 반영되어
    같은 항목 값이 문서마다 갈리는 상황을 잡는다.
    """
    by_label: dict[str, set[str]] = {}
    for label, value in facts:
        by_label.setdefault(label.strip(), set()).add(str(value).strip())
    return [
        f"[사실충돌] '{label}' 에 서로 다른 값 {len(vals)}개 — 개정 반영 누락 의심"
        for label, vals in by_label.items()
        if len(vals) > 1
    ]
