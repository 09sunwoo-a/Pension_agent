"""주제별 추출지식 폴더 → consult_agent/data 레코드 변환기.

06 폴더의 추출지식 문서는 사람이 읽는 검토용 마크다운이다. 그 안의 항목을 지식베이스 레코드
(kinds.json 선언)로 결정론적으로 옮긴다. 손으로 옮기지 않는 이유는 두 가지다 — 항목이 수백 건이라
누락·오타가 생기고, 원문이 개정될 때마다 다시 대조할 방법이 없어진다.

원칙
  · 결정론·멱등 — 같은 입력이면 같은 출력. 생성 JSON 을 손으로 고치지 않는다(고칠 값은 config.py 로).
  · 무손실 출처 — 원문의 출처 표기 줄을 `source_text` 로 그대로 보존하고, 해석에 성공한 것만
    `doc` 레지스트리 id 로 함께 남긴다. 해석 실패는 값을 지어내지 않고 리포트로 알린다.
  · 검토 게이트 — `_draft_` 접두로 쓴다(common/kb_base.iter_knowledge_files 가 `_` 파일을 건너뛴다).
    사람이 확인한 뒤 `--activate` 로 접두를 뗀다.
  · 개인정보 미이관 — 작성자 실명·부점·직급은 옮기지 않는다(AUTHORING.md §4). 출처 추적은
    doc 레지스트리의 글번호·게시일로 한다.

실행
    python build_kb.py            # _draft_kb_{docs,segments,pitches}.json 생성 + 리포트
    python build_kb.py --activate # 검토 끝난 _draft_ 파일을 활성화(접두 제거)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from scripts.kb_build import config

from pension_agent.knowledge.similarity import ngram_sim

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


def first_clause(text: str, limit: int = 70) -> str | None:
    """조건문의 첫 절 — 세그먼트 검색 예시로 쓴다. 너무 길면 자른다."""
    if not text:
        return None
    head = re.split(r"[,·]|또는|그리고", text.strip())[0].strip()
    return head[:limit] if len(head) >= 8 else None


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


def write(name: str, kind: str, title: str, records: list[dict], as_of: str) -> Path:
    path = OUT_DIR / f"_draft_{name}.json"
    doc = {
        "meta": {"kind": kind, "title": title, "as_of": as_of, "confidential": True,
                 "doc_id": name,
                 "note": "src/tools/kb_build/build_kb.py 가 06_주제별_추출지식에서 생성한다. "
                         "직접 편집하지 말고 변환기·config 를 고쳐 다시 생성한다."},
        "records": records,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────
# 1) 원천 문서 레지스트리 (doc)
#
# 01 폴더는 문서 안에 부서·시점 표기가 일정하지 않아 config 시드를 쓰고, 02·03·04 는 파일의
# front-matter·헤더에서 그대로 읽는다. 작성자 실명은 읽지 않는다.
# ─────────────────────────────────────────────────────────────

#: 제목 바로 아래의 부제 줄(`*Level 1 · 2주차 · …*`). 마스터북이 권·주차를 여기 적는다.
_SUBTITLE = re.compile(r"^\*([^*].*?)\*$")

#: 부제에서 제목으로 끌어올릴 조각 = **판·차수 표기만**. 부제 줄에는 발행정보를 적는 문서도
#: 있는데(리밸런싱: "*연금사업본부 · 2021.2 · KB국민은행*"), 부서·시점은 시드가 이미 갖고
#: 있으므로 제목에 섞으면 같은 값을 두 번 말하게 된다.
_EDITION = re.compile(r"(Level\s*\d|\d+\s*주차|제\s*\d+\s*장|Vol\.?\s*\d|Series\s*\d)", re.I)


def _norm(text: str) -> str:
    """제목 비교용 정규화 — 표기 흔들림(공백·구두점)만 걷어낸다."""
    return re.sub(r"[\s:：·/\-—–,()\[\]]", "", text)


def doc_title(path: Path) -> str | None:
    """**문서가 스스로 밝힌 제목.** 선두 H1(들) + 부제.

    제목을 사람이 다시 타이핑하면 조용히 원문과 어긋난다. 실제로 그래서 Series1 의
    「IRP야, KB를 떠나지 마오!」가 통째로 빠진 채 답변 출처로 나가고 있었다 — 행원이
    그 이름으로는 원문을 찾을 수 없다. 그래서 제목만은 원문에서 읽는다.

    · H1 이 여러 줄인 문서가 있다(4주차는 "마스터북 — Level 1 / 4주차" + "계약이전 화법
      (보험/증권)"). 첫 인용문·구분선 전까지의 H1 을 이어붙인다.
    · 부제에서는 **판·차수 표기만** 끌어올려 괄호로 덧붙인다(2주차 → "(Level 1 · 2주차)").
      이미 제목에 있는 말은 두 번 적지 않고, 부서·시점(시드가 가진 값)은 가져오지 않는다.
    """
    heads: list[str] = []
    subtitle = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):
            if subtitle:          # 부제가 나온 뒤의 H1 은 제목이 아니라 본문 절이다
                break
            heads.append(s[2:].strip())
            continue
        if not heads:             # 제목 앞의 머리말(front-matter 등)은 건너뛴다
            continue
        m = _SUBTITLE.match(s)
        if m and not subtitle:
            subtitle = m.group(1).strip()
            continue
        break                     # 인용문·구분선·본문 → 제목 영역 끝
    if not heads:
        return None
    title = " ".join(heads)
    extra = [seg.strip() for seg in subtitle.split("·")
             if _EDITION.search(seg) and _norm(seg) not in _norm(title)]
    return f"{title} ({' · '.join(extra)})" if extra else title


def _front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        key, sep, val = line.partition(":")
        if sep and key.strip():
            out[key.strip()] = val.strip()
    return out


def build_docs() -> tuple[list[dict], dict[str, str]]:
    """doc 레코드 목록과 '파일 basename → doc id' 색인을 만든다.

    색인은 05 문서가 인용하는 변환본 경로를 출처로 되짚는 데 쓴다 — 경로가 곧 원천 문서라
    문자열 추측 없이 정확히 연결된다.
    """
    records: list[dict] = []
    by_base: dict[str, str] = {}

    def add(rid: str, fields: dict, base: str) -> None:
        # 원본 front-matter 의 설명 줄에 작성자 실명·직급이 섞여 들어온다(예: 연수 교안의
        # "(2025.02, ○○○ 차장)"). 레지스트리도 저작 산출물이므로 같은 규칙으로 지운다.
        cleaned = {k: (redact(v) if isinstance(v, str) and k in ("title", "note") else v)
                   for k, v in fields.items()}
        records.append(record(rid, "doc", cleaned))
        by_base[base] = rid

    for path in sorted(GUIDE_DIR.rglob("*.md")):
        seed = config.GUIDE_DOCS.get(path.stem)
        if seed is None:
            note(f"[doc미등록] 01 폴더 파일에 시드 없음: {path.name}")
            continue
        # 제목은 원문에서, 부서·시점은 시드에서. 원문이 제목만으로 문서를 특정하지 못하는
        # 경우에만 시드가 title_override 로 이긴다 — 그 override 가 원문과 어긋나면 리포트가
        # 알린다(손으로 적은 제목이 조용히 낡는 것이 이 검사가 막으려는 사고다).
        title = doc_title(path)
        override = seed.get("title_override")
        if override:
            if (title and _norm(title) not in _norm(override)
                    and not seed.get("title_override_reason")):
                note(f"[제목불일치] {path.name} — title_override '{override}' 가 "
                     f"원문 제목 '{title}' 을 담지 않음(사유 선언도 없음)")
            title = override
        elif not title:
            note(f"[제목없음] {path.name} — 원문에 H1 이 없어 파일명으로 대체")
            title = path.stem.replace("_", " ")
        add(f"doc.{path.stem}", {
            "title": title,
            **{k: v for k, v in seed.items()
               if k not in ("title_override", "title_override_reason")},
            "origin": "행내가이드", "tier": config.TIER_BY_ORIGIN["행내가이드"],
            "customer_facing": False, "origin_file": path.name,
            "path": str(path.relative_to(REPO)).replace("\\", "/"),
        }, path.stem)

    for path in sorted(STARLEARN_DIR.rglob("*.md")):
        if path.name.startswith("_"):
            continue
        fm = _front_matter(path.read_text(encoding="utf-8"))
        title = fm.get("title") or path.stem.replace("_", " ")
        add(f"doc.{path.stem}", {
            "title": title, "dept": "KB StarLearn 직원교육", "published": None,
            "origin": "스타런교육", "tier": config.TIER_BY_ORIGIN["스타런교육"],
            "customer_facing": False, "origin_file": fm.get("source_file") or path.name,
            "path": str(path.relative_to(REPO)).replace("\\", "/"),
            "note": fm.get("origin") or None,
        }, path.stem)

    for path in sorted(HOTTIP_DIR.glob("*.md")):
        fm = _front_matter(path.read_text(encoding="utf-8"))
        no = fm.get("글번호") or path.stem.split("_")[1]
        # 작성자는 게시글 프론트매터의 표기를 그대로 옮긴다. 한때 여기에 "영업점(작성자 정보
        # 미기재)" 라는 상수가 박혀 있었는데, 실명·부점·직급이 게시글에 **적혀 있는데도**
        # 미기재라고 말하는 표시였고 부점도 틀렸다(인재개발부 게시글이 "영업점"으로 나갔다).
        # 출처 표시가 사실과 다른 것은 근거 없는 답변과 같은 문제다.
        author = (fm.get("작성자") or "").strip()
        add(f"doc.hottip.{no}", {
            "title": fm.get("제목") or path.stem, "short": f"핫팁 {no}",
            "dept": author if author and author != "(미지정)" else "영업점(작성자 미상)",
            "published": fm.get("작성일"),
            "origin": "영업점핫팁", "tier": config.TIER_BY_ORIGIN["영업점핫팁"],
            "customer_facing": False, "post_no": no, "url": fm.get("원문 URL"),
            "path": str(path.relative_to(REPO)).replace("\\", "/"),
            "note": "KB StarLearn 「나만의 Hot Tip」 게시글. 작성자 표기는 게시글 프론트매터 그대로다.",
        }, path.stem)

    for path in sorted(KBTHINK_DIR.glob("*.md")):
        if path.stem == "README":
            continue
        text = path.read_text(encoding="utf-8")
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), path.stem)
        url = re.search(r"\((https://kbthink[^)]+)\)", text)
        posted = re.search(r"게시일\s*(\d{2})\.(\d{2})\.(\d{2})", text)
        add(f"doc.kbthink.{path.stem.split('_')[0]}", {
            "title": title, "short": f"KBthink {path.stem.split('_')[0]}",
            "dept": "KB국민은행(대외 공개)",
            "published": f"20{posted.group(1)}-{posted.group(2)}" if posted else None,
            "origin": "KBthink", "tier": config.TIER_BY_ORIGIN["KBthink"],
            "customer_facing": True, "url": url.group(1) if url else None,
            "path": str(path.relative_to(REPO)).replace("\\", "/"),
            "note": "원문을 그대로 옮기지 않은 정리본. 고객 안내문 인용 시 URL 원문 표현 확인.",
        }, path.stem)

    # 08_인사이트는 개별 게시글이 아니라 50건을 전수 분석한 한 편의 문서라, 파일이 하나뿐이지만
    # 원천으로서는 독립적이다(현장 관찰의 출처).
    insight = config.INSIGHT_DIR / "01_현장의목소리_HotTip_50건.md"
    if insight.exists():
        add("doc.insight_hottip50", {
            "title": "현장의 목소리 — Hot Tip 50건 전수 분석", "short": "현장의목소리",
            "dept": "내부 분석", "published": "2026-08",
            "origin": "영업점핫팁", "tier": config.TIER_BY_ORIGIN["영업점핫팁"],
            "customer_facing": False, "origin_file": insight.name,
            "path": str(insight.relative_to(REPO)).replace("\\", "/"),
            "note": "영업점 Hot Tip 50건에서 뽑은 현장 관찰. 본부 공식 지침이 아니다.",
        }, insight.stem)

    return records, by_base


# ─────────────────────────────────────────────────────────────
# 2) 출처 해석 — 05 문서의 출처 표기 → doc id
# ─────────────────────────────────────────────────────────────

# 문서 제목 유사도 채택 문턱. 실측상 같은 문서를 가리키는 표기는 0.35 이상, 다른 문서는 0.2 이하로 갈린다.
_TITLE_MATCH = 0.32


class DocResolver:
    def __init__(self, by_base: dict[str, str], docs: list[dict]):
        self.by_base = by_base
        self.ids = set(by_base.values())
        self.titles = [(d["id"], d["fields"]["title"]) for d in docs if d["fields"].get("title")]
        self.unresolved: list[str] = []
        self.last: str | None = None   # "위와 동일" 표기를 잇기 위한 직전 해석 결과

    def track(self, doc_id: str | None) -> str | None:
        if doc_id:
            self.last = doc_id
        return doc_id

    def resolve(self, attribution: str, owner: str = "") -> str | None:
        """출처 표기 한 줄에서 doc id 를 찾는다. 못 찾으면 None(값을 지어내지 않는다)."""
        if not attribution:
            return None

        # "위와 동일"·"같은 문서" 는 바로 앞 인용의 출처를 잇는 표기다.
        if re.search(r"위와\s*동일|같은\s*(?:문서|파일)|상동", attribution):
            return self.last

        # ① 변환본 경로가 있으면 그게 가장 정확하다 — 파일명이 곧 원천 문서다.
        for raw in re.findall(r"`([^`]+\.md)`", attribution):
            base = Path(raw.replace("\\", "/")).stem
            if base in self.by_base:
                return self.track(self.by_base[base])

        # ② 영업점 Hot Tip 은 글번호로 특정된다.
        post = re.search(r"(?:게시글|Hottip|핫팁|Hot Tip 게시글)\s*(\d{4,6})", attribution)
        if post and f"doc.hottip.{post.group(1)}" in self.ids:
            return self.track(f"doc.hottip.{post.group(1)}")

        # ③ 산문 표기(06/01)는 약칭 키워드로 맞춘다.
        for keyword, base in config.DOC_KEYWORDS:
            if keyword in attribution and base in self.by_base:
                return self.track(self.by_base[base])

        if "KBthink" in attribution or "KB Think" in attribution:
            num = re.search(r"KBthink\s*(\d{2})", attribution)
            cand = f"doc.kbthink.{num.group(1)}" if num else None
            if cand in self.ids:
                return self.track(cand)

        # ④ 「…」·『…』 안의 문서명을 레지스트리 제목과 대조한다. 스타런 교육영상은 파일명과 제목이
        #    달라 키워드 표로는 못 잡히지만, 제목 자체가 표기에 그대로 들어 있어 유사도로 특정된다.
        named = re.findall(r"[「『]([^」』]{6,80})[」』]", attribution)
        best_id, best_score = None, 0.0
        for name in named:
            for doc_id, title in self.titles:
                score = ngram_sim(name, title)
                if score > best_score:
                    best_id, best_score = doc_id, score
        if best_score >= _TITLE_MATCH:
            return self.track(best_id)

        self.unresolved.append(f"{owner or '?'} :: {attribution[:100]}")
        return None


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


# ─────────────────────────────────────────────────────────────
# 4) 06/01 고객세그먼트 → segment
# ─────────────────────────────────────────────────────────────

def build_segments(resolver: DocResolver) -> list[dict]:
    src = EXTRACT / "01_고객세그먼트.md"
    lines = src.read_text(encoding="utf-8").splitlines()
    index = parse_index(lines, stop="## 1. ")
    body_start = next(i for i, ln in enumerate(lines) if ln.startswith("## 1. "))

    records: list[dict] = []
    for item in parse_items(lines, body_start):
        no = item["no"]
        parent = no.split("-")[0] if "-" in no else None
        meta = index.get(no) or index.get(parent or "", {})
        group = (meta or {}).get("group") or "미분류"
        fields, quotes = split_fields(item["body"])

        condition = joined(fields, "조건") or joined(fields, "머리말")
        reason = joined(fields, "이유")
        review = joined(fields, "검토메모")
        derivation = (meta or {}).get("derivation")
        if not derivation:
            m = _DERIVATION.search(" ".join(fields.get("검토메모", []) + fields.get("머리말", [])))
            derivation = m.group(1) if m else None

        scope = "참고" if group.startswith("[참고]") or "[참고]" in item["title"] else "사후관리"
        title = clean(item["title"]).removeprefix("[참고] ").strip()
        conds = config.SEGMENT_CONDS.get(no, [])

        quote_records = []
        for q in quotes:
            attribution = redact(q["source_text"])
            quote_records.append({
                "text": redact(q["text"]),
                "source_text": attribution or None,
                "doc": resolver.resolve(attribution, f"세그 {no}") if attribution else None,
            })

        primary = next((q["doc"] for q in quote_records if q["doc"]), None)
        records.append(record(
            f"seg.{no.zfill(2) if parent is None else no}", "segment",
            {
                "no": no, "title": title, "group": group,
                "derivation": derivation, "decision": (meta or {}).get("decision"),
                "condition_text": redact(condition) or None,
                "reason_text": redact(reason) or None,
                "quotes": quote_records,
                "review_note": redact(review) or None,
                "conds": conds,
                "profile_rule": [],
                "exclusions": config.SEGMENT_EXCLUSIONS.get(no, []),
                "scope": scope,
                "parent": f"seg.{parent.zfill(2)}" if parent else None,
                "tags": {"topics": topics_of(title, condition, reason)},
                # 화법과 같은 이유로 일반 질문 문형을 만들어 붙이지 않는다 — 세그먼트를 찾는 단서는
                # 세그먼트 이름과 조건문 자체다.
                "trigger_examples": [t for t in (title, first_clause(condition)) if t],
                # 원문 임계값과 코드 판정의 차이 기록. 역할까지 config 에서 사람이 정한다 —
                # 상담 중 알아야 오안내를 피하는 차이(caution)와 참고 설명(info)이 갈린다.
                "note": ([dict(config.SEGMENT_NOTES[no])]
                         if no in config.SEGMENT_NOTES else None),
            },
            source={"doc": primary, "locator": f"{config.EXTRACT_REL}/01_고객세그먼트.md § {no}. {title}"},
        ))
    return records


# ─────────────────────────────────────────────────────────────
# 5) 06/03 영업화법 → pitch
# ─────────────────────────────────────────────────────────────

_SPEAKER = re.compile(r"^\*\*\[(고객|키키\s?행원|행원)\]\*\*\s*(.+)$")
_QUOTED = re.compile(r"[\"“]([^\"”]{4,60})[\"”]")


def useful_trigger(text: str, title: str) -> bool:
    """검색 예시로 쓸 만한 고객 발화인지.

    대사에서 뽑은 발화라도 "어떻게 하면 되나요?"·"아, 그래요?" 처럼 어느 상담에나 나오는 말이 섞인다.
    이런 문장은 n-gram 유사도가 '질문 문형'만 보고 무관한 질문(예: "주택청약 금리 어떻게 되나요?")을
    끌어당긴다 — 실제로 회귀 테스트가 이걸로 깨졌다. 그래서 이 카드의 주제어를 담은 발화만 남긴다.
    """
    if len(re.sub(r"[^0-9A-Za-z가-힣]", "", text)) < 8:
        return False
    if any(term in text for term in config.TOPIC_VOCAB):
        return True
    return any(word in text for word in re.findall(r"[가-힣A-Za-z0-9]{3,}", title))


def _dialogue(quote_texts: list[str]) -> list[dict]:
    out: list[dict] = []
    for text in quote_texts:
        for line in text.splitlines():
            m = _SPEAKER.match(line.strip())
            if m:
                speaker = "고객" if m.group(1) == "고객" else "행원"
                out.append({"speaker": speaker, "text": clean(m.group(2))})
    return out


def build_pitches(resolver: DocResolver) -> list[dict]:
    src = EXTRACT / "03_영업화법.md"
    lines = src.read_text(encoding="utf-8").splitlines()
    index = parse_index(lines, stop="# 1부.")
    body_start = next(i for i, ln in enumerate(lines) if ln.startswith("# 1부."))

    records: list[dict] = []
    for item in parse_items(lines, body_start):
        no = item["no"]
        meta = index.get(no)
        if meta is None:
            note(f"[화법인덱스없음] {no}. {item['title'][:40]} — 그룹 미상, 건너뜀")
            continue
        group = meta["group"]
        spec = next((v for k, v in config.PITCH_GROUPS.items() if group.startswith(k)), None)
        if spec is None:
            note(f"[그룹매핑없음] {no}. 그룹 '{group}' — config.PITCH_GROUPS 에 없음, 건너뜀")
            continue
        spec = {**spec, **config.PITCH_OVERRIDES.get(no, {})}

        fields, quotes = split_fields(item["body"])
        summary = joined(fields, "정리") or joined(fields, "머리말")
        quote_records = []
        for q in quotes:
            attribution = redact(q["source_text"])
            quote_records.append({
                "text": redact(q["text"]),
                "source_text": attribution or None,
                "doc": resolver.resolve(attribution, f"화법 {no}") if attribution else None,
            })

        raw_title = clean(item["title"])
        title = raw_title.removeprefix("[참고] ").strip()
        scope = "참고" if raw_title.startswith("[참고]") else "사후관리"

        dialogue = _dialogue([q["text"] for q in quote_records])
        # 검색 예시는 '내용이 있는 말'만 쓴다 — 제목 안의 고객 발화 인용, 대사 속 고객 발화,
        # 그리고 제목의 상황 절("… 고객에게"). "어떻게 말하면 되나요?" 같은 일반 질문 꼬리를
        # 붙이면 n-gram 유사도가 질문 형태만 보고 무관한 질문에도 카드를 물어온다(실측 회귀).
        triggers = [t.strip() for t in _QUOTED.findall(raw_title)]
        triggers += [d["text"] for d in dialogue
                     if d["speaker"] == "고객" and useful_trigger(d["text"], raw_title)][:2]
        if not triggers:
            situation = title.split("→")[0].strip()
            if len(situation) >= 8:
                triggers = [situation]

        key_points = sentences(redact(summary))
        if not key_points:
            key_points = [title]
            note(f"[정리없음] pitch.k03.{no.zfill(3)} — 정리 문단을 찾지 못해 제목으로 대체")

        pair = None
        m = re.search(r"1부\s*(?:항목\s*)?(\d+)", summary)
        if m and m.group(1) != no:
            pair = f"pitch.k03.{m.group(1).zfill(3)}"

        tags = {
            "stage": spec["stage"],
            "customer_type": ["공통"],
            "topics": topics_of(title, summary),
        }
        if spec["type"] == "objection":
            tags["objection_type"] = spec["objection_type"]

        primary = next((q["doc"] for q in quote_records if q["doc"]), None)
        pid = f"pitch.k03.{no.zfill(3)}"
        # 시효성 수치 선언을 카드에 붙인다(config.RATE_SLOTS / CLAIM_CONDITIONS).
        # 원문(quotes·content)은 건드리지 않는다 — 슬롯 치환과 조건 판정은 전부 런타임 몫이다.
        rate_slots = [
            {"was": was, "rate_key": key, "what": what}
            for was, key, what in config.RATE_SLOTS.get(pid, [])
        ]
        claim = config.CLAIM_CONDITIONS.get(pid)
        records.append(record(
            pid, "pitch",
            {
                "title": title, "type": spec["type"], "tags": tags,
                "no": no, "group": group,
                "summary": redact(summary) or None,
                "key_points": key_points,
                "dialogue": dialogue or None,
                "content": None if dialogue else (quote_records[0]["text"] if quote_records else None),
                "quotes": quote_records,
                "trigger_examples": triggers[:3],
                "derivation": meta["derivation"],
                "scope": scope,
                "segments": [],
                "pair": pair,
                "rate_slots": rate_slots or None,
                "claim_condition": claim or None,
                "author_redacted": True,
            },
            source={"doc": primary, "locator": f"{config.EXTRACT_REL}/03_영업화법.md § {no}. {title}"},
        ))
    return records


# ─────────────────────────────────────────────────────────────
# 5-b) 06/02 IRP관리방법론 → method
#
# 03 화법과 구조가 같아 split_fields 를 그대로 쓴다. 다른 점은 필드 이름(상황/액션/주의)과,
# 인라인 불릿과 문단형이 섞여 있다는 것 — 문단형(1·46~49번)은 상황이 없고 본문 전체가 액션이다.
# ─────────────────────────────────────────────────────────────

def build_methods(resolver: DocResolver) -> list[dict]:
    src = EXTRACT / "02_IRP관리방법론.md"
    lines = src.read_text(encoding="utf-8").splitlines()
    index = parse_index(lines, stop="## [1. ")
    body_start = next(i for i, ln in enumerate(lines) if ln.startswith("## [1. "))

    records: list[dict] = []
    for item in parse_items(lines, body_start):
        no = item["no"]
        parent = no.split("-")[0] if "-" in no else None
        meta = index.get(no) or index.get(parent or "", {}) or {}
        group = meta.get("group") or "미분류"
        fields, quotes = split_fields(item["body"])

        situation = joined(fields, "상황")
        action = joined(fields, "액션")
        lead = joined(fields, "머리말")
        if not situation and not action:
            action = lead      # 문단형 항목 — 본문 자체가 판단 규칙이다

        derivation = meta.get("derivation")
        if not derivation:
            m = _DERIVATION.search(" ".join(fields.get("머리말", []) + fields.get("액션", [])))
            derivation = m.group(1) if m else None

        raw_title = clean(item["title"])
        title = raw_title.removeprefix("[참고] ").strip()
        scope = "참고" if group.startswith("[참고]") or raw_title.startswith("[참고]") else "사후관리"

        quote_records = []
        for q in quotes:
            attribution = redact(q["source_text"])
            quote_records.append({
                "text": redact(q["text"]),
                "source_text": attribution or None,
                "doc": resolver.resolve(attribution, f"방법론 {no}") if attribution else None,
            })

        primary = next((q["doc"] for q in quote_records if q["doc"]), None)
        records.append(record(
            f"m.{no.zfill(3) if parent is None else no}", "method",
            {
                "no": no, "title": title, "group": group,
                "situation": redact(situation) or None,
                "action": redact(action) or None,
                "cautions": role_entries(redact(joined(fields, "주의")), "caution",
                                         config.METHOD_CAUTION_ROLES.get(no),
                                         f"방법론 {no}") or None,
                "quotes": quote_records,
                "derivation": derivation,
                "scope": scope,
                "parent": f"m.{parent.zfill(3)}" if parent else None,
                "segments": [],
                "tags": {"topics": topics_of(title, situation, action)},
                "trigger_examples": [t for t in (title, first_clause(situation)) if t],
                "author_redacted": True,
            },
            source={"doc": primary,
                    "locator": f"{config.EXTRACT_REL}/02_IRP관리방법론.md § {no}. {title}"},
        ))
    return records


# ─────────────────────────────────────────────────────────────
# 5-c) 08_인사이트 → fieldtip
#
# 항목이 10개뿐이고 소제목이 고정이라 단순하다. 인용에 작성자 실명이 붙어 있어(「글 제목」(이름))
# redact 가 반드시 걸려야 한다.
# ─────────────────────────────────────────────────────────────

_TIP_AUTHOR = re.compile(r"[—-]\s*「[^」]+」\s*\([가-힣]{2,4}\)")


def build_fieldtips(resolver: DocResolver) -> list[dict]:
    path = config.INSIGHT_DIR / "01_현장의목소리_HotTip_50건.md"
    if not path.exists():
        note("[07없음] 08_인사이트 문서를 찾지 못해 fieldtip 을 만들지 않았다")
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    body_start = next((i for i, ln in enumerate(lines) if ln.startswith("## 1. ")), None)
    if body_start is None:
        note("[07파싱실패] 항목 시작(## 1.)을 찾지 못했다")
        return []

    doc_id = "doc.insight_hottip50"
    records: list[dict] = []
    for item in parse_items(lines, body_start):
        no = item["no"]
        fields, quotes = split_fields(item["body"])
        title = clean(item["title"])
        summary = joined(fields, "정리") or joined(fields, "머리말")
        implication = joined(fields, "시사점")

        # 인용은 `>` 블록이 아니라 불릿이라 split_fields 의 quotes 에 안 잡힌다 — 원문 라인에서 뽑는다.
        bullets = [clean(ln.lstrip("- ").strip()) for ln in item["body"]
                   if ln.strip().startswith("- ") and "「" in ln]
        quote_records = [{"text": redact(_TIP_AUTHOR.sub("", b).strip()), "source_text": None,
                          "doc": doc_id} for b in bullets]

        records.append(record(
            f"tip.{no.zfill(2)}", "fieldtip",
            {
                "no": no, "title": title,
                "summary": redact(summary) or None,
                "quotes": quote_records,
                "implication": redact(implication) or None,
                "segments": [],
                "tags": {"topics": topics_of(title, summary, implication)},
                "trigger_examples": [title],
                "author_redacted": True,
            },
            source={"doc": doc_id, "locator": f"{config.INSIGHT_REL}/01_현장의목소리_HotTip_50건.md § {no}. {title}"},
        ))
    return records


# ─────────────────────────────────────────────────────────────
# 6) 06/04 제도상품팩트 → fact
#
# 04 는 "팩트 1개 = 항목 1개 = 확정값 1개" 규격이라 필드가 규칙적이다. 다만 한 불릿에 여러
# 라벨이 ' · ' 로 이어져 있고(`- **상태**: ✅ 확정 · **성격**: 법령·세제 · **기준**: …`),
# 값 안에도 '·' 가 들어가므로("법령·세제") 구분자로 자르면 안 된다 — 라벨 위치로 구간을 자른다.
# ─────────────────────────────────────────────────────────────

_FACT_LABEL = re.compile(r"\*\*([^*]{2,10})\*\*\s*:\s*")


def normalize_screen(screen: str) -> str:
    """`[06-12-604]` → `06-12-604`. id 와 대조에 쓰는 표준형."""
    return (screen or "").strip().strip("[]").strip()
_SCREEN = re.compile(r"\[\d{2}-[0-9A-Z]{2}-[0-9A-Z]{3}\]")


def _labeled_slots(line: str) -> dict[str, str]:
    """`**라벨**: 값 · **라벨**: 값` 한 줄을 {라벨: 값} 으로 자른다."""
    marks = list(_FACT_LABEL.finditer(line))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        value = line[m.end():end].strip().rstrip("·").strip()
        out[re.sub(r"\s+", "", m.group(1))] = value
    return out


#: 검증 포인트를 항목으로 쪼개는 표지. 04 는 ①~⑩ 로 나눠 적는다(하나뿐이면 번호가 없다).
_PITFALL_SPLIT = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]\s*")

#: 검증 포인트 안의 따옴표 인용. 인용 자체는 옳은 말일 수도 있어서, 이것만으로는 오답이 아니다.
_QUOTED = re.compile(r"[\"“]([^\"“”]{4,80})[\"”]")

#: 인용 **뒤에** 이 말이 붙어 있어야 "그 표현은 틀렸다"는 뜻이다.
#
# 이 판정이 필요한 이유: 04 의 검증 포인트는 틀린 표현과 옳은 표현을 **똑같이 따옴표로**
# 인용한다. `기준은 "평가금액"(가장 정확)` 의 평가금액은 옳은 말이고, `9번("퇴직금 포함 시
# 5년 전 가능" → O)` 의 인용문은 참인 진술이다. 따옴표만 보고 오답 목록에 넣으면 **맞는
# 답을 막는** 검사가 된다 — 검증기가 옳은 문장을 거부하는 것은 틀린 문장을 통과시키는
# 것보다 나쁘다(직원은 왜 막혔는지 알 수 없다).
_WRONG_MARKS = ("오답", "오기", "오안내", "→ X", "→X", "말 것", "금지", "틀린", "혼동")

#: 인용 뒤 몇 글자까지 표지를 찾을지. 같은 항목 안의 **다음** 인용에 붙은 표지를 끌어오지
#: 않을 만큼 좁게 둔다.
_MARK_WINDOW = 25

#: 오답 문자열의 최소 길이. 아래 `_is_phrase` 참고.
_MIN_BARE = 8


def _is_phrase(text: str) -> bool:
    """오답 대조에 쓸 수 있는 **구절**인가. 값 하나만 있는 인용은 쓸 수 없다.

    대조는 답변 안에 그 문자열이 있는지로 하는데, 값 하나짜리 인용은 다른 팩트의 **맞는**
    문장에도 그대로 들어간다 — F22 의 오답 `"5천만원"`(2021년 실효된 예금자보호 한도)은
    F53 의 맞는 문장("퇴직금 5천만원 이상이면 수수료 면제")에도 있다. 그걸 오답으로 잡으면
    검증기가 옳은 답을 막는다.

    구절이면(띄어쓰기가 있거나 충분히 길면) 그런 우연한 일치가 사실상 없다. 값 하나짜리
    오답은 여기서 버린다 — 대조할 수 없는 것은 대조하지 않는다(선언이 없는 것과 같다).
    """
    return " " in text or len(text) >= _MIN_BARE


def _wrong_quotes(text: str) -> list[str]:
    """이 항목이 '틀렸다'고 지목한 표현만 뽑는다. 지목이 없으면 빈 목록."""
    out: list[str] = []
    for m in _QUOTED.finditer(text):
        tail = text[m.end():m.end() + _MARK_WINDOW]
        if "→ O" in tail or "→O" in tail:
            continue                       # 참이라고 표시된 인용
        quoted = m.group(1).strip()
        if any(w in tail for w in _WRONG_MARKS) and _is_phrase(quoted):
            out.append(quoted)
    return out


def _tiers(raw: str) -> list[dict]:
    """`조건 → 값; 조건 → 값` 한 줄을 조건–값 쌍으로 쪼갠다(knowledge/CLAUDE.md 관계 1).

    구분자를 `;` 로 둔 이유는 이 문서의 조건·값 텍스트에 `·` 가 흔히 들어가기 때문이다
    ("법령·세제", "운용관리·자산관리"). `·` 로 자르면 한 쌍이 둘로 쪼개진다.
    """
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
    """검증 포인트 산문을 항목으로 쪼갠다(관계 2).

    **내용을 새로 만들지 않는다** — 행원들이 이미 적어둔 "자주 틀리는 지점"을 기계가
    대조할 수 있는 단위로 나눌 뿐이다. 항목이 틀린 표현을 따옴표로 인용하고 있으면
    (`"5,500만원 이상 13.2%" = 오기`) 그 인용문을 `wrong` 으로 뽑는다 — 답변에 그 문자열이
    그대로 나타났는지가 곧 알려진 오답과의 일치다. **옳은 표현의 인용은 뽑지 않는다**
    (`_wrong_quotes` 주석). 지목이 없는 항목은 `wrong` 없이 주의 문장으로만 남는다 —
    대조할 문자열이 없으면 대조하지 않는다.
    """
    parts = [clean(x).strip() for x in _PITFALL_SPLIT.split(raw) if clean(x).strip()]
    out: list[dict] = []
    for text in parts:
        out.append({"wrong": _wrong_quotes(text), "why": text})
    return out


def _fact_status(raw: str) -> tuple[str, list[str]]:
    """상태 표기(✅ 확정 / ⚠ 확인 필요 / ⏳ 시효 민감, 복합 가능) → (대표 상태, 표시 전체)."""
    marks = [m for m in ("✅", "⚠", "⏳") if m in raw]
    label = {"✅": "확정", "⚠": "확인 필요", "⏳": "시효 민감"}
    primary = "확정" if "✅" in marks else ("시효 민감" if "⏳" in marks else "확인 필요")
    return primary, [label[m] for m in marks]


def build_facts(resolver: DocResolver) -> tuple[list[dict], list[dict]]:
    """활성 팩트와 보류 팩트를 나눠 돌려준다.

    04 자체 규칙이 "⚠ 확인 필요 항목은 해소 전까지 검증 기준으로 쓰지 않는다" 이므로, 확정 근거가
    없는 팩트는 활성 데이터에 넣지 않고 별도 보류 파일로 뺀다 — 지식베이스에 들어가는 순간
    에이전트가 그걸 사실로 답하기 때문이다.
    """
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
            # label 은 04 제목 전체를 쓴다. 레거시 fact 는 "연간 납입한도" 처럼 짧은 라벨이라,
            # 같은 주제라도 문자열이 달라 check_fact_conflicts 의 오탐이 나지 않는다. 값이 정말
            # 어긋나는지는 변환 리포트(_draft_kb_fact_review.md)로 사람이 본다.
            "label": title,
            "value": statement,
            "category": group,
            "as_of": slots.get("기준") or None,
            "status": status,
            "nature": slots.get("성격") or None,
            "customer_facing": "⭕" in slots.get("대외안내", ""),
            "verify_points": slots.get("검증포인트") or None,
            # 관계 선언(knowledge/CLAUDE.md §1·§2) — 답변이 값과 조건을 잘못 짝지었는지,
            # 알려진 오답을 그대로 말했는지 코드가 대조하는 재료다. 선언이 없는 팩트는
            # 대조 대상이 아니다(커버리지 = 저작된 범위).
            "tiers": _tiers(slots.get("조건별값", "")),
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
        key = normalize_screen(screen)
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
                "trigger_examples": [t for t in (title, first_clause(summary)) if t],
                "author_redacted": True,
            },
            source={"doc": primary,
                    "locator": f"{config.EXTRACT_REL}/05_업무처리절차.md § {no}. {title}"},
        ))
    return records


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def activate() -> None:
    moved = 0
    for path in sorted(OUT_DIR.glob("_draft_kb_*.json")):
        target = path.with_name(path.name.removeprefix("_draft_"))
        path.replace(target)
        print(f"  활성화 {path.name} → {target.name}")
        moved += 1
    print(f"{moved}개 파일 활성화" if moved else "활성화할 _draft_ 파일이 없습니다")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if "--activate" in sys.argv:
        activate()
        return 0

    docs, by_base = build_docs()
    resolver = DocResolver(by_base, docs)
    segments = inherit_parent_source(build_segments(resolver))
    pitches = build_pitches(resolver)
    facts, pending = build_facts(resolver)
    procedures = build_procedures(resolver)
    screens_ = build_screens(resolver)
    channels = build_channels(resolver)
    methods = inherit_parent_source(build_methods(resolver))
    fieldtips = build_fieldtips(resolver)

    write("kb_docs", "doc", "원천 문서 레지스트리 (01~04·07 폴더)", docs, "2026-08")
    write("kb_segments", "segment", "고객 세그먼트 — 06/01 고객세그먼트", segments, "2026-08")
    write("kb_methods", "method", "IRP 관리 방법론 — 06/02 IRP관리방법론", methods, "2026-08")
    write("kb_pitches", "pitch", "영업 화법 — 06/03 영업화법", pitches, "2026-08")
    write("kb_facts", "fact", "제도·상품 팩트 — 06/04 제도상품팩트", facts, "2026-08")
    write("kb_procedures", "procedure", "업무 처리 절차 — 06/05 업무처리절차", procedures, "2026-08")
    write("kb_screens", "screen", "단말 화면번호 일람 — 06/05 업무처리절차 표A", screens_, "2026-08")
    write("kb_channels", "channel", "비대면 채널 처리 경로 — 06/05 업무처리절차 표B",
          channels, "2025-03-31")
    write("kb_fieldtips", "fieldtip", "현장의 목소리 — 08_인사이트", fieldtips, "2026-08")
    # 보류 팩트는 활성화하지 않는다 — 04 규칙상 확인 전에는 검증 기준으로 쓸 수 없다.
    path = OUT_DIR / "_hold_kb_facts_pending.json"
    path.write_text(json.dumps(
        {"meta": {"kind": "fact", "title": "제도·상품 팩트(확인 필요) — 활성화 금지",
                  "as_of": "2026-08", "confidential": True,
                  "note": "06/04 가 '⚠ 확인 필요' 로 표시한 팩트. 근거가 확인되기 전에는 지식베이스에 "
                          "넣지 않는다 — 적재되는 순간 에이전트가 사실로 답하기 때문이다. "
                          "해소되면 06/04 원문을 고치고 변환기를 다시 돌린다."},
         "records": pending}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    built = (segments + methods + pitches + facts + procedures + screens_
             + channels + fieldtips)
    print(f"doc {len(docs)}건 · segment {len(segments)}건 · method {len(methods)}건 "
          f"· pitch {len(pitches)}건 · fact {len(facts)}건(보류 {len(pending)}건) "
          f"· procedure {len(procedures)}건 · screen {len(screens_)}건 "
          f"· channel {len(channels)}건 · fieldtip {len(fieldtips)}건 → {OUT_DIR}")
    matched = sum(1 for s in built if (s.get("source") or {}).get("doc"))
    print(f"출처 해석: {matched}/{len(built)}건에 원천 문서 연결")

    # 주의 역할 분류 리포트 — caution 은 답변에 강제 표시되는 문장이므로 활성화 전에 전부
    # 눈으로 검토한다. 규칙이 틀린 항목은 config 의 *_ROLES 예외표에 넣고 다시 생성한다.
    counts: dict[str, dict[str, int]] = {}
    forced: list[str] = []
    for kind_name, cards, field in (("segment", segments, "note"),
                                    ("method", methods, "cautions"),
                                    ("procedure", procedures, "cautions"),
                                    ("screen", screens_, "note"),
                                    ("channel", channels, "note")):
        for r in cards:
            for e in r["fields"].get(field) or []:
                counts.setdefault(kind_name, {})[e["role"]] = \
                    counts.setdefault(kind_name, {}).get(e["role"], 0) + 1
                if e["role"] == "caution":
                    forced.append(f"{r['id']:<18} {e['text'][:70]}")
    print("주의 역할 분류: " + " · ".join(
        f"{k}({', '.join(f'{role} {n}' for role, n in sorted(v.items()))})"
        for k, v in counts.items()))
    print(f"caution(답변 강제 표시) {len(forced)}건 — 활성화 전 검토 대상:")
    for line in forced:
        print("   " + line)
    conds = sum(1 for s in segments if s["fields"]["conds"])
    print(f"CONDS 매핑: {conds}건 (나머지는 conds=[] — 자동 매칭 제외, 검색에는 남음)")

    if resolver.unresolved:
        uniq = sorted(set(resolver.unresolved))
        print(f"\n⚠ 출처 미해석 {len(resolver.unresolved)}건 (고유 {len(uniq)}종) — source_text 로 원문 보존됨")
        for u in uniq[:12]:
            print(f"   · {u}")
        if len(uniq) > 12:
            print(f"   … 외 {len(uniq) - 12}종")
    if _report:
        print(f"\n⚠ 변환 알림 {len(_report)}건")
        for r in _report[:15]:
            print("   " + r)
        if len(_report) > 15:
            print(f"   … 외 {len(_report) - 15}건")

    print("\n검토 후 활성화: python build_kb.py --activate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
