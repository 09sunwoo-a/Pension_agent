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


_STEP = 10_000


def amounts(row: dict) -> dict:
    """부점 DB 행 → 서로 맞는 금액(원). 목록 판정(row_profile)과 합성 고객 상세 원장(demo_detail)이 함께 쓴다.

    원리금보장 몫 = 평가금액 × 원리금보장 비중. 고유계정대·1년 안 만기 상품은 그 안에, ETF·펀드는 그 밖에 둔다 —
    행끼리 어긋나면(시연 합성값) 원리금보장 몫을 넘는 쪽을 줄인다. 원리금보장 몫이 1만원도 안 되면 만기 상품이 없다.
    """
    bal = int(row["eval_amt"])
    safe = round(bal * float(row.get("safe_asset_pct") or 0) / 100)
    idle = min(int(row.get("idle_cash_amt") or 0), safe)
    mat_date = row.get("next_maturity_date")
    mat = min(int(row.get("next_maturity_amt") or 0), (safe - idle) // _STEP * _STEP) if mat_date else 0
    if mat_date and mat < _STEP:
        mat_date, mat = None, 0
    etf = min(int(row.get("etf_eval_amt") or 0), bal - safe)
    return {"bal": bal, "safe": safe, "idle": idle, "mat_date": mat_date, "mat": mat,
            "etf": etf, "fund": bal - safe - etf}


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
    a = amounts(row)
    bal, mat = a["bal"], a["mat_date"]
    isa_amt, isa_date = int(row.get("isa_maturity_amt") or 0), row.get("isa_maturity_date")
    idle_pct = row.get("idle_cash_pct") or 0
    return C.Profile(
        id=ref or f"{row.get('branch_code', '')}:{row['customer_name']}",
        customer_ref=ref,
        nm=row["customer_name"], ag=int(row["age"]), bal=bal,
        rk=row["risk_profile"], grade="",
        # 4분류 [원리금보장+현금, 채권형, 실적배당, 섹터ETF] — 행에는 원리금보장 비중만 있어 나머지를 실적배당으로
        # 4분류 [원리금보장+현금, 채권형, 실적배당, ETF] — 상세 원장(demo_detail)과 같은 금액·같은 반올림
        port=C.port_of([a["safe"], 0, a["fund"], a["etf"]], bal) if bal else [0, 0, 0, 0],
        ret=round(float(row["return_rate"]), 1), retPct=None,
        dopt=row["default_option"], room=int(row.get("tax_credit_room") or 0) // 10_000,
        dorm=row.get("last_contact_days"), nchM=0.0,
        matDD=(date.fromisoformat(mat) - clock.today()).days if mat else None,
        matDate=mat, matAmt=a["mat"],
        activity={"cash_delta_1m": 0},
        # ISA 만기자금(IRP 밖) — 행에 있으면 요건 isa 를 같은 판정으로(customer._isa 와 같은 꼴)
        isa=({"amount": isa_amt, "date": isa_date,
              "dd": (date.fromisoformat(isa_date) - clock.today()).days if isa_date else None,
              "org": None, "within_1m": None} if isa_amt else None),
        pension_eligible=row.get("pension_eligible") == "충족",
        pension_started=row.get("pension_started") == "개시",
        assets=[{"type": "고유계정대", "amount": a["idle"], "pct": idle_pct}],
        cash_idle_pct=round(a["idle"] * 100 / bal) if bal else 0, joined=row.get("joined_date"),
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


def focus_names(emp_no: str | None, case_ids: list[str]) -> set[str]:
    """오늘의 중점관리 고객 이름 — 관리 대상 중 오늘 성립한 CASE 의 중점관리 조건을 전부 만족하는 고객.

    CASE 가 여럿이면 CASE 사이는 OR. 조건은 룰베이스 부점 시트(부점CASE_중점조건)에서만 온다 — 여기에 임계값을
    두지 않는다. 그래서 같은 코드로도 부점마다 · 날마다 성립한 CASE 가 다르면 중점관리 고객도 다르다.
    부점을 못 찾으면 LookupError, DB 를 못 읽으면 DBUnavailable, 표의 조건을 SQL 로 못 옮기면 RuleError.
    """
    from pension_agent.tools.branch_briefing import run, status  # noqa: PLC0415 — 순환 임포트 회피

    by_id = {c.id: c for c in status.rules().cases}
    wheres = [status.focus_sql(by_id[c]) for c in case_ids if c in by_id and by_id[c].focus]
    if not wheres:
        return set()
    mine = run.my_branch(emp_no)
    if not mine:
        raise LookupError("직원 사번으로 소속 부점을 확인하지 못했습니다")
    sql = ("SELECT customer_name FROM v_irp_customer WHERE is_target = 1 AND ("
           + " OR ".join(f"({w})" for w in wheres) + ")")
    _sql, rows, cut = run.execute(sql, mine[0], emp_no, limit=ROSTER_MAX)
    if cut:
        raise run.DBUnavailable("중점관리 명부가 행 수 상한을 넘었습니다")
    return {r["customer_name"] for r in rows}
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
