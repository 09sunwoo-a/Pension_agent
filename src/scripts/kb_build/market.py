"""시황·상품 폴더 → doc + market + lineup 카드, 표 → 관계 선언."""

from __future__ import annotations

import re

from scripts.kb_build import config

from scripts.kb_build.common import REPO, clean, first_clause, note, record, redact, topics_of
from scripts.kb_build.docs import doc_title


# 문서 자체가 원문 — 항목 인덱스 없음, `##` 절이 검색 단위. 문서마다 개요 카드 1 + 절 카드 N
# 절 본문(content)은 원문 그대로. 시효: README ※ 경고 → volatile, front-matter as_of → 기준시점

#: 절 경계 — H1·H2 만(H3 는 본문 소제목).
_MARKET_HEAD = re.compile(r"^(#{1,2})\s+(.+?)\s*$")

#: 카드 제외 절 — 목차 · 저작 검증 메모.
_MARKET_SKIP = ("Contents", "추출 노트")

#: id 금지 문자 정리("2026.08" → "2026-08").
_MARKET_SLUG = re.compile(r"[^0-9A-Za-z가-힣_-]")


def _market_front_matter(text: str) -> tuple[dict, str]:
    """front-matter → (dict, 본문). 인라인 목록([a, b])·블록 목록(- 항목) 지원."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    body = text[end + 4:].lstrip("\n")
    data: dict = {}
    list_key: str | None = None
    for line in text[3:end].splitlines():
        if list_key is not None and re.match(r"^\s+-\s+", line):
            data[list_key].append(re.sub(r"^\s+-\s+", "", line).strip())
            continue
        key, sep, val = line.partition(":")
        if not sep or not key.strip() or line.startswith((" ", "\t")):
            continue
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            data[key] = [v.strip() for v in val[1:-1].split(",") if v.strip()]
            list_key = None
        elif not val:
            data[key] = []
            list_key = key
        else:
            data[key] = val
            list_key = None
    return data, body


def _market_emphasis(text: str) -> str:
    """마크다운 강조·코드표기 제거, 밑줄 유지(`clean()` 은 `as_of` → `asof` 로 깨뜨림)."""
    # 이스케이프 먼저 해제 — 각주 `\\*\\*\\*수협은행…` 의 백슬래시 잔존 방지
    out = re.sub(r"\\([*_`\[\]<>#|])", r"\1", text)
    out = re.sub(r"[*`]+", "", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _readme_note(marker: str) -> str | None:
    """폴더 README 의 `marker` 줄 + 이어지는 들여쓴 줄. 없으면 None(문구를 코드에 두지 않음)."""
    readme = config.MARKET_DIR / "README.md"
    if not readme.exists():
        return None
    lines = readme.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith(marker):
            buf = [ln.strip().lstrip(marker).strip()]
            for nxt in lines[i + 1:]:
                if nxt.strip() and re.match(r"^\s+\S", nxt):
                    buf.append(nxt.strip())
                else:
                    break
            return _market_emphasis(" ".join(buf)) or None
    return None


def _market_warn() -> str | None:
    """README ※ 시효 경고."""
    return _readme_note("※")


def _market_advisory() -> str | None:
    """README ⚖ 인용 고지 — 시황·상품 카드 전부에 부착(운영 판단, 원문 문서에 미삽입)."""
    return _readme_note("⚖")


def _market_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """본문 → (머리말, [(절 제목, 절 본문)]). 첫 H1 은 문서 제목, 이후 H1/H2 는 전부 절 경계."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    seen_title = False
    current: list[str] | None = None
    for raw in body.splitlines():
        m = _MARKET_HEAD.match(raw)
        if m:
            if not seen_title and m.group(1) == "#":
                seen_title = True
                continue
            current = []
            sections.append((clean(m.group(2)), current))
            continue
        if current is not None:
            current.append(raw.rstrip())
        elif seen_title:
            preamble.append(raw.rstrip())
    return ("\n".join(preamble).strip(),
            [(t, "\n".join(b).strip()) for t, b in sections])


#: 검색 키워드 최소 길이(정규화 후) — 한 글자(`금`)는 부분일치로 거의 모든 절에 걸림.
_MIN_KEYWORD = 2


def _market_keywords(keywords: list[str]) -> list[str]:
    return [kw for kw in keywords
            if len(re.sub(r"[^0-9A-Za-z가-힣]", "", kw)) >= _MIN_KEYWORD]


# 표 → 관계 선언(값↔성립 조건). 행 단위로 펴서 ① 검색 입구(열 머리말·행 이름) ② 값–행 오짝 대조 재료
# 원문 칸만 옮김, content 원문 유지

#: 표 한 줄 `| a | b |`.
_TABLE_LINE = re.compile(r"^\s*\|(.+)\|\s*$")
#: 머리말 구분선 `|---|:--:|`.
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

#: 행 이름 열 판정 — 이름 칸 최소 비율.
_KEY_COL_RATIO = 0.6

#: 값 칸 최대 길이 — 초과는 산문 칸(그 안의 수치를 다른 행 값으로 세면 맞는 답 차단).
_VALUE_CELL_MAX = 24


def _table_cells(line: str) -> list[str]:
    return [_market_emphasis(c) for c in _TABLE_LINE.match(line).group(1).split("|")]


#: 값으로 보는 단위(금액·비율만). 날짜 단위 금지 — 「20일」·「1975년」은 행 이름.
_UNIT = r"(?:억원|만원|조원|천원|억|조|만|천|원|%|pt|bp|배)"


def _is_name(cell: str) -> bool:
    """이름 칸 여부 — 글자 포함, 순수 수치·날짜·«수치+단위»(「+65,469억원」) 아님."""
    if not cell:
        return False
    if not re.search(r"[가-힣A-Za-z]", cell):
        return False
    if re.fullmatch(r"[\d.,\-~%\s]+", cell):
        return False
    return not re.fullmatch(rf"[+\-]?[\d.,]+\s*{_UNIT}?", cell)


def _key_columns(rows: list[list[str]]) -> int:
    """왼쪽부터 연속된 행 이름 열 개수(표마다 1~3개)."""
    n = max((len(r) for r in rows), default=0)
    for col in range(n):
        vals = [r[col] for r in rows if col < len(r) and r[col].strip()]
        if not vals:
            return col
        if sum(_is_name(v) for v in vals) / len(vals) < _KEY_COL_RATIO:
            return col
    return n


def _carry_keys(body: list[list[str]], ncol: int) -> list[list[str]]:
    """이름 열 ncol 개를 행마다 채움 — 빈 칸(병합 셀)은 위 행에서 상속."""
    out: list[list[str]] = []
    carry: list[str] = [""] * ncol
    for cells in body:
        row_keys: list[str] = []
        for col in range(ncol):
            val = cells[col].strip() if col < len(cells) else ""
            if val:
                carry[col] = val
            elif carry[col]:
                val = carry[col]
            row_keys.append(val)
        out.append(row_keys)
    return out


def _identifying(filled: list[list[str]], ncol: int) -> set[tuple[int, str]]:
    """행을 특정하는 이름 칸만 — 윗 열 조합이 하나뿐인 이름(합계 행 「포트폴리오」 같은 공통 라벨 제외)."""
    ident: set[tuple[int, str]] = set()
    for col in range(ncol):
        parents: dict[str, set[tuple[str, ...]]] = {}
        for row_keys in filled:
            val = row_keys[col]
            if val:
                parents.setdefault(val, set()).add(tuple(row_keys[:col]))
        ident |= {(col, val) for val, ups in parents.items() if len(ups) == 1}
    return ident


def _markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """본문 마크다운 표 → `(열 머리말, 행들)` 원표기. `_market_tables`·`_market_product_names` 공용."""
    out: list[tuple[list[str], list[list[str]]]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not _TABLE_LINE.match(lines[i]) or _TABLE_SEP.match(lines[i]):
            i += 1
            continue
        if i + 1 >= len(lines) or not _TABLE_SEP.match(lines[i + 1]):
            i += 1
            continue
        columns = [c.strip() for c in _table_cells(lines[i])]
        body: list[list[str]] = []
        j = i + 2
        while j < len(lines) and _TABLE_LINE.match(lines[j]) and not _TABLE_SEP.match(lines[j]):
            body.append(_table_cells(lines[j]))
            j += 1
        i = j
        out.append((columns, body))
    return out


#: 상품 등록부로 인정하는 열 머리말 — 산문에서 긁으면 지어낸 이름이 자기 근거가 됨.
_PRODUCT_COLUMNS = ("상품명", "편입상품", "디폴트옵션 상품")


def _market_product_names(text: str) -> list[str]:
    """표의 상품명 칸 이름들. 판정은 관계 선언과 같은 `_is_name`·`_identifying`(합계 라벨 제외)."""
    out: list[str] = []
    for columns, body in _markdown_tables(text):
        cols = [i for i, c in enumerate(columns) if c in _PRODUCT_COLUMNS]
        if not cols:
            continue
        ncol = _key_columns(body)
        filled = _carry_keys(body, ncol)
        ident = _identifying(filled, ncol)
        for r, cells in enumerate(body):
            for i in cols:
                # 이름 열은 행 특정 여부 확인, 값 열은 칸 그대로
                val = (filled[r][i] if i < ncol
                       else (cells[i].strip() if i < len(cells) else ""))
                if i < ncol and (i, val) not in ident:
                    continue
                if val and _is_name(val) and val not in out:
                    out.append(val)
    return out


#: 백분율 열 셀 값 — 강조·`%` 제거 순수 수치(「**100**」·「3.40」·「30%」).
_PCT_CELL = re.compile(r"^\**\s*(-?\d+(?:\.\d+)?)\s*%?\s*\**$")


def _percent_values(columns: list[str], cells: list[str]) -> list[str]:
    """행의 백분율 열(config.PERCENT_COLUMNS) 값. 열 인덱스로 읽음 — `values` 는 빈 칸 제거로 열과 어긋남."""
    out: list[str] = []
    for i, col in enumerate(columns):
        if col.strip() not in config.PERCENT_COLUMNS or i >= len(cells):
            continue
        m = _PCT_CELL.match(cells[i].strip())
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _header_row_name(cell: str) -> str:
    """둘째 머리말 행 이름 대조용 표기(「**T D F**」 → 「TDF」)."""
    return re.sub(r"[*\s]", "", cell or "")


def _fold_header_rows(columns: list[str], body: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """`config.TABLE_HEADER_ROWS` 행을 열 머리말에 병합(「1975년 (TDF 2035)」)하고 본문에서 제거."""
    kept: list[list[str]] = []
    cols = list(columns)
    for cells in body:
        head = _header_row_name(cells[0]) if cells else ""
        if head and head in config.TABLE_HEADER_ROWS:
            for i, cell in enumerate(cells):
                if i and i < len(cols) and cell.strip():
                    cols[i] = f"{cols[i]} ({head} {cell.strip()})"
            continue
        kept.append(cells)
    return cols, kept


def _market_tables(text: str) -> list[dict]:
    """마크다운 표 → `{"columns", "units", "rows":[{"keys", "cells", "values", "percents"}]}`.

    빈 이름 칸은 위 행에서 상속(병합 셀). `units`·`percents` 는 config.PERCENT_COLUMNS 선언 열.
    """
    out: list[dict] = []
    for columns, body in _markdown_tables(text):
        columns, body = _fold_header_rows(columns, body)
        ncol = _key_columns(body)
        # 이름 열·값 열 둘 다 있는 표만 선언(달력·일정표 제외)
        if ncol == 0 or ncol >= max((len(r) for r in body), default=0):
            continue

        filled = _carry_keys(body, ncol)
        ident = _identifying(filled, ncol)
        units = [c for c in columns if c.strip() in config.PERCENT_COLUMNS]
        rows: list[dict] = []
        for cells, row_keys in zip(body, filled):
            keys: list[str] = []
            for col, val in enumerate(row_keys):
                # 한 글자 이름·행을 특정 못 하는 이름 제외
                if (val and val not in keys and (col, val) in ident
                        and len(re.sub(r"[^0-9A-Za-z가-힣]", "", val)) >= _MIN_KEYWORD):
                    keys.append(val)
            rest = [c.strip() for c in cells[ncol:] if c.strip()]
            if not keys or not rest:
                continue
            row = {"keys": keys, "cells": rest,
                   "values": [c for c in rest if len(c) <= _VALUE_CELL_MAX]}
            pct = _percent_values(columns, cells)
            if pct:
                row["percents"] = pct
            rows.append(row)
        if rows:
            table = {"columns": columns, "rows": rows}
            if units:
                table["units"] = {c: "%" for c in units}
            out.append(table)
    return out


def _table_triggers(tables: list[dict], limit: int = 30) -> list[str]:
    """표의 검색 입구 — 열 머리말(「1975년」) + 행 이름(「알파드림」)."""
    out: list[str] = []
    for table in tables:
        for text in list(table.get("columns") or []) + [
                k for row in table.get("rows") or [] for k in row.get("keys") or []]:
            text = text.strip()
            if len(re.sub(r"[^0-9A-Za-z가-힣]", "", text)) < _MIN_KEYWORD:
                continue
            if len(text) > 40 or text in out:
                continue
            out.append(text)
    return out[:limit]


def _market_triggers(title: str, text: str, keywords: list[str], limit: int = 8) -> list[str]:
    """절 카드 검색 예시 — 절 본문에 나오는 문서 키워드만(정규화 후 부분일치), 없으면 본문 첫 절."""
    flat = re.sub(r"[^0-9A-Za-z가-힣]", "", f"{title} {text}").lower()
    hits = [kw for kw in _market_keywords(keywords)
            if re.sub(r"[^0-9A-Za-z가-힣]", "", kw).lower() in flat]
    # 절 제목 미포함(triggers_of 와 동일)
    return hits[:limit] or [c for c in (first_clause(text),) if c]


#: front-matter category → 카드 종류. 시황·상품 분리(묶으면 추천펀드 질문에 환율 전망 혼입).
MARKET_KINDS: dict[str, str] = {"시황": "market", "상품": "lineup"}


def build_market() -> tuple[list[dict], dict[str, list[dict]]]:
    """시황·상품 폴더 → (doc 레코드, 종류별 카드). 회차별 누적 폴더라 하위 전체 탐색(파일 목록 하드코딩 금지)."""
    warn = _market_warn()
    if not warn:
        note("[05 경고없음] README 의 ※ 시효 안내를 찾지 못함 — market 카드에 시효 표시가 빠진다")
    advisory = _market_advisory()
    if not advisory:
        note("[05 고지없음] README 의 ⚖ 인용 고지를 찾지 못함 — 답변에 정보제공 고지가 빠진다")

    docs: list[dict] = []
    cards: dict[str, list[dict]] = {k: [] for k in MARKET_KINDS.values()}
    for path in sorted(config.MARKET_DIR.rglob("*.md")):
        if path.name == "README.md" or path.name.startswith("_"):
            continue
        fm, body = _market_front_matter(path.read_text(encoding="utf-8"))
        title = fm.get("title") or doc_title(path) or path.stem.replace("_", " ")
        category = fm.get("category")
        as_of = fm.get("as_of")
        if category not in MARKET_KINDS:
            note(f"[05 분류없음] {path.name} — front-matter category 가 시황/상품이 아님: {category!r}, 건너뜀")
            continue
        kind = MARKET_KINDS[category]
        out = cards[kind]
        if not as_of:
            note(f"[05 기준시점없음] {path.name} — front-matter as_of 없음, 건너뜀"
                 " (기준일 없는 시황·상품 수치는 인용 불가 — 폴더 README 수록 규칙)")
            continue

        seed = config.MARKET_DOCS.get(path.stem)
        if seed is None:
            note(f"[05 시드없음] {path.name} — config.MARKET_DOCS 에 부서·발행시점 시드 없음"
                 " (문서는 적재하되 출처 표기가 제목만 남는다)")
            seed = {}

        rel = str(path.relative_to(REPO)).replace("\\", "/")
        slug = _MARKET_SLUG.sub("-", re.sub(r"^\d+_", "", path.stem))
        doc_id = f"doc.k05.{slug}"
        confidentiality = fm.get("confidentiality") or ""
        # confidentiality: "고객용" → True, "행내" → False, 없음 → None(추론 금지)
        customer_facing = (True if confidentiality.startswith("고객용")
                           else False if "행내" in confidentiality else None)
        docs.append(record(doc_id, "doc", {
            "title": redact(title),
            "short": seed.get("short"),
            "dept": seed.get("dept"), "published": seed.get("published"),
            "origin": "시황상품", "tier": config.TIER_BY_ORIGIN["시황상품"],
            "customer_facing": customer_facing,
            "origin_file": fm.get("source_file") or path.name,
            "path": rel,
            "note": fm.get("origin") or None,
        }))

        preamble, sections = _market_sections(body)
        common = {
            "category": category, "group": title, "as_of": as_of,
            "volatile": warn, "advisory": advisory,
            "customer_facing": customer_facing,
        }
        prefix = "mkt" if kind == "market" else "lnp"
        overview_id = f"{prefix}.{slug}.00"
        ov_tables = _market_tables(preamble)
        out.append(record(overview_id, kind, {
            "title": title,
            "topic": clean(fm.get("topic") or "") or None,
            "key_points": fm.get("key_points") or None,
            "content": preamble or None,
            "parent": None,
            "tables": ov_tables or None,
            "product_names": _market_product_names(preamble) or None,
            # category 는 topics 제외 — 두 글자 흔한 말이라 전 카드 동점
            "tags": {"topics": topics_of(title, fm.get("topic") or "",
                                         " ".join(fm.get("key_points") or []))},
            # 제목 미포함 — 문서 키워드 + 표 이름
            "trigger_examples": _market_keywords(fm.get("trigger_keywords") or [])[:24]
                                + _table_triggers(ov_tables),
            **common,
        }, source={"doc": doc_id, "locator": f"{rel} § 개요"}))

        nn = 0
        for sec_title, sec_body in sections:
            if any(sec_title.startswith(skip) for skip in _MARKET_SKIP):
                continue
            if not sec_body:
                note(f"[05 빈절] {path.name} § {sec_title} — 본문 없음, 건너뜀")
                continue
            nn += 1
            sec_tables = _market_tables(sec_body)
            out.append(record(f"{prefix}.{slug}.{nn:02d}", kind, {
                "title": sec_title,
                "content": sec_body,
                "parent": overview_id,
                "tables": sec_tables or None,
                "product_names": _market_product_names(sec_body) or None,
                "tags": {"topics": topics_of(sec_title, sec_body)},
                "trigger_examples": _market_triggers(sec_title, sec_body,
                                                     fm.get("trigger_keywords") or [])
                                    + _table_triggers(sec_tables),
                **common,
            }, source={"doc": doc_id, "locator": f"{rel} § {sec_title}"}))
        if nn == 0:
            note(f"[05 절없음] {path.name} — 절 카드가 0장이다(개요 카드만 적재됨)")

    # 백분율 열 선언 없는 표 리포트 — 새 회차 컬럼 표기 변경 감지용(차단 아님)
    for card in (c for group in cards.values() for c in group):
        for table in card["fields"].get("tables") or []:
            if table.get("units"):
                continue
            note(f"[05 백분율열없음] {card['id']} — 열 {' · '.join(table['columns'])}"
                 f" 중 config.PERCENT_COLUMNS 에 걸린 것이 없다(값에 % 를 붙인 답변은 폐기된다)")
    return docs, cards
