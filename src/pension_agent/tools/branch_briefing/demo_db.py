"""행외 목업 DB(sqlite 메모리) — 전부 시연값.

고객 300명 = 고정 시드 합성 288명 + 목업 고객 12명(customers.json 과 같은 값 — 화면 간 수치 일치).
전월말 스냅샷(`irp_customer_prev`)도 시연값이다 — 오늘 표에서 고정 시드로 «한 달 전» 을 되짚어 만든다.
부점마다 CASE 1(계좌 감소·사전조회·만기 증가)·CASE 2(수익률 하락·미운용 증가)가 서도록 흔든다.
질의는 sql/sqlite 고정 뷰 경유만.
"""

from __future__ import annotations

import random
import sqlite3
import threading
from datetime import date, timedelta

from pension_agent.common import clock
from pension_agent.tools.customer_solution import customer as C

#: 시연 부점(첫 부점에 목업 고객 12명).
BRANCHES = (("0001", "여의도"), ("0002", "강남"), ("0003", "판교"), ("0004", "부산"))
#: 시연 직원(사번 · 이름 · 부점). 3902172 는 문서·테스트용 개발 사번.
EMPLOYEES = (("3902172", "김국민", "0001"), ("3902173", "이하나", "0001"),
             ("3902201", "박서준", "0002"), ("3902301", "최유리", "0003"),
             ("3902401", "정민호", "0004"))

_SYNTH = 288
_SEED = 20261021
#: 전월말 이후 해지된(오늘 표에는 없는) 시연 계좌 수 — 부점마다.
_CLOSED_PER_BRANCH = 6

#: 고객 표 컬럼(순서 = INSERT 순서). 오늘 표와 전월말 표가 같다.
COLUMNS = (
    "customer_name", "age", "star_grade", "risk_profile",
    "branch_code", "manager_emp_no", "joined_date",
    "eval_amt", "return_rate", "safe_asset_pct", "idle_cash_pct",
    "default_option", "pension_started", "pension_eligible",
    "next_maturity_date", "next_maturity_amt",
    "last_contact_days", "target_reasons",
    # 부점 브리핑 세그먼트용(2026-10-22) — 원천 테이블 미확정 시연 컬럼
    "idle_cash_amt", "idle_cash_days", "tax_credit_room", "transfer_inquiry",
    "risk_mismatch", "isa_maturity_date", "isa_maturity_amt",
)
_COL = {name: i for i, name in enumerate(COLUMNS)}

_TYPES = {"age": "INTEGER", "eval_amt": "INTEGER", "return_rate": "REAL",
          "safe_asset_pct": "REAL", "idle_cash_pct": "REAL", "next_maturity_amt": "INTEGER",
          "last_contact_days": "INTEGER", "idle_cash_amt": "INTEGER", "idle_cash_days": "INTEGER",
          "tax_credit_room": "INTEGER", "isa_maturity_amt": "INTEGER"}


def _table_sql(name: str) -> str:
    cols = ",\n  ".join(f"{c} {_TYPES.get(c, 'TEXT')}" for c in COLUMNS)
    return f"CREATE TABLE {name} (\n  {cols}\n);"


_SCHEMA = f"""
CREATE TABLE branch (branch_code TEXT PRIMARY KEY, branch_name TEXT);
CREATE TABLE employee (emp_no TEXT PRIMARY KEY, emp_name TEXT, branch_code TEXT);
{_table_sql("irp_customer_base")}
{_table_sql("irp_customer_prev")}
"""

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None


def _idle_days(p: C.Profile, today: date) -> int | None:
    """고유계정대 금액 변동 없이 지난 일수(시연 근사) — 1개월 변동이 있으면 0, 없으면 마지막 거래 후 경과일."""
    act = p.activity or {}
    if act.get("cash_delta_1m"):
        return 0
    last = act.get("last_order") or act.get("last_trade")
    return (today - date.fromisoformat(last)).days if last else None


def _persona_rows() -> list[tuple]:
    today = clock.today()
    rows = []
    for p in C.PERSONAS:
        mats = sorted(p.maturities or [], key=lambda m: m.get("date") or "")
        first = mats[0] if mats else None
        conds = C.conditions(p)
        names = [C.CONDS.get(c, c) for c in conds]
        cash = next((a for a in p.assets if a["type"] == "고유계정대"), None)
        rows.append((
            p.nm, p.ag, p.club_grade, p.rk, "0001", "3902172", p.joined,
            int(p.bal), p.ret, float(p.port[0]) if p.port else None, p.cash_idle_pct,
            p.dopt, "개시" if p.pension_started else "미개시",
            "충족" if p.pension_eligible else "미충족",
            first["date"] if first else None, int(first["amount"]) if first else None,
            p.dorm, ", ".join(names),
            int(cash["amount"]) if cash else 0, _idle_days(p, today),
            # room 은 만원 단위(customer.Profile.room)
            int(p.room) * 10_000,
            # 사전조회 신청은 원장에 없음 — 이탈 신호 요건(chn·out) 고객을 신청으로 둔 시연값
            "신청" if {"chn", "out"} & set(conds) else "없음",
            "불일치" if "mis" in conds else "일치",
            (p.isa or {}).get("date"), int((p.isa or {}).get("amount") or 0) or None,
        ))
    return rows


_SURNAMES = "김이박최정강조윤장임한오서신권황안송류홍"
_GIVEN = ("민준", "서연", "도윤", "하은", "지호", "수아", "예준", "지유", "현우", "채원",
          "건우", "소율", "우진", "다은", "선우", "하린", "연우", "윤서", "정우", "가은")
_GRADES = ("패밀리", "베스트", "그랜드", "로얄", "MVP")
_RISKS = ("안정형", "안정추구형", "위험중립형", "적극투자형", "공격투자형")
_REASONS = ("디폴트옵션 미설정", "원리금보장상품 편중(80% 이상)", "장기 미접촉",
            "만기예금 보유", "미운용 현금성자산", "연금개시 요건충족 후 미개시",
            "수익률 하위 30%", "운용변경 없음(12개월+)")


def _synthetic_rows() -> list[tuple]:
    rnd = random.Random(_SEED)
    today = clock.today()
    managers = {b: [e[0] for e in EMPLOYEES if e[2] == b] for b, _ in BRANCHES}
    rows = []
    for i in range(_SYNTH):
        branch = BRANCHES[i % len(BRANCHES)][0]
        age = rnd.randint(27, 74)
        bal = rnd.choice((3, 5, 8, 12, 20, 35, 50, 80, 120, 200)) * 1_000_000 \
            + rnd.randint(0, 99) * 100_000
        safe = round(rnd.uniform(5, 100), 1)
        mat = today + timedelta(days=rnd.randint(-20, 200)) if rnd.random() < 0.55 else None
        reasons = sorted(rnd.sample(_REASONS, k=rnd.choice((0, 1, 1, 2, 3))))
        dopt = "미설정" if "디폴트옵션 미설정" in reasons else rnd.choice(("설정", "설정", "미설정"))
        if dopt == "미설정" and "디폴트옵션 미설정" not in reasons:
            reasons = sorted([*reasons, "디폴트옵션 미설정"])
        eligible = age >= 55 and rnd.random() < 0.8
        started = eligible and rnd.random() < 0.4
        base = (
            rnd.choice(_SURNAMES) + rnd.choice(_GIVEN), age, rnd.choice(_GRADES),
            rnd.choice(_RISKS), branch, rnd.choice(managers[branch]),
            (date(2012, 1, 1) + timedelta(days=rnd.randint(0, 5000))).isoformat(),
            bal, round(rnd.uniform(-8, 22), 1), safe, round(rnd.uniform(0, 60), 1),
            dopt, "개시" if started else "미개시", "충족" if eligible else "미충족",
            mat.isoformat() if mat else None,
            rnd.randint(5, 120) * 1_000_000 if mat else None,
            rnd.randint(0, 400), ", ".join(reasons),
        )
        rows.append(base)
    # 부점 브리핑 컬럼은 기존 값의 난수 순서를 바꾸지 않도록 별도 시드로 뒤에 붙인다
    extra = random.Random(_SEED + 2)
    out = []
    for base in rows:
        risk, bal, safe, idle = base[3], base[7], base[9], base[10]
        started = base[12] == "개시"
        out.append(base + (
            round(bal * idle / 100),
            extra.randint(0, 240) if idle >= 30 else extra.randint(0, 20),
            0 if started else extra.choice((0, 0, 1, 3, 5, 9)) * 1_000_000,
            "신청" if extra.random() < 0.15 else "없음",
            # 위험자산 비중은 원리금보장 비중의 나머지로 근사
            "불일치" if C.risk_mismatch(risk, 100 - safe, safe) else "일치",
            None, None,
        ))
    return out


def _prev_rows(current: list[tuple]) -> list[tuple]:
    """전월말 스냅샷(시연값). 오늘 표를 한 달 전으로 되짚는다 — 만기 잔여일수는 전월말 기준으로 맞춘다.

    흔드는 것: 수익률(전월말이 높았다) · 잔액(전월말이 조금 적었다) · 미운용 현금성(일부는 전월말에 운용 중) ·
    사전조회(일부는 이번 달 신청) · 30일 이내 만기(이번 달에 새로 들어왔다) · 해지 계좌(전월말에만 있다).
    """
    rnd = random.Random(_SEED + 1)
    today, prev = clock.today(), clock.prev_month_end()
    out: list[tuple] = []
    for row in current:
        r = list(row)
        r[_COL["return_rate"]] = round(r[_COL["return_rate"]] + 0.6, 1)
        r[_COL["eval_amt"]] = round(r[_COL["eval_amt"]] * 0.98)
        if (r[_COL["idle_cash_pct"]] or 0) >= 50 and rnd.random() < 0.6:
            r[_COL["idle_cash_pct"]] = 20.0
            r[_COL["idle_cash_amt"]] = round(r[_COL["eval_amt"]] * 0.2)
            r[_COL["idle_cash_days"]] = 0
        if r[_COL["transfer_inquiry"]] == "신청" and rnd.random() < 0.7:
            r[_COL["transfer_inquiry"]] = "없음"
        mat = r[_COL["next_maturity_date"]]
        if mat:
            offset = (date.fromisoformat(mat) - today).days
            # 오늘 기준 30일 이내 만기는 전월말에는 창 밖(아직 그 상품이 없거나 더 멀었다)
            prev_offset = offset + 60 if 0 <= offset <= 30 else offset
            r[_COL["next_maturity_date"]] = (prev + timedelta(days=prev_offset)).isoformat()
        out.append(tuple(r))
    # 전월말 이후 해지된 계좌 — 어느 세그먼트에도 걸리지 않게 평이하게
    for code, _ in BRANCHES:
        for k in range(_CLOSED_PER_BRANCH):
            r = list(next(x for x in current if x[_COL["branch_code"]] == code))
            r[_COL["customer_name"]] = f"해지고객{code}-{k + 1}"
            r[_COL["eval_amt"]] = 3_000_000 + k * 500_000
            r[_COL["return_rate"]] = 8.0
            r[_COL["idle_cash_pct"]], r[_COL["idle_cash_amt"]], r[_COL["idle_cash_days"]] = 5.0, 150_000, 0
            r[_COL["safe_asset_pct"]] = 40.0
            r[_COL["default_option"]] = "설정"
            r[_COL["pension_started"]], r[_COL["pension_eligible"]] = "미개시", "미충족"
            r[_COL["next_maturity_date"]] = r[_COL["next_maturity_amt"]] = None
            r[_COL["tax_credit_room"]], r[_COL["transfer_inquiry"]] = 0, "없음"
            r[_COL["risk_mismatch"]] = "일치"
            r[_COL["isa_maturity_date"]] = r[_COL["isa_maturity_amt"]] = None
            r[_COL["target_reasons"]] = ""
            out.append(tuple(r))
    return out


def connection() -> sqlite3.Connection:
    """목업 DB 연결(프로세스당 하나, 읽기 전용)."""
    global _CONN
    with _LOCK:
        if _CONN is None:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            conn.executemany("INSERT INTO branch VALUES (?, ?)", BRANCHES)
            conn.executemany("INSERT INTO employee VALUES (?, ?, ?)", EMPLOYEES)
            current = _persona_rows() + _synthetic_rows()
            marks = ", ".join("?" * len(COLUMNS))
            conn.executemany(f"INSERT INTO irp_customer_base VALUES ({marks})", current)
            conn.executemany(f"INSERT INTO irp_customer_prev VALUES ({marks})", _prev_rows(current))
            conn.commit()
            # 검사를 빠져나간 쓰기 문장 대비
            conn.execute("PRAGMA query_only = ON")
            _CONN = conn
    return _CONN


def reset() -> None:
    """연결 재생성(테스트의 «오늘» 변경 시)."""
    global _CONN
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN = None
