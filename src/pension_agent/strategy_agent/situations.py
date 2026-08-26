"""고객 문제상황 정의 — 06/01 고객세그먼트(segment kind)를 요건 판정 결과와 대조해
"이 고객은 어떤 관리 대상인가"를 확정한다.

⑥~⑨(support.py)는 이 결과를 출발점으로 화법·반론·참고자료·안내 콘텐츠 후보를 모은다 — 전략(①~⑤)이
아니라 '문제상황'이 ⑥~⑨의 1차 키다. 판정 규칙 자체는 customer.conditions() 의 CONDS 그대로이며
(임계값은 팀 합의 사항 — 여기서 새 판정 로직을 만들지 않는다), 세그먼트는 데이터(consult_agent/data
의 segment 레코드)로서 "어떤 CONDS 조합이면 이 세그먼트에 해당한다(`conds`)" 와 "이 상태면 제외한다
(`exclusions`)" 만 선언한다. 세그먼트 데이터가 아직 없으면 빈 목록을 돌려준다(화면은 그 줄을 생략).

의존 방향은 customer ← situations ← support ← engine 이다 — engine 을 임포트하지 않는다.
"""

from __future__ import annotations

import sys

from pension_agent.knowledge import shared_store
from pension_agent.strategy_agent.customer import Profile, conditions

# 세그먼트는 공용 지식(knowledge/data/kb_segments.json)이다. 레코드 수준 source 를 함께
# 쓰기 위해 fields_of() 가 아니라 records() 로 읽는다.
SEGMENTS: list[dict] = shared_store().records("segment")

# 화면에 먼저 올릴 문제상황의 그룹 순서(06/01 주제별 인덱스의 9그룹). 규정 위반 점검(컴플라이언스)과
# 이탈 위험을 앞세우고, 운용 상태 → 수익률 → 납입 → 자금 유입 → 연금개시 → 속성 순으로 내려간다.
# 같은 그룹 안에서는 세그먼트 번호 순(결정론). 목록에 없는 그룹은 맨 뒤.
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

# 제외 조건 이름 → Profile 판정. 세그먼트 레코드의 `exclusions` 에 이 키만 쓴다.
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
    """이 고객에게 해당하는 문제상황(세그먼트) 목록 — 우선순위 순.

    해당 조건: scope 가 사후관리 ∧ `conds` 가 비어있지 않음 ∧ `conds` ⊆ conditions(p) ∧
    `exclusions` 미해당. `conds` 가 빈 세그먼트(이벤트형·데이터 미보유)는 자동 매칭에서 빠지되
    consult_agent 검색에는 남는다. profile_rule(고객 속성식)은 아직 평가하지 않는다.
    """
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
    from pension_agent.strategy_agent.customer import PERSONAS  # noqa: PLC0415

    print(f"세그먼트 레코드 {len(SEGMENTS)}건")
    for p in PERSONAS:
        rows = problem_situations(p)
        print(f"[{p.id} {p.nm}] {len(rows)}건 — " + ", ".join(f"{r['no']} {r['title']}" for r in rows))
