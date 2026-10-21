"""부점 데이터 도구(branch) — 질문 → [LLM] SELECT → 검사 → 실행 → 결과 표를 원장에 적재.

검사 거부·실행 오류는 사유를 실어 최대 RETRIES 번 재작성. 볼 수 있는 뷰·부점 범위·행 수는 코드가 결정.
사번·부점 미확인 시 조회 안 함.
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

#: 재작성 횟수(총 생성 RETRIES + 1 번).
RETRIES = 2

#: 결과 표 셀 최대 글자 수.
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
    """행 목록 → 마크다운 표. 0행도 명시(비우면 LLM 이 채움)."""
    if not rows:
        return "(조회 결과 0건)"
    cols = list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(_cell(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(out)


def _sql(question: str, last_sql: str = "", error: str = "") -> tuple[str | None, str]:
    """LLM 생성 (SQL, 설명). 답할 수 없으면 (None, 사유), 파싱 실패면 ("", 사유)."""
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
    """부점 데이터 질문 조회."""
    if run.backend() == "off":
        raise ToolFailure("branch", "부점 DB 조회가 꺼져 있습니다(BRANCH_DB=off)")
    # 요청의 사번만 사용 — WORKB_EMP_NO 폴백 시 모든 직원이 한 사람의 부점 데이터를 봄
    emp_no = ((state or {}).get("employee_id") or "").strip() or None
    try:
        mine = run.my_branch(emp_no)
    except run.DBUnavailable as exc:
        raise ToolFailure("branch", str(exc)) from exc
    if not mine:
        raise ToolFailure("branch", "직원 사번으로 소속 부점을 확인하지 못해 조회하지 않았습니다")
    code, branch_name = mine

    # 직원 원문 사용(계획 질의는 조건을 흘림), 다르면 계획 질의도 병기
    question = ((state or {}).get("question") or "").strip() or query
    if query and query.strip() and query.strip() != question:
        question = f"{question}\n(이번에 확인할 것: {query.strip()})"

    sql, error, reason = "", "", ""
    for attempt in range(1, RETRIES + 2):
        if error:
            observability.step("branch", "retry", attempt=attempt - 1, error=error[:120])
        written, reason = _sql(question, sql, error)
        if written is None:
            # 뷰로 답할 수 없는 질문 — 재료 없음
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
        except Exception as exc:  # noqa: BLE001 — DB 문법 오류 등은 재작성
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
                 # 실행 SQL 을 출처로 — 수치 역추적용
                 "doc": sql,
                 "score": None, "page": None}],
               meta={"rows": len(rows), "truncated": truncated, "branch": branch_name,
                     "sql": sql})
