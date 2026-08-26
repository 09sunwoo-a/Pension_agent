"""정의 자체의 무결성 검사 — `python -m pension_agent.strategy_agent.engine`.

근거 정합성은 지식베이스 원문을 열어 대조한다. sources 필드의 **존재 여부**만 검사하면
세액공제 전략이 리밸런싱 콜 스크립트를 근거로 인용해도 통과한다. 그 구간을 메우는 것이
이 모듈의 존재 이유다.
"""

from __future__ import annotations

import re

from pension_agent import config
from pension_agent.knowledge.checks import check_broken_refs, check_duplicate_ids
from pension_agent.strategy_agent.customer import CONDS, PRIO, RISK
from pension_agent.strategy_agent.engine import catalog
from pension_agent.strategy_agent.engine.catalog import (
    ACTOR_SUFFIX,
    BRIEFING_SOURCE,
    CLAUSE_ENDINGS,
    LOOKUP_MARKERS,
    TIME_PRESSURE_MARKERS,
    VERBS,
)
from pension_agent.strategy_agent.engine.products import _branch_defs
from pension_agent.strategy_agent.engine.text import _ret_of
from pension_agent.strategy_agent.support import pitch_kb


# ─────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)


def _texts(field) -> list[str]:
    """주의 필드를 문자열 목록으로 편다. 종류에 따라 모양이 둘이다 — 화법은 문자열 목록,
    절차·방법론은 역할이 붙은 `{role, text}` 목록이다(`knowledge/CLAUDE.md` §3).

    여기서는 **역할을 가리지 않는다.** 이 값은 답변에 표시할 문장이 아니라 "이 카드가
    무엇에 대한 것인가"를 재는 검색용 blob 이라, 저작 메모도 단서로는 쓸모가 있다.
    역할로 걸러야 하는 표시 경로는 `consult_agent/kb.py::role_texts` 를 쓴다.
    """
    return [e["text"] if isinstance(e, dict) else e
            for e in (field or []) if e and (not isinstance(e, dict) or e.get("text"))]


def _source_blob(kb, sid: str) -> str | None:
    """인용 대상 카드의 검색용 텍스트. 존재하지 않으면 None.

    **종류를 가리지 않는다.** 전략의 근거는 화법·팩트만이 아니라 고객 세그먼트 정의(누가
    대상인가)·업무 절차(어느 화면에서 확인하나)·관리 방법론(무엇을 먼저 보나)일 수 있다.
    예전에는 화법과 팩트만 알아서, 그 밖의 카드를 근거로 걸면 '지식베이스에 없음'이 됐다.
    """
    card = next((c for c in kb.cards if c["id"] == sid), None)
    if card:
        parts = [card.get("title") or "", *((card.get("tags") or {}).get("topics") or []),
                 *(card.get("key_points") or []), *(card.get("tips") or []),
                 *_texts(card.get("cautions")),
                 card.get("summary") or "", card.get("situation") or "", card.get("action") or ""]
        return _norm(" ".join(p for p in parts if p))
    fct = kb.facts.get(sid)
    if fct:
        return _norm(" ".join([fct["label"], fct.get("value") or "", fct.get("detail") or ""]))
    return None


def validate() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    valid_conds = set(PRIO)
    ids = {s["id"] for s in catalog.SPECS + catalog.SYSTEM_STRATEGIES}

    errors += check_duplicate_ids(s.get("id", "?") for s in catalog.SPECS + catalog.SYSTEM_STRATEGIES)

    # 구조 검증은 플레이북(catalog.SPECS)과 시스템 전략(catalog.SYSTEM_STRATEGIES)에 함께 적용한다. 근거 정합성
    # (sources 교차검증·요건 미대응)은 원천 근거를 전제하는 catalog.SPECS 에만 적용한다(아래 별도 루프).
    for s in catalog.SPECS + catalog.SYSTEM_STRATEGIES:
        sid = s.get("id", "?")

        for req in ("id", "title", "kind", "actor", "clause", "evidence", "confidence"):
            if not s.get(req):
                errors.append(f"[필수누락] {sid} → {req}")
        if s.get("actor") not in ("직원", "고객"):
            errors.append(f"[잘못된값] {sid} actor={s.get('actor')} — 행위 주체 미지정")
        if s.get("actor") == "고객" and s.get("kind") not in ACTOR_SUFFIX:
            errors.append(f"[주체불일치] {sid} — actor=고객 이나 kind={s.get('kind')} 에 부착 어미 미정의")
        if not s.get("when") and not s.get("trigger"):
            errors.append(f"[필수누락] {sid} → when 또는 trigger")
        if s.get("when") and s["when"] not in valid_conds:
            errors.append(f"[잘못된요건] {sid} when={s['when']}")

        for key in ("clause", "clause_if_asset", "clause_if_merged"):
            tpl = s.get(key)
            if not tpl:
                continue
            if any(m in tpl for m in LOOKUP_MARKERS):
                errors.append(
                    f"[로직화대상] {sid}.{key} — 시스템이 보유한 데이터의 조회 지시. "
                    "절이 아니라 briefing 으로 표현한다")
            if any(m in tpl for m in TIME_PRESSURE_MARKERS):
                errors.append(
                    f"[시급성문구] {sid}.{key} — 데이터로 확정되지 않은 시한 표현. "
                    "시급성은 urgency 필드와 실행 순서로 표현한다")
            if tpl.endswith("{product_action}"):
                verbs = [q.get("verb") for q in _branch_defs(s).values()]
                if not verbs or any(v not in VERBS for v in verbs):
                    errors.append(f"[절규약위반] {sid}.{key} — 분기 verb 미정의 또는 허용 외: {verbs}")
            elif not tpl.endswith(CLAUSE_ENDINGS):
                errors.append(f"[절규약위반] {sid}.{key} — 명사형 동작 종결 아님: …{tpl[-14:]}")

        # 상담 화법(talk) — 직원 접촉 전략의 콜스크립트 요약. 고객 대상 동작(actor=고객)에는
        # 두지 않으며, 내부 조회 지시어를 담지 않는다(briefing 과 동일 규약).
        talk = s.get("talk")
        if talk:
            if s.get("actor") != "직원":
                errors.append(f"[화법대상] {sid} — talk 은 직원(접촉) 전략에만 정의한다")
            if any(m in talk for m in LOOKUP_MARKERS):
                errors.append(f"[로직화대상] {sid}.talk — 조회 지시어 포함. 화법에 담지 않는다")

        errors += check_broken_refs(
            (r["target"] for r in s.get("resolves", []) if r.get("target")), ids,
            owner=f"{sid} resolves")
        for rule in s.get("resolves", []):
            policy = rule.get("policy")
            if policy not in ("always", "conditional", "max_amount"):
                errors.append(f"[잘못된값] {sid} resolves.policy={policy}")
            elif policy == "max_amount" and not rule.get("group"):
                errors.append(f"[필수누락] {sid} resolves(max_amount) → group")
            elif policy in ("always", "conditional") and not rule.get("target"):
                errors.append(f"[필수누락] {sid} resolves({policy}) → target")
            if policy == "conditional" and not rule.get("condition"):
                errors.append(f"[필수누락] {sid} resolves(conditional) → condition")
        errors += check_broken_refs(s.get("requires", []), set(catalog.CAPS), owner=f"{sid} requires")

        conf = s.get("confidence")
        if conf not in ("행내가이드", "규정", "추정"):
            errors.append(f"[잘못된값] {sid} confidence={conf}")
        if conf == "행내가이드" and not s.get("sources"):
            errors.append(f"[근거없음] {sid} — confidence=행내가이드 이나 sources 미기재")
        if conf == "규정" and not s.get("regulation"):
            errors.append(f"[근거없음] {sid} — confidence=규정 이나 regulation 미기재")
        if conf == "추정" and s.get("sources"):
            warns.append(f"[등급불일치] {sid} — confidence=추정 이나 sources 존재")
        if s.get("sources") and not s.get("topic_keys"):
            errors.append(f"[검증불가] {sid} — sources 가 있으나 topic_keys 미기재로 대조 불가")
        if s.get("pool") and not s.get("amount"):
            errors.append(f"[정의모순] {sid} — pool 정의됨, amount 미정의")
        if (s.get("impact") or {}).get("base_ref") not in (None, *catalog.BASELINES):
            errors.append(f"[깨진참조] {sid} impact.base_ref={s['impact']['base_ref']}")

    errors += check_duplicate_ids(f"products {r['id']}" for r in catalog.PRODUCTS)
    for r in catalog.PRODUCTS:
        if r["risk"] not in RISK:
            errors.append(f"[잘못된값] {r['id']} risk={r['risk']}")
        if _ret_of(r) is None:
            errors.append(f"[수익률누락] {r['id']} — exp_return·rate 모두 없음")
        if "risk_asset" not in r:
            errors.append(f"[필수누락] {r['id']} → risk_asset (위험자산 한도 산입 여부)")
        if r.get("payout") == "원리금보장" and r.get("depositor_protection") is None:
            errors.append(f"[필수누락] {r['id']} → depositor_protection")

    for cid, c in catalog.CAPS.items():
        if c.get("status") not in ("available", "unavailable", "unknown"):
            errors.append(f"[잘못된값] capabilities {cid} status={c.get('status')}")

    # 근거 정합성 — 지식베이스 원문 대조
    kb = pitch_kb()
    if kb is None:
        warns.append(f"[교차검증생략] 지식베이스 적재 실패({config.KB_DATA_DIR}) — sources 정합성 미검증")
    else:
        for s in catalog.SPECS:
            keys = [_norm(k) for k in s.get("topic_keys", []) if _norm(k)]
            for sid in s.get("sources", []):
                blob = _source_blob(kb, sid)
                if blob is None:
                    errors.append(f"[깨진참조] {s['id']} sources '{sid}' — 지식베이스에 없음")
                    continue
                if keys and not any(k in blob for k in keys):
                    errors.append(
                        f"[근거오인용] {s['id']} → '{sid}' 는 {s['topic_keys']} 를 다루지 않음")
            pitch_ids = {c["id"] for c in kb.pitches}
            for ref in s.get("pitch_refs", []) or []:
                if ref.get("customer_type") not in ("직장인", "사업자", "공통"):
                    errors.append(
                        f"[잘못된값] {s['id']} pitch_refs customer_type={ref.get('customer_type')}")
                if ref.get("id") not in pitch_ids:
                    errors.append(f"[깨진참조] {s['id']} pitch_refs '{ref.get('id')}' — pitch에 없음")
        if _source_blob(kb, BRIEFING_SOURCE) is None:
            errors.append(f"[깨진참조] briefing source '{BRIEFING_SOURCE}' — 지식베이스에 없음")
        for b in catalog.BASELINES.values():
            if b.get("source") not in kb.facts:
                errors.append(f"[깨진참조] baselines {b['id']} source '{b.get('source')}'")
        for a in catalog.ASSETS:
            rid = a.get("source_resource")
            if rid and rid not in kb.resources:
                errors.append(f"[깨진참조] assets {a['id']} source_resource '{rid}'")
            elif rid and kb.resources[rid].get("customer_facing") is False \
                    and a.get("customer_facing") is True:
                errors.append(
                    f"[제공범위위반] assets {a['id']} — 원본 자료 '{rid}' 는 고객 직접 제공 금지")

    warns += [f"[미대응] 요건 '{c}({CONDS[c]})' 에 대응하는 전략 정의 없음"
              for c in sorted(valid_conds - {s.get("when") for s in catalog.SPECS})]
    warns += [f"[제안보류] {cid} status={c['status']} — {c['label']}"
              for cid, c in catalog.CAPS.items() if c.get("status") != "available"]
    warns += [f"[발송보류] assets {a['id']} customer_facing 미확정 — {a['name']}"
              for a in catalog.ASSETS if a.get("customer_facing") is not True]
    return errors, warns
