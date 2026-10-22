"""부점 브리핑(tools/branch_briefing) 회귀 테스트 — SQL 검사 · 부점 범위 · 행 수 상한 · 미조회 조건 · 재작성 · 원장·출처 ·
룰베이스 부점 시트 적재 · 현황·CASE 판정 · 전략 브리핑 숫자 검사 · 관리 대상 추천 순서·첫 추천 유지. LLM 스텁.

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
    """tool.generate 교체 — replies 순차 반환, 프롬프트 기록."""

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
    # LLM 이 다른 부점을 적어도 범위 불변
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
                tools.run("branch_search", state, "고객")
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
        ev = tools.run("branch_search", _state(q="만기 30일 이내 고객 누구야?"), "만기 임박 고객")
    check(len(llm.prompts) == 2 and "고정 뷰만" in llm.prompts[1],
          "검사에 걸리면 이유를 실어 다시 쓰게 한다", str(len(llm.prompts)))
    check(ev is not None and ev["tool"] == "branch_search", "결과가 원장 근거가 된다")
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
            tools.run("branch_search", _state(), "x")
            reason = ""
        except ToolFailure as e:
            reason = e.reason
    check(len(llm.prompts) == tool.RETRIES + 1 and "만들지 못했" in reason,
          f"끝내 검사에 걸리면 {tool.RETRIES + 1}번 시도 후 도구 고장", reason)

    with _LLM(_sql(None, "날씨는 부점 데이터에 없음")):
        ev = tools.run("branch_search", _state(q="오늘 날씨 어때"), "날씨")
    check(ev is None, "뷰로 답할 수 없다고 하면 근거 없음(빗나간 호출)")

    with _LLM(_sql("SELECT customer_name FROM v_irp_customer WHERE age > 200")):
        ev = tools.run("branch_search", _state(), "200세 이상")
    check(ev is not None and "0건" in ev["text"], "0건도 확인한 결과로 싣는다")


def check_registry() -> None:
    check("branch_search" in tools.TOOLS, "branch_search 도구가 레지스트리에 있다")
    check("branch_search" in tools.usable({}), "고객 화면이 닫혀 있어도 쓸 수 있다")
    from pension_agent.prompts import ANSWER_SHAPES  # noqa: PLC0415
    check("branch_search" in ANSWER_SHAPES, "답변 형태 요구가 있다")


class _Today:
    """«오늘» 고정 + 목업 DB 재생성(합성 고객의 만기일이 오늘 기준이라 결과를 고정한다)."""

    def __init__(self, day: str = "2026-10-22"):
        self.day = day

    def __enter__(self):
        from pension_agent.common.clock import TODAY_ENV  # noqa: PLC0415
        from pension_agent.tools.branch_briefing import demo_db  # noqa: PLC0415
        self._env, self._saved = TODAY_ENV, os.environ.get(TODAY_ENV)
        os.environ[TODAY_ENV] = self.day
        demo_db.reset()
        return self

    def __exit__(self, *exc):
        from pension_agent.tools.branch_briefing import demo_db  # noqa: PLC0415
        if self._saved is None:
            os.environ.pop(self._env, None)
        else:
            os.environ[self._env] = self._saved
        demo_db.reset()


def check_rules() -> None:
    from pension_agent.tools.branch_briefing import status  # noqa: PLC0415
    r = status.rules()
    check(len(r.segments) >= 9 and len(r.cases) == 5, "부점 세그먼트·CASE 가 룰베이스 표에서 적재된다",
          f"{len(r.segments)}·{len(r.cases)}")
    check(all(s.conds for s in r.segments), "모든 세그먼트에 조건 행이 있다")
    check([c.priority for c in r.cases] == sorted(c.priority for c in r.cases), "CASE 는 우선순위 순이다")
    check("초안" in r.status, "표가 스스로 초안(검토 전)임을 밝힌다", r.status)
    bs01 = next(s for s in r.segments if s.id == "BS-01")
    check(status.segment_sql(bs01) == "idle_cash_pct >= 50", "BS-01 은 TG-001 과 같은 임계(고유계정대 50%)",
          status.segment_sql(bs01))
    check("납입금 미운용" in bs01.aliases and "현금성 미운용" in bs01.aliases,
          "「납입금 미운용」·「현금성 미운용」은 BS-01 의 다른 이름이다(용어 통일)")
    bad = [status.Segment("X", "x", "D", (("eval_amt; DROP", ">=", 1),), (), (), ""),
           status.Segment("X", "x", "D", (("eval_amt", "LIKE", 1),), (), (), ""),
           status.Segment("X", "x", "D", (("default_option", "=", "미설정' OR '1'='1"),), (), (), ""),
           status.Segment("X", "x", "D", (), (), (), "")]
    rejected = 0
    for seg in bad:
        try:
            status.segment_sql(seg)
        except status.RuleError:
            rejected += 1
    check(rejected == len(bad), "뷰에 없는 필드·연산자·따옴표 값·빈 조건은 SQL 로 옮기지 않는다",
          f"{rejected}/{len(bad)}")
    check("납입금 미운용" in tool.segment_catalog() and "WHERE idle_cash_pct >= 50" in tool.segment_catalog(),
          "고객 검색 SQL 프롬프트에 세그먼트 정의가 실린다")


def check_status() -> None:
    from pension_agent.tools.branch_briefing import status  # noqa: PLC0415
    with _Today():
        st = status.compute(EMP)
        other = status.compute(OTHER_EMP)
        ev = tools.run("branch_status", _state(), "부점 현황")
    ind = {i["id"]: i for i in st["indicators"]}
    check(st["branch"] == "여의도" and st["prev_date"] == "2026-09-30", "비교 기준은 전월말이다", st["prev_date"])
    check(ind["customer_count"]["direction"] == "감소" and ind["total_eval_amt"]["direction"] == "증가"
          and ind["avg_return_rate"]["label"] == "하락", "지표 방향(계좌 감소·잔액 증가·수익률 하락)",
          str({k: v["direction"] for k, v in ind.items()}))
    check([c["id"] for c in st["cases"]] == ["BC-01", "BC-02"], "여의도는 CASE 1·2 가 성립한다",
          str(st["cases_all"]))
    check(other["cases"] == [], "강남은 성립하는 CASE 가 없다(0건도 결과)", str(other["cases_all"]))
    bc02 = st["cases"][1]
    check(bc02["segment_changes"][0]["id"] == "BS-01" and bc02["segment_changes"][0]["delta"] > 0,
          "CASE 는 판정에 쓴 세그먼트의 인원 변화를 싣는다", str(bc02["segment_changes"]))
    text = (ev or {}).get("text", "")
    check(ev is not None and "부점 현황" in text and "오늘의 관리 방향" in text, "branch_status 가 원장 근거가 된다")
    check("idle_cash_pct" not in text and "WHERE" not in text, "재료에 컬럼 이름·SQL 이 없다(개발 용어)")
    check(bool((ev or {}).get("marks")), "근거등급 D 기준이 섞이면 재료 성격 표시가 붙는다")
    check("branch_status" in tools.usable({}), "부점 현황은 고객 화면 없이 쓸 수 있다")

    # 임계값은 표가 정한다 — 세그먼트 증가 최소 인원을 올리면 CASE 가 사라진다
    r = status.rules()
    saved = dict(r.params)
    try:
        r.params["BP-01"] = 1000
        with _Today():
            st2 = status.compute(EMP)
    finally:
        r.params.clear()
        r.params.update(saved)
    check(st2["cases"] == [], "증가 판정 최소 인원(BP-01)을 올리면 CASE 가 성립하지 않는다")


def check_strategy() -> None:
    from pension_agent.tools.branch_briefing import status, strategy  # noqa: PLC0415
    with _Today():
        st = status.compute(EMP)
        none = status.compute(OTHER_EMP)
    nums = strategy.allowed_numbers(st)
    check(nums and all(n.endswith("명") for n in nums), "문장에 쓸 수 있는 숫자는 세그먼트 인원 변화뿐이다",
          str(nums))
    good = (f"부점 잔액은 증가했지만 계좌 수는 줄었고 계약이전 사전조회 신청도 {nums[0]} 늘었습니다. "
            "만기금액이 크고 최근 접촉이 없었던 고객부터 확인하는 것이 좋겠습니다.")
    check(strategy.check_text(good, st) == [], "방향만 쓴 지표 + 세그먼트 인원 변화는 통과한다")
    check(strategy.check_text("계좌 수가 6계좌 줄었습니다.", st) != [], "지표 수치를 쓰면 걸린다")

    saved = strategy.generate
    calls: list[str] = []

    def _gen(*replies):
        it = list(replies)

        def g(prompt, **kw):
            calls.append(prompt)
            return it.pop(0) if len(it) > 1 else it[0]
        return g
    try:
        strategy.generate = _gen(good)
        out = strategy.write(st)
        check(out["status"] == "ok" and out["text"] == good, "검사를 통과한 LLM 문장이 나간다")
        check("우선 관리할 고객" in calls[-1] and "만기금액" in calls[-1], "프롬프트에 판정된 CASE 가 실린다")
        calls.clear()
        strategy.generate = _gen("잔액이 66억원으로 늘었습니다.", good)
        out = strategy.write(st)
        check(out["status"] == "ok" and len(calls) == 2 and "다음 이유" in calls[1],
              "검사에 걸리면 이유를 실어 한 번 다시 쓴다")
        strategy.generate = _gen("잔액이 66억원으로 늘었습니다.")
        out = strategy.write(st)
        check(out["status"] == "fallback" and out["text"] == " ".join(c["strategy"] for c in st["cases"]),
              "두 번 걸리면 기준표의 전략 문장을 그대로 싣는다")

        def down(prompt, **kw):
            raise strategy.LLMError("LLM 미설정 — 테스트")
        strategy.generate = down
        out = strategy.write(st)
        check(out["status"] == "llm_down" and out["text"] is None, "LLM 미연결이면 문장을 만들지 않는다(§11)")
        out = strategy.write(none)
        check(out["status"] == "no_case" and out["text"] == strategy.NO_CASE,
              "CASE 가 없으면 LLM 을 부르지 않고 코드 문장을 싣는다")
    finally:
        strategy.generate = saved


def check_targets() -> None:
    from pension_agent import graph  # noqa: PLC0415
    from pension_agent.tools.customer_solution import target_list  # noqa: PLC0415
    plain = target_list.today_targets()
    with _Today():
        ev = tools.run("branch_targets", _state(q="오늘 중점적으로 관리할 고객은 누구야?"), "관리 대상")
    meta = (ev or {}).get("meta") or {}
    check(ev is not None and meta.get("codes")[:3] == ["mat", "chn", "dor"],
          "관리 대상 추천 순서는 오늘의 CASE 가 가리키는 요건을 따른다", str(meta.get("codes")))
    ordered = target_list.prioritize(plain, meta["codes"])
    check(meta["names"] == [t.profile.nm for t in ordered], "재료의 순서 = prioritize 결과")
    check(sorted(meta["names"]) == sorted(t.profile.nm for t in plain), "선정 대상은 룰베이스 그대로(순서만 바뀜)")
    hit = [n for n, t in zip(meta["names"], ordered) if set(t.conds) & set(meta["codes"])]
    check(meta["names"][:len(hit)] == hit, "CASE 요건을 가진 고객이 앞에 선다")
    check(not re.search(r"\d{6}-\d{7}", ev["text"]) and "first_targets" in meta,
          "첫 추천은 이름으로만 남고 식별번호가 없다")

    first = graph._first_targets([], [ev])
    hist = [{"question": "오늘 누구 봐야 해", "first_targets": first}]
    check(graph._first_targets(hist, []) == first, "다음 턴은 이전 턴의 첫 추천을 잇는다")
    fake = dict(first, names=list(reversed(first["names"])))
    with _Today():
        again = tools.run("branch_targets", {"question": "처음 추천한 고객 다시 보여줘", "employee_id": EMP,
                                             "history": [{"question": "q", "first_targets": fake}]}, "다시")
    am = again["meta"]
    check(am["replayed"] and am["names"] == fake["names"] and "첫 추천" in again["text"],
          "이 대화의 첫 추천이 있으면 다시 계산하지 않고 그 목록을 그대로 싣는다")
    old = dict(first, date="2026-10-21")
    with _Today():
        fresh = tools.run("branch_targets", {"question": "q", "employee_id": EMP,
                                             "history": [{"question": "q", "first_targets": old}]}, "x")
    check(not fresh["meta"]["replayed"], "날짜가 바뀐 첫 추천은 다시 계산한다")
    from pension_agent.actions import memo  # noqa: PLC0415
    table, _ = memo.table_for({}, [again])
    first_name = fake["names"][0]
    check(table.find(first_name) != -1 and table.find(first_name) < table.find(fake["names"][-1]),
          "쪽지 표도 도구가 실은 순서 그대로다")


def main() -> int:
    check_checker()
    check_scope()
    check_limits()
    check_unavailable()
    check_retry_and_evidence()
    check_registry()
    check_rules()
    check_status()
    check_strategy()
    check_targets()

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} 통과")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
