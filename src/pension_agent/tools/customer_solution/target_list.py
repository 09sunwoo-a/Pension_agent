"""«오늘의 타겟 고객» 목록 — 요건 1개 이상 성립 고객 수집·정렬.

판정은 `customer.conditions()`, 순서는 `customer.PRIO` 그대로 사용(별도 판정·점수 금지).
정렬: ① 가장 급한 요건의 PRIO 순 ② 성립 요건 수 많은 순 ③ 고객 id(결정론)
"""

from __future__ import annotations

from dataclasses import dataclass

from pension_agent.tools.customer_solution.customer import (
    CONDS,
    PERSONAS,
    PRIO,
    Profile,
    conditions,
)


@dataclass(frozen=True)
class Target:
    """목록 한 줄 — 고객 Profile 전체와 성립 요건."""

    profile: Profile
    conds: list[str]  # 성립 요건 코드(PRIO 순)

    @property
    def names(self) -> list[str]:
        """요건 표시 이름."""
        return [CONDS[c] for c in self.conds if c in CONDS]

    @property
    def rank(self) -> tuple[int, int, str]:
        """정렬 키(모듈 독스트링의 3단 기준)."""
        head = PRIO.index(self.conds[0]) if self.conds else len(PRIO)
        return (head, -len(self.conds), self.profile.id)


def today_targets(profiles: list[Profile] | None = None) -> list[Target]:
    """오늘의 타겟 고객. 요건 미성립 고객 제외, 로스터가 비면 빈 목록(에러 아님)."""
    found = [Target(p, conditions(p)) for p in (PERSONAS if profiles is None else profiles)]
    return sorted((t for t in found if t.conds), key=lambda t: t.rank)


def prioritize(targets: list[Target], codes: list[str]) -> list[Target]:
    """오늘의 부점 관리 방향(CASE)이 가리키는 요건코드를 가진 고객을 앞으로 — 선정은 그대로, 순서만.

    codes: CASE 우선순위 순 요건코드(`branch_briefing.status.case_codes`). 비면 원래 순서.
    앞으로 오는 고객끼리는 «가장 앞 CASE 의 요건» 순, 그 안에서는 원래 3단 기준(rank).
    """
    if not codes:
        return list(targets)

    def head(t: Target) -> tuple[int, int]:
        hits = [codes.index(c) for c in t.conds if c in codes]
        return (0, min(hits)) if hits else (1, 0)

    return sorted(targets, key=lambda t: (head(t), t.rank))


def by_names(names: list[str], targets: list[Target] | None = None) -> list[Target]:
    """이름 목록 순서대로 Target — 저장해 둔 추천 목록을 다시 세울 때. 없는 이름은 건너뛴다."""
    pool = {t.profile.nm: t for t in (today_targets() if targets is None else targets)}
    return [pool[n] for n in names if n in pool]
