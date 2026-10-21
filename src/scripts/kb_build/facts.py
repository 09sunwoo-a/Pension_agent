"""06/04 제도상품팩트 → fact(활성) + 보류 팩트."""

from __future__ import annotations

import re

from scripts.kb_build import config

from scripts.kb_build.common import EXTRACT, clean, note, record, redact, topics_of, triggers_of
from scripts.kb_build.docs import DocResolver
from scripts.kb_build.market import _TABLE_LINE, _market_tables
from scripts.kb_build.parse import parse_index, parse_items


# 한 불릿에 여러 라벨이 ' · ' 로 연결, 값 안에도 '·' 존재 — 구분자 아닌 라벨 위치로 분할
_FACT_LABEL = re.compile(r"\*\*([^*]{2,10})\*\*\s*:\s*")


_SCREEN = re.compile(r"\[\d{2}-[0-9A-Z]{2}-[0-9A-Z]{3}\]")


def _labeled_slots(line: str) -> dict[str, str]:
    """`**라벨**: 값 · **라벨**: 값` 한 줄 → {라벨: 값}."""
    marks = list(_FACT_LABEL.finditer(line))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        value = line[m.end():end].strip().rstrip("·").strip()
        out[re.sub(r"\s+", "", m.group(1))] = value
    return out


#: 검증 포인트 항목 구분 표지 ①~⑩(하나뿐이면 번호 없음).
_PITFALL_SPLIT = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]\s*")

#: 검증 포인트 안 따옴표 인용(그 자체로는 오답 아님).
_QUOTED = re.compile(r"[\"“]([^\"“”]{4,80})[\"”]")

#: 인용 뒤 오답 표지 — 옳은 표현도 같은 따옴표로 인용되므로 표지 있는 것만 오답.
_WRONG_MARKS = ("오답", "오기", "오안내", "→ X", "→X", "말 것", "금지", "틀린", "혼동")

#: 표지 탐색 범위(다음 인용의 표지를 끌어오지 않게 좁게).
_MARK_WINDOW = 25

#: 오답 문자열 최소 길이(`_is_phrase`).
_MIN_BARE = 8


def _is_phrase(text: str) -> bool:
    """오답 대조 가능한 구절 여부. 값 하나짜리 인용("5천만원")은 다른 팩트의 맞는 문장과 우연 일치 → 제외."""
    return " " in text or len(text) >= _MIN_BARE


def _wrong_quotes(text: str) -> list[str]:
    """항목이 오답으로 지목한 인용 표현 목록."""
    out: list[str] = []
    for m in _QUOTED.finditer(text):
        tail = text[m.end():m.end() + _MARK_WINDOW]
        if "→ O" in tail or "→O" in tail:
            continue                       # 참 표시 인용
        quoted = m.group(1).strip()
        if any(w in tail for w in _WRONG_MARKS) and _is_phrase(quoted):
            out.append(quoted)
    return out


def _tiers(raw: str) -> list[dict]:
    """`조건 → 값; 조건 → 값` → 조건–값 쌍. 구분자 `;`(값 안에 `·` 흔함)."""
    out: list[dict] = []
    for part in raw.split(";"):
        if "→" not in part:
            continue
        when, _, value = part.partition("→")
        when, value = clean(when).strip(), clean(value).strip()
        if when and value:
            out.append({"when": when, "value": value})
    return out


def _pitfalls(raw: str) -> list[dict]:
    """검증 포인트 산문 → [{wrong, why}]. 오답 지목 인용만 `wrong`, 내용 생성 없음."""
    parts = [clean(x).strip() for x in _PITFALL_SPLIT.split(raw) if clean(x).strip()]
    out: list[dict] = []
    for text in parts:
        out.append({"wrong": _wrong_quotes(text), "why": text})
    return out


def _fact_tables_text(body: list[str]) -> str:
    """팩트 절의 마크다운 표 원문 연결(표 사이 빈 줄)."""
    blocks: list[list[str]] = []
    for raw in body:
        if _TABLE_LINE.match(raw.strip()):
            if not blocks or blocks[-1] is None:
                blocks.append([])
            blocks[-1].append(raw.rstrip())
        elif blocks and blocks[-1] is not None and blocks[-1]:
            blocks.append(None)          # 표 끝 표시
    return "\n\n".join("\n".join(b) for b in blocks if b)


def _fact_status(raw: str) -> tuple[str, list[str]]:
    """상태 표기(✅ 확정 / ⚠ 확인 필요 / ⏳ 시효 민감, 복합 가능) → (대표 상태, 표시 전체)."""
    marks = [m for m in ("✅", "⚠", "⏳") if m in raw]
    label = {"✅": "확정", "⚠": "확인 필요", "⏳": "시효 민감"}
    primary = "확정" if "✅" in marks else ("시효 민감" if "⏳" in marks else "확인 필요")
    return primary, [label[m] for m in marks]


def build_facts(resolver: DocResolver) -> tuple[list[dict], list[dict]]:
    """(활성 팩트, 보류 팩트). «⚠ 확인 필요»는 보류 — 적재 시 에이전트가 사실로 답함."""
    src = EXTRACT / "04_제도상품팩트.md"
    lines = src.read_text(encoding="utf-8").splitlines()
    index = parse_index(lines, stop="## [")
    body_start = next(i for i, ln in enumerate(lines) if ln.startswith("## ["))

    active: list[dict] = []
    pending: list[dict] = []
    for item in parse_items(lines, body_start):
        no = item["no"]
        group = (index.get(no) or {}).get("group") or "미분류"
        slots: dict[str, str] = {}
        statement = ""
        for raw in item["body"]:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("**팩트**"):
                statement = clean(line.split(":", 1)[-1])
            elif line.startswith("- **") or line.startswith("**"):
                slots.update({k: clean(v) for k, v in _labeled_slots(line).items()})
        if not statement:
            note(f"[팩트없음] {no} — **팩트** 문장을 찾지 못해 건너뜀")
            continue

        status, flags = _fact_status(slots.get("상태", ""))
        attribution = redact(slots.get("출처", ""))
        title = clean(item["title"])
        fields = {
            "no": no,
            # 카드 색인 필드(title·group·tags.topics·trigger_examples) — LLM 카드 선택 후보용
            "title": title,
            "group": group,
            "tags": {"topics": topics_of(title, statement)},
            # 트리거: 팩트 문장 첫 절 + 검증 포인트 첫 절
            "trigger_examples": triggers_of(f"fact.k04.{no.lower()}", statement,
                                            slots.get("검증포인트")),
            # label 은 제목 전체 — 레거시 짧은 라벨과 달라 check_fact_conflicts 오탐 없음
            "label": title,
            "value": statement,
            "category": group,
            "as_of": slots.get("기준") or None,
            "status": status,
            "nature": slots.get("성격") or None,
            "customer_facing": "⭕" in slots.get("대외안내", ""),
            "verify_points": slots.get("검증포인트") or None,
            # 원문 표 전부 재료로 포함(슬롯 파서가 `| … |` 줄을 버림). 관계 선언은 아래 tables 만
            "content": _fact_tables_text(item["body"]) or None,
            # 관계 선언 — 값–조건 오짝·알려진 오답 대조 재료(선언 없으면 대조 제외)
            "tiers": _tiers(slots.get("조건별값", "")),
            # 이름 열·값 열이 갈리는 표만 선언(_market_tables — 범용 추출기)
            "tables": _market_tables("\n".join(item["body"])) or None,
            "pitfalls": _pitfalls(slots.get("검증포인트", "")),
            "history": slots.get("이력") or None,
            "screens": sorted(set(_SCREEN.findall(statement))),
            "source_text": attribution or None,
            "detail": " · ".join(flags) if len(flags) > 1 else None,
        }
        rec = record(f"fact.k04.{no.lower()}", "fact", fields,
                     source={"doc": resolver.resolve(attribution, f"팩트 {no}") if attribution else None,
                             "locator": f"{config.EXTRACT_REL}/04_제도상품팩트.md § {no}. {title}"})
        (pending if status == "확인 필요" else active).append(rec)

    return active, pending
