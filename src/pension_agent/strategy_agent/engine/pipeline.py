"""prepare() — 요건 판정부터 상위 N 선정까지의 한 줄기.

    요건 판정 → 후보 소집 → 적합성 게이트 → 자금 배분 → 기대효과 산출 → 상위 N 선정

반환값이 LLM 단계(agent.py)의 **유일한** 입력이다. 여기 없는 수치는 문장에 나올 수 없고,
`compose.verify()` 가 그것을 대조한다.

자금 풀 배분이 이 한 함수 안에 있는 이유는 제약이 전역이기 때문이다 — 복수 전략이 같은
재원을 중복 사용하지 않게 하려면 배분을 한 곳에서 순서대로 진행해야 한다.
"""

from __future__ import annotations

from typing import Any

from pension_agent.session_store import summarize_for_briefing
from pension_agent.strategy_agent.customer import (
    CONDS,
    PREF,
    RISK,
    Profile,
    conditions,
)
from pension_agent.strategy_agent.engine.catalog import (
    ALT_N,
    PROTECTION_LIMIT,
    BY_ID,
    CAPS,
    MIN_ALLOC,
    SPECS,
    TOP_N,
)
from pension_agent.strategy_agent.engine.compose import final_clause
from pension_agent.strategy_agent.engine.products import (
    _branch_defs,
    finalize_products,
    static_candidates,
)
from pension_agent.strategy_agent.engine.recommend import top_reference_products
from pension_agent.strategy_agent.engine.render import (
    _action,
    _briefing,
    _build_ctx,
    _customer_header,
    _eval_condition,
    _trim_target,
    _why_this_customer,
)
from pension_agent.strategy_agent.engine.scoring import (
    _amount,
    _card,
    _effect_grade,
    _impact,
    _score,
    _urgency,
    _yield_delta,
)
from pension_agent.strategy_agent.engine.text import _Ctx, _pname, format_sources, won
from pension_agent.strategy_agent.situations import problem_situations
from pension_agent.strategy_agent.support import (
    consult_resource_candidates,
    consult_resources,
    next_event_and_seminar,
    objection_candidates,
    outreach_candidates,
    pick_objections,
    pitch_talk,
    pick_talking_points,
)


# ─────────────────────────────────────────────────────────────

def prepare(p: Profile, top_n: int = TOP_N) -> dict[str, Any]:
    """고객 프로파일로부터 확정 사실을 산출한다. 반환값이 LLM 단계의 유일한 입력이다."""
    conds = conditions(p)
    blocked: dict[str, str] = {}
    dropped: list[str] = []
    unverified: list[str] = []
    needs_confirm: list[str] = []
    cautions: list[str] = []

    # 1) 후보 소집 — 해당 요건이 성립하는 전략만 대상으로 한다.
    #    요건이 성립하지 않는 전략을 정원 충족 목적으로 추가하지 않는다.
    cands = [s for s in SPECS if s.get("when") in conds]

    # 2) 기능 요구사항 — 지원 여부가 확인되지 않은 기능을 전제한 전략은 제안하지 않는다.
    live0 = []
    for s in cands:
        miss = [c for c in s.get("requires", []) if CAPS.get(c, {}).get("status") != "available"]
        if miss:
            labels = ", ".join(CAPS.get(c, {}).get("label", c) for c in miss)
            unverified.append(f"{s['title']} — '{labels}' 지원 여부 미확인으로 제안 보류")
            continue
        live0.append(s)

    # 3) 흡수 — resolves 선언에 따라 겹치는 전략을 하나로 정리한다.
    #    target 방식(always/conditional)은 방향성 있는 흡수, group 방식(max_amount)은
    #    같은 대상을 다투는 전략들 중 조정 폭이 가장 큰 것만 남긴다.
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

    # 3-1) 위험자산 목표 비중을 정하는 전략은 조정 폭이 가장 큰 것만 남긴다.
    #      규정 한도(70%)와 성향 기준(예: 30%)이 동시에 성립할 때 둘을 모두 실행하면
    #      합산 축소가 되어 성향 목표를 지나치게 밑돈다. 정적 흡수로 고정하면 반대로
    #      조정 폭이 작은 전략이 큰 전략을 흡수해 목표가 미달된다.
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

    # 4) 적합성 상한 — '투자성향 불일치' 요건 성립 시 성향 선호 등급까지 상한을 축소한다.
    cap = p.grade
    if "mis" in conds and PREF.get(p.rk):
        cap = RISK[min(RISK.index(cap), RISK.index(PREF[p.rk]))]

    # 5) 금액 무관 게이트 — 위험등급·거래채널로 후보 상품을 확보한다.
    built = []
    for spec in live:
        static = static_candidates(p, spec, cap, blocked)
        if _branch_defs(spec) and not any(static.values()):
            dropped.append(f"{spec['title']} — 위험등급·거래채널 게이트 통과 상품 0건")
            continue
        amount, extra = _amount(p, spec)
        built.append({"spec": spec, "static": static, "req": amount, "amount": amount,
                      "extra": extra, "urgency": _urgency(p, spec)})

    # 6) 자금 풀 배분 — 동일 재원의 중복 사용을 차단한다.
    pools = {"예금": p.dep_amt, "추가납입": p.room * 10_000, "위험자산": p.risk_amt}
    built.sort(key=lambda b: (-b["spec"].get("pool_priority", 0), -b["urgency"]))
    kept = []
    for b in built:
        pool = b["spec"].get("pool")
        if pool and b["req"] <= 0:
            # 재원 풀을 선언한 전략(정리·이동·납입)은 전부 금액 산식을 갖는다 — 대상 금액이
            # 0원이면 요건은 성립해도 이 전략이 움직일 것이 없다. 예: 공격투자형 + 예금 100%
            # 는 'mis' 요건이지만 위험자산 초과분이 0원이라 축소 전략(mis_fix)이 아니라 편중
            # 해소(dep_shift)가 답이다. 0원을 그대로 흘리면 "초과분 0원을 매수" 같은 절이
            # 렌더링된다(9케이스 목업 적재 때 실제로 그랬다).
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

    # 7) 상품 확정 — 배분액 기준으로 최소가입금액·위험자산 한도 여력을 판정한다.
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

    # 8) 조건부 전략 — 상품 게이트 결과에 의존하는 전략을 추가한다.
    if _perf_branch_blocked(final, dropped, blocked):
        spec = BY_ID["st.risk_reassess"]
        final.append({"spec": spec, "static": {}, "products": {}, "req": 0, "amount": 0,
                      "extra": {}, "urgency": _urgency(p, spec)})

    # 9) 기대효과·점수 산출 및 상위 N 선정
    #    기대효과(원)는 선정 점수와 검증에 내부적으로 쓰되, 원금 이동 가정에 의존해 근거가
    #    불명확하므로 산출물에는 노출하지 않는다. 노출용 효과는 수익률 개선폭(yield_delta)이다.
    for b in final:
        b["impact"], b["range"], b["formula"] = _impact(p, b["spec"], b["amount"], b["products"])
        b["yield_delta"] = _yield_delta(b["spec"], b["products"])
        b["mandatory"] = bool(b["spec"].get("mandatory")) or b["spec"]["id"] in mandatory_carry
        b["score"] = _score(b["urgency"], b["impact"], b["range"], b["mandatory"])
    final.sort(key=lambda b: -b["score"])
    selected, rest = final[:top_n], final[top_n:]

    # 메인 문장에 포함되지 않은 전략은 '다른 제안' 으로 노출한다. 수익률 개선폭이 큰 순으로
    # 정렬해 상위 ALT_N 건만 남기고, 그 밖은 제외 항목으로 기록한다.
    rest.sort(key=lambda b: (-(b["yield_delta"] if b["yield_delta"] is not None else -1.0),
                             -b["score"]))
    alternatives, overflow = rest[:ALT_N], rest[ALT_N:]
    dropped += [f"{b['spec']['title']} — 상위 {top_n}건 미포함 (점수 {b['score']})" for b in overflow]

    # 10) 절·근거 렌더링 — 실행 순서(시급성 내림차순)로 정렬한다.
    selected.sort(key=lambda b: (-b["urgency"], -b["score"]))
    prev_action = ""
    for b in selected:
        spec = b["spec"]
        action = _action(spec, b["products"])
        # 동일한 분기 문구가 바로 앞 절에서 이미 제시된 경우에만 축약한다.
        # 인접하지 않은 절을 축약하면 지시 대상이 불명확해진다.
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
        # 흡수된 전략의 근거는 그 전략 자신의 파생값(초과분·목표 비중 등)으로 렌더링한다.
        # 흡수한 쪽의 값을 쓰면 정의되지 않은 슬롯이 원문 그대로 남는다.
        b["evidence_extra"] = []
        for t in extra_evidence.get(spec["id"], []):
            tspec = BY_ID[t]
            t_amt, t_extra = _amount(p, tspec)
            t_ctx = _Ctx({**ctx, **t_extra, "amount": won(t_amt),
                          "trim_target": _trim_target(p, tspec, conds, t_extra)})
            b["evidence_extra"].append(tspec["evidence"].format_map(t_ctx))

        if spec.get("clause_if_asset") and not asset:
            needs_confirm.append("고객 발송 가능 자료 미등록 — assets.json 의 customer_facing 확인 필요")
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

        # 카드 — 긴 지시 문장 대신 항목별 헤드라인·핵심가치·추천상품/대상·한 줄 혜택으로 압축한다.
        products_fmt = {k: _pname(v) for k, v in b["products"].items() if v}
        clause_action = final_clause(
            {"actor": spec["actor"], "kind": spec["kind"], "clause": b["clause"]})
        b["card"] = _card(spec, products_fmt, won(b["amount"]) if b["amount"] else None,
                          _effect_grade(b["yield_delta"]), clause_action,
                          spec.get("benefit") or b["evidence"])
        # 상담 화법 — pitch_refs 로 연결된 화법 카드를 고객유형에 맞춰 실시간 조회하고,
        # 참조가 없는 전략은 자체 talk 필드를 쓴다(둘 다 pitch_talk() 안에서 처리).
        b["talk"] = pitch_talk(spec, p.customer_type)

    # 11) 다른 제안 — 메인 문장 밖 전략의 절을 렌더링한다. 선정 항목과 동일한 슬롯 규칙을
    #     쓰되, 축약(prev_action)·고지/확인 누적 같은 문장용 부수효과는 적용하지 않는다.
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

    # ⑥ 재료 선렌더 — support.pick_talking_points() 가 engine 의 표기 유틸(won·_pname)에 역의존하지
    #    않도록, 금액·상품명 표기를 여기서 확정해 항목에 실어 넘긴다(support → engine 임포트 금지).
    for b in selected + alternatives:
        b["amount_fmt"] = won(b["amount"]) if b.get("amount") else None
        b["products_fmt"] = [_pname(r) for r in b["products"].values() if r]

    # 문제상황 — 성립 요건(conds)에 걸리는 06/01 고객세그먼트. ⑥⑦⑧ 은 전략이 아니라 여기서
    # 출발한다(REQUIREMENTS.md §2 "문제상황 정의"). 세그먼트 데이터가 없으면 빈 목록이라
    # 아래 섹션들은 예전 경로로 그대로 동작한다.
    situations = problem_situations(p, conds)

    # 판단근거 — 선정 항목의 근거 중 핵심 1~3문장. 메인 제안의 근거를 압축해 제시한다.
    rationale = [b["evidence"] for b in selected if b["evidence"]][:3]

    pending = [b["range"] for b in selected if b["range"]]
    return {
        "customer": _customer_header(p),
        "briefing": _briefing(p),
        "conditions": [f"{c}:{CONDS[c]}" for c in conds],
        # 왜 이 고객님인가요 — 최대 3개, 정량 중심(REQUIREMENTS.md ②). 코드가 수치를 산출하고,
        # agent._write_why_this_customer() 가 LLM 해석 문장으로 교체를 시도한다(REQUIREMENTS.md §15).
        # LLM 이 없거나 실패하면 이 규칙 문장이 그대로 남는다.
        "why_this_customer": _why_this_customer(p, conds),
        # 현재 운용상태 AI 코칭 — 국소 진단 2문장(REQUIREMENTS.md ③ §6.1). REQUIREMENTS.md §15 가 이 섹션을
        # LLM 전용으로 지정했으므로 규칙 폴백을 두지 않는다 — agent._write_coaching() 이
        # 채우고, LLM 이 없거나 실패하면 None 으로 남아 화면이 '생성되지 않음'을 표시한다.
        # ⑤ 추천(_recommend)이 이미 쓰는 방식과 같다.
        "coaching": None,
        # LLM 전용 섹션 중 이번 호출에서 생성되지 않은 것과 그 사유. 화면이 "규칙으로 쓴 문장을
        # AI 산출로 오인"하지 않도록, 빈 이유를 사람이 읽을 수 있게 남긴다.
        "llm_skipped": {},
        # 수익률 상위 1% 고객 상품 사례 — 비개인화, 비교 참고용(REQUIREMENTS.md ④).
        "top_holdings": top_reference_products(),
        # 고객님께 안내해보세요 — 문제상황에 맞는 이벤트 1개 + 세미나 1개(REQUIREMENTS.md ⑨).
        "outreach": next_event_and_seminar(situations),
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
        # 이 고객의 문제상황 — ⑥⑦⑧ 후보군의 출발점이자 화면에 한 줄로 노출된다.
        "problem_situations": situations,
        # 상담 화법 — 정확히 2개를 보장한다(REQUIREMENTS.md ⑥). 렌더러가 별도 섹션으로 노출한다.
        "talking_points": pick_talking_points(p, selected, alternatives, situations),
        # 예상 반론 — 정확히 2개를 보장한다(REQUIREMENTS.md ⑦).
        "objections": pick_objections(p, selected, situations),
        # 상담에 참고하세요 — 노하우/가이드 스니펫 최대 2개(REQUIREMENTS.md ⑧).
        "consult_resources": consult_resources(p, conds, situations),
        # ⑦·⑧·⑨ 의 넓은 후보군. REQUIREMENTS.md §15 가 이 셋을 'DB(Rule) + 선별(LLM)' 으로 지정하므로,
        # 조회·필터·정렬까지만 여기서 하고 최종 선별은 agent._select_db_sections() 가 맡는다.
        # 위 세 필드(objections·consult_resources·outreach)는 LLM 선별이 실패했을 때 그대로
        # 남는 규칙 기본값이다.
        "pools": {
            "objections": objection_candidates(p, selected, situations),
            "consult_resources": consult_resource_candidates(p, situations),
            "outreach": outreach_candidates(situations),
        },
        # 상담 이력 — consult_agent 가 기록한 대화이력 요약(REQUIREMENTS.md §14). 읽기만 한다 —
        # strategy_agent 는 세션 저장소에 쓰지 않는다("코드=사실" 경계를 대화이력에도 유지).
        "consult_history": summarize_for_briefing(p.id),
        # 근거 규정 — 규정 근거가 붙은 선정 항목. 적합성 원칙 등 판단의 출처를 추적 가능하게 한다.
        "regulations": [{"title": b["spec"]["title"], "regulation": b["spec"]["regulation"]}
                        for b in selected if b["spec"].get("regulation")],
        "rationale": rationale,
        "alternatives": alt_items,
        # 평가를 통과한 전략 전체(점수 내림차순). items 는 그중 상위 TOP_N 건, alternatives 는
        # 그다음 ALT_N 건이다. 화면 노출은 "제안 1개 + 예비 1개"로 좁히지만(07 기능정의 ① 4),
        # 어떤 전략이 이 고객에게 성립했는지 자체는 여기 남는다 — 선정 로직의 회귀를 노출
        # 개수와 분리해 검사할 수 있어야 하고, 노출 개수를 바꿔도 그 검사가 깨지지 않는다.
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
    """실적배당 경로가 적합성 사유로 전량 차단되었는지 판정한다.

    행내 가이드의 핵심 절차는 실적배당 상품 안내이므로, 이 경로가 통째로 막힌 고객에게는
    성향 재진단이 선행되어야 한다. 근거 등급이 '추정'이라 플레이북(strategies.json)이 아니라
    SYSTEM_STRATEGIES(코드)로 따로 관리하는 조건부 안전망이다.
    """
    if not any("위험등급" in v for v in blocked.values()):
        return False
    if any("실적배당 분기" in d for d in dropped):
        return not any(
            r.get("payout") == "실적배당" for b in final for r in b.get("products", {}).values())
    return False
