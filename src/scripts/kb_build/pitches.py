"""06/03 영업화법 → pitch.

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

import re

from scripts.kb_build import config

from scripts.kb_build.common import EXTRACT, clean, note, record, redact, sentences, topics_of
from scripts.kb_build.docs import DocResolver
from scripts.kb_build.parse import joined, parse_index, parse_items, split_fields


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

        pid = f"pitch.k03.{no.zfill(3)}"
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
        # 보강표를 화법에도 적용한다 — `common.triggers_of` 를 쓰는 다른 종류는 이미 받고
        # 있었는데, 화법만 규칙이 달라(제목 인용 + 고객 발화) 이 줄이 없어서 **표에 적어도
        # 아무 일도 일어나지 않았다.** 규칙이 입구를 못 뽑는 화법 카드가 여기 걸린다:
        # 방치 화법(k03.020)의 고객 발화는 「아, 그래요? 신경 안서 잘 모르겠어요」라
        # `useful_trigger` 가 정당하게 떨어뜨리고, 손실 프레이밍(k03.018·045)은 대사에
        # 고객 발화가 아예 없어 제목 절 하나로 끝난다. 그래서 「그냥 두면 안 되나요」가
        # n-gram 폴백에서 **전부 0.000** 이었다(2026-09-21 실측).
        for extra in config.TRIGGER_EXTRA.get(pid, []):
            if extra not in triggers:
                triggers.append(extra)

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
