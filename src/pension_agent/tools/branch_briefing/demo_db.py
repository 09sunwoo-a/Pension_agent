"""행외 목업 DB(sqlite 메모리) — 전부 시연값.

고객 300명 = 고정 시드 합성 288명 + 목업 고객 12명(customers.json 과 같은 값 — 화면 간 수치 일치).
질의는 sql/sqlite 고정 뷰 경유만.
"""

from __future__ import annotations

import random
import sqlite3
import threading
from datetime import date, timedelta

from pension_agent.tools.customer_solution import customer as C

#: 시연 부점(첫 부점에 목업 고객 12명).
BRANCHES = (("0001", "여의도"), ("0002", "강남"), ("0003", "판교"), ("0004", "부산"))
#: 시연 직원(사번 · 이름 · 부점). 3902172 는 문서·테스트용 개발 사번.
EMPLOYEES = (("3902172", "김국민", "0001"), ("3902173", "이하나", "0001"),
             ("3902201", "박서준", "0002"), ("3902301", "최유리", "0003"),
             ("3902401", "정민호", "0004"))

_SYNTH = 288
_SEED = 20261021

_SCHEMA = """
CREATE TABLE branch (branch_code TEXT PRIMARY KEY, branch_name TEXT);
CREATE TABLE employee (emp_no TEXT PRIMARY KEY, emp_name TEXT, branch_code TEXT);
CREATE TABLE irp_customer_base (
  customer_name TEXT, age INTEGER, star_grade TEXT, risk_profile TEXT,
  branch_code TEXT, manager_emp_no TEXT, joined_date TEXT,
  eval_amt INTEGER, return_rate REAL, safe_asset_pct REAL, idle_cash_pct REAL,
  default_option TEXT, pension_started TEXT, pension_eligible TEXT,
  next_maturity_date TEXT, next_maturity_amt INTEGER,
  last_contact_days INTEGER, target_reasons TEXT
);
"""

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None


def _persona_rows() -> list[tuple]:
    rows = []
    for p in C.PERSONAS:
        mats = sorted(p.maturities or [], key=lambda m: m.get("date") or "")
        first = mats[0] if mats else None
        names = [C.CONDS.get(c, c) for c in C.conditions(p)]
        rows.append((
            p.nm, p.ag, p.club_grade, p.rk, "0001", "3902172", p.joined,
            int(p.bal), p.ret, float(p.port[0]) if p.port else None, p.cash_idle_pct,
            p.dopt, "개시" if p.pension_started else "미개시",
            "충족" if p.pension_eligible else "미충족",
            first["date"] if first else None, int(first["amount"]) if first else None,
            p.dorm, ", ".join(names),
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
    today = C.today()
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
        rows.append((
            rnd.choice(_SURNAMES) + rnd.choice(_GIVEN), age, rnd.choice(_GRADES),
            rnd.choice(_RISKS), branch, rnd.choice(managers[branch]),
            (date(2012, 1, 1) + timedelta(days=rnd.randint(0, 5000))).isoformat(),
            bal, round(rnd.uniform(-8, 22), 1), safe, round(rnd.uniform(0, 60), 1),
            dopt, "개시" if started else "미개시", "충족" if eligible else "미충족",
            mat.isoformat() if mat else None,
            rnd.randint(5, 120) * 1_000_000 if mat else None,
            rnd.randint(0, 400), ", ".join(reasons),
        ))
    return rows


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
            conn.executemany(f"INSERT INTO irp_customer_base VALUES ({', '.join('?' * 18)})",
                             _persona_rows() + _synthetic_rows())
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
