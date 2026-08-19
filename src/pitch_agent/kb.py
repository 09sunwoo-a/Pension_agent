"""지식베이스: 로드 · 검색 · 검증.

외부 라이브러리 의존성 없음 (표준 라이브러리만 사용).
data/ 폴더에 스키마를 따르는 JSON을 넣으면 자동으로 적재된다.

검증 리포트:  python kb.py
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# umbrella 를 import 경로에 올려 공용 모듈을 쓴다(다른 에이전트가 이 파일을 임포트할 때도 동작).
_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.kb_base import (  # noqa: E402
    check_broken_refs,
    check_fact_conflicts,
    ngram_sim as _sim,
)
from common.store import Store  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"


# ─────────────────────────────────────────────────────────────
# 로드
# ─────────────────────────────────────────────────────────────

@dataclass
class KnowledgeBase:
    docs: list[dict] = field(default_factory=list)
    facts: dict[str, dict] = field(default_factory=dict)
    resources: dict[str, dict] = field(default_factory=dict)
    pitches: list[dict] = field(default_factory=list)

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


def load_kb(data_dir: Path = DATA_DIR) -> KnowledgeBase:
    """단일 레코드 형태의 지식 파일을 적재해 KnowledgeBase 로 재구성한다.

    저장 형태는 통합 레코드({id, kind, fields})이지만, 검색·검증 로직이 기대하는
    화법/사실/자료 dict 로 되돌려 담는다(다운스트림 무변경).
    """
    st = Store([data_dir])
    kb = KnowledgeBase()
    for r in st.records("fact"):
        kb.facts[r["id"]] = {"id": r["id"], **(r.get("fields") or {})}
    for r in st.records("resource"):
        kb.resources[r["id"]] = {"id": r["id"], **(r.get("fields") or {})}
    for r in st.records("pitch"):
        kb.pitches.append({"id": r["id"], "_doc": r.get("_doc_title"), **(r.get("fields") or {})})
    seen: dict[str, dict] = {}
    for r in st.records():
        did = r.get("_doc_id")
        if did and did not in seen:
            seen[did] = {"doc_id": did, "meta": {"title": r.get("_doc_title")}}
    kb.docs = list(seen.values())
    return kb


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


def retrieve(kb: KnowledgeBase, *, top_k=3, **criteria) -> list[tuple[float, dict]]:
    """스코프(stage/customer_type)로 후보를 좁힌 뒤, 내용관련도가 기준 미만이면 탈락시키고
    통과한 것만 총점으로 정렬한다."""
    candidates = [p for p in kb.pitches if _matches_scope(p, **criteria)]

    hits = []
    for p in candidates:
        tag_s, top_s = score_parts(p, **criteria)
        if top_s < MIN_TOPICAL:
            continue
        hits.append((tag_s + top_s, p))
    hits.sort(key=lambda x: (-x[0], x[1]["id"]))
    return hits[:top_k]


def build_context(kb: KnowledgeBase, hits: list[tuple[float, dict]]) -> str:
    """검색된 카드 + 그 카드가 참조하는 사실/자료만 프롬프트용 텍스트로 만든다.

    전체 KB를 넣지 않기 때문에 문서가 늘어나도 프롬프트 크기는 일정하다.
    """
    if not hits:
        return "(관련 화법 없음)"

    blocks, used_facts, used_res = [], [], []
    for sc, p in hits:
        t = p["tags"]
        lines = [
            f"### [{p['id']}] {p['title']}  (관련도 {sc:.1f} · {p['_doc']} p.{p.get('page')})",
            f"- 단계 {t['stage']} | 고객 {', '.join(t.get('customer_type') or [])}"
            + (f" | 거절유형 {t['objection_type']}" if t.get("objection_type") else ""),
        ]
        if p.get("dialogue"):
            lines.append("- 원문 스크립트:")
            lines += [f"    {d['speaker']}: {d['text']}" for d in p["dialogue"]]
        lines.append("- 핵심 포인트:")
        lines += [f"    · {k}" for k in p["key_points"]]
        for label, key in (("실행 팁", "tips"), ("주의사항", "cautions")):
            if p.get(key):
                lines.append(f"- {label}:")
                lines += [f"    · {v}" for v in p[key]]
        blocks.append("\n".join(lines))
        used_facts += [f for f in p.get("supporting_facts", []) if f not in used_facts]
        used_res += [r for r in p.get("resources", []) if r not in used_res]

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

    # 문서가 늘어날 때 가장 위험한 케이스: 같은 항목에 다른 값
    errors += check_fact_conflicts((f["label"], f["value"]) for f in kb.facts.values())

    used = {f for p in kb.pitches for f in p.get("supporting_facts", [])}
    warns += [f"[미사용] {fid} 를 참조하는 화법 없음" for fid in kb.facts if fid not in used]

    return errors, warns


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    kb = load_kb()
    print(f"문서 {len(kb.docs)} · 화법 {len(kb.pitches)} · 사실 {len(kb.facts)} · 자료 {len(kb.resources)}")
    print(f"고객유형 {kb.customer_types} / 단계 {kb.stages}")
    print(f"거절유형 {kb.objection_types}\n")

    for p in sorted(kb.pitches, key=lambda x: x["id"]):
        t = p["tags"]
        print(
            f"  {p['id']:<16} {p['title'][:38]:<40} "
            f"{t['stage']}/{t.get('objection_type') or '-'} "
            f"(대화 {len(p.get('dialogue', []))} 근거 {len(p.get('supporting_facts', []))} "
            f"트리거 {len(p.get('trigger_examples', []))})"
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
