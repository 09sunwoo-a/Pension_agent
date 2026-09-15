"""변환기 공통 — 경로 상수 · 리포트(note) · 텍스트 유틸 · 주의/비고 역할 분류 · 레코드 · 상호참조 · 쓰기.

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scripts.kb_build import config


# 경로는 전부 config 가 폴더 번호와 무관하게 해석한다(config.kb_folder). 여기에 `05_…` 같은
# 번호 붙은 문자열을 다시 적지 않는다 — 재번호될 때마다 이 파일이 죽는다.
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


# ─────────────────────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────────────────────

_PII = [(re.compile(p), r) for p, r in config.PII_PATTERNS]


def redact(text: str) -> str:
    """작성자 실명·부점·직급 표기를 지운다. 글번호·게시일은 출처라서 남긴다."""
    out = text
    for pat, repl in _PII:
        out = pat.sub(repl, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def clean(text: str) -> str:
    """마크다운 강조·링크를 걷어낸 평문. 원문 인용에는 쓰지 않고 요약·조건문에만 쓴다."""
    out = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)   # [텍스트](링크) → 텍스트
    out = re.sub(r"[*`_]+", "", out)
    out = out.replace("　", " ").replace("└", "").replace("<br>", " ")
    return re.sub(r"\s{2,}", " ", out).strip()


def sentences(text: str, limit: int = 4) -> list[str]:
    """요약을 직원용 불릿으로 쪼갠다. 번호 매김('(1)')과 문장 끝을 경계로 본다.

    쪼갠 뒤 짧은 조각은 다음 조각에 다시 붙인다 — "본부 교육 영상은" 처럼 번호 앞의 도입구만
    남으면 그 자체로는 읽을 수 없는 불릿이 되기 때문이다.
    """
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


#: 절 구분자. 쉼표는 **숫자 사이의 자릿수 구분("1,800만원")이 아닐 때만** 절을 가른다 —
#: 이전 정규식 `[,·]` 은 그 쉼표에서도 잘라 팩트 11장·세그먼트 7장의 검색 예시가
#: 「개인형IRP의 연간 납입한도는 1」처럼 숫자 중간에서 끊겼다. 가운뎃점(·)은 절 구분자가
#: 아니라 명사 나열("연금저축·DC")이라 더는 가르지 않는다 — "IRP·DC 적립금 중 …" 이
#: 「IRP」에서 잘려 8자 미만으로 버려지던 자리다. 문장 끝(". ")과 줄표(" — ")는 가른다.
#: "또는"·"그리고"는 양쪽에 공백이 있을 때만 절 구분자다 — "연금저축(또는 타사 IRP)" 의 괄호
#: 안은 나열이지 절이 아니다.
_CLAUSE_SPLIT = re.compile(r"(?<!\d),|,(?!\d)|\s—\s|(?<=\.)\s|\s또는\s|\s그리고\s")


def first_clause(text: str, limit: int = 70) -> str | None:
    """본문의 첫 절 — 검색 예시로 쓴다. 8자 미만인 절은 건너뛰고 다음 절을 본다. 너무 길면 자른다.

    「데이터 — '25.11~'26.4 이탈고객 분석: …」처럼 앞에 짧은 표지가 붙은 본문은 표지를
    건너뛰어야 내용이 있는 절이 잡힌다.
    """
    if not text:
        return None
    for head in _CLAUSE_SPLIT.split(text.strip()):
        head = head.strip().rstrip(".")
        if len(head) < 8:
            continue
        if len(head) > limit:
            # 낱말 중간에서 끊지 않는다 — 「…습관이 돼 있」처럼 끝이 잘리면 검색 예시로 읽히지 않는다.
            cut = head.rfind(" ", limit // 2, limit + 1)
            head = head[:cut if cut > 0 else limit].rstrip(" ,:;(")
        return head
    return None


def triggers_of(card_id: str, *texts: str | None, limit: int = 3) -> list[str]:
    """카드의 검색 예시(trigger_examples) — 본문 첫 절들 + config.TRIGGER_EXTRA.

    **제목은 넣지 않는다.** 카드 목록 한 줄(`consult_agent/evidence/kb_index.py::_card_line`)은 제목 뒤에
    예상질문을 최대 2개만 싣는다. 제목이 첫 칸을 차지하면 LLM 이 보는 정보 칸은 하나뿐이다
    (2026-09-04 실측: 633장 중 388장이 그랬다). 제목은 이미 한 줄 앞에 있고, n-gram 폴백은
    `kb.score_parts` 가 제목을 예상질문과 같은 방식으로 함께 잰다.

    질문 문형("…는 어떻게 되나요?")을 만들어 붙이지 않는 규약은 그대로다 — 일반 문형은
    n-gram 유사도가 문형만 보고 무관한 질문을 끌어당긴다(`useful_trigger` 의 사고 기록).
    여기 실리는 것은 전부 **원문 본문의 절**이거나, 사람이 원문 주제어로 적은 TRIGGER_EXTRA 다.
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
    """검색 태그. 어휘를 config 에 고정해 두고 본문에 실제로 나온 것만 단다."""
    blob = " ".join(t or "" for t in texts)
    return [t for t in config.TOPIC_VOCAB if t in blob]


# ─────────────────────────────────────────────────────────────
# 주의·비고의 역할 분류 (knowledge/CLAUDE.md 관계 3)
#
# 원문의 비고·⚠ 유의에는 성격이 다른 두 가지가 섞여 있다 — 상담 중 지켜야 할 주의와,
# 지식베이스 저작·검증 메모("판독 불확실", "PDF 미수록 → 확인 필요"). 후자가 직원 답변에
# 그대로 실리면 직원에게 쓸모없는 문장이 뜨고 진짜 주의가 그 사이에 묻힌다. 그래서 역할을
# **데이터에 선언**해 두고, 소비 코드(guard·tools)는 선언만 본다 — 예전에는 소비 코드가
# 문자열 휴리스틱(guard._AUTHORING)으로 런타임에 걸렀는데, 그러면 분류 결과가 어디에도
# 남지 않아 검토할 수 없고, 거르는 곳이 여러 군데면 한 곳만 고쳐진다.
#
# 역할 어휘:
#   caution   상담 중 지켜야 할 주의. 답변에 반드시 실린다(notices).
#   info      직원에게 보여도 되는 참고 비고. 답변 재료에 실린다.
#   authoring 저작·검증 메모. 직원에게 띄우지 않는다.
#
# 분류는 규칙 → 빌드 리포트 검토 → config 예외표 순서다. 규칙이 틀리는 항목은
# config.*_NOTE_ROLES 에 사람이 역할을 지정하고 다시 생성한다(멱등).
# ─────────────────────────────────────────────────────────────

#: 저작·검증 메모의 표지. guard._AUTHORING 이 쓰던 6개를 흡수해 확장한 것 —
#: 원문 비고·유의의 실제 표현에서 왔다("판독 갈림", "PDF 미수록", "오기 추정" …).
_AUTHORING_MARKS = ("필자", "팀 논의", "팀 확인", "팀 검증", "확인 필요", "현행 여부",
                    "표기", "상충", "미수록", "수록 범위 밖", "판독", "화면번호안내PDF",
                    "추정", "해소", "오기", "불일치")


def role_entries(raw: str, default: str, override: list[dict] | None = None,
                 owner: str = "") -> list[dict]:
    """비고·주의 한 칸 → `[{"role", "text"}]`.

    규칙: ① config 예외가 있으면 그대로 쓴다(단 예외의 text 가 원문 칸에 없으면 리포트 —
    원문이 바뀌었는데 예외표가 낡은 채 남는 것을 잡는다). ② 저작 표지가 있으면 authoring.
    ③ 원문이 굵게(`**…**`) 강조했으면 caution. ④ 나머지는 종류별 기본값.

    칸 하나에 주의와 저작 메모가 섞인 경우는 규칙으로 못 가른다 — 그 칸은 예외표에서
    사람이 쪼갠다(config.SCREEN_NOTE_ROLES 참고).
    """
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
    """레코드 한 건. 값이 없는 선택 필드는 null 로 쓰지 않고 아예 뺀다.

    소비 쪽은 전부 `.get(...)` 으로 읽으므로 '없음'의 뜻은 같은데, null 을 남기면 리스트 필드를
    `p.get("dialogue", [])` 처럼 기본값과 함께 읽는 코드가 None 을 받아 터진다(실제로 겪었다).
    """
    rec = {"id": rid, "kind": kind,
           "fields": {k: v for k, v in fields.items() if v is not None}}
    if source:
        rec["source"] = {k: v for k, v in source.items() if v is not None}
    return rec


def inherit_parent_source(records: list[dict]) -> list[dict]:
    """하위 항목(12-1·1-1 …)의 출처를 상위 항목에서 잇는다.

    05 문서는 원문 인용을 상위 항목에만 달고 하위 항목은 조건·액션만 적는다. 그래서 하위 항목의
    출처가 비는데, 실제로는 같은 문서에서 온 같은 갈래다 — 지어내는 게 아니라 문서 구조를 반영한다.
    """
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


# ━━ 항목 상호참조 — 「항목 41·48」을 id 로 올린다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 06 원문은 다른 항목을 가리킬 때 「→ 항목 41·48」·「(항목 41 참조)」로 적는다. 그 번호는
# **지식베이스 안의 항목 번호**이지 단말 화면의 값이 아닌데, 파생 텍스트에 맨숫자로 남으면
# 답변이 그것을 화면의 값으로 읽는다. 실제로 나갔던 문장이 이렇다 —
#
#   표A 원문:  거래구분 ① 과세이연/계약이전입금 ② ISA 만기자금 입금 → 항목 41·48
#   답변:      거래구분에서 'ISA 만기자금 입금'(항목 48)을 선택하면 …
#
# 41·48 은 proc.041(과세이연 입금 5단계)·proc.048(ISA 만기자금 입금)을 가리키는 포인터인데,
# 답변은 그것을 단말에서 고르는 항목번호로 옮겼다. 직원은 단말에서 48번을 찾게 된다.
# **검증기는 못 잡는다** — "48" 이 근거 안에 실제로 있으므로 수치 검사를 그대로 통과한다
# (숫자의 존재만 보고 그 숫자가 무엇의 번호인지는 보지 않는다).
#
# 그래서 두 가지를 한다. ① 파생 텍스트의 표기를 「지식항목 N」으로 바꿔 무엇의 번호인지
# 스스로 밝히게 하고 ② 번호를 `refs`(관계 §5 — knowledge/CLAUDE.md)로 올려 깨진 참조를
# 검증기가 잡게 한다. 원문(quotes·source_text)은 건드리지 않는다(루트 절대 규칙 1).

#: 파생 텍스트에서 「항목 41」을 「지식항목 41」로. 뒤에 숫자가 오는 것만 바꾸고("이 항목을"은
#: 그대로), 이미 붙은 것은 다시 붙이지 않는다 — 변환기는 몇 번을 돌려도 같아야 한다.
_XREF_WORD = re.compile(r"(?<!지식)항목(?=\s*\d)")

#: 표A 의 「… → 항목 41·48」은 칸 끝에 붙는 **참고 포인터**다. 그 화살표가 이 카드에서 가장
#: 위험한 자리다 — 앞에 「거래구분 ①②」처럼 단말에서 실제로 고르는 순번이 서 있어서, 화살표
#: 뒤의 번호가 그 순번의 연장으로 읽힌다. 「관련」을 붙여 무엇인지 못박는다.
_XREF_ARROW = re.compile(r"→\s*(?!관련)지식항목")

#: 「항목 41·48」·「항목 1·2·5·6·16」처럼 번호가 이어 붙는 형태까지 읽는다.
_XREF_NUMS = re.compile(r"지식항목\s*(\d+(?:\s*[·,]\s*\d+)*)")

#: `refs` 를 찾을 파생 텍스트 필드. 원문 인용 필드는 여기 없다 — 원문은 고치지 않는다.
_XREF_FIELDS = ("summary", "key_points", "note", "implication")


def _xref_mark(value: Any) -> Any:
    """파생 텍스트의 항목 표기에 「지식」을 붙인다. 문자열·리스트·{role,text} 를 함께 훑는다."""
    if isinstance(value, str):
        return _XREF_ARROW.sub("→ 관련 지식항목", _XREF_WORD.sub("지식항목", value))
    if isinstance(value, list):
        return [_xref_mark(v) for v in value]
    if isinstance(value, dict):
        return {k: (_xref_mark(v) if k == "text" else v) for k, v in value.items()}
    return value


def link_xrefs(records: list[dict], target: str, index: dict[str, dict[str, str]],
               report: list[str]) -> list[dict]:
    """항목 상호참조를 표기하고 `refs` 로 올린다.

    `target` 은 **번호가 가리키는 종류**다 — 같은 원문 파일 안의 번호이기 때문이다. 05 에서
    나온 셋(procedure·screen·channel)은 전부 05 의 절차 항목을 가리키므로 `proc` 하나다.

    해소하지 못한 번호는 조용히 버리지 않고 리포트에 남긴다 — 지어내지 않는 것과 같은
    이유로, 못 이은 것은 못 이었다고 보여야 다음 저작자가 확인한다.
    """
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
                    refs.append(hit)          # 자기 자신은 참조로 세우지 않는다
        if refs:
            rec["refs"] = refs
    return records


def xref_index(*groups: list[dict]) -> dict[str, dict[str, str]]:
    """종류별 «항목번호 → 카드 id» 색인. 번호는 카드가 `fields.no` 로 이미 갖고 있다."""
    index: dict[str, dict[str, str]] = {}
    for group in groups:
        for rec in group:
            no = (rec.get("fields") or {}).get("no")
            if no is not None:
                index.setdefault(rec["id"].split(".")[0], {})[str(no)] = rec["id"]
    return index


def write(name: str, kind: str, title: str, records: list[dict], as_of: str,
          origin_dir: Path | None = None) -> Path:
    """생성 파일 하나. `origin_dir` 은 이 파일이 어느 원문 폴더에서 나왔는지 — meta.note 에
    적어 둔다. 종류마다 원문 폴더가 다른데 note 가 06 을 상수로 말하면, 05 에서 나온 파일을
    보고 06 을 고치러 가게 된다(생성물을 손으로 고치지 말라는 안내가 엉뚱한 곳을 가리킨다)."""
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
