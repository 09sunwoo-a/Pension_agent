"""06/01 고객세그먼트 → segment.

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

from scripts.kb_build import config

from scripts.kb_build.common import EXTRACT, clean, record, redact, topics_of, triggers_of
from scripts.kb_build.docs import DocResolver
from scripts.kb_build.parse import _DERIVATION, joined, parse_index, parse_items, split_fields


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
        seg_id = f"seg.{no.zfill(2) if parent is None else no}"
        records.append(record(
            seg_id, "segment",
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
                # 세그먼트 이름(제목, 목록 한 줄에 이미 있다)과 조건문·이유 자체다.
                "trigger_examples": triggers_of(seg_id, condition, reason),
                # 원문 임계값과 코드 판정의 차이 기록. 역할까지 config 에서 사람이 정한다 —
                # 상담 중 알아야 오안내를 피하는 차이(caution)와 참고 설명(info)이 갈린다.
                "note": ([dict(config.SEGMENT_NOTES[no])]
                         if no in config.SEGMENT_NOTES else None),
            },
            source={"doc": primary, "locator": f"{config.EXTRACT_REL}/01_고객세그먼트.md § {no}. {title}"},
        ))
    return records
