"""06/05 업무처리절차 → screen(표A) · channel(표B) · procedure.

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

import re

from scripts.kb_build import config
from pension_agent.consult_agent.effects import screens

from scripts.kb_build.common import EXTRACT, clean, note, record, redact, role_entries, topics_of, triggers_of
from scripts.kb_build.docs import DocResolver
from scripts.kb_build.facts import _SCREEN
from scripts.kb_build.parse import joined, parse_index, parse_items, split_fields


# ─────────────────────────────────────────────────────────────
# 7) 06/05 업무처리절차 「표A. 단말 화면번호 일람」 → screen
#
# 이 표는 오랫동안 적재되지 않았다. 변환기가 `## [조회·진단 경로]` 아래의 절차 항목 74건만
# 읽었고, 그 위의 화면번호 대응표 88행은 통째로 건너뛰었다. 그래서 **절차 항목이 본문에서
# 언급하지 않은 화면은 지식베이스에 존재하지 않았다** — "포트폴리오 운용현황 조회 화면
# 번호는?"에 [06-12-604] 가 원문 표에 버젓이 있는데도 "찾지 못했습니다"로 답하던 이유다.
#
# 화면번호는 직원이 가장 자주 묻는 것 중 하나이고(07/01 "화면번호·처리 순서까지 담는다"),
# 표는 이미 업무 그룹·화면명·용도·신뢰도까지 정리돼 있다. 옮겨 적기만 하면 되는 재료였다.
# ─────────────────────────────────────────────────────────────

#: 표A 의 행. `| [06-12-604] | 포트폴리오 운용현황 조회 | 연금 로보… | △ | 비고 |`
_SCREEN_ROW = re.compile(
    r"^\|\s*(\[[0-9A-Za-z]{2}-[0-9A-Za-z]{2}-[0-9A-Za-z]{3}\])\s*\|(.+)$")

#: 표A·표B 의 원천 문서. 06/05 는 이 문서를 업무 그룹별로 재배열한 정리본이다
#: (표B 는 그 문서 부록 2 의 스타뱅킹·인터넷뱅킹 목록을 업무별로 병합한 것이다).
SCREEN_DOC = "doc.퇴직연금_주요거래_화면번호_안내"
CHANNEL_DOC = SCREEN_DOC

#: 신뢰도 표기 → 읽을 수 있는 말. 표 머리말이 정의한 그대로다(교차확인 N건 / 단일 자료).
_CONFIDENCE = {"○": "여러 자료에서 교차확인", "△": "단일 자료에만 등장"}


def build_screens(resolver: DocResolver) -> list[dict]:
    """표A 를 화면 레지스트리로 옮긴다. 표의 값을 그대로 싣고 새로 만들지 않는다."""
    src = EXTRACT / "05_업무처리절차.md"
    lines = src.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("### 표A."))
    end = next(i for i, ln in enumerate(lines[start:], start) if ln.startswith("### 표B."))

    # 표A 머리말의 ⚠ 경고 — 표B 와 같은 규약이다. "번호가 낡았을 수 있다"는 문구를 코드가
    # 들고 있으면 원문이 바뀔 때 두 곳이 갈리므로, 붙일지도 문구도 원문에서 읽는다(§12 gap 16
    # 이 channel 에서 같은 이유로 생겼다). 경고는 원문이 "현행 확인 필요"를 표기한 화면에만
    # 싣는다 — 머리말 스스로 그 범위를 그렇게 정의한다.
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

        # 같은 화면번호가 여러 그룹에 나오면 먼저 나온 것을 남긴다 — 표가 업무 그룹별
        # 재배열이라 중복이 있을 수 있고, 번호가 곧 id 이므로 중복 id 를 만들 수 없다.
        key = screens.normalize(screen)   # 런타임과 같은 표준형 — 대조 상대가 그쪽이다
        if key in seen:
            continue
        seen.add(key)

        # 원문이 "현행 확인 필요"라고 표기한 화면 — 번호가 낡았을 수 있다는 뜻이고, 직원이
        # 알아야 처리 전에 확인한다. "확인 필요"가 **해소됐다**고 적은 비고(04-12-640)까지
        # 부분문자열로 걸면 반대 뜻의 문장을 경고로 뒤집어 읽는다.
        stale = "확인 필요" in remark and "해소" not in remark

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
             "trigger_examples": [f"{title} 화면번호", f"{title} 어느 화면"]},
            # 표A 의 원천은 화면번호 안내 문서다 — 06/05 는 그것을 업무 그룹별로 재배열한
            # 정리본이라, 출처는 원천 문서를 가리켜야 한다(§3 사내 파일명은 출처가 아니다).
            # 표 밖에서 온 번호는 비고가 "수록 범위 밖"이라고 적어두므로 거기서 갈린다.
            source={"doc": None if "범위 밖" in remark or "미수록" in remark else SCREEN_DOC,
                    "locator": f"{config.EXTRACT_REL}/05_업무처리절차.md § 표A. {group} — {screen}"}))
    return records


# ─────────────────────────────────────────────────────────────
# 8) 06/05 업무처리절차 「표B. 비대면 채널 처리 경로」 → channel
#
# 표A 와 같은 이유로 빠져 있었다 — 변환기가 절차 항목만 읽었다. 이 표는 61행짜리로,
# "고객이 스타뱅킹에서 직접 상품변경하려면 어느 메뉴인가"에 답하는 유일한 재료다.
# 직원이 고객에게 전화로 경로를 불러주는 자리이므로 메뉴 이름 한 마디가 곧 답이다.
#
# screen(단말 화면번호)과 나누는 기준은 **누가 하는가**다 — screen 은 직원이 단말에서,
# channel 은 고객이 앱·웹에서. 같은 업무라도 답이 다르고, 묻는 사람도 다르다.
# ─────────────────────────────────────────────────────────────

#: 표B 머리말이 적어둔 기준시점. `(**2025.03.31 기준**)` 에서 날짜만 꺼낸다.
_CHANNEL_AS_OF = re.compile(r"\*\*([\d.]{8,10})\s*기준\*\*")

#: 표B 머리말의 ⚠ 경고. 원문 스스로 "메뉴명이 바뀔 수 있다는 안내를 함께 넣어야 한다"고
#: 규정한 것(항목 17)이라, 문구도 붙일지 여부도 **원문에서 읽는다**. 코드가 따로 들고
#: 있으면 원문이 바뀔 때 두 곳이 갈리고, 갈리면 답변이 틀린 기준시점을 말한다.
_CHANNEL_WARN = re.compile(r"^⚠\s*\*\*([^*]+)\*\*")

#: 표B 의 카테고리 행. `| **조회·확인** | | | |` 처럼 첫 칸만 굵게 차 있고 나머지는 빈다.
_CHANNEL_GROUP = re.compile(r"^\|\s*\*\*([^*]+)\*\*\s*\|[\s|]*$")

#: 그 채널 목록에 없음(원문의 '–'). 빈 값과 같게 다룬다 — 없는 것을 지어내지 않는다.
_CHANNEL_NONE = {"-", "–", "—", ""}


def _cells(line: str) -> list[str]:
    """표 한 행을 셀 목록으로. 앞뒤 파이프는 버린다."""
    return [clean(c).strip() for c in line.strip().strip("|").split("|")]


def _channel_path(raw: str) -> str | None:
    """채널 경로 한 칸. '–'(목록에 없음)와 빈칸은 None 으로 접는다."""
    val = raw.lstrip("●").strip()
    return None if val in _CHANNEL_NONE else val


def build_channels(resolver: DocResolver) -> list[dict]:
    """표B 와 그 머리말의 이용 가능 시간 예외 표를 비대면 채널 재료로 옮긴다."""
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
        # 메뉴 경로 자체도 검색 단서다 — "변경관리 메뉴가 뭐냐"처럼 직원이 메뉴 이름으로
        # 물으면 업무명(task)만으로는 카드에 닿지 않는다(kinds.json searchable 선언과 대응).
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
        # 머리말 인용블록의 이용 가능 시간 예외 표. 24시간 원칙의 **예외**만 적혀 있다.
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
            continue          # 두 채널 모두 없으면 비대면으로 못 하는 업무다
        # 변수 이름을 `note` 로 두면 모듈의 리포트 함수 note() 를 가려서, 위쪽의
        # note("[표B 기준시점없음] …") 호출이 UnboundLocalError 로 죽는다(변환기가
        # 경고 대신 크래시로 끝난다). 지역 변수는 remark 로 둔다.
        remark = cells[3] if len(cells) > 3 else ""
        where = " / ".join(x for x in (f"스타뱅킹 {star}" if star else "",
                                       f"인터넷뱅킹 {ibank}" if ibank else "") if x)
        add(cells[0], {"starbanking": star, "ibank": ibank,
                       "note": role_entries(remark, "info",
                                            config.CHANNEL_NOTE_ROLES.get(cells[0]),
                                            f"channel {cells[0]}") or None,
                       "summary": where,
                       # 비고가 단말 화면번호 대응을 적어둔 행이 있다 — 그대로 옮긴다.
                       "screens": sorted(set(_SCREEN.findall(remark)))},
            group)
    return records


# ─────────────────────────────────────────────────────────────
# 9) 06/05 업무처리절차 → procedure
# ─────────────────────────────────────────────────────────────

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

        # ⚠ 유의는 인용이 아니라 별도 필드로 싣는다. 역할은 일괄 authoring — 05 의 ⚠ 유의
        # 블록은 "필자 해석 · 팀 검증 필요 · 현행 확인 필요" 같은 저작 검증 메모가 본체라
        # (guard.py 가 같은 판단으로 이 종류를 가드 재료에서 빼 왔다) 표지·굵기 규칙으로
        # 가를 수 없다. 항목 안에 상담 주의가 섞인 경우는 config 예외표에서 사람이 가른다.
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
        # 화면번호는 **이 절차가 실제로 여는 화면**이다 — ⚠ 유의 박스는 훑지 않는다.
        #
        # 유의 박스에 화면번호가 나오는 것은 그 절차의 화면이라서가 아니라 각주·확인 방법이라서다.
        # 39번(비대면 실물이전)의 유의는 ⑤단계 스타뱅킹 메뉴의 단말 대응 화면을 괄호로 적어둔
        # 것인데, 그것이 카드의 화면번호가 되는 바람에 "비대면 실물이전 화면번호는
        # [06-12-151]" 이라는 답이 나갔다 — [06-12-151]은 개인부담금 한도 조회 화면이다.
        # 18번의 유의도 "단말에서 실제로 걸어 확인해보라"는 검증 방법이다.
        # 유의를 인용 목록에서 뺀 것과 같은 경계를 화면번호에도 적용한다.
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
                # ▶ 는 '고객에게 그대로 안내해도 되는 절차', ⚠ 는 '자료 간 상충·확인 필요'.
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
