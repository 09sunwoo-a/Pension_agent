"""LLM 이 쓴 SQL 의 실행 전 검사.

통과 조건: SELECT 한 문장(주석·WITH 금지 — WITH 는 고정 뷰를 덮어씀) · 쓰기 낱말 없음 ·
FROM/JOIN 은 고정 뷰만 · 시스템 객체(sp_·xp_·@@) 없음. 문자열 검사라 애매하면 거부.
"""

from __future__ import annotations

import re

from pension_agent.tools.branch_briefing.views import VIEWS


class SQLRejected(ValueError):
    """검사 거부. 메시지는 LLM 재작성 지시용."""


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|"
    r"CALL|INTO|DECLARE|SET|WITH|LOAD|UNLOAD|BACKUP|RESTORE|DUMP|SHUTDOWN|COMMIT|ROLLBACK)\b",
    re.IGNORECASE)
_SYSTEM = re.compile(r"\b(sp_|xp_|sa_|sys\w*|dbo)\w*|@@|\bmaster\b", re.IGNORECASE)
_KEYWORDS = {"WHERE", "GROUP", "ORDER", "HAVING", "ON", "JOIN", "INNER", "LEFT", "RIGHT",
             "FULL", "OUTER", "CROSS", "UNION", "LIMIT", "AS", "AND", "OR"}
_TOKEN = re.compile(r"[A-Za-z_][\w.]*|\(|\)|,|\S")


def _mask_strings(sql: str) -> str:
    """문자열 리터럴 내용 제거(값 안 낱말 오판 방지)."""
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _tables(sql: str) -> list[str]:
    """FROM·JOIN 뒤 테이블 이름들(하위 질의 괄호 제외)."""
    toks = _TOKEN.findall(sql)
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i].upper()
        if t in ("FROM", "JOIN"):
            i += 1
            while i < len(toks):
                if toks[i] == "(":
                    break                       # 하위 질의 — 그 안의 FROM 은 따로 걸린다
                out.append(toks[i])
                i += 1
                # 별칭(AS x · x) 건너뛰기
                if i < len(toks) and toks[i].upper() == "AS":
                    i += 2
                elif i < len(toks) and re.fullmatch(r"[A-Za-z_]\w*", toks[i]) \
                        and toks[i].upper() not in _KEYWORDS:
                    i += 1
                if t == "FROM" and i < len(toks) and toks[i] == ",":
                    i += 1                      # FROM a, b
                    continue
                break
        else:
            i += 1
    return out


def check(sql: str) -> str:
    """통과 시 정리한 SQL 반환, 아니면 SQLRejected."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise SQLRejected("SQL 이 비어 있습니다")
    masked = _mask_strings(text)
    if ";" in masked:
        raise SQLRejected("문장은 하나만 씁니다(세미콜론 금지)")
    if "--" in masked or "/*" in masked:
        raise SQLRejected("주석은 쓰지 않습니다")
    if not re.match(r"\s*SELECT\b", masked, re.IGNORECASE):
        raise SQLRejected("SELECT 문만 씁니다")
    bad = _FORBIDDEN.search(masked)
    if bad:
        raise SQLRejected(f"쓸 수 없는 낱말: {bad.group(1).upper()}")
    if _SYSTEM.search(masked):
        raise SQLRejected("시스템 테이블·프로시저·변수는 쓸 수 없습니다")
    tables = _tables(masked)
    if not tables:
        raise SQLRejected("FROM 에 뷰가 없습니다")
    outside = [t for t in tables if t.lower() not in VIEWS]
    if outside:
        raise SQLRejected(f"고정 뷰만 조회할 수 있습니다({', '.join(VIEWS)}) — 쓴 것: {', '.join(outside)}")
    return text
