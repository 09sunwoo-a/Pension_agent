"""원천 문서 레지스트리(doc) · 출처 표기 → doc id 해석(DocResolver).

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.kb_build import config
from pension_agent.knowledge.similarity import ngram_sim

from scripts.kb_build.common import GUIDE_DIR, HOTTIP_DIR, KBTHINK_DIR, REPO, STARLEARN_DIR, note, record, redact


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
