"""고객 문제상황 — 세그먼트 레코드(`conds`·`exclusions`)를 요건 판정 결과와 대조. ⑥~⑨ 의 1차 키.

판정은 customer.conditions() 그대로(새 판정 금지). engine 임포트 금지.
"""

from __future__ import annotations

import sys

from pension_agent.knowledge import shared_store
from pension_agent.tools.customer_solution.customer import Profile, conditions

# 레코드 수준 source 필요 — fields_of() 대신 records()
SEGMENTS: list[dict] = shared_store().records("segment")

# 문제상황 그룹 표시 순서. 그룹 내 세그먼트 번호 순, 목록 밖 그룹은 맨 뒤
GROUP_ORDER: tuple[str, ...] = (
    "컴플라이언스·제외 조건",
    "이탈 위험·방어",
    "운용 상태·리밸런싱",
    "수익률·관리 공백",
    "납입·세액공제",
    "자금 유입 이벤트",
    "연금개시·수령",
    "연령·투자성향·등급·행동",
)

# 제외 조건 이름 → Profile 판정(`exclusions` 허용 키)
_EXCLUSIONS = {
    "pension_started": lambda p: bool(getattr(p, "pension_started", False)),
}


def _group_rank(group: str | None) -> int:
    g = group or ""
    for i, name in enumerate(GROUP_ORDER):
        if g.startswith(name):
            return i
    return len(GROUP_ORDER)


def _no_key(no: str | None) -> tuple[int, int]:
    """'12-1' → (12, 1), '7' → (7, 0). 숫자가 아니면 맨 뒤."""
    head, _, tail = str(no or "").partition("-")
    try:
        return int(head), int(tail or 0)
    except ValueError:
        return (10**6, 0)


def _excluded(p: Profile, exclusions: list[str]) -> bool:
    return any(_EXCLUSIONS.get(name, lambda _p: False)(p) for name in exclusions)


def _row(rec: dict) -> dict:
    f = rec.get("fields") or {}
    return {
        "id": rec.get("id"),
        "no": f.get("no"),
        "title": f.get("title"),
        "group": f.get("group"),
        "why": f.get("reason_text"),
        "condition_text": f.get("condition_text"),
        "conds": list(f.get("conds") or []),
        "source": rec.get("source"),
        "doc_title": rec.get("_doc_title"),
    }


def problem_situations(p: Profile, conds: list[str] | None = None) -> list[dict]:
    """해당 문제상황 목록(우선순위 순). 사후관리 ∧ conds≠∅ ∧ conds⊆conditions(p) ∧ 제외 미해당."""
    active = set(conds if conds is not None else conditions(p))
    out: list[dict] = []
    for rec in SEGMENTS:
        f = rec.get("fields") or {}
        if (f.get("scope") or "사후관리") != "사후관리":
            continue
        need = [c for c in (f.get("conds") or []) if c]
        if not need or not set(need) <= active:
            continue
        if _excluded(p, f.get("exclusions") or []):
            continue
        out.append(_row(rec))
    out.sort(key=lambda r: (_group_rank(r["group"]), _no_key(r["no"])))
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from pension_agent.tools.customer_solution.customer import PERSONAS  # noqa: PLC0415

    print(f"세그먼트 레코드 {len(SEGMENTS)}건")
    if not PERSONAS:
        print("등록된 고객이 없어 문제상황 덤프를 건너뜁니다 (customer.PERSONAS 가 비어 있음).")
    for p in PERSONAS:
        rows = problem_situations(p)
        print(f"[{p.id} {p.nm}] {len(rows)}건 — " + ", ".join(f"{r['no']} {r['title']}" for r in rows))
