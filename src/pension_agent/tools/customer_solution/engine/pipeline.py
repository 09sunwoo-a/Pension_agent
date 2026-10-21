"""prepare() — 요건 판정 → 후보 소집 → 적합성 게이트 → 자금 배분 → 기대효과 → 상위 N 선정.

반환값이 LLM 단계의 유일한 입력. 자금 풀 배분은 전역 제약이라 한 함수 안에서 순차 처리.
"""

from __future__ import annotations

from typing import Any

from pension_agent.common.session_store import summarize_for_briefing
from pension_agent.tools.customer_solution.customer import (
    CONDS,
    PREF,
    RISK,
    Profile,
    conditions,
)
from pension_agent.tools.customer_solution.engine.catalog import (
    ALT_N,
    PROTECTION_LIMIT,
    BY_ID,
    CAPS,
    MIN_ALLOC,
    SPECS,
    TOP_N,
)
from pension_agent.tools.customer_solution.engine.compose import final_clause
from pension_agent.tools.customer_solution.engine.products import (
    _branch_defs,
    finalize_products,
    static_candidates,
)
from pension_agent.tools.customer_solution.engine.recommend import top_reference_products
from pension_agent.tools.customer_solution.engine.render import (
    _action,
    _briefing,
    _build_ctx,
    _account_state,
    _customer_header,
    _eval_condition,
    _trim_target,
    _why_this_customer,
)
from pension_agent.tools.customer_solution.engine.scoring import (
    _amount,
    _card,
    _effect_grade,
    _impact,
    _score,
    _urgency,
    _yield_delta,
)
from pension_agent.tools.customer_solution.engine.text import _Ctx, _pname, format_sources, won
from pension_agent.tools.customer_solution.situations import problem_situations
from pension_agent.tools.customer_solution.support import (
    consult_resource_candidates,
    consult_resources,
    next_event_and_seminar,
    objection_candidates,
    outreach_candidates,
    pick_objections,
    pitch_talk,
    pick_talking_points,
)


def _owned_product_names(p: Profile) -> list[str]:
    """원장에 이름이 있는 상품(보유분 + 동연령 상위1%) — verify 인용 허가용, 고유계정대 제외.

    원장 값만 사용(LLM 산출 금지 — 지어낸 이름이 재료와 답변에서 서로를 근거로 통과하는 순환 방지).
    """
    names = [h["name"] for h in (p.holdings or [])
             if h.get("name") and h.get("type") != "고유계정대"]
    peer = p.peer or {}
    names += [*(peer.get("top1_funds") or []), *(peer.get("top1_etfs") or [])]
    return sorted({n for n in names if n})


def prepare(p: Profile, top_n: int = TOP_N) -> dict[str, Any]:
    """고객 프로필 → 확정 사실(LLM 단계의 유일한 입력)."""
    conds = conditions(p)
    blocked: dict[str, str] = {}
    dropped: list[str] = []
    unverified: list[str] = []
    needs_confirm: list[str] = []
    cautions: list[str] = []

    # 1) 후보 소집 — 성립 요건 전략만(정원 채우기 금지)
    cands = [s for s in SPECS if s.get("when") in conds]

    # 2) 지원 미확인 기능 전제 전략 보류
    live0 = []
    for s in cands:
        miss = [c for c in s.get("requires", []) if CAPS.get(c, {}).get("status") != "available"]
        if miss:
            labels = ", ".join(CAPS.get(c, {}).get("label", c) for c in miss)
            unverified.append(f"{s['title']} — '{labels}' 지원 여부 미확인으로 제안 보류")
            continue
        live0.append(s)

    # 3) 흡수 — resolves: target(always/conditional) 방향성 흡수, group(max_amount) 최대 조정 폭만
    present = {s["id"] for s in live0}
    absorbed: dict[str, str] = {}
    merged: set[str] = set()
    for s in live0:
        for rule in s.get("resolves", []):
            target = rule.get("target")
            if not target or target not in present:
                continue
            if rule["policy"] == "always":
                absorbed.setdefault(target, s["id"])
            elif rule["policy"] == "conditional" and _eval_condition(rule["condition"], p):
                absorbed.setdefault(target, s["id"])
                merged.add(s["id"])

    # 3-1) 위험자산 목표 전략은 조정 폭 최대만 — 둘 다 실행 시 합산 축소, 정적 흡수 시 목표 미달
    groups: dict[str, list[dict]] = {}
    for s in live0:
        if s["id"] in absorbed:
            continue
        for rule in s.get("resolves", []):
            if rule["policy"] == "max_amount":
                groups.setdefault(rule["group"], []).append(s)
    mandatory_carry: set[str] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        amounts = {s["id"]: _amount(p, s)[0] for s in members}
        win = max(members, key=lambda s: amounts[s["id"]])
        for s in members:
            if s["id"] != win["id"]:
                absorbed.setdefault(s["id"], win["id"])
                if s.get("mandatory"):
                    mandatory_carry.add(win["id"])

    extra_evidence: dict[str, list[str]] = {}
    for tid, by in absorbed.items():
        dropped.append(f"{BY_ID[tid]['title']} — '{BY_ID[by]['title']}' 에 흡수 (동일 원인·재원)")
        extra_evidence.setdefault(by, []).append(tid)
    live = [s for s in live0 if s["id"] not in absorbed]

    # 4) 적합성 상한 — 'mis' 성립 시 성향 선호 등급까지 축소
    cap = p.grade
    if "mis" in conds and PREF.get(p.rk):
        cap = RISK[min(RISK.index(cap), RISK.index(PREF[p.rk]))]

    # 5) 금액 무관 게이트
    built = []
    for spec in live:
        static = static_candidates(p, spec, cap, blocked)
        if _branch_defs(spec) and not any(static.values()):
            dropped.append(f"{spec['title']} — 위험등급·거래채널 게이트 통과 상품 0건")
            continue
        amount, extra = _amount(p, spec)
        built.append({"spec": spec, "static": static, "req": amount, "amount": amount,
                      "extra": extra, "urgency": _urgency(p, spec)})

    # 6) 자금 풀 배분 — 동일 재원 중복 사용 차단
    pools = {"예금": p.dep_amt, "추가납입": p.room * 10_000, "위험자산": p.risk_amt}
    built.sort(key=lambda b: (-b["spec"].get("pool_priority", 0), -b["urgency"]))
    kept = []
    for b in built:
        pool = b["spec"].get("pool")
        if pool and b["req"] <= 0:
            # 대상 0원 제외 — 미제외 시 "초과분 0원을 매수" 같은 절 렌더링
            dropped.append(f"{b['spec']['title']} — 대상 금액 0원 (요건은 성립하나 실행 대상 없음)")
            continue
        if pool:
            alloc = min(b["req"], pools.get(pool, 0))
            if alloc < MIN_ALLOC:
                dropped.append(f"{b['spec']['title']} — 동일 재원({pool}) 이 선행 전략에 배분 완료")
                continue
            pools[pool] -= alloc
            b["amount"] = alloc
        kept.append(b)

    # 7) 상품 확정 — 배분액 기준 최소가입금액·위험자산 여력
    headroom = p.risk_headroom_amt
    final = []
    for b in kept:
        spec = b["spec"]
        hr = None if spec.get("reduces_risk") else headroom
        products, dead = finalize_products(p, spec, b["static"], b["amount"], hr, blocked)
        dropped += dead
        if _branch_defs(spec) and not products:
            dropped.append(
                f"{spec['title']} — 배분액 {won(b['amount'])} 기준 적합성 게이트 통과 상품 0건")
            if spec.get("pool") and b["amount"]:
                pools[spec["pool"]] += b["amount"]  # 미사용 재원 반환
            continue
        if hr is not None and any(r.get("risk_asset") for r in products.values()):
            headroom = max(0, headroom - b["amount"])
        b["products"] = products
        final.append(b)

    # 8) 게이트 결과 의존 조건부 전략
    if _perf_branch_blocked(final, dropped, blocked):
        spec = BY_ID["st.risk_reassess"]
        final.append({"spec": spec, "static": {}, "products": {}, "req": 0, "amount": 0,
                      "extra": {}, "urgency": _urgency(p, spec)})

    # 9) 기대효과·점수·상위 N. 기대효과(원)는 내부용, 노출은 yield_delta
    for b in final:
        b["impact"], b["range"], b["formula"] = _impact(p, b["spec"], b["amount"], b["products"])
        b["yield_delta"] = _yield_delta(b["spec"], b["products"])
        b["mandatory"] = bool(b["spec"].get("mandatory")) or b["spec"]["id"] in mandatory_carry
        b["score"] = _score(b["urgency"], b["impact"], b["range"], b["mandatory"])
    final.sort(key=lambda b: -b["score"])
    selected, rest = final[:top_n], final[top_n:]

    # 나머지는 개선폭 순 상위 ALT_N 건만 '다른 제안', 그 밖은 제외 기록
    rest.sort(key=lambda b: (-(b["yield_delta"] if b["yield_delta"] is not None else -1.0),
                             -b["score"]))
    alternatives, overflow = rest[:ALT_N], rest[ALT_N:]
    dropped += [f"{b['spec']['title']} — 상위 {top_n}건 미포함 (점수 {b['score']})" for b in overflow]

    # 10) 절·근거 렌더링(시급성 순)
    selected.sort(key=lambda b: (-b["urgency"], -b["score"]))
    prev_action = ""
    for b in selected:
        spec = b["spec"]
        action = _action(spec, b["products"])
        # 바로 앞 절과 같은 분기 문구만 축약(비인접 축약 시 지시 대상 불명확)
        if action and action == prev_action and len(b["products"]) > 1:
            action = "앞 항목과 동일한 기준으로 배분"
        prev_action = _action(spec, b["products"])

        asset, ctx = _build_ctx(p, spec, b["products"], b["amount"], action, conds, b["extra"])
        tpl = spec["clause_if_asset"] if asset else spec["clause"]
        if spec["id"] in merged and spec.get("clause_if_merged"):
            tpl = spec["clause_if_merged"]
        b["clause"] = tpl.format_map(ctx)

        ev = spec["evidence_if_merged"] if (spec["id"] in merged and spec.get("evidence_if_merged")) \
            else spec["evidence"]
        b["evidence"] = ev.format_map(ctx)
        # 흡수된 전략 근거는 자기 파생값으로 렌더링(흡수한 쪽 값 사용 시 미정의 슬롯 잔존)
        b["evidence_extra"] = []
        for t in extra_evidence.get(spec["id"], []):
            tspec = BY_ID[t]
            t_amt, t_extra = _amount(p, tspec)
            t_ctx = _Ctx({**ctx, **t_extra, "amount": won(t_amt),
                          "trim_target": _trim_target(p, tspec, conds, t_extra)})
            b["evidence_extra"].append(tspec["evidence"].format_map(t_ctx))

        # needs_confirm 은 직원 재료로 노출 — 저작자용 메모 추가 금지
        if spec.get("impact", {}) and (spec.get("impact") or {}).get("kind") == "tax_credit" \
                and not p.income_bracket:
            needs_confirm.append(
                f"총급여 구간 미확인 — 공제율을 보수적으로 {p.tax_credit_rate:.1%} 적용")
        for r in b["products"].values():
            if r.get("risk_asset") and r.get("risk_asset_exempt") is None:
                needs_confirm.append(f"{r['name']} — 적격 TDF 위험자산 한도 예외 여부 미확인")
            if r.get("depositor_protection") is False:
                cautions.append(f"{r['name']} — 예금자보호 비대상 상품. 가입 전 고지 필요")
            elif r.get("depositor_protection") and b["amount"] > PROTECTION_LIMIT:
                cautions.append(
                    f"{r['name']} — 배분액 {won(b['amount'])} 중 예금자보호 한도"
                    f"({won(PROTECTION_LIMIT)}) 초과분 {won(b['amount'] - PROTECTION_LIMIT)} 안내 필요")

        products_fmt = {k: _pname(v) for k, v in b["products"].items() if v}
        clause_action = final_clause(
            {"actor": spec["actor"], "kind": spec["kind"], "clause": b["clause"]})
        b["card"] = _card(spec, products_fmt, won(b["amount"]) if b["amount"] else None,
                          _effect_grade(b["yield_delta"]), clause_action,
                          spec.get("benefit") or b["evidence"])
        b["talk"] = pitch_talk(spec, p.customer_type)

    # 11) 다른 제안 절 렌더링 — 축약·고지/확인 누적 미적용
    alt_items = []
    for b in alternatives:
        spec = b["spec"]
        _, ctx = _build_ctx(p, spec, b["products"], b["amount"],
                            _action(spec, b["products"]), conds, b["extra"])
        clause = spec["clause"].format_map(ctx)
        alt_items.append({
            "id": spec["id"], "title": spec["title"],
            "clause": final_clause({"actor": spec["actor"], "kind": spec["kind"], "clause": clause}),
            "effect_grade": _effect_grade(b["yield_delta"]),
            "yield_delta": b["yield_delta"],
            "source_titles": format_sources(spec.get("sources", []))
                             or ([spec["regulation"]] if spec.get("regulation") else []),
        })

    # ⑥ 표기 선렌더 — support → engine 임포트 금지
    for b in selected + alternatives:
        b["amount_fmt"] = won(b["amount"]) if b.get("amount") else None
        b["products_fmt"] = [_pname(r) for r in b["products"].values() if r]

    # 문제상황 — ⑥⑦⑧ 의 출발점
    situations = problem_situations(p, conds)

    # 판단근거 — 선정 항목 근거 최대 3문장
    rationale = [b["evidence"] for b in selected if b["evidence"]][:3]

    pending = [b["range"] for b in selected if b["range"]]
    return {
        "customer": _customer_header(p),
        "briefing": _briefing(p),
        # 대화형 재료 — 정상 항목 포함(화면 미표시)
        "account_state": _account_state(p),
        "conditions": [f"{c}:{CONDS[c]}" for c in conds],
        # ② 왜 이 고객 — 최대 3개 규칙 문장(LLM 교체 실패 시 그대로)
        "why_this_customer": _why_this_customer(p, conds),
        # ③ AI 코칭 — LLM 전용(규칙 폴백 없음), 미생성 시 None
        "coaching": None,
        # LLM 전용 섹션 미생성 사유
        "llm_skipped": {},
        # ④ 상위 1% 상품 사례(비교 참고용)
        "top_holdings": top_reference_products(p),
        # 인용 허가 목록(추천 목록 아님) — verify.allowed_facts
        "owned_products": _owned_product_names(p),
        # ⑨ 이벤트 1 + 세미나 1
        "outreach": next_event_and_seminar(situations, name=p.nm),
        "items": [{
            "id": b["spec"]["id"], "title": b["spec"]["title"], "kind": b["spec"]["kind"],
            "actor": b["spec"]["actor"], "clause": b["clause"], "evidence": b["evidence"],
            "evidence_extra": b["evidence_extra"], "card": b["card"], "talk": b["talk"],
            "amount": won(b["amount"]) if b["amount"] else None,
            "products": {k: _pname(v) for k, v in b["products"].items() if v},
            "urgency": b["urgency"], "impact": b["impact"], "impact_range": b["range"],
            "formula": b["formula"], "score": b["score"], "mandatory": b["mandatory"],
            "effect_grade": _effect_grade(b["yield_delta"]), "yield_delta": b["yield_delta"],
            "confidence": b["spec"]["confidence"], "sources": b["spec"].get("sources", []),
            "source_titles": format_sources(b["spec"].get("sources", [])),
            "regulation": b["spec"].get("regulation", ""),
        } for b in selected],
        "problem_situations": situations,
        "talking_points": pick_talking_points(p, selected, alternatives, situations),
        "objections": pick_objections(p, selected, situations),
        "consult_resources": consult_resources(p, conds, situations),
        # ⑦⑧⑨ LLM 선별용 후보군. 위 세 필드는 선별 실패 시 규칙 기본값
        "pools": {
            "objections": objection_candidates(p, selected, situations),
            "consult_resources": consult_resource_candidates(p, situations),
            "outreach": outreach_candidates(situations, name=p.nm),
        },
        # 상담 이력 — 세션 저장소 읽기 전용
        "consult_history": summarize_for_briefing(p.id),
        # 규정 근거가 붙은 선정 항목
        "regulations": [{"title": b["spec"]["title"], "regulation": b["spec"]["regulation"]}
                        for b in selected if b["spec"].get("regulation")],
        "rationale": rationale,
        "alternatives": alt_items,
        # 평가 통과 전략 전체(점수 순) — 노출 개수와 분리된 선정 회귀 검사용
        "candidates": [{"id": b["spec"]["id"], "title": b["spec"]["title"],
                        "score": b["score"], "urgency": b["urgency"],
                        "yield_delta": b["yield_delta"], "mandatory": b["mandatory"]}
                       for b in sorted(final, key=lambda x: -x["score"])],
        "impact_total": sum(b["impact"] for b in selected if b["impact"] and b["impact"] > 0),
        "impact_pending": (sum(r[0] for r in pending), sum(r[1] for r in pending)) if pending else None,
        "risk_cap": cap,
        "needs_slot": [b["spec"]["title"] for b in selected if len(b["products"]) > 1],
        "dropped": dropped,
        "unverified": unverified,
        "needs_confirm": sorted(set(needs_confirm)),
        "cautions": sorted(set(cautions)),
        "blocked_products": sorted(set(blocked.values())),
        "sources": sorted({s for b in selected for s in b["spec"].get("sources", [])}),
        "source_titles": format_sources(
            sorted({s for b in selected for s in b["spec"].get("sources", [])})),
    }


def _perf_branch_blocked(final: list[dict], dropped: list[str], blocked: dict) -> bool:
    """실적배당 경로가 위험등급 게이트로 전량 차단됐는지(→ st.risk_reassess 발동)."""
    if not any("위험등급" in v for v in blocked.values()):
        return False
    if any("실적배당 분기" in d for d in dropped):
        return not any(
            r.get("payout") == "실적배당" for b in final for r in b.get("products", {}).values())
    return False
