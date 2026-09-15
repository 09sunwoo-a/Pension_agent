"""05 계열 문서 공통 파싱 — 인덱스(그룹·도출·판단) + 항목 블록 + 필드 분해.

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

import re

from scripts.kb_build.common import clean


# ─────────────────────────────────────────────────────────────
# 3) 05 문서 공통 파싱 — 인덱스(그룹·도출·판단) + 항목 블록
# ─────────────────────────────────────────────────────────────

_INDEX_HEADING = re.compile(r"^###\s+(.+?)\s*$")
_INDEX_BOLD_ROW = re.compile(r"^\|\s*\|\s*\*\*\[([^\]]+)\]")
_INDEX_ROW = re.compile(r"^\|\s*(F?\d+(?:-\d+)?)\s*\|(.*)$")
_ITEM = re.compile(r"^(#{2,4})\s+(F?\d+(?:-\d+)?)\.\s+(.+?)\s*$")


def _group_name(raw: str) -> str:
    """인덱스 그룹 제목에서 장식을 뗀다 — 건수 표기와 '인덱스 —' 접두."""
    name = re.sub(r"\s*\([^)]*\d+건[^)]*\)\s*$", "", raw.strip())
    return re.sub(r"^인덱스\s*[—-]\s*", "", name).strip()


def parse_index(lines: list[str], stop: str, *, group_style: str = "heading") -> dict[str, dict]:
    """인덱스 표에서 항목번호 → {group, derivation, decision} 를 읽는다.

    그룹을 본문 헤더 상태기계로 잡지 않는 이유: 06/01·03 은 1부와 2부가 같은 그룹 제목을 반복해서
    쓰기 때문에 제목만으로는 어느 부의 것인지 구분되지 않는다. 인덱스는 항목번호로 그룹을 못박아 준다.

    그룹 표기 형태가 문서마다 다르다 — 01·03·04 는 `###` 소제목, 05 는 표 안의 굵은 행이다.
    """
    out: dict[str, dict] = {}
    group = None
    for line in lines:
        if line.startswith(stop):
            break
        if group_style == "heading":
            m = _INDEX_HEADING.match(line)
            if m:
                group = _group_name(m.group(1))
                continue
        elif group_style == "bold_row":
            m = _INDEX_BOLD_ROW.match(line)
            if m:
                group = _group_name(m.group(1))
                continue
        m = _INDEX_ROW.match(line)
        if m and group:
            cells = [c.strip() for c in m.group(2).split("|")]
            derivation = next((c for c in cells if c in ("명시", "통합", "추론")), None)
            decision = next((c for c in cells if c.startswith(("✅ 채택", "❌", "🔶"))), None)
            out[m.group(1)] = {"group": group, "derivation": derivation, "decision": decision,
                               "marks": [c for c in cells if c]}
    return out


def parse_items(lines: list[str], start_at: int) -> list[dict]:
    """`## N. 제목` / `### N-M. 제목` 형태의 항목 블록을 잘라낸다.

    본문 헤더 레벨이 부마다 다르다(06/01 은 1부 `## N.`+`### N-M.`, 2부 `### N.`+`#### N-M.`)
    이라서 레벨이 아니라 '번호 + 마침표' 패턴으로 항목을 인식한다. `### 1차(01_사내가이드) 기준`
    같은 비항목 제목은 번호 뒤가 마침표가 아니라 자연히 걸러진다.
    """
    items: list[dict] = []
    current: dict | None = None
    for line in lines[start_at:]:
        m = _ITEM.match(line)
        if m:
            current = {"no": m.group(2), "title": clean(m.group(3)), "body": []}
            items.append(current)
            continue
        if line.startswith("# ") or (line.startswith("## ") and current):
            # 새 부(1부/2부) 또는 그룹 헤더 — 직전 항목을 닫는다(그룹 헤더 아래 산문은 항목이 아님).
            current = None
        if current is not None:
            current["body"].append(line)
    return items


_FIELD_HEAD = re.compile(
    r"^\*\*(조건|이유|원문\s*\d*|검토\s*메모|공통\s*검토\s*메모|방식|재사용\s*포인트|정리"
    r"|상황|액션|주의|현장의\s*목소리[^*]*|실제\s*현장의\s*목소리|→\s*에이전트\s*시사점)\*\*")
_FIELD_INLINE = re.compile(r"^-\s*\*\*(조건|이유|검토\s*메모|정리|상황|액션|주의|연결)\*\*\s*[—:-]\s*(.+)$")
_DERIVATION = re.compile(r"도출:\s*(명시|통합|추론)")

# 06/03·06/05 는 출처를 인용 블록 밖에 따로 적는다:
#     — **원천**: 행내 PDF 『…』 (부서, 날짜)
#        **변환본**: `경로.md` § 위치 — 추출 경로
# 06/01 은 인용 블록 안에 `> — 『문서』 부서, 날짜` 로 적는다. 두 형태를 모두 받는다.
_ATTRIB = re.compile(r"^\s*(?:—\s*)?\*\*(?:원천|변환본|출처)\*\*")


def _field_key(raw: str) -> str:
    k = re.sub(r"\s+", "", raw)
    if k.startswith("원문") or k == "실제현장의목소리":
        return "원문"          # 07 은 '실제 현장의 목소리' 아래에 인용이 온다
    if k in ("검토메모", "공통검토메모"):
        return "검토메모"
    if k in ("방식",):
        return "조건"          # [참고] 항목은 '조건' 자리에 '방식'을 쓴다
    if k in ("재사용포인트",):
        return "이유"          # 같은 이유로 '이유' 자리에 '재사용 포인트'를 쓴다
    if k.startswith("현장의목소리"):
        return "정리"          # 07 의 요약 문단
    if k.startswith("→에이전트시사점"):
        return "시사점"
    return k


def split_fields(body: list[str]) -> tuple[dict[str, list[str]], list[dict]]:
    """항목 블록을 필드별 텍스트와 인용 블록으로 나눈다.

    반환 (fields, quotes). quotes 는 [{text, source_text}] — 출처 해석은 호출자가 한다.
    """
    fields: dict[str, list[str]] = {}
    quotes: list[dict] = []
    section = "머리말"
    qbuf: list[str] = []
    qsrc: list[str] = []

    def flush_quote() -> None:
        if qbuf:
            quotes.append({"text": "\n".join(qbuf).strip(),
                           "source_text": " ".join(qsrc).strip()})
        qbuf.clear()
        qsrc.clear()

    for raw in body:
        line = raw.rstrip()
        if line.startswith(">"):
            inner = line[1:].strip()
            # 출처 줄만 em-dash 로 시작한다. `> - …` 은 인용문 안의 불릿이라 본문으로 둔다.
            if inner.startswith("—"):
                qsrc.append(inner.lstrip("— ").strip())
            elif inner:
                if qsrc:                       # 출처 뒤에 새 인용이 시작되면 앞 블록을 닫는다
                    flush_quote()
                qbuf.append(inner)
            continue
        if not line.strip():
            continue
        if _ATTRIB.match(line):                # 인용 블록 밖의 출처 줄(06/03 형태)
            qsrc.append(line.strip().lstrip("— ").strip())
            continue
        flush_quote()

        m = _FIELD_INLINE.match(line)
        if m:
            fields.setdefault(_field_key(m.group(1)), []).append(clean(m.group(2)))
            continue
        m = _FIELD_HEAD.match(line)
        if m:
            section = _field_key(m.group(1))
            rest = line[m.end():].strip(" —:-")
            if rest and not rest.startswith("`"):
                fields.setdefault(section, []).append(clean(rest))
            continue
        if line.startswith("---"):
            section = "머리말"
            continue
        fields.setdefault(section, []).append(clean(line.lstrip("- ")))

    flush_quote()

    # "— **원천**(원문 1~7 공통):" 처럼 출처 한 줄이 앞선 인용 여러 개를 한꺼번에 가리키는 경우가 있다.
    # 그 표기가 있으면 출처가 비어 있던 앞 인용들에 같은 출처를 소급해 붙인다(원문의 뜻 그대로).
    for i, q in enumerate(quotes):
        if "공통" not in q["source_text"]:
            continue
        for prev in quotes[:i]:
            if not prev["source_text"]:
                prev["source_text"] = q["source_text"]

    return fields, quotes


# 필드 헤더에 딸린 안내 문구("— 누구를 골라내나")는 내용이 아니라 읽는 법 설명이라 값에서 뺀다.
_HEADER_HINT = re.compile(r"^(누구를|왜\s|어떤\s|이 조건이|왜$)")


def joined(fields: dict[str, list[str]], key: str) -> str:
    vals = [v for v in fields.get(key, [])
            if v and not (len(v) <= 24 and _HEADER_HINT.match(v))]
    return " ".join(vals).strip()
