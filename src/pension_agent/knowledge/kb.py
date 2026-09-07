"""카드 지식베이스 — 적재 · 시효성 · 출처 · 검색. **두 에이전트가 함께 쓴다.**

`store.py` 가 레코드 파일을 그대로 적재한다면, 여기는 그 레코드를 검색·표시용 카드
(`KnowledgeBase`)로 재구성하고, 적재 길목에서 시효성 수치를 처리하고, 카드의 출처를
원천 문서명으로 되짚고, n-gram+태그 채점으로 카드를 찾는다.

예전에는 이 전부가 `consult_agent/kb.py` 에 있었고, strategy_agent 가 ⑥⑦⑧ 후보군을 만들
때 그것을 거꾸로 임포트했다(strategy → consult 역방향 간선). 두 에이전트가 함께 읽는
지식 카드의 적재는 데이터 접근 계층(이 패키지)이 소유하는 것이 맞아 여기로 옮겼다 —
consult_agent 전용인 LLM 카드 선택용 계층 인덱스·프롬프트 컨텍스트는 그쪽에 남아 있다.

외부 라이브러리 의존성 없음 (표준 라이브러리만 사용).
data/ 폴더에 스키마를 따르는 JSON을 넣으면 자동으로 적재된다.

검증 리포트:  python -m pension_agent.knowledge.kb
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
    # 카드 id → 카드. **손으로 출처 dict 를 만드는 자리**가 원천 문서를 되짚는 데 쓴다
    # (고객 재료의 ⑥⑦⑧ 카드 — tools.py). 검색 경로는 hit 에 카드가 실려 오므로 필요 없다.
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
    # 격리를 끝낸 **뒤에** 색인한다 — 앞에서 만들면 후보군에서 뺀 카드가 색인에 남는다.
    kb.cards_by_id = {c["id"]: c for c in kb.cards}
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


def source_url(kb: KnowledgeBase, card: dict) -> str | None:
    """카드 원천 문서의 게시글 URL. 레지스트리에 없으면 None — 지어내지 않는다.

    URL 은 문서 레지스트리(`doc.url` — 영업점 핫팁 게시글 등)가 가진 것만 쓴다.
    출처 표기(문서명·부서·시점)와 같은 곳에서 한 번만 관리한다는 규약 그대로다.
    """
    doc = kb.docs_by_id.get(
        (card.get("_source") or {}).get("doc") or card.get("_doc_ref") or "")
    return (doc or {}).get("url") or None


def card_source_meta(kb: KnowledgeBase, card_id: str) -> dict:
    """카드 id 하나의 출처 표기 — 문서명(`doc`)과 게시글 URL(`url`). 카드가 없으면 빈 dict.

    검색으로 온 재료는 `sources_of()` 가 hit 의 카드에서 둘을 함께 뽑는다. 문제는 **손으로
    출처 dict 를 만드는 자리**다 — 고객 재료에 실리는 ⑥⑦⑧ 카드가 그렇다. strategy_agent 가
    넘겨주는 항목에는 문서명(`source`)만 있고 URL 이 없어서, 출처에 URL 을 싣기로 한 변경
    (`tools.source_lines`)이 그 재료에만 닿지 않았다 — 화면에는 ↗ 줄이 붙는 근거와 안 붙는
    근거가 섞여 나오고, 직원은 왜 어떤 것만 원문으로 갈 수 있는지 알 수 없다.

    출처 표기를 만드는 곳은 하나여야 한다(§3)는 규약 그대로, 손으로 만드는 자리도 여기서
    되짚어 같은 함수(`origin_of`·`source_url`)를 쓴다.
    """
    card = kb.cards_by_id.get(card_id) or kb.facts.get(card_id)
    if not card:
        return {}
    return {"doc": origin_of(kb, card), "url": source_url(kb, card)}


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


def matches_scope(pitch: dict, customer_type=None, stage=None, **_ignored) -> bool:
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


# ─────────────────────────────────────────────────────────────
# 검증  (python -m pension_agent.knowledge.kb)
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
