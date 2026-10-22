"""부점 현황 — 지표 · 세그먼트 인원 · 전월말 대비 변화 · 오늘의 CASE 판정. 전부 코드(LLM 없음).

기준은 룰베이스 xlsx 의 부점 시트 6종(`targets.json` 의 branch_* — 에이전트 초안, 기획자 검토 전).
여기는 그 표를 읽어 SQL 을 만들고 센다 — 임계값·조건을 코드에 두지 않는다.

    rules()          표 → Segment · Case · 임계값
    segment_sql()    세그먼트 조건 → WHERE (필드·연산자·값은 표에서, 형식은 여기서 검사)
    compute()        사번 → 부점 현황 dict (화면 패널 · branch_status 도구 · 전략 브리핑이 같은 값을 쓴다)
    focus_sql()      CASE 의 중점관리 조건 → WHERE (오늘의 중점관리 고객 = 관리 대상 ∩ 이 조건)
    target_limits()  관리 대상 추천에 싣는 최대 인원(중점관리 · 중점관리 없음)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from pension_agent.common import clock, config
from pension_agent.tools.branch_briefing import check, run, views

#: 부점 지표 — 대상ID → (이름, 단위, 증가·감소 최소 변화폭 PARAM_ID, CASE 판정에 쓰는 핵심 지표인가).
#: 단위: 원 · % · 그 밖은 개수 단위. 계좌 수는 고객 수로 센다(개인형IRP 1인 1계좌 가정 — DEMO_STATUS 기재).
#: 부점 수익률·원리금보장 비중은 적립금(평가금액) 가중이다.
INDICATORS: dict[str, tuple[str, str, str, bool]] = {
    "total_eval_amt": ("부점 잔액", "원", "BP-03", True),
    "customer_count": ("계좌 수", "계좌", "BP-02", True),
    "branch_return_rate": ("부점 수익률", "%", "BP-04", True),
    "idle_cash_total": ("고유계정대 잔액", "원", "BP-03", False),
    "safe_asset_ratio": ("원리금보장 비중", "%", "BP-07", False),
    "transfer_out_count": ("올해 계약이전", "좌", "BP-02", False),
    "transfer_out_amt": ("올해 계약이전 금액", "원", "BP-03", False),
    "low_return_count": ("수익률 저조 고객", "명", "BP-02", False),
    "high_balance_count": ("적립금 1억 이상 고객", "명", "BP-02", False),
    "high_balance_share": ("적립금 1억 이상 고객의 적립금 비중", "%", "BP-07", False),
}
#: 수익률 저조 · 고액 적립금 기준 PARAM_ID(부점 브리핑 화면 기획안 — 근거등급 D)
_LOW_RETURN_PARAM, _HIGH_BALANCE_PARAM = "BP-08", "BP-09"
_SEGMENT_PARAM = "BP-01"
_MAX_CASES_PARAM = "BP-05"
_FOCUS_MAX_PARAM, _PLAIN_MAX_PARAM = "BP-10", "BP-11"

_OPS = (">=", "<=", "=")
#: 방향 표기 — 판정 값은 증가·감소 그대로, 사람이 읽는 말만 바꾼다
_WORDS = {("branch_return_rate", "감소"): "하락", ("branch_return_rate", "증가"): "상승"}
#: 표의 문자 조건값 형식 — 따옴표·세미콜론이 끼면 SQL 로 옮기지 않는다
_TEXT_VALUE = re.compile(r"[가-힣A-Za-z0-9 ]{1,20}")


class RuleError(ValueError):
    """표의 조건을 SQL 로 옮길 수 없음(필드·연산자·값 형식). 표를 고쳐야 한다."""


@dataclass(frozen=True)
class Segment:
    id: str
    name: str
    grade: str
    conds: tuple[tuple[str, str, object], ...]   # (필드, 연산자, 값) — 전부 AND
    codes: tuple[str, ...]                        # 연결 요건코드(customer.CONDS)
    aliases: tuple[str, ...]                      # 직원 표현
    rule_text: str                                # 조건식(사람이 읽는 원문 표기)


@dataclass(frozen=True)
class Case:
    id: str
    name: str
    priority: int
    grade: str
    conds: tuple[tuple[str, str, str], ...]      # (대상구분, 대상ID, 방향)
    feature: str                                  # 우선관리 고객 특징
    codes: tuple[str, ...]                        # 연결 요건코드 — 관리 대상 추천 정렬용
    strategy: str                                 # 상담·운용 전략(표 원문)
    focus: tuple[tuple[str, str, object], ...] = ()   # 중점관리 조건(필드, 연산자, 값) — 전부 AND
    focus_notes: tuple[str, ...] = ()                 # 그 조건의 설명(표 원문 — 재료에 싣는다)


@dataclass(frozen=True)
class Rules:
    segments: tuple[Segment, ...]
    cases: tuple[Case, ...]
    params: dict[str, object]
    status: str                                   # 적용상태(초안 여부) — 표 그대로


def _split(cell) -> tuple[str, ...]:
    return tuple(x.strip() for x in str(cell or "").split(";") if x.strip())


def _grade(cell) -> str:
    return str(cell or "?").strip()[:1] or "?"


@lru_cache(maxsize=1)
def rules() -> Rules:
    """targets.json 의 부점 시트 → Rules. 표가 없으면 빈 Rules(조회는 되고 CASE 는 없다)."""
    doc = json.loads(config.TARGETS_JSON.read_text(encoding="utf-8"))
    conds: dict[str, list] = {}
    for c in sorted(doc.get("branch_segment_conditions") or [], key=lambda c: int(c.get("순서") or 0)):
        conds.setdefault(c["SEG_ID"], []).append((c["조건필드"], c["연산자"], c["조건값"]))
    segments = tuple(
        Segment(s["SEG_ID"], s["세그먼트명"], _grade(s.get("근거등급")), tuple(conds.get(s["SEG_ID"], ())),
                _split(s.get("연결 요건코드")), _split(s.get("직원 표현")), str(s.get("조건식") or ""))
        for s in doc.get("branch_segments") or [])
    cconds: dict[str, list] = {}
    for c in sorted(doc.get("branch_case_conditions") or [], key=lambda c: int(c.get("순서") or 0)):
        cconds.setdefault(c["CASE_ID"], []).append((c["대상구분"], c["대상ID"], c["방향"]))
    cases = tuple(sorted(
        (Case(c["CASE_ID"], c["CASE명"], int(c.get("우선순위") or 99), _grade(c.get("근거등급")),
              tuple(cconds.get(c["CASE_ID"], ())), str(c.get("우선관리 고객 특징") or ""),
              _split(c.get("연결 요건코드")), str(c.get("상담·운용 전략") or ""))
         for c in doc.get("branch_cases") or []),
        key=lambda c: c.priority))
    focus: dict[str, list] = {}
    for c in sorted(doc.get("branch_case_focus") or [], key=lambda c: int(c.get("순서") or 0)):
        focus.setdefault(c["CASE_ID"], []).append(c)
    cases = tuple(
        Case(c.id, c.name, c.priority, c.grade, c.conds, c.feature, c.codes, c.strategy,
             tuple((f["조건필드"], f["연산자"], f["조건값"]) for f in focus.get(c.id, ())),
             tuple(str(f.get("설명") or "") for f in focus.get(c.id, ())))
        for c in cases)
    params = {p["PARAM_ID"]: p.get("값") for p in doc.get("branch_params") or []}
    states = {str(s.get("적용상태") or "") for s in doc.get("branch_segments") or []}
    return Rules(segments, cases, params, " / ".join(sorted(s for s in states if s)))


def _sql_value(field: str, op: str, value: object) -> str:
    known = {c for c, _t, _d in views.columns("v_irp_customer")}
    if field not in known:
        raise RuleError(f"조건필드 {field!r} 는 고정 뷰에 없는 컬럼입니다")
    if op not in _OPS:
        raise RuleError(f"연산자 {op!r} 는 쓸 수 없습니다({' · '.join(_OPS)})")
    if isinstance(value, bool) or value is None:
        raise RuleError(f"조건값 {value!r} 를 SQL 로 옮길 수 없습니다")
    if isinstance(value, (int, float)):
        return f"{field} {op} {value}"
    text = str(value).strip()
    if not _TEXT_VALUE.fullmatch(text):
        raise RuleError(f"조건값 {text!r} 형식을 SQL 로 옮길 수 없습니다")
    return f"{field} {op} '{text}'"


def segment_sql(seg: Segment) -> str:
    """세그먼트 → WHERE 절 본문(AND). 조건이 없으면 RuleError — 빈 조건은 «전원»이 된다."""
    if not seg.conds:
        raise RuleError(f"{seg.id} 에 조건 행이 없습니다")
    return " AND ".join(_sql_value(f, o, v) for f, o, v in seg.conds)


def focus_sql(case: Case) -> str:
    """CASE → 중점관리 조건 WHERE 절 본문(AND). 조건이 없으면 RuleError."""
    if not case.focus:
        raise RuleError(f"{case.id} 에 중점관리 조건 행이 없습니다")
    return " AND ".join(_sql_value(f, o, v) for f, o, v in case.focus)


def _snapshot_sql(view: str, segments: tuple[Segment, ...], low: float, high: float) -> str:
    big = f"eval_amt >= {high:g}"
    cols = ["COUNT(*) AS customer_count", "SUM(eval_amt) AS total_eval_amt",
            "ROUND(SUM(eval_amt * return_rate) / SUM(eval_amt), 2) AS branch_return_rate",
            "SUM(idle_cash_amt) AS idle_cash_total",
            "ROUND(SUM(eval_amt * safe_asset_pct) / SUM(eval_amt), 1) AS safe_asset_ratio",
            f"SUM(CASE WHEN return_rate < {low:g} THEN 1 ELSE 0 END) AS low_return_count",
            f"SUM(CASE WHEN {big} THEN 1 ELSE 0 END) AS high_balance_count",
            f"ROUND(100.0 * SUM(CASE WHEN {big} THEN eval_amt ELSE 0 END) / SUM(eval_amt), 1)"
            " AS high_balance_share"]
    cols += [f"SUM(CASE WHEN {segment_sql(s)} THEN 1 ELSE 0 END) AS seg_{s.id.replace('-', '_')}"
             for s in segments]
    return f"SELECT {', '.join(cols)} FROM {view}"


def _transfer_sql(day: date) -> str:
    """그 날 기준 올해 계약이전(타사 유출) 건수·금액 — 1월 1일부터 그 날까지."""
    return ("SELECT COUNT(*) AS transfer_out_count, SUM(transfer_amt) AS transfer_out_amt "
            f"FROM v_transfer_out WHERE transfer_date >= '{day.year}-01-01' "
            f"AND transfer_date <= '{day.isoformat()}'")


def _snapshot(view: str, day: date, r: Rules, code: str, emp_no: str | None) -> dict:
    """한 시점(오늘 · 전월말)의 지표·세그먼트 인원."""
    low, high = _param(r, _LOW_RETURN_PARAM, 1), _param(r, _HIGH_BALANCE_PARAM, 100_000_000)
    out: dict = {}
    for sql in (_snapshot_sql(view, r.segments, low, high), _transfer_sql(day)):
        # 코드가 만든 SQL 도 같은 검사를 지난다 — 표에서 온 값이 SQL 을 바꾸는 길을 막는 두 번째 문
        _final, rows, _cut = run.execute(check.check(sql), code, emp_no)
        out.update(dict(rows[0]) if rows else {})
    return out


def _direction(delta: float, step: float) -> str:
    if delta >= step:
        return "증가"
    if delta <= -step:
        return "감소"
    return "변화 없음"


def target_limits() -> tuple[int, int]:
    """(오늘의 중점관리 고객 최대 인원, 중점관리 고객이 없을 때 싣는 인원) — 부점_임계값 BP-10 · BP-11."""
    r = rules()
    return int(_param(r, _FOCUS_MAX_PARAM, 100)), int(_param(r, _PLAIN_MAX_PARAM, 30))


def _param(rules_: Rules, pid: str, default: float) -> float:
    try:
        return float(rules_.params.get(pid))
    except (TypeError, ValueError):
        return default


def _matches(case: Case, ind: dict[str, dict], seg: dict[str, dict]) -> list[str] | None:
    """CASE 조건이 전부 성립하면 근거 문장 목록, 아니면 None. 모르는 대상ID 는 불성립(지어내지 않는다)."""
    why = []
    for kind, target, want in case.conds:
        row = (ind if kind == "지표" else seg).get(target)
        if row is None or row["direction"] != want:
            return None
        why.append(f"{row['name']} {_WORDS.get((target, want), want)}")
    return why if case.conds else None


def compute(emp_no: str | None) -> dict:
    """사번 → 부점 현황. 부점을 못 찾으면 None 대신 run.DBUnavailable·LookupError.

    반환: branch · base_date · prev_date · indicators[] · segments[] · cases[] · rule_status
    """
    mine = run.my_branch(emp_no)
    if not mine:
        raise LookupError("직원 사번으로 소속 부점을 확인하지 못했습니다")
    code, name = mine
    r = rules()
    now = _snapshot("v_irp_customer", clock.today(), r, code, emp_no)
    prev = _snapshot("v_irp_customer_prev", clock.prev_month_end(), r, code, emp_no)

    indicators: dict[str, dict] = {}
    for key, (label, unit, pid, core) in INDICATORS.items():
        a, b = now.get(key) or 0, prev.get(key) or 0
        direction = _direction(a - b, _param(r, pid, 1))
        indicators[key] = {"id": key, "name": label, "unit": unit, "core": core, "now": a, "prev": b,
                           "delta": round(a - b, 2), "direction": direction,
                           "label": _WORDS.get((key, direction), direction)}
    seg_step = _param(r, _SEGMENT_PARAM, 5)
    segments: dict[str, dict] = {}
    for s in r.segments:
        col = f"seg_{s.id.replace('-', '_')}"
        a, b = int(now.get(col) or 0), int(prev.get(col) or 0)
        segments[s.id] = {"id": s.id, "name": s.name, "grade": s.grade, "now": a, "prev": b,
                          "delta": a - b, "direction": _direction(a - b, seg_step),
                          "rule": s.rule_text}
    cases = []
    for c in r.cases:
        why = _matches(c, indicators, segments)
        if why is None:
            continue
        cases.append({"id": c.id, "name": c.name, "priority": c.priority, "grade": c.grade,
                      "why": why, "feature": c.feature, "strategy": c.strategy, "codes": list(c.codes),
                      "focus_notes": list(c.focus_notes),
                      # 판정에 쓴 세그먼트의 인원 변화 — 전략 브리핑 문장이 쓸 수 있는 유일한 수치
                      "segment_changes": [
                          {"id": t, "name": segments[t]["name"], "delta": segments[t]["delta"]}
                          for k, t, _w in c.conds if k == "세그먼트"]})
    limit = int(_param(r, _MAX_CASES_PARAM, 2))
    return {
        "branch": name,
        "base_date": clock.today().isoformat(),
        "prev_date": clock.prev_month_end().isoformat(),
        "indicators": list(indicators.values()),
        "segments": list(segments.values()),
        "cases": cases[:limit],
        "cases_all": [c["id"] for c in cases],
        "rule_status": r.status,
        # 세그먼트 «증가·감소» 판정 최소 인원(BP-01)
        "segment_step": int(seg_step),
    }


def case_codes(emp_no: str | None) -> list[str]:
    """오늘의 CASE 가 가리키는 요건코드(CASE 우선순위 순, 중복 제거). 현황을 못 구하면 빈 목록."""
    try:
        cases = compute(emp_no)["cases"]
    except (LookupError, run.DBUnavailable, RuleError, check.SQLRejected):
        return []
    out: list[str] = []
    for c in cases:
        out += [x for x in c["codes"] if x not in out]
    return out
