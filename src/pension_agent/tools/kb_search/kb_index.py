"""대화형 전용 지식베이스 도구 — 카드 선택용 계층 인덱스(버킷) · 프롬프트 컨텍스트 · 출처 표기.

적재·시효성·검색은 pension_agent.knowledge.kb 소유(재노출 금지).
"""

from __future__ import annotations

from pension_agent.knowledge.kb import KnowledgeBase, origin_of, source_url


# 버킷 = kind × group(모든 카드에 있는 유일한 축). L0 카탈로그 → L1 슬라이스 2단.

#: 버킷 코드 머리글자. 새 종류 적재 시 함께 추가 — 누락 시 X 로 묶여 카탈로그에서 구분 불가.
_BUCKET_LETTER = {"pitch": "P", "procedure": "R", "segment": "S", "method": "M",
                  "fieldtip": "F", "screen": "N", "channel": "C", "market": "K",
                  "lineup": "L"}

#: L0 카탈로그의 종류별 한 줄 설명. 적재 종류 전부 필수 — 누락 시 LLM 이 묶음 용도를 모름.
_KIND_DESC = {
    "pitch": "고객에게 실제로 하는 말 — 대사·논거·반론 대응",
    "fact": "제도·상품의 확정 수치 — 한도·세율·수수료",
    "procedure": "시스템에서 처리하는 절차 — 조회 경로·화면·처리 순서",
    "screen": "직원이 단말에서 여는 화면 — 화면번호·화면명",
    "channel": "고객이 앱·웹에서 직접 하는 경로 — 스타뱅킹·인터넷뱅킹 메뉴",
    "segment": "대상 고객을 고르는 조건 — 누구를 왜 관리 대상으로 뽑나",
    "method": "판단 방법론 — 무엇을 어떤 기준으로 정하나",
    "fieldtip": "영업점 현장 관찰 — 본부 지침이 아닌 참고",
    "market": "시장이 어떻게 돌아가나 — 시황·환율·금리·경제 이벤트와 투자전략",
    "lineup": "우리가 뭘 파나 — 추천펀드·디폴트옵션 포트폴리오·투자성향별·TDF 빈티지",
}

#: 버킷 카탈로그의 종류와 순서. 적재 종류 전부 필수 — 누락 시 LLM 후보에서 통째로 빠짐.
_KIND_ORDER = ("pitch", "fact", "procedure", "screen", "channel", "segment", "method",
               "fieldtip", "market", "lineup")

#: 인덱스 문자 예산(≈ 2k 토큰).
INDEX_BUDGET_CHARS = 4000

def sources_of(kb: KnowledgeBase, hits: list[tuple[float, dict]]) -> list[dict]:
    """검색 결과 → 근거 목록. doc 은 원문 문서 출처(id 는 역추적용)."""
    return [{"id": c["id"], "title": c.get("title") or c.get("label"),
             "doc": origin_of(kb, c), "url": source_url(kb, c),
             "score": round(s, 2), "page": c.get("page")} for s, c in hits]


def product_names(kb: KnowledgeBase) -> set[str]:
    """카드가 선언한 상품 이름 전부 — 상품명 실재 대조용 등록부. 추론 금지, 선언만."""
    return {n for c in kb.cards for n in (c.get("product_names") or []) if n}


def advisory_note(kb: KnowledgeBase) -> str | None:
    """카드가 선언한 투자권유 고지 문구(카드 외 재료용). 없으면 None — 코드 상수화 금지."""
    return next((c["advisory"] for c in kb.cards if c.get("advisory")), None)


_NO_GROUP = "(미분류)"


def _bucket_key(card: dict) -> tuple[str, str]:
    return card["_kind"], (card.get("group") or _NO_GROUP)


def buckets(kb: KnowledgeBase, kinds: tuple[str, ...] | None = None) -> dict[str, dict]:
    """버킷 코드 → {kind, group, cards}. 코드는 프롬프트 1회용 라벨(영속 식별자 아님)."""
    pool = kb.cards if kinds is None else [c for c in kb.cards if c["_kind"] in kinds]
    grouped: dict[tuple[str, str], list[dict]] = {}
    for c in pool:
        grouped.setdefault(_bucket_key(c), []).append(c)

    out: dict[str, dict] = {}
    for kind in _KIND_ORDER:
        groups = sorted(g for (k, g) in grouped if k == kind)
        letter = _BUCKET_LETTER.get(kind, "X")
        for n, group in enumerate(groups, 1):
            cards = sorted(grouped[(kind, group)], key=lambda c: c["id"])
            out[f"{letter}{n:02d}"] = {"kind": kind, "group": group, "cards": cards}
    return out


def index_catalog(kb: KnowledgeBase, kinds: tuple[str, ...] | None = None) -> str:
    """L0 — 버킷 목록(카드 제목 미포함)."""
    bk = buckets(kb, kinds)
    lines: list[str] = []
    for kind in _KIND_ORDER:
        items = [(code, b) for code, b in bk.items() if b["kind"] == kind]
        if not items:
            continue
        total = sum(len(b["cards"]) for _, b in items)
        lines.append(f"■ {kind} — {_KIND_DESC.get(kind, '')} (총 {total}장)")
        lines += [f"  {code}  {b['group']} ({len(b['cards'])}장)" for code, b in items]
    return "\n".join(lines)


def _card_line(card: dict, examples: int) -> str:
    """L1 한 줄. examples=0 이면 제목만."""
    parts = [f"[{card['id']}] {card.get('title') or ''}"]
    if examples:
        ex = "; ".join((card.get("trigger_examples") or [])[:examples])
        if ex:
            parts.append(f"예상질문: {ex}")
    return " | ".join(parts)


def whole_index(kb: KnowledgeBase, kinds: tuple[str, ...] | None = None,
                *, budget_chars: int = INDEX_BUDGET_CHARS) -> str | None:
    """전체 카드 L1 인덱스가 예산 안이면 그 텍스트(L0 생략용), 아니면 None.

    제목만 압축(examples=0)은 미사용 — 후보 정보가 2단보다 얇아짐.
    """
    bk = buckets(kb, kinds)
    if not bk:
        return None
    for examples in (2, 1):
        blocks = ["\n".join([f"── {b['kind']} / {b['group']}"] +
                            [_card_line(c, examples) for c in b["cards"]])
                  for b in bk.values()]
        text = "\n".join(blocks)
        if len(text) <= budget_chars:
            return text
    return None


def index_slice(kb: KnowledgeBase, codes: list[str] | tuple[str, ...],
                *, kinds: tuple[str, ...] | None = None,
                budget_chars: int = INDEX_BUDGET_CHARS) -> str:
    """L1 — 고른 버킷의 카드 인덱스. 예산 초과 시 압축, 그래도 넘으면 생략 수 명시. 없는 코드는 무시."""
    bk = buckets(kb, kinds)
    picked = [bk[c] for c in dict.fromkeys(codes) if c in bk]   # 중복 제거·입력 순서 유지
    if not picked:
        return ""

    # 압축: 예상질문 2개 → 1개 → 제목만
    for examples in (2, 1, 0):
        blocks = []
        for b in picked:
            head = f"── {b['kind']} / {b['group']}"
            blocks.append("\n".join([head] + [_card_line(c, examples) for c in b["cards"]]))
        text = "\n".join(blocks)
        if len(text) <= budget_chars:
            return text

    # 예산은 실제 상한 — 생략 안내 줄 자리를 미리 확보
    total = sum(len(b["cards"]) for b in picked)
    notice = f"(예산 초과로 {total}장 생략 — 버킷을 좁혀 다시 고를 것)"
    room = max(0, budget_chars - len(notice) - 1)

    kept: list[str] = []
    used, shown = 0, 0
    for b in picked:
        head = f"── {b['kind']} / {b['group']}"
        if used + len(head) + 1 > room:
            break
        kept.append(head)
        used += len(head) + 1
        for c in b["cards"]:
            line = _card_line(c, 0)
            if used + len(line) + 1 > room:
                break
            kept.append(line)
            used += len(line) + 1
            shown += 1
    if shown < total:
        kept.append(f"(예산 초과로 {total - shown}장 생략 — 버킷을 좁혀 다시 고를 것)")
    return "\n".join(kept)


def build_context(kb: KnowledgeBase, hits: list[tuple[float, dict]]) -> str:
    """검색된 카드와 그 참조 사실·자료의 프롬프트용 텍스트."""
    if not hits:
        return "(관련 화법 없음)"

    blocks, used_facts, used_res = [], [], []
    for _sc, p in hits:
        t = p.get("tags") or {}
        scope = ", ".join(x for x in (t.get("stage"), *(t.get("customer_type") or [])) if x)
        # 카드 id·관련도 미포함 — 재료에 있으면 답변에 id 가 그대로 나옴(출처에만 유지)
        lines = [f"■ {p['title']}"]
        if scope:
            lines.append(f"- 적용 범위 {scope}"
                         + (f" | 거절유형 {t['objection_type']}" if t.get("objection_type") else ""))
        if p.get("condition_text"):
            lines.append(f"- 대상 조건: {p['condition_text']}")
        if p.get("reason_text"):
            lines.append(f"- 관리 이유: {p['reason_text']}")
        if p.get("dialogue"):
            lines.append("- 원문 스크립트:")
            lines += [f"    {d['speaker']}: {d['text']}" for d in p["dialogue"]]
        if p.get("key_points"):
            lines.append("- 핵심 포인트:")
            lines += [f"    · {k}" for k in p["key_points"]]
        elif p.get("content"):
            lines.append(f"- 내용: {p['content']}")
        for label, key in (("실행 팁", "tips"), ("주의사항", "cautions")):
            if p.get(key):
                lines.append(f"- {label}:")
                lines += [f"    · {v}" for v in p[key]]
        lines.append(f"- 출처: {origin_of(kb, p)}"
                     + (f" p.{p['page']}" if p.get("page") else ""))
        blocks.append("\n".join(lines))
        used_facts += [f for f in p.get("supporting_facts") or [] if f not in used_facts]
        used_res += [r for r in p.get("resources") or [] if r not in used_res]

    fl = ["### 근거 사실 (수치는 이 범위 안에서만 인용)"]
    for fid in used_facts:
        f = kb.facts.get(fid)
        if not f:
            continue
        line = f"- {f['label']}: {f['value']}"
        if f.get("detail"):
            line += f" / {f['detail']}"
        if f.get("assumptions"):
            line += f" (전제: {f['assumptions']})"
        fl.append(line)
    blocks.append("\n".join(fl))

    if used_res:
        rl = ["### 함께 활용할 자료"]
        for rid in used_res:
            r = kb.resources.get(rid)
            if r:
                tag = "고객제공 가능" if r.get("customer_facing") else "내부용(고객 직접 제공 금지)"
                rl.append(f"- {r['name']} [{tag}] — {r.get('purpose') or ''}")
        blocks.append("\n".join(rl))

    return "\n\n".join(blocks)

