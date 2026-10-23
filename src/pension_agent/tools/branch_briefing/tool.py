"""부점 브리핑 도구 둘.

    branch_search   고객 검색 — 질문 → [LLM] SELECT → 검사 → 실행 → 결과 표를 원장에 적재
    branch_status   부점 현황 — 지표·세그먼트 전월말 대비 변화·오늘의 CASE(status.compute, LLM 없음)


branch_search: 검사 거부·실행 오류는 사유를 실어 최대 RETRIES 번 재작성. 볼 수 있는 뷰·부점 범위·행 수는 코드가 결정.
사번·부점 미확인 시 조회 안 함.
"""

from __future__ import annotations

import re

from pension_agent.common import clock, observability
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
    pct = _percent_columns()
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(_cell(r.get(c)) + ("%" if c in pct and r.get(c) is not None else "")
                              + _past(c, r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(out)


def _past(col: str, value) -> str:
    """남은 일수가 음수인 칸에 뜻을 붙인다 — 「-13」을 답변이 «만기까지 -13일»로 옮겼다(2026-10-23 실측)."""
    what = views.PAST_WHEN_NEGATIVE.get(col)
    if what and isinstance(value, (int, float)) and value < 0:
        return f" ({what} {abs(int(value))}일 지남)"
    return ""


def _percent_columns() -> set[str]:
    """뷰 목록이 «(%)» 로 선언한 컬럼 — 셀에 % 를 붙인다. 답변은 「수익률 -9.4%」로 쓰는데 셀이 맨숫자면
    검증기가 다른 주장으로 보고 답을 폐기한다(기준서 §6 «단위 표기 — 데이터가 선언한 열뿐»)."""
    return {c for v in views.VIEWS for c, t, _d in (views.VIEWS[v][1] or []) if "(%)" in t}


_OPS = {">=": "이상", "<=": "이하", ">": "초과", "<": "미만", "=": "", "<>": "아님", "!=": "아님"}
_NUM = r"(-?\d+(?:\.\d+)?)"
#: 문자(날짜) 값의 크기 비교 — 「transfer_date >= '2026-01-01'」 → «계약이전 일자 2026-01-01부터»
_TEXT_OPS = {">=": "부터", "<=": "까지", ">": " 이후", "<": " 이전"}
_WHERE = re.compile(r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|$)", re.IGNORECASE | re.DOTALL)


def _unit(kind: str, num: str) -> str:
    if "(%)" in kind:
        return f"{num}%"
    if "(원)" in kind:
        return won(int(float(num)))
    if "(일)" in kind:
        return f"{num}일"
    if "(세)" in kind:
        return f"{num}세"
    return num


def criteria(sql: str) -> list[str]:
    """조회문의 WHERE 조건 → 직원이 읽는 말(「만기 상품: 정기예금」 · 「만기까지 남은 일수 0일~30일」 ·
    「원리금보장·현금성 자산 비중 80% 이상」). 조회문 순서대로.

    재료에 조건이 SQL 로만 있으면 답변이 그 조건을 말로 옮기지 못하고 빠뜨린다 — 「정기예금 만기 한 달 이내」를
    조회에는 걸고 답변 첫머리에서는 빼 «정기예금 조건을 못 찾았다»로 읽혔다(2026-10-23 실측). 백분율 조건이 맨숫자로만
    있으면 답변의 «80% 이상»이 자료 밖 수치로 폐기되기도 했다. 단위는 뷰 목록의 타입 선언에서만 온다(table 과 같은
    선언 — 기준서 §6 «단위 표기 — 데이터가 선언한 열뿐»).
    """
    m = _WHERE.search(sql)
    if not m:
        return []
    where = m.group(1)
    kinds = {c: (t, d) for v in views.VIEWS for c, t, d in (views.VIEWS[v][1] or [])}
    found: list[tuple[int, str]] = []
    for col, (kind, desc) in kinds.items():
        label = views.CONDITION_LABELS.get(col) or re.split(r" — |\. |\(", desc)[0].strip()
        for x in re.finditer(rf"\b{col}\s+BETWEEN\s+{_NUM}\s+AND\s+{_NUM}", where, re.IGNORECASE):
            found.append((x.start(), f"{label} {_unit(kind, x.group(1))}~{_unit(kind, x.group(2))}"))
        for x in re.finditer(rf"\b{col}\s*(>=|<=|<>|!=|>|<|=)\s*{_NUM}(?![\w.])", where):
            past = views.PAST_WHEN_NEGATIVE.get(col)
            if past and (x.group(1), x.group(2)) in (("<", "0"), ("<=", "-1")):  # «만기까지 0일 미만» 대신
                found.append((x.start(), f"{past}가 지남"))
                continue
            if past and (x.group(1), x.group(2)) in ((">=", "0"), (">", "-1")):
                found.append((x.start(), f"{past}가 아직 안 지남"))
                continue
            if "0/1" in kind:  # 여부 컬럼 — 1 이면 그것만, 0 이면 그것이 아닌 고객
                yes = (x.group(1) in ("=", ">=")) == (x.group(2) == "1")
                found.append((x.start(), f"{label}{'만' if yes else ' 아님'}"))
                continue
            found.append((x.start(), f"{label} {_unit(kind, x.group(2))} {_OPS[x.group(1)]}".rstrip()))
        for x in re.finditer(rf"\b{col}\s*(=|<>|!=)\s*'([^']*)'", where):
            value = views.VALUE_LABELS.get((col, x.group(2)), x.group(2))
            found.append((x.start(), f"{label}: {value}" + (" 아님" if x.group(1) != "=" else "")))
        for x in re.finditer(rf"\b{col}\s*(>=|<=|>|<)\s*'([^']*)'", where):
            found.append((x.start(), f"{label} {x.group(2)}{_TEXT_OPS[x.group(1)]}"))
        for x in re.finditer(rf"\b{col}\s+LIKE\s+'([^']*)'", where, re.IGNORECASE):
            word = re.sub(r"^%|%$", "", x.group(1))
            note = _reasons().get(word) if col == "target_reasons" else None
            if note is not None:  # 요건 판정 결과로 건 조건 — 그 요건의 기준을 함께
                found.append((x.start(), f"관리 대상 요건 «{word}»" + (f"({note})" if note else "")))
            else:
                found.append((x.start(), f"{label}에 «{word}» 포함"))
    # 같은 조건을 두 뷰에 건 조회(오늘 · 전월말 비교)는 한 번만
    return list(dict.fromkeys(text for _pos, text in sorted(found)))


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
    lines += reason_catalog({_bare(s.name) for s in status.rules().segments})
    return "\n".join(lines) or "(정의 없음)"


def _bare(name: str) -> str:
    return re.split(r"[(（]", name)[0].strip()


def reason_catalog(segment_names: set[str]) -> list[str]:
    """관리 대상 요건 이름 → target_reasons LIKE. 「장기 미접촉」을 SQL 작성 LLM 이 «90일 이상»처럼 스스로 정하지 않고,
    요건 판정(customer.conditions)이 이미 채운 사유를 그대로 쓰게 한다(2026-10-23 실측 — 판정을 두 번 만들지 않는다).
    세그먼트와 이름이 같은 요건은 세그먼트 정의가 먼저라 싣지 않는다."""
    out = []
    for name, note in _reasons().items():
        if name not in segment_names:
            out.append(f"- {name}(관리 대상 요건{' — ' + note if note else ''}) → WHERE target_reasons LIKE '%{name}%'")
    return out


def _reasons() -> dict[str, str]:
    """관리 대상 요건 이름 → 기준 설명(코드 상수에서 — 판정과 같은 값). 설명이 없는 요건은 빈 문자열."""
    from pension_agent.tools.customer_solution import customer as C  # noqa: PLC0415
    notes = {"dor": f"마지막 접촉 후 {C.DORMANT_DAYS}일 이상"}
    return {_bare(name): notes.get(code, "") for code, name in C.CONDS.items() if _bare(name)}


_SELECT_WORD = re.compile(r"\bSELECT\b", re.IGNORECASE)

#: 조회할 수 없는 질문의 재료 문장(코드). 사유는 SQL 작성 LLM 의 설명이라 숫자가 있으면 싣지 않는다 —
#: 원장에 실리면 그 숫자가 인용 허용이 된다(LLM 이 쓴 문장으로 경계를 넓히지 않는다, 루트 규칙 2).
CANNOT = "이 질문은 부점 고객 데이터에 있는 항목으로 조회할 수 없습니다."
CANNOT_NO_SUBSTITUTE = ("조건 일부만으로 조회하거나 다른 고객 목록(관리 대상 추천 등)으로 대신하면 "
                        "물은 것의 답이 아닙니다.")


def _cannot(query: str, branch_name: str, reason: str) -> Evidence:
    """뷰로 답할 수 없는 질문 → «조회하지 못함» 재료(확인한 결과)."""
    why = reason.strip() if reason and not re.search(r"\d", reason) else ""
    observability.step("branch_search", branch_name, cannot=why[:120] or "-")
    text = "\n".join(x for x in (
        f"■ 부점 데이터 조회 — {branch_name} 부점 · 조회하지 못함",
        f"· {CANNOT}",
        f"· 조회할 수 없는 이유: {why}" if why else "",
        f"· {CANNOT_NO_SUBSTITUTE}",
    ) if x)
    return _ev("branch_search", query, text,
               [{"id": "branch_db", "title": f"부점 데이터 조회 ({branch_name}) — 조회할 수 없는 질문",
                 "doc": None, "score": None, "page": None}],
               meta={"rows": 0, "truncated": False, "branch": branch_name, "sql": None,
                     "cannot": True})


def _sql(question: str, last_sql: str = "", error: str = "") -> tuple[str | None, str]:
    """LLM 생성 (SQL, 설명). 답할 수 없으면 (None, 사유), 파싱 실패면 ("", 사유)."""
    retry = BRANCH_SQL_RETRY_BLOCK.format(sql=last_sql, error=error) if error else ""
    # 오늘 날짜 — 없으면 «올해»를 LLM 이 2024 로 짐작했다(2026-10-23 실측)
    raw = generate(BRANCH_SQL_PROMPT.format(today=clock.today().isoformat(),
                                            catalog=views.catalog(), segments=segment_catalog(),
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
        if written is not None and not _SELECT_WORD.search(written):
            # sql 칸에 조회문 대신 설명을 적은 응답 — «조회할 수 없다»는 답으로 읽는다
            written, reason = None, reason or written
        if written is None:
            # 뷰로 답할 수 없는 질문 — «확인한 결과»로 싣는다(재료 0건으로 두면 계획이 다른 도구의 목록으로 답한다)
            return _cannot(query, branch_name, reason)
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
    note_line = (f"※ 결과가 {run.MAX_ROWS}행을 넘어 앞의 {run.MAX_ROWS}행만 실었습니다."
                 if truncated else "")
    text = "\n".join(x for x in (
        f"■ 부점 데이터 조회 — {branch_name} 부점 · 조회 {len(rows)}행",
        f"· 조회 목적: {reason}" if reason else "",
        ("· 조회 조건: " + " · ".join(criteria(sql))) if criteria(sql) else "",
        "· 실행한 조회문:",
        "```sql", sql, "```",
        table(rows),
        note_line,
    ) if x)
    return _ev("branch_search", query, text,
               [{"id": "branch_db",
                 "title": f"부점 데이터 조회 ({branch_name})",
                 # 실행 SQL 을 출처로 — 수치 역추적용
                 "doc": sql,
                 "score": None, "page": None}],
               meta={"rows": len(rows), "truncated": truncated, "branch": branch_name,
                     "sql": sql})


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


#: 부점 브리핑 화면 틀을 붙이는 질문 — 직원이 부점 전체 상태를 물었을 때만(«디폴트옵션 미설정 고객이 지난달보다
#: 늘었어?»처럼 한 고객군을 물은 답에 지표 표가 따라붙지 않게). 2026-10-23 사용자 결정 — 화면 기획안의 느낌.
BRIEFING_WORDS = ("브리핑", "현황")
_WEEKDAYS = "월화수목금토일"


def _eok(v: float) -> str:
    """금액 줄임 표기(화면 기획안) — 1천만원 이상은 «526.8억»처럼 억 단위 소수 한 자리, 그 밖은 원 표기."""
    if abs(v) >= 10_000_000:
        return f"{abs(v) / 100_000_000:,.1f}억"
    return won(int(abs(v)))


def _short(ind: dict, key: str = "now") -> str:
    v = ind[key]
    if ind["unit"] == "원":
        return _eok(v)
    if ind["unit"] == "%":
        return f"{v:.2f}%" if ind["id"] == "branch_return_rate" else f"{v:.1f}%"
    return f"{int(v):,}{'명' if ind['unit'] == '계좌' else ind['unit']}"


def _arrow(ind: dict) -> str:
    """증감 표기 «(▲ 1.3억)». 판정 기준 미만의 변화는 붙이지 않는다(«변화 없음»)."""
    if ind["direction"] == "변화 없음":
        return ""
    d = ind["delta"]
    mark = "▲" if d > 0 else "▼"
    if ind["unit"] == "원":
        size = _eok(d)
    elif ind["unit"] == "%":
        size = f"{abs(d):.2f}%p" if ind["id"] == "branch_return_rate" else f"{abs(d):.1f}%p"
    else:
        size = f"{abs(int(d)):,}{ind['unit']}"
    return f" ({mark} {size})"


def briefing_frame(st: dict, focus: int | None) -> dict:
    """부점 브리핑 답변의 틀(코드가 만든다 — LLM 은 가운데 문장만 쓴다). 화면 기획안의 브리핑 카드 · 지표 목록 ·
    고객 카드를 마크다운 인용 상자 + 표 하나로 옮긴 것(2026-10-23 사용자 결정 «A안 · 열 이름 부점 현황 | 값»).

    반환: head(인용 상자 첫 줄) · caption(인용 상자 끝 줄) · table · bold(본문에서 굵게 할 «N명»)."""
    ind = {i["id"]: i for i in st["indicators"]}
    day = clock.today()
    rows = [(ind[k]["name"], _short(ind[k]) + _arrow(ind[k]))
            for k in ("total_eval_amt", "branch_return_rate", "idle_cash_total", "safe_asset_ratio")]
    rows.append((ind["transfer_out_count"]["name"],
                 f"{_short(ind['transfer_out_count'])} · {_short(ind['transfer_out_amt'])}"))
    rows += [
        ("우리부점 관리 고객 (개인형 IRP)", f"**{_short(ind['customer_count'])}**"),
        (f"{ind['low_return_count']['name']} (수익률 {st.get('low_return_pct', 1):g}% 미만)",
         f"**{_short(ind['low_return_count'])}**"),
        (ind["high_balance_count"]["name"],
         f"**{_short(ind['high_balance_count'])}** (전체 적립금의 {_short(ind['high_balance_share'])})"),
    ]
    table = "\n".join(["| 부점 현황 | 값 |", "|---|---:|", *(f"| {a} | {b} |" for a, b in rows)])
    return {"head": f"**오늘의 부점 브리핑** · {day.month}월 {day.day}일 {_WEEKDAYS[day.weekday()]}요일",
            "caption": f"_전월말·오늘 고객상태 및 관리신호 분석 · {day.month}.{day.day} 기준_",
            "table": table, "bold": f"{focus}명" if focus else ""}


def framed(answer: str, frame: dict) -> str:
    """LLM 이 쓴 브리핑 문장을 틀에 넣는다 — 인용 상자(제목 · 문장 · 분석 기준) 아래 표. 찾은 고객 수는 굵게."""
    body = answer.strip()
    if frame.get("bold"):
        body = re.sub(rf"(고객\s*)?(?<![\d,]){re.escape(frame['bold'])}",
                      lambda m: f"**{m.group(0).strip()}**", body, count=1)
    lines = [frame["head"], "", *body.splitlines(), "", frame["caption"]]
    box = "\n".join(f"> {ln}" if ln.strip() else ">" for ln in lines)
    return f"{box}\n\n{frame['table']}"


def _focus_count(emp_no: str | None, st: dict) -> int | None:
    """오늘의 중점관리 고객 수(관리 대상 추천과 같은 명부 — roster.focus_names). 오늘 CASE 가 없으면 None."""
    from pension_agent.tools.branch_briefing import roster  # noqa: PLC0415
    if not st["cases"]:
        return None
    try:
        return len(roster.focus_names(emp_no, [c["id"] for c in st["cases"]]))
    except (LookupError, run.DBUnavailable, status.RuleError):
        return None


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
    focus = _focus_count(emp_no, st)
    if focus is not None:
        text += (f"\n· 오늘의 중점관리 고객: {focus}명 — 관리 대상 중 오늘의 관리 방향의 «우선 관리할 고객» 조건을 "
                 "모두 갖춘 고객")
    question = ((state or {}).get("question") or "").strip()
    frame = briefing_frame(st, focus) if any(w in question for w in BRIEFING_WORDS) else None
    if frame:
        text += ("\n· 화면 표: 이 답변 아래에 부점 지표 표(잔액·수익률·고유계정대·원리금보장 비중·계약이전·고객 수)가 "
                 "함께 나갑니다")
    uses_draft = any(c["grade"] == "D" for c in st["cases"]) or any(
        s["grade"] == "D" for s in st["segments"])
    return _ev("branch_status", query, text,
               [{"id": "branch_status",
                 "title": f"부점 현황 ({st['branch']} · {st['base_date']})",
                 "doc": "부점 DB 고정 뷰(오늘 · 전월말) · 기준은 타겟 룰베이스 부점 시트",
                 "score": None, "page": None}],
               marks=[DRAFT_MARK] if uses_draft else None,
               meta={"status": st, "focus": focus, "frame": frame})
