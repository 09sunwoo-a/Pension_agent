"""인사이트(현장의 목소리) → fieldtip."""

from __future__ import annotations

import re

from scripts.kb_build import config

from scripts.kb_build.common import clean, note, record, redact, topics_of, triggers_of
from scripts.kb_build.docs import DocResolver
from scripts.kb_build.parse import joined, parse_items, split_fields


# 인용에 작성자 실명(「글 제목」(이름)) — redact 필수
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

        # 인용은 불릿(`>` 아님) — split_fields 대신 원문 라인에서 추출
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
                # 정리·시사점 첫 절
                "trigger_examples": triggers_of(tip_id, redact(summary), redact(implication)),
                "author_redacted": True,
            },
            source={"doc": doc_id, "locator": f"{config.INSIGHT_REL}/01_현장의목소리_HotTip_50건.md § {no}. {title}"},
        ))
    return records
