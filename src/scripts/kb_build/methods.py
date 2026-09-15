"""06/02 IRP관리방법론 → method.

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

from scripts.kb_build import config

from scripts.kb_build.common import EXTRACT, clean, record, redact, role_entries, topics_of, triggers_of
from scripts.kb_build.docs import DocResolver
from scripts.kb_build.parse import _DERIVATION, joined, parse_index, parse_items, split_fields


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
        method_id = f"m.{no.zfill(3) if parent is None else no}"
        # 상황이 비어 있는 문단형 항목(1·46~49·112~126번)은 액션 첫 절이 단서다. 둘 다 없으면
        # 주제 태그를 한 묶음으로 싣는다 — 원문에 실제로 나온 어휘라 지어낸 것이 아니다.
        topic_bag = " ".join(topics_of(title, situation, action))
        records.append(record(
            method_id, "method",
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
                "trigger_examples": (triggers_of(method_id, situation, action)
                                     or triggers_of(method_id, topic_bag)),
                "author_redacted": True,
            },
            source={"doc": primary,
                    "locator": f"{config.EXTRACT_REL}/02_IRP관리방법론.md § {no}. {title}"},
        ))
    return records
