"""검사된 SQL 실행 — 고정 뷰를 WITH 로 붙이고 부점 범위를 채우고 행 수 제한.

BRANCH_DB: demo(기본, sqlite 목업) · sybase(MCP, sql/sybase 작성 필요) · off. 그 외 값은 off 취급.
부점코드는 사번으로 코드가 조회해 고정 SQL 에 삽입 — LLM SQL 로 변경 불가.
"""

from __future__ import annotations

import os
import re

from pension_agent.common import clock, config
from pension_agent.tools.branch_briefing.views import MY_BRANCH, VIEWS

#: 반환 행 수 상한(초과 시 잘림 표시).
MAX_ROWS = 100

BACKENDS = ("demo", "sybase", "off")


class DBUnavailable(RuntimeError):
    """DB 미연결(꺼짐 · 설정 없음 · 고정 SQL 미작성)."""


def backend() -> str:
    name = (os.getenv("BRANCH_DB") or "demo").strip().lower()
    return name if name in BACKENDS else "off"


def _sql_file(name: str) -> str:
    folder = "sqlite" if backend() == "demo" else "sybase"
    text = (config.BRANCH_SQL_DIR / folder / f"{name}.sql").read_text(encoding="utf-8")
    if "TODO(행내)" in text:
        raise DBUnavailable(f"고정 뷰 SQL 이 아직 작성되지 않았습니다(sql/{folder}/{name}.sql)")
    # 주석 줄 제거
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("--")).strip()


def _fill(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def _rows(sql: str, emp_no: str | None) -> list[dict]:
    name = backend()
    if name == "off":
        raise DBUnavailable("부점 DB 조회가 꺼져 있습니다(BRANCH_DB)")
    if name == "demo":
        from pension_agent.tools.branch_briefing import demo_db  # noqa: PLC0415
        cur = demo_db.connection().execute(sql)
        return [dict(r) for r in cur.fetchmany(MAX_ROWS + 1)]
    from pension_agent.common.mcp import client as mcp_client, sybase  # noqa: PLC0415
    if not emp_no:
        raise DBUnavailable("직원 사번이 없어 행내 DB 에 접속할 수 없습니다")
    try:
        return sybase.query(sql, emp_no)
    except mcp_client.MCPUnavailable as exc:
        raise DBUnavailable(str(exc)) from exc


def my_branch(emp_no: str | None) -> tuple[str, str] | None:
    """직원 사번 → (부점코드, 부점명). 사번 꼴이 아니거나 찾지 못하면 None."""
    # 지연 임포트 — 모듈 수준이면 note ↔ tools 순환
    from pension_agent.common.note import EMP_NO_PATTERN  # noqa: PLC0415
    if not emp_no or not re.fullmatch(EMP_NO_PATTERN, emp_no):
        return None
    rows = _rows(_fill(_sql_file(MY_BRANCH), emp_no=emp_no), emp_no)
    if not rows:
        return None
    code, name = str(rows[0].get("branch_code") or ""), str(rows[0].get("branch_name") or "")
    # 고정 SQL 에 문자열 삽입되므로 형식 검증 필수
    if not re.fullmatch(r"[0-9A-Za-z]{1,10}", code):
        return None
    return code, name


def _limited(sql: str) -> str:
    """행 수 상한 적용. Sybase 는 TOP n(작은 쪽), sqlite 는 TOP → LIMIT 번역만."""
    m = re.match(r"\s*SELECT\s+(DISTINCT\s+)?(TOP\s+(\d+)\s+)?", sql, re.IGNORECASE)
    distinct = m.group(1) or ""
    n = min(int(m.group(3)), MAX_ROWS) if m.group(3) else None
    body = sql[m.end():]
    if backend() == "demo":
        out = f"SELECT {distinct}{body}"
        return f"{out} LIMIT {n}" if n is not None and not re.search(r"\bLIMIT\b", out, re.I) else out
    return f"SELECT {distinct}TOP {n if n is not None else MAX_ROWS + 1} {body}"


def execute(sql: str, branch_code: str, emp_no: str | None) -> tuple[str, list[dict], bool]:
    """고정 뷰를 붙여 실행 → (실행한 SQL, 행, 잘렸는지)."""
    values = {"branch_code": branch_code, "today": clock.today().isoformat(),
              # 전월말 뷰(v_irp_customer_prev)의 기준일
              "prev_date": clock.prev_month_end().isoformat()}
    ctes = ",\n".join(f"{name} AS (\n{_fill(_sql_file(name), **values)}\n)" for name in VIEWS)
    final = f"WITH {ctes}\n{_limited(sql)}"
    rows = _rows(final, emp_no)
    return final, rows[:MAX_ROWS], len(rows) > MAX_ROWS
