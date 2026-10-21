"""검사를 마친 SQL 을 실행한다 — 고정 뷰를 붙이고, 부점 범위를 채우고, 행 수를 자른다.

    my_branch(emp_no)          → (부점코드, 부점명) | None
    execute(sql, branch_code)  → (실행한 SQL, 행 목록, 잘렸는지)

━━ 백엔드 ━━
`BRANCH_DB` 환경변수가 고른다.
  demo    행외 목업 DB(sqlite 메모리 — demo_db.py). 기본값이다(저장소가 시연 상태다)
  sybase  행내 Sybase IQ(MCP `sybaseiq_query`). `sql/sybase/*.sql` 을 채워야 켜진다
  off     조회하지 않는다 — «DB 미연결»
표에 없는 값도 «DB 미연결»이다(끄려고 넣은 값이 켜는 쪽으로 틀리면 안 된다).

━━ 실행할 SQL 의 모양 ━━
    WITH v_irp_customer AS (<고정 SQL>), v_branch_summary AS (<고정 SQL>) <LLM 이 쓴 SELECT>
DB 에 뷰를 만들지 않는다 — 실행할 때마다 고정 SQL 을 앞에 붙인다(DB 권한이 필요 없다).
부점코드는 코드가 직원 사번으로 조회한 값이고, 고정 SQL 안에 들어간다. LLM 이 쓴 SQL 에는
부점 조건이 없어도 된다 — 넣어도 바꿀 수 없다.
"""

from __future__ import annotations

import os
import re

from pension_agent.common import clock, config
from pension_agent.tools.branch_briefing.views import MY_BRANCH, VIEWS

#: 한 번에 돌려주는 행 수의 상한. 넘으면 자르고 잘렸다고 밝힌다.
MAX_ROWS = 100

BACKENDS = ("demo", "sybase", "off")


class DBUnavailable(RuntimeError):
    """조회할 DB 가 없다(꺼짐 · 설정 없음 · 고정 SQL 미작성)."""


def backend() -> str:
    name = (os.getenv("BRANCH_DB") or "demo").strip().lower()
    return name if name in BACKENDS else "off"


def _sql_file(name: str) -> str:
    folder = "sqlite" if backend() == "demo" else "sybase"
    text = (config.BRANCH_SQL_DIR / folder / f"{name}.sql").read_text(encoding="utf-8")
    if "TODO(행내)" in text:
        raise DBUnavailable(f"고정 뷰 SQL 이 아직 작성되지 않았습니다(sql/{folder}/{name}.sql)")
    # 주석 줄은 떼고 붙인다 — 실행할 SQL 에 주석이 섞이면 검사·로그가 읽기 어렵다.
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
    # 지연 임포트 — note 가 모듈 수준에서 tools 를 읽으므로(쪽지 본문의 타겟) 여기서 올리면 순환이다.
    from pension_agent.common.note import EMP_NO_PATTERN  # noqa: PLC0415
    if not emp_no or not re.fullmatch(EMP_NO_PATTERN, emp_no):
        return None
    rows = _rows(_fill(_sql_file(MY_BRANCH), emp_no=emp_no), emp_no)
    if not rows:
        return None
    code, name = str(rows[0].get("branch_code") or ""), str(rows[0].get("branch_name") or "")
    # 부점코드는 고정 SQL 안에 문자열로 들어간다 — 꼴을 확인하지 않은 값은 넣지 않는다.
    if not re.fullmatch(r"[0-9A-Za-z]{1,10}", code):
        return None
    return code, name


def _limited(sql: str) -> str:
    """행 수 상한. Sybase 는 SELECT TOP n 을 넣고(이미 있으면 작은 쪽), sqlite 는 TOP 을
    LIMIT 으로 옮긴다(목업 DB 에서 같은 SQL 을 돌리기 위한 번역 — 그 밖의 문법은 옮기지 않는다)."""
    m = re.match(r"\s*SELECT\s+(DISTINCT\s+)?(TOP\s+(\d+)\s+)?", sql, re.IGNORECASE)
    distinct = m.group(1) or ""
    n = min(int(m.group(3)), MAX_ROWS) if m.group(3) else None
    body = sql[m.end():]
    if backend() == "demo":
        out = f"SELECT {distinct}{body}"
        return f"{out} LIMIT {n}" if n is not None and not re.search(r"\bLIMIT\b", out, re.I) else out
    return f"SELECT {distinct}TOP {n if n is not None else MAX_ROWS + 1} {body}"


def execute(sql: str, branch_code: str, emp_no: str | None) -> tuple[str, list[dict], bool]:
    """검사를 마친 SQL 에 고정 뷰를 붙여 실행한다. (실행한 SQL, 행, 잘렸는지)."""
    values = {"branch_code": branch_code, "today": clock.today().isoformat()}
    ctes = ",\n".join(f"{name} AS (\n{_fill(_sql_file(name), **values)}\n)" for name in VIEWS)
    final = f"WITH {ctes}\n{_limited(sql)}"
    rows = _rows(final, emp_no)
    return final, rows[:MAX_ROWS], len(rows) > MAX_ROWS
