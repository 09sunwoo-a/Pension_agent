"""지식베이스: 로드 · 검색 · 검증.

외부 라이브러리 의존성 없음 (표준 라이브러리만 사용).
data/ 폴더에 스키마를 따르는 JSON을 넣으면 자동으로 적재된다.

검증 리포트:  python kb.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


from pension_agent import config, market
from pension_agent.knowledge import Store
from pension_agent.knowledge.checks import check_broken_refs, check_fact_conflicts
from pension_agent.knowledge.schema import REGISTRY
from pension_agent.knowledge.similarity import ngram_sim as _sim

DATA_DIR = config.KB_DATA_DIR

# ─────────────────────────────────────────────────────────────
# 검색 매칭용 동의어 정규화 (도메인 특화라 kb_base.py 가 아닌 여기 둔다)
#
# n-gram 유사도는 표기가 겹쳐야 잡히므로, 실무에서 흔히 혼용되는 표현이 카드
# 저작 시 쓰인 표준 용어와 문자 단위로 다르면 검색이 실패한다. 다만 이 치환은
# 검색 채점용일 뿐이고, 검색이 "같은 카드"로 묶는다는 건 곧 "같은 걸 물었다"고
# 취급한다는 뜻이라 — **법적·제도적으로 다른 개념을 묶으면 그 자체가 오안내다.**
# (실제로 "소득공제→세액공제"가 여기 있었던 건 실수였다: 둘은 다른 세제
# 개념이라 소득공제를 물었는데 세액공제 카드가 매칭되면 없는 화법을 있다고
# 답하는 셈이 된다.) 그래서 기준을 "실무에서 흔히 혼용됨"이 아니라 "법적·
# 절차적으로 같은 대상을 가리키는 표기 차이임이 확실함"으로 좁혔다 — 확신이
# 없는 항목(이관 vs 이전의 방향성, 해약 vs 해지의 상품별 법적 차이 등)은
# 여기 넣지 않는다.
# ─────────────────────────────────────────────────────────────

_SYNONYMS: dict[str, str] = {
    "불입": "납입",   # 부담금 불입 = 부담금 납입, 법령·양쪽 문서 모두에서 같은 뜻으로 쓰임
    "입금": "납입",   # "IRP 입금" = "IRP 납입", 도메인 내 실제 혼용 확인됨(같은 행위)
    "타은행": "타행",
    "다른은행": "타행",
    "다른 은행": "타행",
}


def _expand_synonyms(text: str) -> str:
    """검색 채점 전 동의어 정규화. 순서 무관하게 전부 치환한다(포함 관계 없음)."""
    out = text
    for k, v in _SYNONYMS.items():
        out = out.replace(k, v)
    return out


# ─────────────────────────────────────────────────────────────
# 로드
# ─────────────────────────────────────────────────────────────

@dataclass
class KnowledgeBase:
    docs: list[dict] = field(default_factory=list)
    facts: dict[str, dict] = field(default_factory=dict)
    resources: dict[str, dict] = field(default_factory=dict)
    pitches: list[dict] = field(default_factory=list)
    # 주장 성립 조건이 깨져 후보군에서 뺀 카드. 검색·후보군에는 안 들어가지만 왜 빠졌는지는
    # 남는다(kb.py 리포트 · tools/demo_status.py).
    suppressed: list[dict] = field(default_factory=list)
    # 검색 대상 카드 전체(화법·세그먼트 등 consumed=retrieval 인 모든 종류). 화법 카드는
    # pitches 와 **같은 dict 객체**를 공유하므로 id 조회·검증 경로는 영향을 받지 않는다.
    cards: list[dict] = field(default_factory=list)
    # 원천 문서 레지스트리(doc kind). 카드의 source.doc 가 이걸 가리킨다.
    docs_by_id: dict[str, dict] = field(default_factory=dict)

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
    """레코드를 검색·표시용 flat dict 로 편다.

    `_source` 를 함께 싣는 게 중요하다 — 06 추출지식에서 변환한 카드의 `_doc`(파일 제목)은
    "영업 화법 — 06/03" 같은 변환본 이름이지 원천 문서가 아니다. 출처를 원문 그대로 말하려면
    레코드의 source.doc → doc 레지스트리 조인이 필요하다.
    """
    return {"id": r["id"], "_kind": kind, "_doc": r.get("_doc_title"),
            "_doc_ref": r.get("_doc_ref"),
            "_source": r.get("source"), **(r.get("fields") or {})}


# ─────────────────────────────────────────────────────────────
# 주의·비고의 역할 — 데이터 선언을 읽는 유일한 통로
#
# note·cautions 필드는 `[{"role", "text"}]` 다(kinds.json). 역할은 변환기가 데이터에
# 선언해 두고(build_kb.role_entries + config 예외표), 소비 코드는 여기서 선언만 읽는다 —
# 예전에는 guard 가 문자열 휴리스틱(_AUTHORING)으로 런타임에 걸렀는데, 그러면 분류가
# 어디에도 남지 않아 검토할 수 없고, screen·channel 비고처럼 거르는 코드가 없는 자리로는
# 저작 메모가 그대로 새어 나갔다(consult CLAUDE.md §12 지워진 gap 17).
#
#   caution   상담 중 지켜야 할 주의 — 답변에 강제 표시(notices)
#   info      직원에게 보여도 되는 참고 비고 — 답변 재료에 실림
#   authoring 저작·검증 메모 — 직원에게 띄우지 않음
# ─────────────────────────────────────────────────────────────

#: 역할 선언을 갖는 종류 → 필드. validate() 가 선언 누락을 ERROR 로 잡는 범위이기도 하다.
ROLE_FIELDS: dict[str, str] = {"procedure": "cautions", "screen": "note",
                               "channel": "note", "method": "cautions",
                               "segment": "note"}
_ROLES = frozenset({"caution", "info", "authoring"})


def role_texts(entries: list | None, *roles: str) -> list[str]:
    """주의·비고 필드에서 해당 역할의 문장만 꺼낸다(중복은 한 번만).

    역할 선언이 없는 항목은 어느 역할로도 세지 않는다 — 판단 근거는 데이터의 선언이고
    추론이 아니다(marks.py 와 같은 원칙). 선언 누락은 validate() 가 ERROR 로 잡는다.
    """
    out: list[str] = []
    for e in entries or []:
        if isinstance(e, dict) and e.get("role") in roles:
            text = (e.get("text") or "").strip()
            if text and text not in out:
                out.append(text)
    return out


# ─────────────────────────────────────────────────────────────
# 시효성 수치 — 금리 슬롯 치환 · 주장 성립 조건 판정
#
# 원문(quotes·source_text)은 절대 고치지 않는다. 대신 적재 시점에
#   · rate_slots  → 대사 표시용 텍스트에서 원문 금리 표기를 현재값으로 바꿔 끼우고,
#   · claim_condition → 주장이 지금도 참인지 판정해 거짓이면 카드를 후보군에서 뺀다.
#
# 여기(load_kb)가 유일한 길목이다 — strategy_agent.support 의 ⑥⑦ 후보군도, consult_agent 의
# 화법 검색도 전부 kb.pitches 를 본다. 한 곳에서 처리하면 소비자마다 잊어버릴 일이 없다.
# ─────────────────────────────────────────────────────────────

# 슬롯을 치환할 필드 = **행원에게 보여주는 파생 텍스트**만.
# quotes 는 원문 인용이라 절대 건드리지 않는다 — 저장소 수록 규칙(원문 그대로 보존)이고,
# source_text·doc 로 추적되는 값이라 여기를 고치면 "출처는 진짜인데 수치는 가짜인 카드"가 된다.
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
    """원문 금리 표기를 현재값으로 바꿔 끼운다. 원문 인용(quotes)은 그대로 둔다.

    표기가 파생 텍스트에 없고 인용문에만 있는 카드도 있다(대사 자체엔 금리가 없는 경우).
    그런 카드는 치환하지 않고 **현재값을 참고 표시로 붙인다** — 원문을 고치지 않으면서
    행원이 "지금은 얼마인지"를 알 수 있게 하는 유일한 방법이다.
    """
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
            # 대사에 금리가 없고 인용문에만 있는 카드. 원문은 그대로, 현재값만 알려준다.
            notes.append({**slot, "was": was, "now": now})
    if applied:
        card["_rate_slots_applied"] = applied
    if notes:
        card["_rate_notes"] = notes
    if applied or notes:
        # 치환값의 출처·시점을 카드가 들고 다닌다. 화면·답변이 "언제 기준 금리인지"를 말할 수
        # 있어야 하고(구성 원칙 4), 더미 금리로 만든 문장은 리포트가 집계해야 한다.
        card["_rate_as_of"] = snap.get("as_of")
        card["_rate_dummy"] = bool(snap.get("dummy"))
    if missing:
        card["_rate_missing"] = missing


def _check_claim(card: dict) -> None:
    """주장 성립 조건을 판정한다. 거짓이면 `_suppressed`, 판정 불가면 `_verify_first`."""
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
            # 수치만 갈아끼우면 "~보다 높다"는 결론이 거짓이 된 채로 남는다. 카드를 뺀다.
            card["_suppressed"] = (
                f"주장 미성립: {cond['claim']} (현재 {left:g}% vs {right:g}%)")
        return
    if kind == "unknown":
        # 판정 근거가 시스템에 없다. 지어내지 않고, 카드를 살리되 확인을 요구한다.
        card["_verify_first"] = f"{cond['claim']} — {cond.get('why', '근거 확인 필요')}"


def apply_freshness(cards: list[dict]) -> None:
    for c in cards:
        _fill_rate_slots(c)
        _check_claim(c)


def usable(card: dict) -> bool:
    """후보군에 올릴 수 있는 카드인가. 주장이 깨진 카드는 제외한다."""
    return not card.get("_suppressed")


def load_kb(data_dir: Path = DATA_DIR) -> KnowledgeBase:
    """단일 레코드 형태의 지식 파일을 적재해 KnowledgeBase 로 재구성한다.

    저장 형태는 통합 레코드({id, kind, fields})이지만, 검색·검증 로직이 기대하는
    화법/사실/자료 dict 로 되돌려 담는다(다운스트림 무변경).

    검색 대상 종류는 kinds.json 의 `consumed: retrieval` 선언에서 읽는다 — 새 종류를 선언하면
    적재가 따라오고, 여기 목록을 고칠 일이 없다.
    """
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
                kb.pitches.append(card)   # 같은 객체를 공유한다(사본 아님)
            elif kind == "fact":
                # 팩트는 **두 자리에 같은 객체로** 산다 — `facts` 는 id 로 참조하는 자리
                # (화법의 supporting_facts·전략 근거), `cards` 는 검색 색인이다. 예전에는
                # 앞엣것만 있어서 팩트가 LLM 카드 선택의 후보가 못 됐다(9종 중 유일).
                # 사본을 만들면 한쪽만 고쳐지는 자리가 생기므로 화법과 같은 규약으로 둔다.
                kb.facts[card["id"]] = card

    seen: dict[str, dict] = {}
    for r in st.records():
        did = r.get("_doc_id")
        if did and did not in seen:
            seen[did] = {"doc_id": did, "meta": {"title": r.get("_doc_title")}}
    kb.docs = list(seen.values())

    # 시효성 처리 → 주장이 깨진 카드 격리.
    # 소비처가 여럿이라(검색·⑥⑦ 후보군·반론 DB·카드 인덱스) 각자 거르게 두면 언젠가
    # 한 곳이 빠진다. 여기서 한 번만 빼고, 뺀 사실은 kb.suppressed 로 남겨 리포트가 본다.
    apply_freshness(kb.cards)
    kb.suppressed = [c for c in kb.cards if not usable(c)]
    if kb.suppressed:
        dropped = {c["id"] for c in kb.suppressed}
        kb.cards = [c for c in kb.cards if c["id"] not in dropped]
        kb.pitches = [c for c in kb.pitches if c["id"] not in dropped]
    return kb


def source_label(kb: KnowledgeBase, card: dict) -> str | None:
    """카드의 출처 한 줄 — "문서명(부서, 시점)". 원천 문서를 못 찾으면 None.

    06 기능정의가 요구하는 '근거와 기준시점 표기'의 최소 단위다. 지어내지 않는다 —
    레지스트리에 없으면 표시하지 않는다.

    레코드의 `source.doc` 가 없으면 파일 단위 선언(`meta.source_doc` → `_doc_ref`)으로
    물러선다. 손으로 저작한 화법 파일(pitch_ch0*·guide01)이 그 경우다 — 파일 하나가
    통째로 원천 문서 하나라 레코드마다 같은 doc 를 반복해 적지 않았다.
    """
    doc = kb.docs_by_id.get(
        (card.get("_source") or {}).get("doc") or card.get("_doc_ref") or "")
    if not doc:
        return None
    meta = ", ".join(x for x in (doc.get("dept"), doc.get("published")) if x)
    return f"{doc['title']} ({meta})" if meta else doc["title"]


def origin_of(kb: KnowledgeBase, card: dict) -> str:
    """답변에 붙일 출처 한 줄. **적재 파일 이름(`_doc`)은 절대 쓰지 않는다.**

    `_doc` 은 지식 파일의 이름표라서 "영업 화법 — 06/03 영업화법"처럼 변환본 이름이 나온다.
    그걸 출처라고 내보내면 행원이 고객에게 옮길 수 없는 답이 된다(사내 json 파일명이지
    원문 문서가 아니다). 그래서 원천 문서 → 원문 표기 → 추출지식 절 순으로 물러서고,
    끝내 없으면 없다고 말한다. 지어내지 않는다.
    """
    label = source_label(kb, card)
    if label:
        return label
    if card.get("source_text"):                       # 원문이 스스로 밝힌 출처 표기
        return str(card["source_text"])
    locator = (card.get("_source") or {}).get("locator")
    if locator:
        # 원천 문서가 지정되지 않은 카드(대개 여러 자료를 합친 '통합' 항목).
        # 추출지식의 절 제목까지는 밝힐 수 있으므로 거기까지 말한다.
        section = locator.split("§", 1)[1].strip() if "§" in locator else locator
        return f"원천 문서 미지정 — 추출지식 § {section}"
    return "출처 미상 — 확인 필요"


# ─────────────────────────────────────────────────────────────
# 검색  (LLM 호출 없음 · 결정적)
# ─────────────────────────────────────────────────────────────

def score_parts(
    pitch: dict, customer_type=None, objection_type=None, stage=None, utterance=None
) -> tuple[float, float]:
    """(태그점수, 내용관련도) 를 나눠서 돌려준다.

    태그점수는 순위를 매기는 데만 쓰고, 채택 여부는 내용관련도로 판단한다.
    고객유형·단계는 어떤 질문에나 붙는 상수 보너스라서, 이것만으로 통과시키면
    지식베이스와 무관한 질문에도 엉뚱한 카드가 딸려 나온다.

    거절유형(objection_type)은 내용관련도로 계산해 채택에 관여한다. LLM 이 라벨을
    잘못 붙이면 발화 근거 없이 채택될 수 있는데(확신 있는 오답), 그 케이스는 검색을
    좁히기보다 verify 노드가 질문 의도와 대조해 걸러낸다(정답률↑, 재현율 유지).
    """
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
        topical_s += (
            max((_sim(utterance, ex) for ex in pitch.get("trigger_examples", [])), default=0.0) * 4.0
        )
        flat = re.sub(r"[^0-9a-zA-Z가-힣]", "", utterance)
        for kw in list(tags.get("topics", [])) + [pitch["title"]]:
            k = re.sub(r"[^0-9a-zA-Z가-힣]", "", kw)
            if len(k) >= 2 and k in flat:
                topical_s += 0.8

    return tag_s, topical_s


# 내용관련도 채택 기준. 실측상 유관 질문 0.55~2.1, 무관 질문 0.00~0.42 로 갈린다.
MIN_TOPICAL = 0.5


def _matches_scope(pitch: dict, customer_type=None, stage=None, **_ignored) -> bool:
    """stage/customer_type 이 주어지면, 그 값(또는 '공통')과 안 맞는 카드는 후보에서 제외한다.

    챕터가 늘어나면서 거절유형 라벨이 챕터를 넘어 같은 문자열을 쓰는 경우가 생겼다
    (예: '수수료 비교'가 퇴직금/계약이전 두 챕터에 다 있음). stage 를 아는 경우엔
    이 필터가 먼저 다른 챕터 카드를 걸러내므로, 라벨이 같아도 채점 단계까지 안 간다.
    """
    tags = pitch["tags"]
    if stage and tags.get("stage") not in (stage, "공통"):
        return False
    if customer_type:
        ctypes = tags.get("customer_type") or []
        if customer_type not in ctypes and "공통" not in ctypes:
            return False
    return True


# 세그먼트가 걸린 카드에 얹는 태그 가산점. 태그 쪽에만 더한다 — 내용관련도(MIN_TOPICAL) 문턱은
# 그대로 두어야 "이 고객 세그먼트에 걸렸다"는 이유만으로 무관한 카드가 채택되지 않는다.
SEGMENT_BONUS = 3.0


def retrieve(kb: KnowledgeBase, *, top_k=3, kinds=None, segments=None,
             **criteria) -> list[tuple[float, dict]]:
    """스코프(stage/customer_type)로 후보를 좁힌 뒤, 내용관련도가 기준 미만이면 탈락시키고
    통과한 것만 총점으로 정렬한다.

    `kinds` 를 주면 그 종류의 카드에서 찾는다(기본값 None 은 화법 카드 — 기존 동작).
    `segments` 를 주면 그 세그먼트에 연결된 카드의 순위를 올린다(채택 문턱은 불변).
    """
    pool = kb.pitches if kinds is None else [c for c in kb.cards if c["_kind"] in kinds]
    wanted = set(segments or ())

    hits = []
    for p in pool:
        if not _matches_scope(p, **criteria):
            continue
        tag_s, top_s = score_parts(p, **criteria)
        if top_s < MIN_TOPICAL:
            continue
        if wanted and wanted & set(p.get("segments") or ()):
            tag_s += SEGMENT_BONUS
        hits.append((tag_s + top_s, p))
    hits.sort(key=lambda x: (-x[0], x[1]["id"]))
    return hits[:top_k]


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
             "doc": origin_of(kb, c),
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


# ─────────────────────────────────────────────────────────────
# 검증  (python kb.py)
# ─────────────────────────────────────────────────────────────

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

    # 주의·비고의 역할 선언 누락 — 선언이 없으면 소비 코드가 아무 역할로도 안 세서,
    # 진짜 주의가 조용히 빠진다. 마이그레이션·저작 누락을 여기서 잡는다.
    for card in kb.cards:
        field = ROLE_FIELDS.get(card.get("_kind") or "")
        if not field:
            continue
        for e in card.get(field) or []:
            if (not isinstance(e, dict) or e.get("role") not in _ROLES
                    or not (e.get("text") or "").strip()):
                errors.append(f"[역할누락] {card['id']} {field} 항목에 role 선언이 없다: "
                              f"{str(e)[:50]}")

    # 문서가 늘어날 때 가장 위험한 케이스: 같은 항목에 다른 값
    errors += check_fact_conflicts((f["label"], f["value"]) for f in kb.facts.values())

    # 화법이 참조하지 않는 사실 — 저작 누락 신호다. 다만 06/04 제도상품팩트에서 옮겨온 세트는
    # 화법의 근거로 쓰라고 만든 게 아니라 그 자체가 답변 재료(fact 도구)라, 미참조가 정상이다.
    used = {f for p in kb.pitches for f in p.get("supporting_facts") or []}
    warns += [f"[미사용] {fid} 를 참조하는 화법 없음"
              for fid in kb.facts if fid not in used and not fid.startswith("fact.k04.")]

    return errors, warns


if __name__ == "__main__":
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
