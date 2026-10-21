"""LLM 이 쓴 SQL 검사 — 실행 전에 코드가 막는다.

통과 조건은 넷이다.
  ① SELECT 문 하나 — 세미콜론으로 이은 여러 문장 · 주석 · WITH(고정 뷰 이름을 덮어쓸 수 있다)를 막는다
  ② 쓰기·실행 낱말이 없다 — INSERT·UPDATE·DELETE·DROP·EXEC·SELECT INTO 등
  ③ FROM·JOIN 이 가리키는 것은 고정 뷰뿐이다 — 원본 테이블·시스템 테이블·다른 스키마를 못 본다
  ④ 시스템 프로시저·변수(sp_·xp_·@@)를 부르지 않는다

이것은 문자열 검사라 SQL 문법 전체를 이해하지 못한다 — 판정이 애매하면 막는 쪽으로 기운다.
행외 목업 DB 는 연결 자체가 읽기 전용이고(demo_db), 행내는 DB 계정 권한이 마지막 문이다.
"""

from __future__ import annotations

import re

from pension_agent.tools.branch_briefing.views import VIEWS


class SQLRejected(ValueError):
    """검사에 걸린 SQL. 메시지는 LLM 에게 돌려줘 다시 쓰게 하는 데 쓴다."""


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|"
    r"CALL|INTO|DECLARE|SET|WITH|LOAD|UNLOAD|BACKUP|RESTORE|DUMP|SHUTDOWN|COMMIT|ROLLBACK)\b",
    re.IGNORECASE)
_SYSTEM = re.compile(r"\b(sp_|xp_|sa_|sys\w*|dbo)\w*|@@|\bmaster\b", re.IGNORECASE)
_KEYWORDS = {"WHERE", "GROUP", "ORDER", "HAVING", "ON", "JOIN", "INNER", "LEFT", "RIGHT",
             "FULL", "OUTER", "CROSS", "UNION", "LIMIT", "AS", "AND", "OR"}
_TOKEN = re.compile(r"[A-Za-z_][\w.]*|\(|\)|,|\S")


def _mask_strings(sql: str) -> str:
    """문자열 리터럴 안을 지운다 — 값 안의 낱말(«'DELETE'»)로 오판하지 않게."""
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _tables(sql: str) -> list[str]:
    """FROM·JOIN 뒤에 오는 테이블 이름들. 괄호(하위 질의)는 건너뛴다."""
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
    """통과하면 정리한 SQL 을, 아니면 SQLRejected 를 낸다."""
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
