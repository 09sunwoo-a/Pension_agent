"""consult_agent 전용 지식베이스 도구 — LLM 카드 선택용 계층 인덱스(버킷) · 프롬프트 컨텍스트.

적재·시효성·출처·검색은 두 에이전트가 함께 쓰는 것이라 `pension_agent.knowledge.kb` 가
소유한다. 여기 남은 것은 대화형만 쓰는 것들이다 — 카드를 종류별 버킷으로 묶어 LLM 이
고르게 하는 인덱스(`buckets`·`index_catalog`·`whole_index`·`index_slice`), 고른 카드와 그
근거 사실·자료를 프롬프트 텍스트로 펴는 `build_context`, 검색 결과의 출처 표기 `sources_of`.

공용 이름은 아래에서 그대로 재노출한다 — 이 모듈을 `KBMOD` 로 받아 `KBMOD.retrieve`·
`KBMOD.role_texts` 로 부르던 자리(노드·테스트·스크립트)가 그대로 성립하게 하기 위해서다.
새 코드는 소유자(`pension_agent.knowledge.kb`)에서 직접 가져온다.

검증 리포트:  python -m pension_agent.knowledge.kb  (이 모듈로 실행해도 같은 리포트가 나온다)
"""

from __future__ import annotations

from pension_agent.knowledge.kb import (  # noqa: F401 — 공용 이름 재노출 (위 머리말)
    DATA_DIR,
    MIN_TOPICAL,
    ROLE_FIELDS,
    SEGMENT_BONUS,
    KnowledgeBase,
    apply_freshness,
    card_source_meta,
    load_kb,
    matches_scope,
    origin_of,
    retrieve,
    role_texts,
    score_parts,
    source_label,
    source_url,
    usable,
    validate,
)


# ─────────────────────────────────────────────────────────────
# 계층 인덱스 — 카드 선택을 2단으로 쪼갠다 (작은 컨텍스트 모델용)
#
# 카드 429장을 한 목록으로 펴면 약 30k 토큰이고, 행내에서 쓸 수 있는 모델
# (gemma4-31b·dna3.0-35b)에는 그대로 실을 수 없다. 그래서 kind × group 으로
# 버킷을 만들어 **L0 카탈로그(버킷 목록, 400여 토큰) → L1 슬라이스(고른 버킷의
# 카드만)** 두 단계로 나눈다. 가장 큰 버킷도 2k 토큰 아래라 두 호출 모두 작다.
#
# 축을 group 으로 잡은 이유: 모든 카드 종류가 갖고 있는 유일한 완전한 축이다.
# tags.topics 는 96종으로 더 촘촘하지만 429장 중 69장에 아예 없어서, 그걸 축으로
# 쓰면 그 69장은 어떤 버킷에도 안 들어간다 — 검색되지 않는 카드가 생긴다.
#
# 안전 규약은 card_index 시절과 같다. 후보가 되는 카드 자체가 이미 검증 게이트를
# 통과한 kb 적재분으로 한정돼 있으므로, LLM 이 아무리 자유롭게 판단해도 저작되지
# 않은(⚠확인필요·⏳시효민감 등 미승인) 내용을 새로 끌어올 수 없다 — 안전장치는
# 응답 파싱 단계가 아니라 이 인덱스 구성 단계에서 이미 걸린다. 버킷 코드도 같은
# 원리로, 목록에 없는 코드는 조회되지 않는다.
# ─────────────────────────────────────────────────────────────

#: 버킷 코드 머리글자. procedure 는 pitch 와 겹치지 않게 R(routine)을, screen 은 S(segment)와
#: 겹치지 않게 N(number)을 쓴다. 여기 없는 종류는 X 로 묶이는데, 그러면 코드가 서로 구분되지
#: 않으므로 **새 종류를 적재하면 여기 한 줄을 함께 늘린다** — 그러지 않으면 그 종류의 카드가
#: 버킷 카탈로그에서 사실상 안 보인다(test_consult_agent 의 버킷 커버리지 검사가 잡는다).
#: market 은 M(method)·S(segment)가 이미 쓰이고 있어 K(기반지식), lineup 은 L 을 쓴다.
_BUCKET_LETTER = {"pitch": "P", "procedure": "R", "segment": "S", "method": "M",
                  "fieldtip": "F", "screen": "N", "channel": "C", "market": "K",
                  "lineup": "L"}

#: L0 카탈로그에 붙이는 종류별 한 줄 설명. "이 종류가 답이 되는 질문"을 적는다.
#: **적재되는 종류는 전부 여기 있어야 한다** — 빠지면 카탈로그에 종류 이름과 장수만 뜨고
#: LLM 은 그 묶음이 무엇에 답하는지 모른 채 고르게 된다. screen·channel 143장이 오랫동안
#: 그 상태였다(설명 없이 "■ screen — (총 87장)"으로만 떴다).
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

#: 카드 종류 표시 순서(고정). 카탈로그가 실행마다 같은 순서로 나오게 한다.
#: 버킷 카탈로그에 싣는 종류와 그 순서. **적재되는 종류는 전부 여기 있어야 한다** —
#: 빠지면 그 종류의 카드가 버킷에 안 들어가고, LLM 이 고를 후보 목록에서 통째로 사라진다
#: (n-gram 폴백으로만 닿게 되어, 사실상 "있는데 못 찾는" 상태가 된다).
_KIND_ORDER = ("pitch", "fact", "procedure", "screen", "channel", "segment", "method",
               "fieldtip", "market", "lineup")

#: index_slice 의 기본 문자 예산. 한글은 대략 2자/토큰이라 4000자 ≈ 2k 토큰이고,
#: 가장 큰 버킷(3,652자) 하나가 통째로 들어간다.
INDEX_BUDGET_CHARS = 4000

def sources_of(kb: KnowledgeBase, hits: list[tuple[float, dict]]) -> list[dict]:
    """검색 결과 → 답변에 붙일 근거 목록(역추적용). 카드는 title, 팩트는 label 을 쓴다.

    `doc` 이 화면·CLI 가 읽어줄 **원문 출처**다. `id` 는 카드 식별자로 남지만 그것만
    보여주면 사내 json 안의 코드가 근거처럼 보인다 — 출처는 원본 문서 이름으로 말한다.
    """
    return [{"id": c["id"], "title": c.get("title") or c.get("label"),
             "doc": origin_of(kb, c), "url": source_url(kb, c),
             "score": round(s, 2), "page": c.get("page")} for s, c in hits]


def product_names(kb: KnowledgeBase) -> set[str]:
    """지식베이스가 **선언한** 상품 이름 전부(카드의 `product_names`).

    답변이 상품명을 말했을 때 「실재하는 상품인가」를 대조하는 등록부의 한쪽이다
    (다른 쪽은 strategy_agent 의 상품 카탈로그 — `nodes/plan.py::_known_products`).

    이게 없던 동안 등록부는 데모 카탈로그 12종뿐이었고, **행내 원문 표에 버젓이 있는
    상품**(「KB 온국민 TDF 시리즈」·「KB RISE 미국ETF 모아드림」…)을 말한 답변이 전부
    '미등록'으로 판정돼 통째로 버려졌다 — 그 자리에 근거 원문이 덤프됐다.

    이름을 여기서 **추론하지 않는다.** 카드가 선언한 것만 읽는다 — 어느 칸을 상품명으로
    볼지는 변환기가 정한다(`build_kb._PRODUCT_COLUMNS`).
    """
    return {n for c in kb.cards for n in (c.get("product_names") or []) if n}


def advisory_note(kb: KnowledgeBase) -> str | None:
    """지식베이스가 선언한 **인용 고지** — "정보 제공 목적 · 투자권유 시 자본시장법·당행
    규정 준수 의무"(05 폴더 README 가 선언하고 변환기가 카드마다 싣는다).

    카드가 아닌 재료(코드가 계산한 적합성 판정)에도 이 표시가 필요한데, 문구를 코드
    상수로 두면 §12 gap 20 이 경계하는 모양이 된다 — 재료 종류마다 코드 상수가 하나씩
    생기면 「표시는 데이터 선언이 정한다」(§7)가 사실상 없어진다. 그래서 선언된 문구를
    한 곳에서 읽어 쓴다. 선언이 없으면 None 이고, 그러면 표시도 붙지 않는다.
    """
    return next((c["advisory"] for c in kb.cards if c.get("advisory")), None)


_NO_GROUP = "(미분류)"


def _bucket_key(card: dict) -> tuple[str, str]:
    return card["_kind"], (card.get("group") or _NO_GROUP)


def buckets(kb: KnowledgeBase, kinds: tuple[str, ...] | None = None) -> dict[str, dict]:
    """버킷 코드 → {kind, group, cards}. 코드는 (종류 고정순서, group 사전순)으로 매긴다.

    코드는 프롬프트 한 번에서만 쓰는 일회성 라벨이고 영속 식별자가 아니다 —
    카드가 늘어 group 이 새로 생기면 뒤 번호가 밀린다. 영속 식별자는 카드 id 뿐이다.
    """
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
    """L0 — 버킷 목록만. 카드 제목은 넣지 않는다(그게 L1 의 몫이다)."""
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
    """L1 한 줄. examples=0 이면 제목까지만(예산이 빡빡할 때의 압축 단계)."""
    parts = [f"[{card['id']}] {card.get('title') or ''}"]
    if examples:
        ex = "; ".join((card.get("trigger_examples") or [])[:examples])
        if ex:
            parts.append(f"예상질문: {ex}")
    return " | ".join(parts)


def whole_index(kb: KnowledgeBase, kinds: tuple[str, ...] | None = None,
                *, budget_chars: int = INDEX_BUDGET_CHARS) -> str | None:
    """이 종류 **전체** 카드의 L1 인덱스가 예산에 들어가면 그 텍스트를, 아니면 None.

    버킷 선택(L0) 호출을 생략할 수 있는지의 판정이다. 2단(버킷 → 카드)이 필요한 이유는
    "카드 429장 평면 목록 ≈ 30k 토큰"인데, 종류별로 재보면 그 전제가 안 서는 종류가
    있다 — channel(56장 3,492자)·market·lineup·fieldtip 은 전 카드를 한 번에 보여줘도
    예산 안이다. 그런 종류에서 버킷을 고르게 하는 것은 후보를 좁히는 게 아니라 LLM
    왕복 하나를 그냥 쓰는 것이고, 버킷 오선택으로 답이 든 카드가 후보에서 빠지는 자리만
    하나 늘린다.

    판정은 데이터가 한다 — 카드가 늘어 예산을 넘으면 None 이 되고 호출부는 저절로
    2단으로 돌아간다. 제목만 남기는 압축(examples=0)까지는 내려가지 않는다: 예상질문이
    없는 제목 나열은 2단에서 보던 것보다 후보 정보가 얇아져, 왕복 하나를 아끼려고
    선택 품질을 파는 것이 된다(그 자리는 그대로 2단이 맞다 — screen 이 이 경우다).
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
    """L1 — 고른 버킷의 카드 인덱스. 예산을 넘으면 압축하고, 그래도 넘으면 잘라내되 밝힌다.

    목록에 없는 코드는 조용히 버린다(존재하지 않는 버킷이므로 후보가 없는 것과 같다).
    """
    bk = buckets(kb, kinds)
    picked = [bk[c] for c in dict.fromkeys(codes) if c in bk]   # 중복 제거·입력 순서 유지
    if not picked:
        return ""

    # 압축 사다리: 예상질문 2개 → 1개 → 제목만. 각 단계에서 예산에 들어가면 멈춘다.
    for examples in (2, 1, 0):
        blocks = []
        for b in picked:
            head = f"── {b['kind']} / {b['group']}"
            blocks.append("\n".join([head] + [_card_line(c, examples) for c in b["cards"]]))
        text = "\n".join(blocks)
        if len(text) <= budget_chars:
            return text

    # 제목만으로도 안 들어가면 카드를 잘라낸다 — 몇 장을 못 보여줬는지는 반드시 남긴다.
    # 예산은 실제 상한이다(작은 컨텍스트에 싣는 게 목적이므로). 그래서 생략 안내 한 줄
    # 자리를 미리 떼어두고, 버킷 헤더도 예산 안에서만 붙인다.
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
    """검색된 카드 + 그 카드가 참조하는 사실/자료만 프롬프트용 텍스트로 만든다.

    전체 KB를 넣지 않기 때문에 문서가 늘어나도 프롬프트 크기는 일정하다.
    """
    if not hits:
        return "(관련 화법 없음)"

    blocks, used_facts, used_res = [], [], []
    for sc, p in hits:
        t = p.get("tags") or {}
        scope = ", ".join(x for x in (t.get("stage"), *(t.get("customer_type") or [])) if x)
        lines = [f"### [{p['id']}] {p['title']}  (관련도 {sc:.1f})"]
        if scope:
            lines.append(f"- 적용 범위 {scope}"
                         + (f" | 거절유형 {t['objection_type']}" if t.get("objection_type") else ""))
        # 세그먼트 카드는 대사가 아니라 '누구를 고르나 / 왜 고르나' 가 본문이다.
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
        # 출처는 원천 문서(01~04)로 말한다 — 적재 파일 제목으로 물러서지 않는다(origin_of).
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


if __name__ == "__main__":
    from pension_agent.knowledge.kb import main

    main()
