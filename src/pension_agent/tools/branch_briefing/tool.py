"""부점 브리핑 도구 둘.

    branch_search   고객 검색 — 질문 → [LLM] SELECT → 검사 → 실행 → 결과 표를 원장에 적재
    branch_status   부점 현황 — 지표·세그먼트 전월말 대비 변화·오늘의 CASE(status.compute, LLM 없음)


branch_search: 검사 거부·실행 오류는 사유를 실어 최대 RETRIES 번 재작성. 볼 수 있는 뷰·부점 범위·행 수는 코드가 결정.
사번·부점 미확인 시 조회 안 함.
"""

from __future__ import annotations

import re

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
from pension_agent.tools.branch_briefing import check, run, status, views
from pension_agent.tools.customer_solution.engine.text import won

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


def segment_catalog() -> str:
    """SQL 프롬프트에 싣는 세그먼트 정의(룰베이스 표) — 「납입금 미운용」 같은 직원 표현을 표의 조건으로 옮기게 한다."""
    lines = []
    for seg in status.rules().segments:
        try:
            where = status.segment_sql(seg)
        except status.RuleError:
            continue
        alias = f" · 다른 이름: {', '.join(seg.aliases)}" if seg.aliases else ""
        lines.append(f"- {seg.name}{alias} → WHERE {where}")
    return "\n".join(lines) or "(정의 없음)"


def _sql(question: str, last_sql: str = "", error: str = "") -> tuple[str | None, str]:
    """LLM 생성 (SQL, 설명). 답할 수 없으면 (None, 사유), 파싱 실패면 ("", 사유)."""
    retry = BRANCH_SQL_RETRY_BLOCK.format(sql=last_sql, error=error) if error else ""
    raw = generate(BRANCH_SQL_PROMPT.format(catalog=views.catalog(), segments=segment_catalog(),
                                            question=question, retry=retry),
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
        raise ToolFailure("branch_search", "부점 DB 조회가 꺼져 있습니다(BRANCH_DB=off)")
    # 요청의 사번만 사용 — WORKB_EMP_NO 폴백 시 모든 직원이 한 사람의 부점 데이터를 봄
    emp_no = ((state or {}).get("employee_id") or "").strip() or None
    try:
        mine = run.my_branch(emp_no)
    except run.DBUnavailable as exc:
        raise ToolFailure("branch_search", str(exc)) from exc
    if not mine:
        raise ToolFailure("branch_search", "직원 사번으로 소속 부점을 확인하지 못해 조회하지 않았습니다")
    code, branch_name = mine

    # 직원 원문 사용(계획 질의는 조건을 흘림), 다르면 계획 질의도 병기
    question = ((state or {}).get("question") or "").strip() or query
    if query and query.strip() and query.strip() != question:
        question = f"{question}\n(이번에 확인할 것: {query.strip()})"

    sql, error, reason = "", "", ""
    for attempt in range(1, RETRIES + 2):
        if error:
            observability.step("branch_search", "retry", attempt=attempt - 1, error=error[:120])
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
            raise ToolFailure("branch_search", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — DB 문법 오류 등은 재작성
            error = f"{type(exc).__name__}: {exc}"[:300]
    else:
        raise ToolFailure("branch_search", f"조회문을 만들지 못했습니다 — {error}")

    observability.step("branch_search", branch_name, attempt=attempt, rows=len(rows),
                       truncated=truncated or None)
    from pension_agent.tools import customer_selection  # noqa: PLC0415
    from pension_agent.tools.branch_briefing import roster  # noqa: PLC0415

    inspected = check._mask_strings(sql)
    is_list = (not rows or any("customer_ref" in row or "customer_name" in row for row in rows)
               or (bool(re.search(r"\bFROM\s+v_irp_customer(?:_prev)?\b", inspected, re.I))
                   and not re.search(r"\b(?:COUNT|SUM|AVG|MIN|MAX)\s*\(|\bGROUP\s+BY\b", inspected, re.I)))
    identities = []
    for row in rows if is_list else []:
        identity = dict(row)
        if row.get("customer_ref") and not row.get("customer_name"):
            profile = roster.lookup(row["customer_ref"], emp_no)
            if profile:
                identity.update(customer_name=profile.nm, age=profile.ag, star_grade=profile.club_grade)
        identities.append(identity)
    listed = customer_selection.snapshot(state, [
        {"ref": row.get("customer_ref"), "name": row.get("customer_name"), "age": row.get("age"),
         "grade": row.get("star_grade"), "balance": row.get("eval_amt")}
        for row in identities], code, question) if is_list else None
    # 내부 식별자는 코드 전용. 응답 표와 모델 근거에는 순번만 수록.
    visible = [{k: v for k, v in row.items() if k != "customer_ref"} for row in rows]
    shown = [{"순서": i, **{k: v for k, v in row.items() if k != "순서"}}
             for i, row in enumerate(visible, 1)] if is_list else visible
    note_line = (f"※ 결과가 {run.MAX_ROWS}행을 넘어 앞의 {run.MAX_ROWS}행만 실었습니다."
                 if truncated else "")
    text = "\n".join(x for x in (
        f"■ 부점 데이터 조회 — {branch_name} 부점 · 조회 {len(rows)}행",
        f"· 조회 목적: {reason}" if reason else "",
        "· 실행한 조회문:",
        "```sql", sql, "```",
        table(shown),
        note_line,
    ) if x)
    meta = {"rows": len(rows), "truncated": truncated, "branch": branch_name, "sql": sql}
    if listed is not None:
        meta.update(customer_list=listed, document="\n\n".join(x for x in (
            f"목록 {listed['number']} · {branch_name} 고객 조회 — {len(rows)}명",
            reason, table(shown), note_line,
            "번호나 이름으로 이어서 질문해 주세요. 예: ‘2번 고객 자세히’." if rows else "",
        ) if x))
    return _ev("branch_search", query, text,
               [{"id": "branch_db",
                 "title": f"부점 데이터 조회 ({branch_name})",
                 # 실행 SQL 을 출처로 — 수치 역추적용
                 "doc": sql,
                 "score": None, "page": None}],
               meta=meta)


#: D 등급(원문에 없는 설계 제안) 기준을 쓴 재료의 성격 표시 — 루트 CLAUDE.md «D 를 A 와 같은 얼굴로 세우지 않는다»
DRAFT_MARK = ("부점 세그먼트·CASE·임계값 중 근거등급 D(기획자 설계 제안) 기준이 섞여 있어요 — "
              "행내 확정 기준이 아니라 운영 중 조정될 수 있어요.")


def _value(ind: dict, key: str) -> str:
    v = ind[key]
    if ind["unit"] == "원":
        return won(v)
    if ind["unit"] == "%":
        return f"{v:.2f}%" if ind["id"] == "branch_return_rate" else f"{v:.1f}%"
    return f"{int(v):,}{ind['unit']}"


def _change(seg: dict, st: dict) -> str:
    if seg["direction"] != "변화 없음":
        return f"{seg['delta']:+d}명, {seg['direction']}"
    if seg["delta"] == 0:
        return "변화 없음"
    return f"{seg['delta']:+d}명, 판정 기준 {st['segment_step']}명 미만"


def status_text(st: dict) -> str:
    """부점 현황 dict → 재료 텍스트(branch_status 도구 · 전략 브리핑이 같은 문장을 본다)."""
    lines = [f"■ 부점 현황 — {st['branch']} 부점 · {st['base_date']} 기준 · 비교 기준 전월말({st['prev_date']})",
             "· 부점 지표(전월말 → 오늘):"]
    for ind in st["indicators"]:
        lines.append(f"    - {ind['name']}: {_value(ind, 'prev')} → {_value(ind, 'now')} ({ind['label']})")
    moved = [s for s in st["segments"] if s["direction"] != "변화 없음"]
    rest = [s for s in st["segments"] if s["direction"] == "변화 없음"]
    lines.append(f"· 고객군 인원 — 판정 기준({st['segment_step']}명) 이상 변한 고객군(전월말 → 오늘):")
    lines += [f"    - {s['name']}: {s['prev']}명 → {s['now']}명 ({_change(s, st)}) · 근거등급 {s['grade']}"
              for s in moved] or ["    - 없음"]
    lines.append("· 고객군 인원 — 그 밖(변화 없음 또는 판정 기준 미만, 크게 변했다고 말하지 않는다):")
    lines += [f"    - {s['name']}: {s['prev']}명 → {s['now']}명 ({_change(s, st)}) · 근거등급 {s['grade']}"
              for s in rest] or ["    - 없음"]
    if st["cases"]:
        lines.append(f"· 오늘의 관리 방향 {len(st['cases'])}가지(기준표의 조건으로 판정 — 전부 답한다):")
        for c in st["cases"]:
            lines.append(f"    - {c['name']} (근거등급 {c['grade']}) — 판정 근거: {' · '.join(c['why'])}")
            lines.append(f"      우선 관리할 고객: {c['feature']}")
            lines.append(f"      상담·운용 전략: {c['strategy']}")
    else:
        # 0건도 명시 — «판정 안 함»과 구별
        lines.append("· 오늘의 관리 방향: 전월말 대비 표의 CASE 조건을 모두 채운 것이 없습니다.")
    return "\n".join(lines)


def _branch_status(state: AgentState, query: str) -> Evidence | None:
    """오늘 부점 현황 — 지표·세그먼트 변화·오늘의 CASE. 화면 패널과 같은 계산(status.compute)."""
    if run.backend() == "off":
        raise ToolFailure("branch_status", "부점 DB 조회가 꺼져 있습니다(BRANCH_DB=off)")
    emp_no = ((state or {}).get("employee_id") or "").strip() or None
    try:
        st = status.compute(emp_no)
    except LookupError as exc:
        raise ToolFailure("branch_status", str(exc)) from exc
    except (run.DBUnavailable, status.RuleError, check.SQLRejected) as exc:
        raise ToolFailure("branch_status", str(exc)) from exc
    observability.step("branch_status", st["branch"], cases=",".join(st["cases_all"]) or "-")
    text = status_text(st)
    uses_draft = any(c["grade"] == "D" for c in st["cases"]) or any(
        s["grade"] == "D" for s in st["segments"])
    return _ev("branch_status", query, text,
               [{"id": "branch_status",
                 "title": f"부점 현황 ({st['branch']} · {st['base_date']})",
                 "doc": "부점 DB 고정 뷰(오늘 · 전월말) · 기준은 타겟 룰베이스 부점 시트",
                 "score": None, "page": None}],
               marks=[DRAFT_MARK] if uses_draft else None,
               meta={"status": st})
