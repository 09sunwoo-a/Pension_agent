"""카드 지식베이스 — 레코드를 검색·표시용 카드로 재구성, 시효성 처리, 출처 표기, n-gram+태그 검색.

검증 리포트:  python -m pension_agent.knowledge.kb
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


from pension_agent.common import config
from pension_agent.knowledge import market
from pension_agent.knowledge import Store
from pension_agent.knowledge.checks import check_broken_refs, check_fact_conflicts
from pension_agent.knowledge.schema import REGISTRY
from pension_agent.knowledge.similarity import ngram_sim as _sim

DATA_DIR = config.KB_DATA_DIR

# 검색 채점용 동의어 정규화. 법적·절차적으로 같은 대상의 표기 차이만 — 다른 개념(소득공제 vs 세액공제)을 묶으면 오안내
_SYNONYMS: dict[str, str] = {
    "불입": "납입",
    "입금": "납입",
    "타은행": "타행",
    "다른은행": "타행",
    "다른 은행": "타행",
}


def _expand_synonyms(text: str) -> str:
    """검색 채점 전 동의어 치환."""
    out = text
    for k, v in _SYNONYMS.items():
        out = out.replace(k, v)
    return out


@dataclass
class KnowledgeBase:
    docs: list[dict] = field(default_factory=list)
    facts: dict[str, dict] = field(default_factory=dict)
    resources: dict[str, dict] = field(default_factory=dict)
    pitches: list[dict] = field(default_factory=list)
    # 주장 성립 조건이 깨져 후보군에서 뺀 카드(리포트용)
    suppressed: list[dict] = field(default_factory=list)
    # 검색 대상 카드 전체(consumed=retrieval). 화법·팩트는 pitches·facts 와 같은 객체 공유
    cards: list[dict] = field(default_factory=list)
    # 원천 문서 레지스트리(doc kind)
    docs_by_id: dict[str, dict] = field(default_factory=dict)
    # 카드 id → 카드. 손으로 출처 dict 를 만드는 자리(⑥⑦⑧ 카드)의 원천 문서 조회용
    cards_by_id: dict[str, dict] = field(default_factory=dict)

    @property
    def customer_types(self) -> list[str]:
        out: set[str] = set()
        for p in self.pitches:
            out.update(p["tags"].get("customer_type") or [])
        return sorted(out - {"공통"})

    @property
    def objection_types(self) -> list[str]:
        return sorted(
            {p["tags"]["objection_type"] for p in self.pitches if p["tags"].get("objection_type")}
        )

    @property
    def stages(self) -> list[str]:
        return sorted({p["tags"]["stage"] for p in self.pitches} - {"공통"})


def _flat(r: dict, kind: str) -> dict:
    """레코드 → 검색·표시용 flat dict. `_doc` 은 변환본 파일명이라 출처는 `_source` 로 조회."""
    return {"id": r["id"], "_kind": kind, "_doc": r.get("_doc_title"),
            "_doc_ref": r.get("_doc_ref"),
            "_source": r.get("source"), **(r.get("fields") or {})}


# 주의·비고 역할 선언 읽기의 단일 통로 — caution(강제 표시) · info(답변 재료) · authoring(미표출)

#: 역할 선언을 갖는 종류 → 필드. validate() 의 선언 누락 검사 범위.
ROLE_FIELDS: dict[str, str] = {"procedure": "cautions", "screen": "note",
                               "channel": "note", "method": "cautions",
                               "segment": "note"}
_ROLES = frozenset({"caution", "info", "authoring"})


def role_texts(entries: list | None, *roles: str) -> list[str]:
    """해당 역할의 문장만 추출(중복 제거). 역할 미선언 항목은 미계수."""
    out: list[str] = []
    for e in entries or []:
        if isinstance(e, dict) and e.get("role") in roles:
            text = (e.get("text") or "").strip()
            if text and text not in out:
                out.append(text)
    return out


# 시효성 수치 — 적재 시 1회 처리(소비처별 필터 금지). 원문(quotes·source_text)은 불변
#   rate_slots → 파생 텍스트의 금리 표기를 현재값으로 치환
#   claim_condition → 주장이 거짓이면 후보군에서 제외

# 슬롯 치환 대상 = 파생 텍스트만. quotes 수정 금지 — «출처는 진짜인데 수치는 가짜인 카드»가 됨
_SLOT_TEXT_FIELDS = ("title", "summary", "content")


def _sub_in_dialogue(dialogue: list, was: str, now: str) -> tuple[list, bool]:
    hit = False
    out = []
    for turn in dialogue:
        if isinstance(turn, dict):
            t = dict(turn)
            for k, v in turn.items():
                if isinstance(v, str) and was in v:
                    t[k] = v.replace(was, now); hit = True
            out.append(t)
        elif isinstance(turn, str) and was in turn:
            out.append(turn.replace(was, now)); hit = True
        else:
            out.append(turn)
    return out, hit


def _fill_rate_slots(card: dict) -> None:
    """원문 금리 표기를 현재값으로 치환. 인용문에만 있는 표기는 치환 대신 참고 표시(_rate_notes)."""
    slots = card.get("rate_slots") or []
    if not slots:
        return
    snap = market.current()
    applied, notes, missing = [], [], []
    for slot in slots:
        val = market.rate(slot["rate_key"])
        if val is None:
            missing.append(slot["what"])
            continue
        was, now = slot["was"], f"{val:g}%"
        hit = False
        for f in _SLOT_TEXT_FIELDS:
            if isinstance(card.get(f), str) and was in card[f]:
                card[f] = card[f].replace(was, now); hit = True
        if isinstance(card.get("key_points"), list):
            kp = [k.replace(was, now) if isinstance(k, str) and was in k else k
                  for k in card["key_points"]]
            hit = hit or kp != card["key_points"]
            card["key_points"] = kp
        if isinstance(card.get("dialogue"), list):
            card["dialogue"], d_hit = _sub_in_dialogue(card["dialogue"], was, now)
            hit = hit or d_hit
        if hit:
            applied.append({**slot, "was": was, "now": now})
        else:
            # 인용문에만 금리가 있는 카드 — 현재값만 참고 표시
            notes.append({**slot, "was": was, "now": now})
    if applied:
        card["_rate_slots_applied"] = applied
    if notes:
        card["_rate_notes"] = notes
    if applied or notes:
        # 치환값의 기준시점·더미 여부를 카드가 보유(화면 표시·리포트 집계용)
        card["_rate_as_of"] = snap.get("as_of")
        card["_rate_dummy"] = bool(snap.get("dummy"))
    if missing:
        card["_rate_missing"] = missing


def _check_claim(card: dict) -> None:
    """주장 성립 조건 판정. 거짓이면 `_suppressed`, 판정 불가면 `_verify_first`."""
    cond = card.get("claim_condition")
    if not cond:
        return
    kind = cond.get("kind")
    if kind == "compare":
        left, right = market.rate(cond["left"]), market.rate(cond["right"])
        if left is None or right is None:
            card["_verify_first"] = (
                f"{cond['claim']} — 비교에 필요한 수치가 없어 확인 후 사용하세요")
            return
        ok = left > right if cond["op"] == ">" else left < right
        if not ok:
            # 수치만 바꾸면 «~보다 높다» 결론이 거짓으로 남음 → 카드 제외
            card["_suppressed"] = (
                f"주장 미성립: {cond['claim']} (현재 {left:g}% vs {right:g}%)")
        return
    if kind == "unknown":
        # 판정 근거 없음 — 카드는 살리고 확인 요구
        card["_verify_first"] = f"{cond['claim']} — {cond.get('why', '근거 확인 필요')}"


def apply_freshness(cards: list[dict]) -> None:
    for c in cards:
        _fill_rate_slots(c)
        _check_claim(c)


def usable(card: dict) -> bool:
    """후보군에 올릴 수 있는 카드인가(주장 깨진 카드 제외)."""
    return not card.get("_suppressed")


def load_kb(data_dir: Path = DATA_DIR) -> KnowledgeBase:
    """지식 파일 적재 → KnowledgeBase. 검색 대상 종류는 kinds.json 의 `consumed: retrieval`."""
    st = Store([data_dir])
    kb = KnowledgeBase()
    for r in st.records("resource"):
        kb.resources[r["id"]] = {"id": r["id"], **(r.get("fields") or {})}
    for r in st.records("doc"):
        kb.docs_by_id[r["id"]] = {"id": r["id"], **(r.get("fields") or {})}

    for kind, spec in REGISTRY.items():
        if spec.get("consumed") != "retrieval":
            continue
        for r in st.records(kind):
            card = _flat(r, kind)
            kb.cards.append(card)
            if kind == "pitch":
                kb.pitches.append(card)   # 같은 객체 공유(사본 아님)
            elif kind == "fact":
                # facts(id 참조)와 cards(검색 색인)에 같은 객체 — 사본이면 한쪽만 고쳐짐
                kb.facts[card["id"]] = card

    seen: dict[str, dict] = {}
    for r in st.records():
        did = r.get("_doc_id")
        if did and did not in seen:
            seen[did] = {"doc_id": did, "meta": {"title": r.get("_doc_title")}}
    kb.docs = list(seen.values())

    # 시효성 처리 → 주장 깨진 카드 격리(여기서 1회, 사실은 kb.suppressed 에 기록)
    apply_freshness(kb.cards)
    kb.suppressed = [c for c in kb.cards if not usable(c)]
    if kb.suppressed:
        dropped = {c["id"] for c in kb.suppressed}
        kb.cards = [c for c in kb.cards if c["id"] not in dropped]
        kb.pitches = [c for c in kb.pitches if c["id"] not in dropped]
    # 격리 후 색인 — 먼저 만들면 뺀 카드가 색인에 남음
    kb.cards_by_id = {c["id"]: c for c in kb.cards}
    return kb


def source_label(kb: KnowledgeBase, card: dict) -> str | None:
    """카드의 출처 한 줄 — "문서명(부서, 시점)". source.doc 없으면 파일 단위 `_doc_ref`. 없으면 None."""
    doc = kb.docs_by_id.get(
        (card.get("_source") or {}).get("doc") or card.get("_doc_ref") or "")
    if not doc:
        return None
    meta = ", ".join(x for x in (doc.get("dept"), doc.get("published")) if x)
    return f"{doc['title']} ({meta})" if meta else doc["title"]


def source_url(kb: KnowledgeBase, card: dict) -> str | None:
    """원천 문서의 게시글 URL(doc.url). 없으면 None."""
    doc = kb.docs_by_id.get(
        (card.get("_source") or {}).get("doc") or card.get("_doc_ref") or "")
    return (doc or {}).get("url") or None


def card_source_meta(kb: KnowledgeBase, card_id: str) -> dict:
    """카드 id 의 출처 표기 {doc, url}. 손으로 출처 dict 를 만드는 자리용. 카드 없으면 빈 dict."""
    card = kb.cards_by_id.get(card_id) or kb.facts.get(card_id)
    if not card:
        return {}
    return {"doc": origin_of(kb, card), "url": source_url(kb, card)}


def origin_of(kb: KnowledgeBase, card: dict) -> str:
    """답변용 출처 한 줄. 원천 문서 → 원문 표기 → 추출지식 절 → «출처 미상». 파일명(`_doc`) 사용 금지."""
    label = source_label(kb, card)
    if label:
        return label
    if card.get("source_text"):
        return str(card["source_text"])
    locator = (card.get("_source") or {}).get("locator")
    if locator:
        # 원천 문서 미지정 카드(통합 항목) — 추출지식 절 제목까지
        section = locator.split("§", 1)[1].strip() if "§" in locator else locator
        return f"원천 문서 미지정 — 추출지식 § {section}"
    return "출처 미상 — 확인 필요"


def score_parts(
    pitch: dict, customer_type=None, objection_type=None, stage=None, utterance=None
) -> tuple[float, float]:
    """(태그점수, 내용관련도). 순위는 합, 채택은 내용관련도로만 — 태그는 상수 보너스라 무관한 카드를 통과시킴."""
    tags = pitch["tags"]
    ctypes = tags.get("customer_type") or []
    tag_s = topical_s = 0.0

    if customer_type:
        tag_s += 3.0 if customer_type in ctypes else (1.5 if "공통" in ctypes else 0.0)
    if stage and tags.get("stage") in (stage, "공통"):
        tag_s += 2.0

    if objection_type:
        if tags.get("objection_type") == objection_type:
            topical_s += 4.0
        elif tags.get("objection_type"):
            topical_s += _sim(objection_type, tags["objection_type"]) * 2.0

    if utterance:
        utterance = _expand_synonyms(utterance)
        # 제목도 예상질문과 같은 방식으로 채점(변환기가 trigger_examples 에서 제목 제외)
        entrances = list(pitch.get("trigger_examples") or []) + [pitch.get("title") or ""]
        topical_s += max((_sim(utterance, ex) for ex in entrances if ex), default=0.0) * 4.0
        flat = re.sub(r"[^0-9a-zA-Z가-힣]", "", utterance)
        for kw in list(tags.get("topics", [])) + [pitch["title"]]:
            k = re.sub(r"[^0-9a-zA-Z가-힣]", "", kw)
            if len(k) >= 2 and k in flat:
                topical_s += 0.8

    return tag_s, topical_s


# 내용관련도 채택 기준.
MIN_TOPICAL = 0.5


def matches_scope(pitch: dict, customer_type=None, stage=None, **_ignored) -> bool:
    """stage/customer_type 이 주어지면 그 값(또는 '공통')과 안 맞는 카드 제외."""
    tags = pitch["tags"]
    if stage and tags.get("stage") not in (stage, "공통"):
        return False
    if customer_type:
        ctypes = tags.get("customer_type") or []
        if customer_type not in ctypes and "공통" not in ctypes:
            return False
    return True


# 세그먼트 연결 카드의 태그 가산점. MIN_TOPICAL 문턱은 불변 — 세그먼트만으로 무관한 카드 채택 방지
SEGMENT_BONUS = 3.0


def retrieve(kb: KnowledgeBase, *, top_k=3, kinds=None, segments=None,
             **criteria) -> list[tuple[float, dict]]:
    """스코프로 좁히고 내용관련도 미달 제외 후 총점 정렬. kinds 기본 None = 화법, segments 는 순위만 가산."""
    pool = kb.pitches if kinds is None else [c for c in kb.cards if c["_kind"] in kinds]
    wanted = set(segments or ())

    hits = []
    for p in pool:
        if not matches_scope(p, **criteria):
            continue
        tag_s, top_s = score_parts(p, **criteria)
        if top_s < MIN_TOPICAL:
            continue
        if wanted and wanted & set(p.get("segments") or ()):
            tag_s += SEGMENT_BONUS
        hits.append((tag_s + top_s, p))
    hits.sort(key=lambda x: (-x[0], x[1]["id"]))
    return hits[:top_k]


_STAGES = {"신규", "계약이전", "추가납입", "퇴직금", "운용관리", "이탈방어", "수령", "공통"}
_TYPES = {"proposal", "objection", "guide"}


def validate(kb: KnowledgeBase) -> tuple[list[str], list[str]]:
    errors, warns, seen = [], [], {}

    for p in kb.pitches:
        pid = p["id"]
        if pid in seen:
            errors.append(f"[중복ID] {pid}")
        seen[pid] = True

        for req in ("id", "type", "title", "tags", "key_points"):
            if not p.get(req):
                errors.append(f"[필수누락] {pid} → {req}")
        if p.get("type") not in _TYPES:
            errors.append(f"[잘못된값] {pid} type={p.get('type')}")
        if p["tags"].get("stage") not in _STAGES:
            errors.append(f"[잘못된값] {pid} stage={p['tags'].get('stage')}")
        if p.get("type") == "objection" and not p["tags"].get("objection_type"):
            errors.append(f"[태그누락] {pid} objection 카드인데 objection_type 없음")

        errors += check_broken_refs(p.get("supporting_facts", []), set(kb.facts), owner=pid)
        errors += check_broken_refs(p.get("resources", []), set(kb.resources), owner=pid)

        if not p.get("trigger_examples"):
            warns.append(f"[검색불가] {pid} trigger_examples 없음")
        if not p.get("supporting_facts"):
            warns.append(f"[근거없음] {pid} supporting_facts 없음")

    # 주의·비고 역할 선언 누락 — 누락 시 진짜 주의가 조용히 빠짐
    for card in kb.cards:
        field = ROLE_FIELDS.get(card.get("_kind") or "")
        if not field:
            continue
        for e in card.get(field) or []:
            if (not isinstance(e, dict) or e.get("role") not in _ROLES
                    or not (e.get("text") or "").strip()):
                errors.append(f"[역할누락] {card['id']} {field} 항목에 role 선언이 없다: "
                              f"{str(e)[:50]}")

    # 사실충돌
    errors += check_fact_conflicts((f["label"], f["value"]) for f in kb.facts.values())

    # 화법 미참조 사실 경고. fact.k04.* 는 fact 도구 재료라 제외
    used = {f for p in kb.pitches for f in p.get("supporting_facts") or []}
    warns += [f"[미사용] {fid} 를 참조하는 화법 없음"
              for fid in kb.facts if fid not in used and not fid.startswith("fact.k04.")]

    return errors, warns


def main() -> None:
    """지식베이스 점검 리포트 — 카드 수·종류별 장수·검증 ERROR/WARN."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    kb = load_kb()
    kinds = ", ".join(f"{k} {sum(1 for c in kb.cards if c['_kind'] == k)}"
                      for k in sorted({c["_kind"] for c in kb.cards}))
    print(f"적재 파일 {len(kb.docs)} · 검색카드 {len(kb.cards)}({kinds}) "
          f"· 사실 {len(kb.facts)} · 자료 {len(kb.resources)} · 원천문서 {len(kb.docs_by_id)}")
    print(f"고객유형 {kb.customer_types} / 단계 {kb.stages}")
    print(f"거절유형 {kb.objection_types}\n")

    for p in sorted(kb.pitches, key=lambda x: x["id"]):
        t = p["tags"]
        print(
            f"  {p['id']:<20} {p['title'][:38]:<40} "
            f"{t['stage']}/{t.get('objection_type') or '-'} "
            f"(대화 {len(p.get('dialogue') or [])} 근거 {len(p.get('supporting_facts') or [])} "
            f"트리거 {len(p.get('trigger_examples') or [])})"
        )

    errors, warns = validate(kb)
    print()
    print("✅ ERROR 없음" if not errors else f"❌ ERROR {len(errors)}건")
    for e in errors:
        print("   " + e)
    print(f"⚠️  WARN {len(warns)}건")
    for w in warns:
        print("   " + w)
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
