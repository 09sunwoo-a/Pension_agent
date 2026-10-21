"""범용 무결성 검증 — ID 중복 · 깨진 참조 · 사실충돌."""

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
    """실재하지 않는 참조 id 보고."""
    prefix = f"{owner} → " if owner else ""
    return [f"[깨진참조] {prefix}{r}" for r in refs if r not in valid_ids]


def check_fact_conflicts(facts: Iterable[tuple[str, str]]) -> list[str]:
    """같은 label 에 서로 다른 value 보고 — 개정이 일부 문서에만 반영된 경우 검출."""
    by_label: dict[str, set[str]] = {}
    for label, value in facts:
        by_label.setdefault(label.strip(), set()).add(str(value).strip())
    return [
        f"[사실충돌] '{label}' 에 서로 다른 값 {len(vals)}개 — 개정 반영 누락 의심"
        for label, vals in by_label.items()
        if len(vals) > 1
    ]
