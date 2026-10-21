"""06/05 업무처리절차 → screen(표A) · channel(표B) · procedure."""

from __future__ import annotations

import re

from scripts.kb_build import config
from pension_agent.actions import screens

from scripts.kb_build.common import EXTRACT, clean, note, record, redact, role_entries, topics_of, triggers_of
from scripts.kb_build.docs import DocResolver
from scripts.kb_build.facts import _SCREEN
from scripts.kb_build.parse import joined, parse_index, parse_items, split_fields


# 「표A. 단말 화면번호 일람」 → screen

#: 표A 행 `| [06-12-604] | 포트폴리오 운용현황 조회 | 연금 로보… | △ | 비고 |`.
_SCREEN_ROW = re.compile(
    r"^\|\s*(\[[0-9A-Za-z]{2}-[0-9A-Za-z]{2}-[0-9A-Za-z]{3}\])\s*\|(.+)$")

#: 표A·표B 원천 문서(06/05 는 업무 그룹별 재배열 정리본).
SCREEN_DOC = "doc.퇴직연금_주요거래_화면번호_안내"
CHANNEL_DOC = SCREEN_DOC

#: 신뢰도 표기 → 표 머리말 정의 문구.
_CONFIDENCE = {"○": "여러 자료에서 교차확인", "△": "단일 자료에만 등장"}

#: «주요 기능» 칸의 항목 참조("→ 관련 지식항목 1·3") — 검색 예시에서 제거.
_ITEM_REF = re.compile(r"\s*→\s*(?:관련 지식)?항목[^,;.]*")

#: 검색 예시 한 줄 길이 상한(낱말 경계 절단).
_SUMMARY_TRIGGER_CHARS = 60


def _attested_aliases(source: str) -> dict[str, str]:
    """config.SCREEN_TERM_ALIASES 중 원문 괄호 병기(「디폴트옵션(사전지정운용)」)가 있는 것만. 없으면 알림."""
    out: dict[str, str] = {}
    for term, alias in config.SCREEN_TERM_ALIASES.items():
        a, b = re.escape(term), re.escape(alias)
        if re.search(rf"{a}[가-힣]*\s*\(\s*{b}|{b}[가-힣]*\s*\(\s*{a}", source):
            out[term] = alias
        else:
            note(f"[같은말 근거없음] {term} = {alias} — 원문에 괄호 병기가 없어 화면 카드에 붙이지 않음")
    return out


def _screen_aliases(title: str, summary: str, attested: dict[str, str]) -> dict[str, str]:
    """화면 카드의 «같은 말» — 원문 표기만 있고 직원이 부르는 이름이 없는 것."""
    text = f"{title} {summary or ''}"
    return {t: a for t, a in attested.items() if t in text and a not in text}


def _screen_triggers(title: str, summary: str,
                     aliases: dict[str, str] | None = None) -> list[str]:
    """screen 검색 예시 — 화면명 + 표A «주요 기능» 칸(항목 참조 제거) + «같은 말» 괄호 병기.

    «주요 기능»은 둘째 칸 고정 — 카드 목록 줄은 예상질문을 앞 2개만 표시.
    """
    out = [f"{title} 화면번호"]
    text = " ".join(_ITEM_REF.sub("", summary or "").split()).strip(" .")
    if len(text) > _SUMMARY_TRIGGER_CHARS:
        cut = text.rfind(" ", _SUMMARY_TRIGGER_CHARS // 2, _SUMMARY_TRIGGER_CHARS + 1)
        text = text[:cut if cut > 0 else _SUMMARY_TRIGGER_CHARS].rstrip(" ,:;(·")
    for term, alias in (aliases or {}).items():
        text = re.sub(rf"({re.escape(term)}[가-힣]*)", rf"\1({alias})", text, count=1)
    if len(text) >= 4 and text != title:
        out.append(text)
    out.append(f"{title} 어느 화면")
    return out


def build_screens(resolver: DocResolver) -> list[dict]:
    """표A → 화면 레지스트리(표 값 그대로)."""
    src = EXTRACT / "05_업무처리절차.md"
    text = src.read_text(encoding="utf-8")
    lines = text.splitlines()
    # «같은 말» 근거는 표B 병기 — 파일 전체 탐색
    attested = _attested_aliases(text)
    start = next(i for i, ln in enumerate(lines) if ln.startswith("### 표A."))
    end = next(i for i, ln in enumerate(lines[start:], start) if ln.startswith("### 표B."))

    # 표A 머리말 ⚠ 경고 — 문구는 원문에서, «현행 확인 필요» 화면에만 부착
    warn = next((clean(_CHANNEL_WARN.match(ln.strip()).group(1)).strip()
                 for ln in lines[start:end] if _CHANNEL_WARN.match(ln.strip())), None)
    if not warn:
        note("[표A 경고없음] 머리말의 ⚠ 안내를 찾지 못함 — 번호가 낡았을 수 있다는 표시가 빠진다")

    records: list[dict] = []
    group = "미분류"
    seen: set[str] = set()
    for raw in lines[start:end]:
        line = raw.strip()
        if line.startswith("#### "):
            group = clean(line[5:]).strip()
            continue
        m = _SCREEN_ROW.match(line)
        if not m:
            continue
        raw_cells = [c.strip() for c in m.group(2).split("|")]
        cells = [clean(c).strip() for c in raw_cells]
        screen, title = m.group(1), (cells[0] if cells else "")
        if not title:
            note(f"[화면명없음] {screen} — 화면명이 비어 건너뜀")
            continue
        summary = cells[1] if len(cells) > 1 else ""
        confidence_raw = cells[2] if len(cells) > 2 else ""
        remark = cells[3] if len(cells) > 3 else ""
        raw_remark = raw_cells[3] if len(raw_cells) > 3 else ""
        mark = next((k for k in _CONFIDENCE if k in confidence_raw), "")

        # 중복 화면번호는 첫 행만(번호 = id)
        key = screens.normalize(screen)   # 런타임과 같은 표준형
        if key in seen:
            continue
        seen.add(key)

        # «확인 필요» 비고 — «해소» 언급 비고는 제외(반대 뜻)
        stale = "확인 필요" in remark and "해소" not in remark
        aliases = _screen_aliases(title, summary, attested)

        records.append(record(
            f"screen.{key.lower()}", "screen",
            {"screen": screen, "title": title, "group": group,
             "summary": summary or None,
             "screens": [screen],
             "confidence": (f"{_CONFIDENCE[mark]}({confidence_raw})" if mark else None),
             "note": role_entries(raw_remark, "info",
                                  config.SCREEN_NOTE_ROLES.get(key), f"screen {key}") or None,
             "status": "확인 필요" if stale else None,
             "volatile": (warn if stale else None),
             "tags": {"topics": [group]},
             # 파생 필드(원문 summary 유지)
             "aliases": [f"{t} = {a}" for t, a in aliases.items()] or None,
             "trigger_examples": _screen_triggers(title, summary, aliases)},
            # 출처는 화면번호 안내 문서, 비고 «범위 밖»·«미수록»이면 None
            source={"doc": None if "범위 밖" in remark or "미수록" in remark else SCREEN_DOC,
                    "locator": f"{config.EXTRACT_REL}/05_업무처리절차.md § 표A. {group} — {screen}"}))
    return records


# 「표B. 비대면 채널 처리 경로」 → channel(고객이 앱·웹에서; screen 은 직원이 단말에서)

#: 표B 머리말 기준시점 `(**2025.03.31 기준**)`.
_CHANNEL_AS_OF = re.compile(r"\*\*([\d.]{8,10})\s*기준\*\*")

#: 표B 머리말 ⚠ 경고 — 문구·부착 여부 모두 원문에서(코드에 문구 보유 금지).
_CHANNEL_WARN = re.compile(r"^⚠\s*\*\*([^*]+)\*\*")

#: 표B 카테고리 행 `| **조회·확인** | | | |`.
_CHANNEL_GROUP = re.compile(r"^\|\s*\*\*([^*]+)\*\*\s*\|[\s|]*$")

#: 채널 목록에 없음(원문 '–') — 빈 값 취급.
_CHANNEL_NONE = {"-", "–", "—", ""}


def _cells(line: str) -> list[str]:
    """표 한 행 → 셀 목록."""
    return [clean(c).strip() for c in line.strip().strip("|").split("|")]


def _channel_path(raw: str) -> str | None:
    """채널 경로 한 칸. '–'·빈칸은 None."""
    val = raw.lstrip("●").strip()
    return None if val in _CHANNEL_NONE else val


def build_channels(resolver: DocResolver) -> list[dict]:
    """표B + 머리말의 이용 가능 시간 예외 표 → channel."""
    src = EXTRACT / "05_업무처리절차.md"
    lines = src.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("### 표B."))
    end = next((i for i, ln in enumerate(lines[start + 1:], start + 1)
                if ln.startswith("## ")), len(lines))

    head = "\n".join(lines[start:end])
    as_of_m = _CHANNEL_AS_OF.search(head)
    as_of = as_of_m.group(1) if as_of_m else None
    warn = next((clean(_CHANNEL_WARN.match(ln.strip()).group(1)).strip()
                 for ln in lines[start:end] if _CHANNEL_WARN.match(ln.strip())), None)
    if not as_of:
        note("[표B 기준시점없음] 머리말에서 '(**YYYY.MM.DD 기준**)' 을 찾지 못함")
    if not warn:
        note("[표B 경고없음] 머리말의 ⚠ 안내를 찾지 못함 — 경로가 낡을 수 있다는 표시가 빠진다")

    records: list[dict] = []
    seen: set[str] = set()

    def add(task: str, fields: dict, group: str) -> None:
        key = re.sub(r"[^0-9A-Za-z가-힣]", "", task).lower()[:40]
        if not key or key in seen:
            return
        seen.add(key)
        # 메뉴 경로도 검색 단서
        paths = [v for v in (fields.get("starbanking"), fields.get("ibank")) if v]
        records.append(record(
            f"channel.{len(records) + 1:03d}", "channel",
            {"task": task, "title": task, "group": group,
             "as_of": as_of, "volatile": warn,
             "tags": {"topics": ["비대면채널", group]},
             "trigger_examples": [f"{task} 스타뱅킹 경로", f"고객이 직접 {task} 하는 방법",
                                  *paths],
             **fields},
            source={"doc": CHANNEL_DOC,
                    "locator": f"{config.EXTRACT_REL}/05_업무처리절차.md § 표B. {group} — {task}"}))

    group = "미분류"
    for raw in lines[start:end]:
        line = raw.strip()
        # 머리말 인용블록의 이용 가능 시간 예외 표
        if line.startswith(">") and line.lstrip("> ").startswith("|"):
            cells = _cells(line.lstrip("> "))
            if len(cells) < 3 or "---" in cells[0] or cells[0] in ("업무", ""):
                continue
            add(cells[0], {"hours": cells[1],
                           "note": role_entries(cells[2], "info",
                                                config.CHANNEL_NOTE_ROLES.get(cells[0]),
                                                f"channel {cells[0]}") or None,
                           "summary": f"비대면 이용 가능 시간 {cells[1]}"
                                      + (f" · {cells[2]}" if cells[2] else "")},
                "이용 가능 시간 예외")
            continue
        if not line.startswith("|") or "---" in line:
            continue
        m = _CHANNEL_GROUP.match(line)
        if m:
            group = clean(m.group(1)).strip()
            continue
        cells = _cells(line)
        if len(cells) < 3 or cells[0] in ("업무", ""):
            continue
        star, ibank = _channel_path(cells[1]), _channel_path(cells[2])
        if not star and not ibank:
            continue          # 두 채널 모두 없음 — 비대면 불가 업무
        # 변수명 `note` 금지 — 리포트 함수 note() 를 가려 UnboundLocalError
        remark = cells[3] if len(cells) > 3 else ""
        where = " / ".join(x for x in (f"스타뱅킹 {star}" if star else "",
                                       f"인터넷뱅킹 {ibank}" if ibank else "") if x)
        add(cells[0], {"starbanking": star, "ibank": ibank,
                       "note": role_entries(remark, "info",
                                            config.CHANNEL_NOTE_ROLES.get(cells[0]),
                                            f"channel {cells[0]}") or None,
                       "summary": where,
                       # 비고의 단말 화면번호 대응
                       "screens": sorted(set(_SCREEN.findall(remark)))},
            group)
    return records


# 절차 항목 → procedure

_LEGACY_NO = re.compile(r"\*\(구:\s*([^)]+)\)\*")


def build_procedures(resolver: DocResolver) -> list[dict]:
    src = EXTRACT / "05_업무처리절차.md"
    lines = src.read_text(encoding="utf-8").splitlines()
    index = parse_index(lines, stop="## 화면·채널 일람", group_style="bold_row")
    body_start = next(i for i, ln in enumerate(lines) if ln.startswith("## [조회·진단 경로]"))

    records: list[dict] = []
    for item in parse_items(lines, body_start):
        no = item["no"]
        meta = index.get(no) or {}
        group = meta.get("group") or "미분류"
        fields, quotes = split_fields(item["body"])
        body_text = "\n".join(item["body"])

        summary = joined(fields, "정리") or joined(fields, "머리말")
        quote_records = []
        for q in quotes:
            attribution = redact(q["source_text"])
            quote_records.append({
                "text": redact(q["text"]),
                "source_text": attribution or None,
                "doc": resolver.resolve(attribution, f"절차 {no}") if attribution else None,
            })

        # ⚠ 유의는 별도 필드, 기본 authoring(저작 검증 메모가 본체). 상담 주의는 config 예외표로
        cautions = [q["text"] for q in quote_records if q["text"].startswith("⚠")]
        quote_records = [q for q in quote_records if not q["text"].startswith("⚠")]
        override = config.PROCEDURE_CAUTION_ROLES.get(no)
        if override is not None:
            blob = clean(" ".join(redact(c) for c in cautions))
            for e in override:
                if clean(e["text"]) not in blob:
                    note(f"[역할예외 불일치] 절차 {no} — 예외표의 '{e['text'][:30]}…' 이 원문에 없음")
            caution_entries = [dict(e) for e in override]
        else:
            caution_entries = [{"role": "authoring", "text": redact(c)} for c in cautions]

        title = clean(item["title"])
        # 화면번호는 절차가 여는 화면만 — ⚠ 유의 박스 제외(각주·확인 방법의 번호가 오답이 됨)
        scan = "\n".join([summary or "", *(q["text"] for q in quote_records)])
        screens = sorted(set(_SCREEN.findall(scan)))
        marks = " ".join(meta.get("marks") or [])
        legacy = _LEGACY_NO.search(body_text)

        primary = next((q["doc"] for q in quote_records if q["doc"]), None)
        records.append(record(
            f"proc.{no.zfill(3)}", "procedure",
            {
                "no": no, "title": title, "group": group,
                "summary": redact(summary) or None,
                "quotes": quote_records,
                "screens": screens,
                "cautions": caution_entries or None,
                # ▶ 고객 안내 가능 · ⚠ 자료 간 상충·확인 필요
                "customer_facing": "▶" in marks or "▶고객 안내 가능" in body_text,
                "status": "확인 필요" if "⚠" in marks else None,
                "legacy_no": legacy.group(1) if legacy else None,
                "segments": [],
                "tags": {"topics": topics_of(title, summary)},
                "trigger_examples": triggers_of(f"proc.{no.zfill(3)}", summary),
                "author_redacted": True,
            },
            source={"doc": primary,
                    "locator": f"{config.EXTRACT_REL}/05_업무처리절차.md § {no}. {title}"},
        ))
    return records
