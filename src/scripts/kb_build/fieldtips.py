"""08_인사이트 → fieldtip.

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

import re

from scripts.kb_build import config

from scripts.kb_build.common import clean, note, record, redact, topics_of, triggers_of
from scripts.kb_build.docs import DocResolver
from scripts.kb_build.parse import joined, parse_items, split_fields


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

        tip_id = f"tip.{no.zfill(2)}"
        records.append(record(
            tip_id, "fieldtip",
            {
                "no": no, "title": title,
                "summary": redact(summary) or None,
                "quotes": quote_records,
                "implication": redact(implication) or None,
                "segments": [],
                "tags": {"topics": topics_of(title, summary, implication)},
                # 다른 종류와 같은 규약 — 정리·시사점 첫 절. 오래 `[title]` 하나뿐이어서
                # 10장 전부가 제목 밖의 검색 단서가 없었다(README 「n-gram 폴백이 약하다」).
                "trigger_examples": triggers_of(tip_id, redact(summary), redact(implication)),
                "author_redacted": True,
            },
            source={"doc": doc_id, "locator": f"{config.INSIGHT_REL}/01_현장의목소리_HotTip_50건.md § {no}. {title}"},
        ))
    return records
