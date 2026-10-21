"""원천 문서 레지스트리(doc) · 출처 표기 → doc id 해석(DocResolver)."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.kb_build import config
from pension_agent.knowledge.similarity import ngram_sim

from scripts.kb_build.common import GUIDE_DIR, HOTTIP_DIR, KBTHINK_DIR, REPO, STARLEARN_DIR, note, record, redact


# 원천 문서 레지스트리(doc) — 행내가이드는 config 시드, 나머지는 front-matter·헤더. 작성자 실명 미수집

#: 제목 바로 아래 부제 줄(`*Level 1 · 2주차 · …*`).
_SUBTITLE = re.compile(r"^\*([^*].*?)\*$")

#: 부제에서 제목으로 올릴 판·차수 표기만(부서·시점은 시드에 있음).
_EDITION = re.compile(r"(Level\s*\d|\d+\s*주차|제\s*\d+\s*장|Vol\.?\s*\d|Series\s*\d)", re.I)


def _norm(text: str) -> str:
    """제목 비교용 정규화(공백·구두점 제거)."""
    return re.sub(r"[\s:：·/\-—–,()\[\]]", "", text)


def doc_title(path: Path) -> str | None:
    """원문 제목 — 선두 H1(여러 줄이면 연결) + 부제의 판·차수 표기(제목에 없는 것만, 괄호)."""
    heads: list[str] = []
    subtitle = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):
            if subtitle:          # 부제 뒤 H1 은 본문 절
                break
            heads.append(s[2:].strip())
            continue
        if not heads:             # 제목 앞 front-matter 등 건너뜀
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
    """doc 레코드 목록 + «파일 basename → doc id» 색인(변환본 경로 인용의 출처 해석용)."""
    records: list[dict] = []
    by_base: dict[str, str] = {}

    def add(rid: str, fields: dict, base: str) -> None:
        # front-matter 설명 줄의 작성자 실명·직급 제거
        cleaned = {k: (redact(v) if isinstance(v, str) and k in ("title", "note") else v)
                   for k, v in fields.items()}
        records.append(record(rid, "doc", cleaned))
        by_base[base] = rid

    for path in sorted(GUIDE_DIR.rglob("*.md")):
        seed = config.GUIDE_DOCS.get(path.stem)
        if seed is None:
            note(f"[doc미등록] 01 폴더 파일에 시드 없음: {path.name}")
            continue
        # 제목은 원문, 부서·시점은 시드. title_override 가 원문과 어긋나면 리포트
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
        # 작성자는 게시글 front-matter 표기 그대로(상수 대체 금지 — 출처 표시 왜곡)
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

    # 인사이트 — Hot Tip 50건 전수 분석 문서 한 편(독립 원천)
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


# 출처 해석 — 출처 표기 → doc id

# 제목 유사도 채택 문턱(같은 문서 0.35 이상, 다른 문서 0.2 이하)
_TITLE_MATCH = 0.32


class DocResolver:
    def __init__(self, by_base: dict[str, str], docs: list[dict]):
        self.by_base = by_base
        self.ids = set(by_base.values())
        self.titles = [(d["id"], d["fields"]["title"]) for d in docs if d["fields"].get("title")]
        self.unresolved: list[str] = []
        self.last: str | None = None   # "위와 동일" 용 직전 해석 결과

    def track(self, doc_id: str | None) -> str | None:
        if doc_id:
            self.last = doc_id
        return doc_id

    def resolve(self, attribution: str, owner: str = "") -> str | None:
        """출처 표기 한 줄 → doc id. 실패 시 None + unresolved 기록."""
        if not attribution:
            return None

        # "위와 동일"·"같은 문서" → 직전 출처
        if re.search(r"위와\s*동일|같은\s*(?:문서|파일)|상동", attribution):
            return self.last

        # ① 변환본 경로(파일명 = 원천 문서)
        for raw in re.findall(r"`([^`]+\.md)`", attribution):
            base = Path(raw.replace("\\", "/")).stem
            if base in self.by_base:
                return self.track(self.by_base[base])

        # ② Hot Tip 글번호
        post = re.search(r"(?:게시글|Hottip|핫팁|Hot Tip 게시글)\s*(\d{4,6})", attribution)
        if post and f"doc.hottip.{post.group(1)}" in self.ids:
            return self.track(f"doc.hottip.{post.group(1)}")

        # ③ 산문 표기 — 약칭 키워드
        for keyword, base in config.DOC_KEYWORDS:
            if keyword in attribution and base in self.by_base:
                return self.track(self.by_base[base])

        if "KBthink" in attribution or "KB Think" in attribution:
            num = re.search(r"KBthink\s*(\d{2})", attribution)
            cand = f"doc.kbthink.{num.group(1)}" if num else None
            if cand in self.ids:
                return self.track(cand)

        # ④ 「…」·『…』 안 문서명 ↔ 레지스트리 제목 유사도
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
