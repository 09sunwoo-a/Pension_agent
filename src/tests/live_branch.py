"""부점 브리핑 시험 질문을 실제 LLM 으로 돌려 채점한다(tests/branch_questions.py).

    python -m tests.live_branch            # 바뀐 것만 — 지난번 통과 후 관련 코드·질문이 그대로면 건너뜀
    python -m tests.live_branch --list     # LLM 없이 무엇을 돌릴지만 본다
    python -m tests.live_branch --only deposit_focus,past_deposit
    python -m tests.live_branch --all      # 전부 다시

LLM 키(LLM_*)가 환경에 있어야 한다. 무료 키는 느리므로 LLM_MAX_CONCURRENCY=1 LLM_MIN_INTERVAL_SEC=10 을 권한다.
결과는 tests/.live_cache/branch.json(저장소 밖)에 남는다 — 질문마다 통과 여부 · 답변 · LLM 이 쓴 조회문 · 걸린 것.

«관련 코드»는 프롬프트 · 계획/답변 노드 · 검증기 · 부점 브리핑 도구(SQL 포함) · 관리 대상 요건 판정이다. 그중 하나라도
바뀌면 모든 질문을 다시 돌린다(어느 질문에 영향이 가는지 코드로 가를 수 없다). 오프라인으로 먼저 거르는 것은
tests.test_branch 의 check_question_set 이다 — 그게 통과한 뒤에 이걸 돌린다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import tests  # noqa: F401 — 기준일 고정(PINNED_TODAY) — 오프라인 정답과 같은 날짜
from tests import branch_questions as BQ

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SRC = Path(__file__).resolve().parents[1]
CACHE = SRC / "tests" / ".live_cache" / "branch.json"
WATCHED = ["pension_agent/prompts", "pension_agent/nodes", "pension_agent/evidence",
           "pension_agent/tools/branch_briefing", "pension_agent/tools/__init__.py",
           "pension_agent/tools/customer_solution/customer.py", "pension_agent/graph.py"]


def code_fingerprint() -> str:
    h = hashlib.sha256()
    for root in WATCHED:
        path = SRC / root
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for f in files:
            if f.is_file() and f.suffix in (".py", ".sql", ".json"):
                h.update(str(f.relative_to(SRC)).encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:16]


def fingerprint(q: BQ.Q, code: str) -> str:
    return hashlib.sha256((code + json.dumps(asdict(q), ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:16]


def _load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _rows(sql: str) -> tuple[list[dict], bool]:
    from pension_agent.tools.branch_briefing import check, run  # noqa: PLC0415
    _final, rows, truncated = run.execute(check.check(sql), BQ.BRANCH_CODE, BQ.EMP)
    return rows, truncated


def _values(rows: list[dict]) -> set:
    out = set()
    for r in rows:
        for v in r.values():
            out.add(round(v, 2) if isinstance(v, float) else v)
    return out


def compare(ref: list[dict], ref_cut: bool, got: list[dict], got_cut: bool) -> str:
    """정답 조회 결과와 LLM 조회 결과 비교. 같으면 빈 문자열."""
    if ref and "customer_name" in ref[0]:
        if not got or "customer_name" not in got[0]:
            return "정답은 고객 명단인데 LLM 조회 결과에 customer_name 이 없음"
        if ref_cut:
            return "" if got_cut else f"정답은 100행을 넘는데 LLM 조회는 {len(got)}행"
        a, b = {r["customer_name"] for r in ref}, {r["customer_name"] for r in got}
        if a == b:
            return ""
        return (f"명단이 다름 — 정답 {len(a)}명 · LLM {len(b)}명 · 빠진 사람 {sorted(a - b)[:5]} · "
                f"더한 사람 {sorted(b - a)[:5]}")
    missing = [v for v in _values(ref) if isinstance(v, (int, float)) and v not in _values(got)]
    return f"정답 값이 LLM 조회 결과에 없음: {missing}" if missing else ""


def grade(q: BQ.Q, out: dict) -> tuple[list[str], str | None]:
    """→ (걸린 것, LLM 이 쓴 조회문)."""
    from pension_agent.nodes import plan  # noqa: PLC0415
    answer = out.get("answer") or ""
    evidence = (out.get("trace") or {}).get("evidence") or []
    faults: list[str] = []
    used = [e.get("tool") for e in evidence]
    if not set(q.tool.split("|")) & set(used):
        faults.append(f"도구 «{q.tool}»를 쓰지 않음(쓴 도구: {used or '없음'})")
    if plan.is_failure_notice(answer):
        faults.append("실패 안내로 답함")
    mine = next((e for e in evidence if e.get("tool") == "branch_search"), None)
    llm_sql = next((c.get("doc") for c in (mine or {}).get("cards") or [] if c.get("id") == "branch_db"), None)
    names, count = None, None
    if "branch_search" in q.tool.split("|") and mine:
        cannot = "조회하지 못함" in (mine.get("text") or "")
        if q.sql is None:
            if not cannot:
                faults.append(f"조회할 수 없는 질문인데 조회함: {llm_sql}")
        elif cannot or not llm_sql:
            faults.append("조회할 수 있는 질문인데 «조회하지 못함»")
        else:
            ref, ref_cut = _rows(q.sql)
            try:
                got, got_cut = _rows(llm_sql)
                diff = compare(ref, ref_cut, got, got_cut)
            except Exception as exc:  # noqa: BLE001
                diff = f"LLM 조회문을 다시 실행하지 못함: {exc}"
            if diff:
                faults.append(diff)
            if ref and "customer_name" in ref[0]:
                names, count = [r["customer_name"] for r in ref], len(ref)
    faults += BQ.answer_faults(q, answer, names, count)
    return faults, llm_sql


def run_one(q: BQ.Q) -> dict:
    from pension_agent import graph  # noqa: PLC0415
    started = time.time()
    try:
        out = graph.ask(q.question, [], session_id=f"live-{q.id}", x_client_user=f"{BQ.EMP}-qa",
                        employee_id=BQ.EMP)
        faults, llm_sql = grade(q, out)
        answer = out.get("answer") or ""
    except Exception as exc:  # noqa: BLE001 — 한 질문이 죽어도 나머지는 돈다
        faults, llm_sql, answer = [f"실행 오류: {type(exc).__name__}: {exc}"], None, ""
    return {"passed": not faults, "faults": faults, "answer": answer, "sql": llm_sql,
            "seconds": round(time.time() - started), "at": time.strftime("%Y-%m-%d %H:%M")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    cache, code = _load(), code_fingerprint()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    unknown = only - {q.id for q in BQ.QUESTIONS}
    if unknown:
        print(f"없는 질문 id: {', '.join(sorted(unknown))}")
        return 2
    todo = []
    for q in BQ.QUESTIONS:
        last = cache.get(q.id) or {}
        fresh = last.get("passed") and last.get("fingerprint") == fingerprint(q, code)
        if (only and q.id in only) or (not only and (args.all or not fresh)):
            todo.append(q)
        elif args.list:
            print(f"  건너뜀  {q.id} — 지난번 통과({last.get('at')}) 후 바뀐 것 없음")
    if args.list:
        for q in todo:
            last = cache.get(q.id) or {}
            why = ("처음" if not last else "지난번 실패" if not last.get("passed") else "코드·질문이 바뀜")
            print(f"  돌림    {q.id} — {why}")
        return 0
    for i, q in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {q.id}: {q.question}", flush=True)
        result = run_one(q) | {"fingerprint": fingerprint(q, code), "question": q.question}
        cache[q.id] = result
        _save(cache)
        print(("  ✓ 통과" if result["passed"] else "  ✗ " + "\n  ✗ ".join(result["faults"]))
              + f"  ({result['seconds']}초)", flush=True)
        print("  답: " + result["answer"].split("\n\n")[0].replace("\n", "\n     ")[:600], flush=True)
    done = [cache.get(q.id) or {} for q in BQ.QUESTIONS]
    passed = sum(1 for d in done if d.get("passed"))
    print(f"\n{passed}/{len(BQ.QUESTIONS)} 통과(이번에 돌린 것 {len(todo)}개 · 나머지는 지난 결과)")
    return 0 if passed == len(BQ.QUESTIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
