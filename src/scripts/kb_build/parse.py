"""추출지식 문서 공통 파싱 — 인덱스(그룹·도출·판단) + 항목 블록 + 필드 분해."""

from __future__ import annotations

import re

from scripts.kb_build.common import clean


_INDEX_HEADING = re.compile(r"^###\s+(.+?)\s*$")
_INDEX_BOLD_ROW = re.compile(r"^\|\s*\|\s*\*\*\[([^\]]+)\]")
_INDEX_ROW = re.compile(r"^\|\s*(F?\d+(?:-\d+)?)\s*\|(.*)$")
_ITEM = re.compile(r"^(#{2,4})\s+(F?\d+(?:-\d+)?)\.\s+(.+?)\s*$")


def _group_name(raw: str) -> str:
    """인덱스 그룹 제목의 건수 표기·'인덱스 —' 접두 제거."""
    name = re.sub(r"\s*\([^)]*\d+건[^)]*\)\s*$", "", raw.strip())
    return re.sub(r"^인덱스\s*[—-]\s*", "", name).strip()


def parse_index(lines: list[str], stop: str, *, group_style: str = "heading") -> dict[str, dict]:
    """인덱스 표 → 항목번호별 {group, derivation, decision}. 그룹 표기는 `###`(heading) 또는 표 굵은 행(bold_row).

    본문 헤더가 아닌 인덱스 사용 — 1부·2부가 같은 그룹 제목 반복.
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
    """`## N. 제목` / `### N-M. 제목` 항목 블록 분할. 헤더 레벨이 부마다 달라 «번호 + 마침표»로 인식."""
    items: list[dict] = []
    current: dict | None = None
    for line in lines[start_at:]:
        m = _ITEM.match(line)
        if m:
            current = {"no": m.group(2), "title": clean(m.group(3)), "body": []}
            items.append(current)
            continue
        if line.startswith("# ") or (line.startswith("## ") and current):
            # 새 부·그룹 헤더 — 직전 항목 종료
            current = None
        if current is not None:
            current["body"].append(line)
    return items


_FIELD_HEAD = re.compile(
    r"^\*\*(조건|이유|원문\s*\d*|검토\s*메모|공통\s*검토\s*메모|방식|재사용\s*포인트|정리"
    r"|상황|액션|주의|현장의\s*목소리[^*]*|실제\s*현장의\s*목소리|→\s*에이전트\s*시사점)\*\*")
_FIELD_INLINE = re.compile(r"^-\s*\*\*(조건|이유|검토\s*메모|정리|상황|액션|주의|연결)\*\*\s*[—:-]\s*(.+)$")
_DERIVATION = re.compile(r"도출:\s*(명시|통합|추론)")

# 인용 블록 밖 출처 줄(`— **원천**: …` · `**변환본**: …`). 블록 안 `> — 『문서』` 형태는 split_fields 가 처리
_ATTRIB = re.compile(r"^\s*(?:—\s*)?\*\*(?:원천|변환본|출처)\*\*")


def _field_key(raw: str) -> str:
    k = re.sub(r"\s+", "", raw)
    if k.startswith("원문") or k == "실제현장의목소리":
        return "원문"          # 인사이트: '실제 현장의 목소리' 아래 인용
    if k in ("검토메모", "공통검토메모"):
        return "검토메모"
    if k in ("방식",):
        return "조건"          # [참고] 항목의 '방식'
    if k in ("재사용포인트",):
        return "이유"          # [참고] 항목의 '재사용 포인트'
    if k.startswith("현장의목소리"):
        return "정리"          # 인사이트 요약 문단
    if k.startswith("→에이전트시사점"):
        return "시사점"
    return k


def split_fields(body: list[str]) -> tuple[dict[str, list[str]], list[dict]]:
    """항목 블록 → (fields, quotes). quotes 는 [{text, source_text}], 출처 해석은 호출자."""
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
            # 출처 줄만 em-dash 시작(`> - …` 는 인용 안 불릿)
            if inner.startswith("—"):
                qsrc.append(inner.lstrip("— ").strip())
            elif inner:
                if qsrc:                       # 출처 뒤 새 인용 → 앞 블록 종료
                    flush_quote()
                qbuf.append(inner)
            continue
        if not line.strip():
            continue
        if _ATTRIB.match(line):                # 인용 블록 밖 출처 줄
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

    # "— **원천**(원문 1~7 공통):" — 출처 빈 앞 인용들에 소급 적용
    for i, q in enumerate(quotes):
        if "공통" not in q["source_text"]:
            continue
        for prev in quotes[:i]:
            if not prev["source_text"]:
                prev["source_text"] = q["source_text"]

    return fields, quotes


# 필드 헤더 안내 문구("— 누구를 골라내나") — 값에서 제외
_HEADER_HINT = re.compile(r"^(누구를|왜\s|어떤\s|이 조건이|왜$)")


def joined(fields: dict[str, list[str]], key: str) -> str:
    """절의 줄들을 공백으로 연결. 마크다운 표 줄은 줄바꿈 유지(한 줄로 뭉개짐 방지)."""
    vals = [v for v in fields.get(key, [])
            if v and not (len(v) <= 24 and _HEADER_HINT.match(v))]
    if not any(v.lstrip().startswith("|") for v in vals):
        return " ".join(vals).strip()
    out: list[str] = []
    for v in vals:
        if v.lstrip().startswith("|") or (out and out[-1].lstrip().startswith("|")):
            out.append("\n" + v)          # 표 줄과 바로 뒤 줄은 새 줄
        else:
            out.append((" " if out else "") + v)
    return "".join(out).strip()
