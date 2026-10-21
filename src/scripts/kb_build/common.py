"""변환기 공통 — 경로 상수 · 리포트(note) · 텍스트 유틸 · 주의/비고 역할 분류 · 레코드 · 상호참조 · 쓰기."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scripts.kb_build import config


# 경로는 config 에서만 — 번호 붙은 폴더 이름 하드코딩 금지
REPO = config.REPO
EXTRACT = config.EXTRACT_DIR
OUT_DIR = config.OUT_DIR

GUIDE_DIR = config.GUIDE_DIR
STARLEARN_DIR = config.STARLEARN_DIR
HOTTIP_DIR = config.HOTTIP_DIR
KBTHINK_DIR = config.KBTHINK_DIR

_report: list[str] = []


def note(msg: str) -> None:
    _report.append(msg)


_PII = [(re.compile(p), r) for p, r in config.PII_PATTERNS]


def redact(text: str) -> str:
    """작성자 실명·부점·직급 제거. 글번호·게시일(출처)은 유지."""
    out = text
    for pat, repl in _PII:
        out = pat.sub(repl, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def clean(text: str) -> str:
    """마크다운 강조·링크 제거 평문. 요약·조건문 전용(원문 인용에 사용 금지)."""
    out = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)   # [텍스트](링크) → 텍스트
    out = re.sub(r"[*`_]+", "", out)
    out = out.replace("　", " ").replace("└", "").replace("<br>", " ")
    return re.sub(r"\s{2,}", " ", out).strip()


def sentences(text: str, limit: int = 4) -> list[str]:
    """요약 → 직원용 불릿. 번호('(1)')·문장 끝 기준 분할, 짧은 조각은 다음 조각에 병합."""
    if not text:
        return []
    marked = re.sub(r"\s*(?<![\d(])\((\d)\)\s*", r"\n(\1) ", text)
    parts: list[str] = []
    for chunk in marked.split("\n"):
        parts += [s.strip() for s in re.split(r"(?<=[.!?])\s+", chunk) if s.strip()]

    merged: list[str] = []
    carry = ""
    for part in parts:
        candidate = f"{carry} {part}".strip() if carry else part
        if len(candidate) < 14:
            carry = candidate
            continue
        merged.append(candidate)
        carry = ""
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}".strip()
        else:
            merged.append(carry)
    return merged[:limit]


#: 절 구분자. 자릿수 쉼표("1,800만원")·가운뎃점(명사 나열)·괄호 안 "또는"은 분할 제외.
_CLAUSE_SPLIT = re.compile(r"(?<!\d),|,(?!\d)|\s—\s|(?<=\.)\s|\s또는\s|\s그리고\s")


def first_clause(text: str, limit: int = 70) -> str | None:
    """검색 예시용 첫 절. 8자 미만 절은 건너뜀, 마크다운 표 앞 산문까지만, 길면 낱말 경계에서 자름."""
    if not text:
        return None
    text = text.split("\n|", 1)[0]
    for head in _CLAUSE_SPLIT.split(text.strip()):
        head = head.strip().rstrip(".")
        if len(head) < 8:
            continue
        if len(head) > limit:
            # 낱말 중간 절단 금지
            cut = head.rfind(" ", limit // 2, limit + 1)
            head = head[:cut if cut > 0 else limit].rstrip(" ,:;(")
        return head
    return None


def triggers_of(card_id: str, *texts: str | None, limit: int = 3) -> list[str]:
    """검색 예시(trigger_examples) — 본문 첫 절 + config.TRIGGER_EXTRA.

    제목 미포함(카드 목록 줄에 이미 있음 — 예상질문 칸 낭비). 질문 문형 생성 금지(n-gram 오매칭).
    """
    out: list[str] = []
    for text in texts:
        clause = first_clause(text or "")
        if clause and clause not in out:
            out.append(clause)
    for extra in config.TRIGGER_EXTRA.get(card_id, []):
        if extra not in out:
            out.append(extra)
    return out[:limit]


def topics_of(*texts: str) -> list[str]:
    """검색 태그 — config.TOPIC_VOCAB 중 본문에 나온 것만."""
    blob = " ".join(t or "" for t in texts)
    return [t for t in config.TOPIC_VOCAB if t in blob]


# 주의·비고 역할 분류 — 데이터에 선언, 소비 코드는 선언만 참조
#   caution   상담 중 주의. 답변에 반드시 포함
#   info      참고 비고. 답변 재료에 포함
#   authoring 저작·검증 메모. 직원 화면 미표시
# 규칙 오분류는 config.*_NOTE_ROLES 예외표로 지정

#: 저작·검증 메모 표지(원문 비고·유의의 실제 표현).
_AUTHORING_MARKS = ("필자", "팀 논의", "팀 확인", "팀 검증", "확인 필요", "현행 여부",
                    "표기", "상충", "미수록", "수록 범위 밖", "판독", "화면번호안내PDF",
                    "추정", "해소", "오기", "불일치")


def role_entries(raw: str, default: str, override: list[dict] | None = None,
                 owner: str = "") -> list[dict]:
    """비고·주의 한 칸 → `[{"role", "text"}]`. 우선순위: config 예외 → 저작 표지 → 굵게(caution) → 기본값."""
    text = clean(raw).strip()
    if not text:
        return []
    if override is not None:
        for e in override:
            if e["text"] not in text:
                note(f"[역할예외 불일치] {owner} — 예외표의 '{e['text'][:30]}…' 이 원문 칸에 없음")
        return [dict(e) for e in override]
    if any(m in text for m in _AUTHORING_MARKS):
        return [{"role": "authoring", "text": text}]
    if "**" in raw:
        return [{"role": "caution", "text": text}]
    return [{"role": default, "text": text}]


def record(rid: str, kind: str, fields: dict, source: dict | None = None) -> dict:
    """레코드 한 건. None 필드는 생략 — `.get(k, [])` 소비 코드가 None 을 받지 않게."""
    rec = {"id": rid, "kind": kind,
           "fields": {k: v for k, v in fields.items() if v is not None}}
    if source:
        rec["source"] = {k: v for k, v in source.items() if v is not None}
    return rec


def inherit_parent_source(records: list[dict]) -> list[dict]:
    """하위 항목(12-1·1-1 …)에 상위 항목의 출처 doc 상속(원문 인용이 상위에만 있음)."""
    by_id = {r["id"]: r for r in records}
    for rec in records:
        parent_id = (rec.get("fields") or {}).get("parent")
        if not parent_id or (rec.get("source") or {}).get("doc"):
            continue
        parent = by_id.get(parent_id)
        parent_doc = (parent or {}).get("source", {}).get("doc")
        if parent_doc:
            rec.setdefault("source", {})["doc"] = parent_doc
    return records


# 항목 상호참조 — 「항목 41·48」은 지식베이스 항목 번호(단말 화면 값 아님)
# 맨숫자로 두면 답변이 단말 항목번호로 오독, 수치 검증도 통과 → 「지식항목 N」 표기 + refs 연결
# 원문(quotes·source_text)은 수정 금지

#: 「항목 41」→「지식항목 41」. 숫자 앞만, 이미 붙은 것 제외(멱등).
_XREF_WORD = re.compile(r"(?<!지식)항목(?=\s*\d)")

#: 표A 「→ 항목 N」 포인터에 「관련」 부착 — 앞의 단말 순번(①②)의 연장으로 오독 방지.
_XREF_ARROW = re.compile(r"→\s*(?!관련)지식항목")

#: 「항목 41·48」 같은 연속 번호 포함.
_XREF_NUMS = re.compile(r"지식항목\s*(\d+(?:\s*[·,]\s*\d+)*)")

#: refs 탐색 대상 파생 텍스트 필드(원문 인용 필드 제외).
_XREF_FIELDS = ("summary", "key_points", "note", "implication")


def _xref_mark(value: Any) -> Any:
    """파생 텍스트 항목 표기에 「지식」 부착. 문자열·리스트·{role,text} 대상."""
    if isinstance(value, str):
        return _XREF_ARROW.sub("→ 관련 지식항목", _XREF_WORD.sub("지식항목", value))
    if isinstance(value, list):
        return [_xref_mark(v) for v in value]
    if isinstance(value, dict):
        return {k: (_xref_mark(v) if k == "text" else v) for k, v in value.items()}
    return value


def link_xrefs(records: list[dict], target: str, index: dict[str, dict[str, str]],
               report: list[str]) -> list[dict]:
    """항목 상호참조 표기 + `refs` 연결. `target` 은 번호가 가리키는 종류, 미해소 번호는 report 에 기록."""
    for rec in records:
        fields = rec.get("fields") or {}
        for key in _XREF_FIELDS:
            if key in fields:
                fields[key] = _xref_mark(fields[key])
        blob = json.dumps({k: fields.get(k) for k in _XREF_FIELDS}, ensure_ascii=False)
        refs: list[str] = []
        for run in _XREF_NUMS.findall(blob):
            for num in re.split(r"[·,]", run):
                hit = index.get(target, {}).get(num.strip())
                if hit is None:
                    report.append(f"[미해소참조] {rec['id']} → {target} 항목 {num.strip()}")
                elif hit != rec["id"] and hit not in refs:
                    refs.append(hit)          # 자기 참조 제외
        if refs:
            rec["refs"] = refs
    return records


def xref_index(*groups: list[dict]) -> dict[str, dict[str, str]]:
    """종류별 «항목번호(fields.no) → 카드 id» 색인."""
    index: dict[str, dict[str, str]] = {}
    for group in groups:
        for rec in group:
            no = (rec.get("fields") or {}).get("no")
            if no is not None:
                index.setdefault(rec["id"].split(".")[0], {})[str(no)] = rec["id"]
    return index


def write(name: str, kind: str, title: str, records: list[dict], as_of: str,
          origin_dir: Path | None = None) -> Path:
    """생성 파일 하나 쓰기. `origin_dir` 은 meta.note 에 적을 원문 폴더(기본 추출지식)."""
    path = OUT_DIR / f"_draft_{name}.json"
    src_name = (origin_dir or EXTRACT).name
    doc = {
        "meta": {"kind": kind, "title": title, "as_of": as_of, "confidential": True,
                 "doc_id": name,
                 "note": f"src/scripts/kb_build/build_kb.py 가 {src_name}에서 생성한다. "
                         "직접 편집하지 말고 변환기·config 를 고쳐 다시 생성한다."},
        "records": records,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
