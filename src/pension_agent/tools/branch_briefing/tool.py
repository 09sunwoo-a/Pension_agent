"""부점 데이터 도구(branch) — 직원의 질문을 고정 뷰 SELECT 로 옮겨 실행하고 결과를 근거로 싣는다.

    질문 → [LLM] SELECT 한 문장 → check(검사) → run.execute(고정 뷰 + 부점 범위 + 행 수 상한)
         → 결과 표를 원장에 싣는다 → compose 가 그 표로 답한다

걸리면(검사 거부·실행 오류) 이유를 실어 LLM 에게 다시 쓰게 한다 — 최대 `RETRIES` 번.

경계는 전부 코드가 쥔다(루트 CLAUDE.md 규칙 2).
  · 무엇을 볼 수 있나 — 고정 뷰 두 개(views.py). LLM 은 원본 테이블 이름을 모른다
  · 어느 부점인가 — 직원 사번으로 코드가 조회한 부점(run.my_branch). 질문에 다른 부점을
    적어도 v_irp_customer 에는 직원의 부점 고객만 있다
  · 몇 행인가 — run.MAX_ROWS
사번이나 부점을 확인하지 못하면 조회하지 않는다.
"""

from __future__ import annotations

from pension_agent.common import observability
from pension_agent.common.llm import LLMError, available, generate, json_object  # noqa: F401
from pension_agent.evidence.record import Evidence, _ev
from pension_agent.prompts.branch import (
    BRANCH_SQL_PROMPT,
    BRANCH_SQL_RETRY_BLOCK,
    BRANCH_SQL_SYSTEM,
)
from pension_agent.state import AgentState
from pension_agent.tools.base import ToolFailure
from pension_agent.tools.branch_briefing import check, run, views

#: 검사·실행에 걸렸을 때 다시 쓰게 하는 횟수. 첫 시도까지 합쳐 RETRIES + 1 번 생성한다.
RETRIES = 2

#: 결과 표에 싣는 열 하나의 최대 글자 수 — 사유 목록처럼 긴 값이 표를 무너뜨리지 않게.
CELL_MAX = 60


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:,.2f}".rstrip("0").rstrip(".")
    elif isinstance(value, int) and not isinstance(value, bool):
        text = f"{value:,}"
    else:
        text = str(value)
    text = text.replace("|", "/").replace("\n", " ")
    return text if len(text) <= CELL_MAX else text[:CELL_MAX - 1] + "…"


def table(rows: list[dict]) -> str:
    """행 목록 → 마크다운 표. 0행이면 그 사실을 적는다(침묵하면 LLM 이 채운다)."""
    if not rows:
        return "(조회 결과 0건)"
    cols = list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(_cell(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(out)


def _sql(question: str, last_sql: str = "", error: str = "") -> tuple[str | None, str]:
    """LLM 이 쓴 SQL 과 그 설명. 쓸 수 없다고 답했으면 (None, 사유)."""
    retry = BRANCH_SQL_RETRY_BLOCK.format(sql=last_sql, error=error) if error else ""
    raw = generate(BRANCH_SQL_PROMPT.format(catalog=views.catalog(), question=question, retry=retry),
                   system=BRANCH_SQL_SYSTEM, max_tokens=600, temperature=0.0,
                   name="branch.sql")
    data = json_object(raw)
    if not isinstance(data, dict):
        return "", "LLM 응답을 JSON 으로 읽지 못했습니다"
    sql = data.get("sql")
    reason = str(data.get("reason") or "").strip()
    if not sql:
        return None, reason or "부점 데이터로 답할 수 없는 질문입니다"
    return str(sql), reason


def _branch(state: AgentState, query: str) -> Evidence | None:
    """부점 데이터 질문 하나를 조회한다."""
    if run.backend() == "off":
        raise ToolFailure("branch", "부점 DB 조회가 꺼져 있습니다(BRANCH_DB=off)")
    # 사번은 이번 요청이 넘긴 값만 쓴다. 쪽지의 환경변수 폴백(WORKB_EMP_NO)은 쓰지 않는다 —
    # 그 폴백으로 떨어지면 모든 직원이 그 한 사람의 부점 데이터를 보게 된다.
    emp_no = ((state or {}).get("employee_id") or "").strip() or None
    try:
        mine = run.my_branch(emp_no)
    except run.DBUnavailable as exc:
        raise ToolFailure("branch", str(exc)) from exc
    if not mine:
        raise ToolFailure("branch", "직원 사번으로 소속 부점을 확인하지 못해 조회하지 않았습니다")
    code, branch_name = mine

    # 질문은 직원의 원문을 쓴다 — 계획이 줄인 질의는 조건(«30일 이내»)을 흘릴 수 있다.
    # 계획 질의가 원문과 다르면 함께 실어 무엇을 찾는 중인지도 알린다.
    question = ((state or {}).get("question") or "").strip() or query
    if query and query.strip() and query.strip() != question:
        question = f"{question}\n(이번에 확인할 것: {query.strip()})"

    sql, error, reason = "", "", ""
    for attempt in range(1, RETRIES + 2):
        if error:
            observability.step("branch", "retry", attempt=attempt - 1, error=error[:120])
        written, reason = _sql(question, sql, error)
        if written is None:
            # 뷰로 답할 수 없다고 LLM 이 판단했다 — «확인한 0건»이 아니라 재료 없음이다.
            return None
        sql = written
        if not sql:
            error = reason
            continue
        try:
            sql = check.check(sql)
            final, rows, truncated = run.execute(sql, code, emp_no)
            break
        except check.SQLRejected as exc:
            error = str(exc)
        except run.DBUnavailable as exc:
            raise ToolFailure("branch", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — DB 문법 오류 등. 이유를 실어 다시 쓰게 한다
            error = f"{type(exc).__name__}: {exc}"[:300]
    else:
        raise ToolFailure("branch", f"조회문을 만들지 못했습니다 — {error}")

    observability.step("branch", branch_name, attempt=attempt, rows=len(rows),
                       truncated=truncated or None)
    note_line = (f"※ 결과가 {run.MAX_ROWS}행을 넘어 앞의 {run.MAX_ROWS}행만 실었습니다."
                 if truncated else "")
    text = "\n".join(x for x in (
        f"■ 부점 데이터 조회 — {branch_name} 부점 · 조회 {len(rows)}행",
        f"· 조회 목적: {reason}" if reason else "",
        "· 실행한 조회문:",
        "```sql", sql, "```",
        table(rows),
        note_line,
    ) if x)
    return _ev("branch", query, text,
               [{"id": "branch_db",
                 "title": f"부점 데이터 조회 ({branch_name})",
                 # 실행한 SQL 을 출처에 남긴다 — 직원이 «어떻게 센 숫자인가»를 거슬러 볼 수 있다.
                 "doc": sql,
                 "score": None, "page": None}],
               meta={"rows": len(rows), "truncated": truncated, "branch": branch_name,
                     "sql": sql})
