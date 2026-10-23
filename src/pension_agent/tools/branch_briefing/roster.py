"""부점 명부 — 부점 DB 고객 행 → Profile, 그리고 그 부점의 관리 대상 명부.

요건 판정은 다시 만들지 않는다: 행을 Profile 로 옮기고 `customer.conditions()` 를 그대로 부른다.
목업 고객(customers.json)은 원장 Profile 을 그대로 쓴다 — 화면 브리핑과 같은 값을 말하려고.

행에 없는 원장 값은 «없음»으로 둔다(지어내지 않는다): 수익률 백분위(retPct) · 운용변경 경과(nchM) ·
보유 종목(holdings) · 1개월 고유계정대 변동(0 — 방치로 본다) · ISA(목업 고객만). 그래서 부점 DB 의 합성
고객에게는 «수익률 하위 30%»·«운용변경 없음»·«판매중단 펀드 보유»·«ISA 만기자금» 요건이 서지 않는다.
"""

from __future__ import annotations

from datetime import date
from dataclasses import replace
import re

from pension_agent.common import clock
from pension_agent.tools.customer_solution import customer as C

#: 관리 대상 명부를 읽는 행 수 상한(LLM SQL 상한 run.MAX_ROWS 와 별개).
ROSTER_MAX = 2_000

#: 목업 고객이 있는 부점(customers.json 12명 — 전부 여의도종합금융센터).
PERSONA_BRANCH = "0001"


def _persona(name: str) -> C.Profile | None:
    matches = [p for p in C.PERSONAS if p.nm == name]
    return matches[0] if len(matches) == 1 else None


def row_profile(row: dict) -> C.Profile:
    """부점 DB 고객 행(v_irp_customer 컬럼 또는 원본 표 컬럼) → Profile."""
    ref = row.get("customer_ref")
    if ref:
        from pension_agent.tools.branch_briefing import demo_db, run  # noqa: PLC0415
        found = C.get_profile(demo_db.detail_id(ref)) if run.backend() == "demo" else None
        if found is not None and found.nm == row["customer_name"] and found.ag == int(row["age"]):
            return replace(found, customer_ref=ref)
    elif row.get("branch_code") in (None, PERSONA_BRANCH):
        found = _persona(row["customer_name"])
        if found is not None:
            return found
    mat = row.get("next_maturity_date")
    safe = int(round(row.get("safe_asset_pct") or 0))
    idle_pct = row.get("idle_cash_pct") or 0
    return C.Profile(
        id=ref or f"{row.get('branch_code', '')}:{row['customer_name']}",
        customer_ref=ref,
        nm=row["customer_name"], ag=int(row["age"]), bal=int(row["eval_amt"]),
        rk=row["risk_profile"], grade="",
        # 4분류 [원리금보장+현금, 채권형, 실적배당, 섹터ETF] — 행에는 원리금보장 비중만 있어 나머지를 실적배당으로
        port=[safe, 0, 100 - safe, 0], ret=float(row["return_rate"]), retPct=None,
        dopt=row["default_option"], room=int(row.get("tax_credit_room") or 0) // 10_000,
        dorm=row.get("last_contact_days"), nchM=0.0,
        matDD=(date.fromisoformat(mat) - clock.today()).days if mat else None,
        matDate=mat, matAmt=int(row.get("next_maturity_amt") or 0),
        activity={"cash_delta_1m": 0},
        pension_eligible=row.get("pension_eligible") == "충족",
        pension_started=row.get("pension_started") == "개시",
        assets=[{"type": "고유계정대", "amount": int(row.get("idle_cash_amt") or 0), "pct": idle_pct}],
        cash_idle_pct=int(round(idle_pct)), joined=row.get("joined_date"),
        club_grade=row.get("star_grade"),
    )


def reasons(row: dict) -> str:
    """그 행의 관리 대상 사유(요건 이름을 쉼표로) — 원장의 target_reasons 칸을 판정으로 채운다."""
    return ", ".join(C.CONDS[c] for c in C.conditions(row_profile(row)))


def branch_profiles(emp_no: str | None) -> tuple[str, list[C.Profile]]:
    """사번 → (부점명, 그 부점 관리 대상 Profile). 부점을 못 찾으면 LookupError, DB 를 못 읽으면 DBUnavailable."""
    from pension_agent.tools.branch_briefing import run  # noqa: PLC0415 — 순환 임포트 회피

    mine = run.my_branch(emp_no)
    if not mine:
        raise LookupError("직원 사번으로 소속 부점을 확인하지 못했습니다")
    code, name = mine
    # 명부는 상한을 넓혀 읽는다 — 잘린 명부로 «오늘의 중점관리 고객 N명»을 말하지 않으려고
    _sql, rows, cut = run.execute("SELECT * FROM v_irp_customer WHERE is_target = 1", code, emp_no,
                                  limit=ROSTER_MAX)
    if cut:
        raise run.DBUnavailable("관리 대상 명부가 행 수 상한을 넘었습니다")
    return name, [row_profile({**r, "branch_code": code}) for r in rows]


def lookup(customer_ref: str, emp_no: str | None) -> C.Profile | None:
    """현재 직원 부점에서 내부 식별자로 한 행만 확인. 이름·순번으로 재검색하지 않음."""
    from pension_agent.tools.branch_briefing import run  # noqa: PLC0415

    mine = run.my_branch(emp_no)
    if not mine or not re.fullmatch(r"[0-9A-Za-z]{1,10}:[0-9]+", customer_ref):
        return None
    code, _name = mine
    _sql, rows, cut = run.execute(
        f"SELECT * FROM v_irp_customer WHERE customer_ref = '{customer_ref}'", code, emp_no)
    if cut or len(rows) != 1:
        return None
    return row_profile({**rows[0], "branch_code": code})


def find_names(names: list[str], emp_no: str | None) -> tuple[str, list[C.Profile]]:
    """직원 부점 전체에서 정확한 이름 후보 조회. 관리 대상 여부로 제한하지 않음."""
    from pension_agent.tools.branch_briefing import run  # noqa: PLC0415

    mine = run.my_branch(emp_no)
    if not mine:
        raise LookupError("직원 사번으로 소속 부점을 확인하지 못했습니다")
    # 고정 코드 질의의 값만 허용. SQL 조각·따옴표·와일드카드는 받지 않음.
    safe = sorted({n for n in names if re.fullmatch(r"[가-힣A-Za-z]{2,30}", n)})
    if not safe:
        return mine[0], []
    values = ", ".join(f"'{name}'" for name in safe)
    _sql, rows, cut = run.execute(
        f"SELECT * FROM v_irp_customer WHERE customer_name IN ({values}) ORDER BY customer_ref",
        mine[0], emp_no, limit=ROSTER_MAX)
    if cut:
        raise run.DBUnavailable("이름 조회 결과가 행 수 상한을 넘었습니다. 고객 구분 정보를 추가해 주세요")
    return mine[0], [row_profile({**r, "branch_code": mine[0]}) for r in rows]
