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
