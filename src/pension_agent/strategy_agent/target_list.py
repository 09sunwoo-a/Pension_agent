"""«오늘의 타겟 고객» — 서비스를 열면 가장 먼저 뜨는 관리 대상 목록.

화면(브리핑)은 **고객 하나**를 펼치는 자리이고, 여기는 그 앞에 놓이는 **오늘 볼 사람들**의
목록이다. 그래서 이 모듈이 하는 일은 둘뿐이다 — 누가 목록에 오르나, 어떤 순서로 오르나.

━━ 판정을 다시 만들지 않는다 ━━
누가 타겟인가는 `customer.conditions()` 가 이미 정한다(그 임계값의 상위 기준은 기획자
확인표 `targets.json` 이다 — 루트 CLAUDE.md). 여기서 «목록용 판정»을 따로 두면 화면이
말하는 요건과 목록이 말하는 요건이 갈린다. 이 모듈은 요건이 **하나라도** 성립한 고객을
모으기만 한다.

━━ 순서도 새로 만들지 않는다 ━━
정렬은 `customer.PRIO` 를 그대로 쓴다. 그 배열은 원래 «한 고객 안에서 요건을 어떤 순서로
표기하나»를 정하는 값인데, 그것이 곧 «어느 요건이 더 급한가»의 유일한 선언이다. 여기서
새 우선순위 점수를 만들면 화면의 요건 순서와 목록의 고객 순서가 서로 다른 기준을 말하게
된다(그리고 그 새 기준은 어느 문서에도 없는 수가 된다 — CLAUDE.md 「지식베이스에 없는
기준은 만들지 않는다」).

    1순위  가장 급한 요건이 PRIO 상 앞선 고객
    2순위  성립 요건이 많은 고객
    3순위  고객 id (결정론 — 같은 입력이면 같은 순서)
"""

from __future__ import annotations

from dataclasses import dataclass

from pension_agent.strategy_agent.customer import (
    CONDS,
    PERSONAS,
    PRIO,
    Profile,
    conditions,
)


@dataclass(frozen=True)
class Target:
    """목록 한 줄 = 고객 한 명과 그 고객에게 성립한 요건.

    `Profile` 을 통째로 들고 있는다 — 렌더러가 요건별로 «무엇 때문에 걸렸는지»의 원장 값을
    붙이려면 프로필이 필요하고, 여기서 미리 골라 담으면 표시할 값이 늘 때마다 이 dataclass 를
    고쳐야 한다.
    """

    profile: Profile
    conds: list[str]  # 성립 요건 코드. conditions() 가 준 PRIO 순서 그대로다.

    @property
    def names(self) -> list[str]:
        """요건의 사람이 읽는 이름. 코드가 이미 아는 값이라 지어낼 자리가 없다."""
        return [CONDS[c] for c in self.conds if c in CONDS]

    @property
    def rank(self) -> tuple[int, int, str]:
        """정렬 키. 위 docstring 의 3단 기준 그대로다."""
        head = PRIO.index(self.conds[0]) if self.conds else len(PRIO)
        return (head, -len(self.conds), self.profile.id)


def today_targets(profiles: list[Profile] | None = None) -> list[Target]:
    """오늘의 타겟 고객. 요건이 하나도 성립하지 않은 고객은 목록에 오르지 않는다.

    로스터가 비어 있으면 빈 목록이다(에러가 아니다) — `customers.json` 이 없을 때 화면이
    "등록된 고객 없음"으로 빠지는 것과 같은 규약이다.
    """
    found = [Target(p, conditions(p)) for p in (PERSONAS if profiles is None else profiles)]
    return sorted((t for t in found if t.conds), key=lambda t: t.rank)
