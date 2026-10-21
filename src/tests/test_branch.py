"""메인 브리핑(tools/branch_briefing) 회귀 테스트 — LLM 을 스텁으로 갈아끼우므로 API 키 없이 돌아간다.

여기서 재는 것:
  · 검사(check) — SELECT 하나 · 고정 뷰만 · 쓰기·시스템 낱말 없음 · 문자열 안의 낱말은 오판하지 않음
  · 부점 범위 — 사번으로 조회한 부점의 고객만 보이고, LLM 이 다른 부점을 적어도 바뀌지 않음
  · 행 수 상한 — TOP n 번역 · MAX_ROWS 초과 시 잘림 표시
  · 사번이 없거나 모르는 사번 · 백엔드 off · Sybase 고정 SQL 미작성이면 조회하지 않음(«확인하지 못함»)
  · 검사에 걸리면 이유를 실어 다시 쓰게 함 · 끝내 못 쓰면 도구 고장
  · 결과가 원장 근거가 되고 SQL 이 출처에 남음 · 고객 식별번호가 결과에 없음

실행: python -m tests.test_branch
"""

from __future__ import annotations

import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent import tools
from pension_agent.tools.base import ToolFailure
from pension_agent.tools.branch_briefing import check as CK
from pension_agent.tools.branch_briefing import run, tool, views

_results: list[tuple[bool, str, str]] = []

EMP = "3902172"          # 시연 사번 — 여의도(0001)
OTHER_EMP = "3902201"    # 강남(0002)


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))
    print(f"{'✓' if cond else '✗'} {label}" + (f" — {detail}" if detail and not cond else ""))


class _LLM:
    """tool.generate 를 갈아끼운다. replies 를 차례로 돌려주고 프롬프트를 남긴다."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __enter__(self) -> _LLM:
        self._saved = tool.generate

        def _gen(prompt, **kw):
            self.prompts.append(prompt)
            return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        tool.generate = _gen
        return self

    def __exit__(self, *exc) -> None:
        tool.generate = self._saved


def _sql(s: str | None, reason: str = "확인") -> str:
    import json
    return json.dumps({"sql": s, "reason": reason}, ensure_ascii=False)


def _state(emp: str | None = EMP, q: str = "질문") -> dict:
    return {"question": q, "employee_id": emp}


# ─────────────────────────────────────────────────────────────
def check_checker() -> None:
    ok = ["SELECT customer_name FROM v_irp_customer",
          "select count(*) from v_irp_customer where default_option = '미설정'",
          "SELECT TOP 3 customer_name FROM v_irp_customer ORDER BY eval_amt DESC;",
          "SELECT branch_name, target_count FROM v_branch_summary s",
          "SELECT customer_name FROM v_irp_customer WHERE target_reasons LIKE '%DELETE%'"]
    for s in ok:
        try:
            CK.check(s)
            passed = True
        except CK.SQLRejected as e:
            passed, s = False, f"{s} → {e}"
        check(passed, f"검사 통과: {s[:50]}", s)

    bad = ["DELETE FROM v_irp_customer",
           "SELECT * FROM irp_customer_base",
           "SELECT 1 FROM v_irp_customer; DROP TABLE branch",
           "SELECT customer_name FROM v_irp_customer -- 주석",
           "WITH v_irp_customer AS (SELECT * FROM irp_customer_base) SELECT * FROM v_irp_customer",
           "SELECT * INTO x FROM v_irp_customer",
           "SELECT * FROM v_irp_customer, irp_customer_base",
           "SELECT * FROM v_irp_customer WHERE age IN (SELECT age FROM employee)",
           "SELECT @@version FROM v_irp_customer",
           "SELECT * FROM sys.tables",
           "UPDATE v_irp_customer SET age = 1",
           ""]
    for s in bad:
        try:
            CK.check(s)
            rejected = False
        except CK.SQLRejected:
            rejected = True
        check(rejected, f"검사 거부: {s[:50] or '(빈 문자열)'}")


def check_scope() -> None:
    mine = run.my_branch(EMP)
    check(mine == ("0001", "여의도"), "사번 → 부점: 3902172 는 여의도", str(mine))
    check(run.my_branch("9999999") is None, "모르는 사번은 부점 없음")
    check(run.my_branch("12' OR '1'='1") is None, "사번 꼴이 아니면 조회하지 않음")

    _, rows, _ = run.execute("SELECT DISTINCT branch_name FROM v_irp_customer", "0001", EMP)
    check([r["branch_name"] for r in rows] == ["여의도"], "고객 뷰에는 직원 부점 고객만 있다",
          str(rows))
    # LLM 이 다른 부점을 조건으로 적어도 범위는 바뀌지 않는다 — 결과가 비거나 자기 부점뿐이다.
    _, rows, _ = run.execute("SELECT customer_name FROM v_irp_customer WHERE branch_name = '강남'",
                             "0001", EMP)
    check(rows == [], "다른 부점을 조건으로 적어도 다른 부점 고객은 안 보인다", str(rows[:3]))
    _, rows, _ = run.execute("SELECT branch_name, is_my_branch FROM v_branch_summary", "0002",
                             OTHER_EMP)
    flags = {r["branch_name"]: r["is_my_branch"] for r in rows}
    check(len(rows) == 4 and flags.get("강남") == 1 and flags.get("여의도") == 0,
          "부점 비교는 집계 뷰로 — 내 부점 표시가 사번을 따른다", str(flags))


def check_limits() -> None:
    final, rows, cut = run.execute("SELECT TOP 3 customer_name FROM v_irp_customer ORDER BY eval_amt DESC",
                                   "0001", EMP)
    check(len(rows) == 3 and not cut and "LIMIT 3" in final, "TOP n → 목업 DB 에서는 LIMIT n",
          final[-80:])
    _, rows, cut = run.execute("SELECT customer_name FROM v_irp_customer", "0001", EMP)
    check(len(rows) <= run.MAX_ROWS, f"행 수 상한 {run.MAX_ROWS}")
    saved = run.MAX_ROWS
    try:
        run.MAX_ROWS = 10
        _, rows, cut = run.execute("SELECT customer_name FROM v_irp_customer", "0001", EMP)
        check(len(rows) == 10 and cut, "상한을 넘으면 자르고 잘렸다고 표시한다", f"{len(rows)} {cut}")
    finally:
        run.MAX_ROWS = saved
    check("TOP 101" in _sybase_limited("SELECT customer_name FROM v_irp_customer"),
          "Sybase 는 TOP 을 넣어 상한을 건다")
    check("TOP 5 " in _sybase_limited("SELECT TOP 5 customer_name FROM v_irp_customer"),
          "Sybase — 질문이 정한 TOP 은 그대로(상한 이하)")
    check("TOP 100 " in _sybase_limited("SELECT TOP 5000 customer_name FROM v_irp_customer"),
          "Sybase — 상한을 넘는 TOP 은 상한으로 줄인다")


def _sybase_limited(sql: str) -> str:
    saved = os.environ.get("BRANCH_DB")
    os.environ["BRANCH_DB"] = "sybase"
    try:
        return run._limited(sql)
    finally:
        if saved is None:
            os.environ.pop("BRANCH_DB", None)
        else:
            os.environ["BRANCH_DB"] = saved


def check_unavailable() -> None:
    def failed(state, env: str | None = None) -> str:
        saved = os.environ.get("BRANCH_DB")
        if env is not None:
            os.environ["BRANCH_DB"] = env
        try:
            with _LLM(_sql("SELECT customer_name FROM v_irp_customer")):
                tools.run("branch", state, "고객")
            return ""
        except ToolFailure as e:
            return e.reason
        finally:
            if saved is None:
                os.environ.pop("BRANCH_DB", None)
            else:
                os.environ["BRANCH_DB"] = saved

    from pension_agent.common.note import EMP_NO_ENV  # noqa: PLC0415
    saved_env = os.environ.pop(EMP_NO_ENV, None)
    try:
        check("부점" in failed(_state(None)), "사번이 없으면 조회하지 않는다(도구 고장)")
    finally:
        if saved_env is not None:
            os.environ[EMP_NO_ENV] = saved_env
    check("부점" in failed(_state("9999999")), "모르는 사번이면 조회하지 않는다")
    check("꺼져" in failed(_state(), "off"), "BRANCH_DB=off 면 조회하지 않는다")
    check("꺼져" in failed(_state(), "sqlite"), "표에 없는 값은 off 로 본다")
    check("작성되지" in failed(_state(), "sybase"), "Sybase 고정 뷰 SQL 이 TODO 면 조회하지 않는다")


def check_retry_and_evidence() -> None:
    with _LLM(_sql("SELECT customer_name FROM irp_customer_base"),
              _sql("SELECT customer_name, days_to_maturity FROM v_irp_customer "
                   "WHERE days_to_maturity BETWEEN 0 AND 30 ORDER BY days_to_maturity")) as llm:
        ev = tools.run("branch", _state(q="만기 30일 이내 고객 누구야?"), "만기 임박 고객")
    check(len(llm.prompts) == 2 and "고정 뷰만" in llm.prompts[1],
          "검사에 걸리면 이유를 실어 다시 쓰게 한다", str(len(llm.prompts)))
    check(ev is not None and ev["tool"] == "branch", "결과가 원장 근거가 된다")
    text = (ev or {}).get("text", "")
    check("| customer_name | days_to_maturity |" in text, "결과가 표로 실린다", text[:200])
    src = ((ev or {}).get("sources") or [{}])[0]
    check(src.get("id") == "branch_db" and "v_irp_customer" in (src.get("doc") or ""),
          "실행한 SQL 이 출처에 남는다", str(src))
    check(not re.search(r"\d{6}-\d{7}", text), "결과에 고객 식별번호가 없다")
    check("만기 30일 이내 고객 누구야?" in llm.prompts[0], "SQL 은 직원의 원문 질문으로 쓴다")
    check(views.catalog().splitlines()[0] in llm.prompts[0], "프롬프트에 고정 뷰 목록이 실린다")
    check("irp_customer_base" not in llm.prompts[0], "프롬프트에 원본 테이블 이름이 없다")

    with _LLM(_sql("SELECT * FROM branch")) as llm:
        try:
            tools.run("branch", _state(), "x")
            reason = ""
        except ToolFailure as e:
            reason = e.reason
    check(len(llm.prompts) == tool.RETRIES + 1 and "만들지 못했" in reason,
          f"끝내 검사에 걸리면 {tool.RETRIES + 1}번 시도 후 도구 고장", reason)

    with _LLM(_sql(None, "날씨는 부점 데이터에 없음")):
        ev = tools.run("branch", _state(q="오늘 날씨 어때"), "날씨")
    check(ev is None, "뷰로 답할 수 없다고 하면 근거 없음(빗나간 호출)")

    with _LLM(_sql("SELECT customer_name FROM v_irp_customer WHERE age > 200")):
        ev = tools.run("branch", _state(), "200세 이상")
    check(ev is not None and "0건" in ev["text"], "0건도 확인한 결과로 싣는다")


def check_registry() -> None:
    check("branch" in tools.TOOLS, "branch 도구가 레지스트리에 있다")
    check("branch" in tools.usable({}), "고객 화면이 닫혀 있어도 쓸 수 있다")
    from pension_agent.prompts import ANSWER_SHAPES  # noqa: PLC0415
    check("branch" in ANSWER_SHAPES, "답변 형태 요구가 있다")


# ─────────────────────────────────────────────────────────────
def main() -> int:
    check_checker()
    check_scope()
    check_limits()
    check_unavailable()
    check_retry_and_evidence()
    check_registry()

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} 통과")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
