"""전략 제안 에이전트 — LLM 단계 및 최종 조립.

    propose(profile) -> {sentence, insight, source, rejected, facts}

파이프라인
    engine.prepare()      결정적 재료 산출 (요건 판정·적합성 게이트·금액·기대효과)
    LLM                   실행 순서 판단 · 인과 해석 · 문장 작성
    engine.verify()       재료 이탈 검증. 실패 시 engine.compose_rule() 결과로 대체

LLM 미설정, 호출 실패, 응답 파싱 실패, 항목 임의 변경, 재료 이탈 중 어느 경우에도
응답은 반환된다. 폴백 사용 여부와 사유는 반환값의 source·reason 필드로 확인한다.

미구현 확장 지점
    Tier2 전략 선택. 행내 가이드가 없는 요건 조합에 대해 LLM 이 지식베이스 후보 중에서
    전략을 선택하는 경로이며, 현재는 Tier1(strategies.json 에 정의된 플레이북)만 사용한다.

실행: python agent.py [고객명]
"""

from __future__ import annotations

import json
import re
from typing import Any

import engine
import llm
from customer import PERSONAS, Profile
from prompts import (
    FALLBACK_PROMPT,
    FALLBACK_SYSTEM,
    RECOMMEND_PROMPT,
    RECOMMEND_SYSTEM,
    SYSTEM,
    TALK_PROMPT,
    TALK_SYSTEM,
    WRITE_PROMPT,
)


def _prompt(facts: dict) -> str:
    items = [{
        "id": it["id"], "title": it["title"], "구분": it["kind"], "주체": it["actor"],
        "clause": it["clause"], "근거": it["evidence"], "금액": it["amount"],
        "상품": it["products"], "시급성": it["urgency"],
        "핵심가치": it["card"]["tag"], "혜택": it["card"]["benefit"],
        "수익률개선효과": it["effect_grade"],
        "필수여부": it["mandatory"],
    } for it in facts["items"]]
    briefing = {k: v for k, v in facts["briefing"].items() if k != "source"}
    return WRITE_PROMPT.format(
        customer=json.dumps(facts["customer"], ensure_ascii=False),
        briefing=json.dumps(briefing, ensure_ascii=False),
        conditions=", ".join(facts["conditions"]) or "없음",
        items=json.dumps(items, ensure_ascii=False, indent=1),
        risk_cap=facts["risk_cap"],
        needs_slot=", ".join(facts["needs_slot"]) or "없음",
        dropped="; ".join(facts["dropped"]) or "없음",
        cautions="; ".join(facts["cautions"]) or "없음",
    )


def _fallback_prompt(facts: dict) -> str:
    """Tier2 폴백용 프롬프트. 확정 실행 항목이 없으므로 보유현황·요건만 재료로 넘긴다."""
    briefing = {k: v for k, v in facts["briefing"].items() if k != "source"}
    return FALLBACK_PROMPT.format(
        customer=json.dumps(facts["customer"], ensure_ascii=False),
        briefing=json.dumps(briefing, ensure_ascii=False),
        conditions=", ".join(facts["conditions"]) or "없음",
    )


def _parse(raw: str) -> dict | None:
    """응답에서 JSON 객체를 추출한다. 코드블록·전후 설명이 붙어도 처리한다."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _write_talking_scripts(facts: dict) -> None:
    """talking_points 각 항목에 고객 맞춤 대고객 화법 스크립트(script)를 채운다(요건정의서 ⑥).

    실패(LLM 미가용·호출 실패·파싱 실패·재료 이탈)해도 조용히 건너뛴다 — 이 경우 talk(화법
    카드 핵심포인트 나열)를 그대로 쓰면 되므로 propose() 전체를 막을 이유가 없다.
    """
    points = facts.get("talking_points") or []
    if not points or not llm.available():
        return
    payload = [{"title": tp["title"], "화법_핵심": tp["talk"],
                "금액": tp.get("amount"), "상품": tp.get("products")} for tp in points]
    try:
        raw = llm.generate(
            TALK_PROMPT.format(points=json.dumps(payload, ensure_ascii=False, indent=1)),
            system=TALK_SYSTEM, max_tokens=500,
        )
    except Exception:
        return
    data = _parse(raw)
    if not isinstance(data, dict):
        return
    for tp in points:
        script = str(data.get(tp["title"]) or "").strip()
        if script and engine.verify(script, facts)[0]:
            tp["script"] = script


def _recommend(p: Profile) -> dict | None:
    """⑤ '이런 상품이 적합할 수 있어요' — 상품 1개 + 포트폴리오 1개를 LLM 이 폐쇄 후보군에서
    고른다(요건정의서 ⑤). 실패(후보 없음·LLM 미가용·파싱 실패·목록 밖 id·재료 이탈)하면
    None 을 반환해 섹션을 노출하지 않는다 — 근거 없는 추천을 보여주느니 비우는 쪽을 택한다.
    """
    pool = engine.candidate_pool_for_recommendation(p)
    if not pool["products"] or not llm.available():
        return None

    customer_state = {
        "투자성향": p.rk, "연령": p.ag, "평가금액": engine.won(p.bal),
        "포트폴리오": dict(zip(engine.PORT_LABELS, p.port, strict=True)),
        "투자기간": f"{p.invest_period_years}년" if p.invest_period_years is not None else "미확인",
        "연금수령여부": "수령 중" if p.pension_started else "미개시",
    }
    products_payload = [{
        "id": r["id"], "name": r["name"], "category": r["category"], "risk": r["risk"],
        "return_1y": engine.product_return(r), "region": r.get("region"),
        "asset_class": r.get("asset_class"), "strategy": r.get("strategy_desc"),
        "features": r.get("features"),
    } for r in pool["products"]]
    portfolios_payload = [{
        "id": pf["id"], "name": pf["name"], "description": pf["description"],
        "allocation": pf["allocation"],
    } for pf in pool["portfolios"]]

    try:
        raw = llm.generate(
            RECOMMEND_PROMPT.format(
                customer_state=json.dumps(customer_state, ensure_ascii=False),
                products=json.dumps(products_payload, ensure_ascii=False, indent=1),
                portfolios=json.dumps(portfolios_payload, ensure_ascii=False, indent=1),
            ),
            system=RECOMMEND_SYSTEM, max_tokens=700,
        )
    except Exception:
        return None

    data = _parse(raw)
    if not isinstance(data, dict):
        return None

    by_pid = {r["id"]: r for r in pool["products"]}
    product = by_pid.get(str(data.get("product_id") or ""))
    if product is None:
        return None
    by_fid = {pf["id"]: pf for pf in pool["portfolios"]}
    portfolio = by_fid.get(str(data.get("portfolio_id") or "")) if data.get("portfolio_id") else None

    verify_facts = engine.recommendation_facts(p, product, portfolio)
    product_reason = str(data.get("product_reason") or "").strip()
    if not product_reason or not engine.verify(product_reason, verify_facts)[0]:
        return None

    portfolio_reason = str(data.get("portfolio_reason") or "").strip()
    if portfolio and (not portfolio_reason or not engine.verify(portfolio_reason, verify_facts)[0]):
        portfolio = None
        portfolio_reason = ""

    combined_reason = str(data.get("combined_reason") or "").strip()
    if combined_reason and not engine.verify(combined_reason, verify_facts)[0]:
        combined_reason = ""

    return engine.render_recommendation(product, portfolio, product_reason, portfolio_reason, combined_reason)


def propose(p: Profile, *, use_llm: bool = True, top_n: int = engine.TOP_N) -> dict[str, Any]:
    """고객 프로파일에 대한 전략 제안 문장을 생성한다.

    tier 로 답변의 근거 계층을 구분한다(사람이 검토·관리할 수 있도록 provenance 를 남긴다).
      · '행내전략'  strategies.json 플레이북 전략이 매칭됨(Tier1). 확정 재료를 LLM 이 문장화.
      · 'LLM판단'   매칭 전략이 없어 LLM 이 보유현황만 근거로 참고 의견을 작성(Tier2). 검토 필요.
      · '미매칭'    매칭 전략도, 쓸 LLM 도 없어 '제안 항목 없음' 만 반환.
    """
    facts = engine.prepare(p, top_n=top_n)
    if use_llm and llm.available():
        _write_talking_scripts(facts)
        facts["recommendation"] = _recommend(p)
    else:
        facts["recommendation"] = None
    out: dict[str, Any] = {
        "customer": p.nm, "facts": facts, "sentence": engine.compose_rule(facts),
        "insight": "", "source": "규칙", "tier": "행내전략", "reason": "", "rejected": [],
    }

    # Tier2 — 매칭 전략이 없으면 LLM 이 대신 참고 의견을 작성한다. 근거 계층은 tier 로 표시한다.
    if not facts["items"]:
        return _fallback(facts, out, use_llm)

    # Tier1 — 매칭 전략의 확정 재료를 LLM 이 문장으로 옮긴다.
    if not use_llm:
        out["reason"] = "LLM 미사용 설정(use_llm=False)"
        return out
    if not llm.available():
        out["reason"] = "LLM 미설정 — LLM_BASE_URL / LLM_API_KEY 환경변수 없음"
        return out

    try:
        raw = llm.generate(_prompt(facts), system=SYSTEM)
    except Exception as e:  # 게이트웨이 장애·타임아웃 등
        out["reason"] = f"LLM 호출 실패 ({type(e).__name__})"
        return out

    data = _parse(raw)
    if not data:
        out["reason"] = "LLM 응답 파싱 실패 — JSON 객체를 찾을 수 없음"
        return out

    order = [str(x) for x in (data.get("order") or [])]
    sentence = str(data.get("sentence") or "").strip()
    ids = [it["id"] for it in facts["items"]]
    if sorted(order) != sorted(ids):
        out["reason"] = "실행 항목이 임의로 추가·누락됨"
        out["rejected"] = [f"요청 {ids} / 응답 {order}"]
        return out
    if not sentence:
        out["reason"] = "LLM 응답에 sentence 없음"
        return out

    ok, bad = engine.verify(sentence, facts)
    if not ok:
        out["reason"] = "재료에 없는 값이 포함됨 — 폴백 적용"
        out["rejected"] = bad
        return out

    facts["items"].sort(key=lambda it: order.index(it["id"]))
    out["sentence"] = sentence
    out["insight"] = str(data.get("insight") or "").strip()
    out["source"] = "LLM"
    return out


def _fallback(facts: dict, out: dict[str, Any], use_llm: bool) -> dict[str, Any]:
    """Tier2 — 매칭 전략이 없을 때 LLM 이 보유현황만 근거로 참고 의견을 작성한다.

    확정 재료가 없으므로 행내 근거가 없는 'LLM 판단' 이다. tier 로 이를 표시해 사람이 검토·관리
    대상으로 삼게 한다. LLM 이 없거나 산출이 재료(보유현황·요건)를 이탈하면 '제안 항목 없음'
    규칙 문장을 유지하고 tier 를 '미매칭' 으로 남긴다(환각 차단은 Tier1 과 동일하게 verify 적용)."""
    out["tier"] = "미매칭"
    if not use_llm:
        out["reason"] = "행내 매칭 전략 없음 · LLM 미사용 설정(use_llm=False)"
        return out
    if not llm.available():
        out["reason"] = "행내 매칭 전략 없음 · LLM 미설정"
        return out
    try:
        raw = llm.generate(_fallback_prompt(facts), system=FALLBACK_SYSTEM)
    except Exception as e:  # 게이트웨이 장애·타임아웃 등
        out["reason"] = f"행내 매칭 전략 없음 · LLM 호출 실패({type(e).__name__})"
        return out

    data = _parse(raw)
    sentence = str((data or {}).get("sentence") or "").strip()
    if not sentence:
        out["reason"] = "행내 매칭 전략 없음 · LLM 응답 없음/파싱 실패"
        return out

    ok, bad = engine.verify(sentence, facts)
    if not ok:
        out["reason"] = "행내 매칭 전략 없음 · LLM 산출이 보유현황 밖 값 포함 → 폴백"
        out["rejected"] = bad
        return out

    out["sentence"] = sentence
    out["insight"] = str((data or {}).get("insight") or "").strip()
    out["source"] = "LLM"
    out["tier"] = "LLM판단"
    out["reason"] = "행내 근거 없음 — LLM 참고 의견(검토 필요)"
    return out


def _print(r: dict) -> None:
    f = r["facts"]
    print(f"\n■ {r['customer']}  ·  요건 {len(f['conditions'])}건")
    print("  " + ", ".join(c.split(":", 1)[1] for c in f["conditions"]))
    if f.get("why_this_customer"):
        print("\n  [왜 이 고객님인가요]")
        for line in f["why_this_customer"]:
            print(f"    · {line}")
    bf = f["briefing"]
    src = "; ".join(engine.format_sources([bf["source"]]))
    print(f"  [보유현황] {' | '.join(f'{k} {v}' for k, v in bf.items() if k != 'source')}"
          f"  (근거 {src})")
    # 전략 제안 — 긴 지시 문장 대신 항목별 카드로 제시한다.
    print("\n  [전략 제안]")
    if r.get("insight"):
        print(f"    ▷ {r['insight']}")
    if not f["items"]:  # Tier2 LLM 참고 의견 또는 '제안 항목 없음'
        print(f"    {r['sentence']}")
    for i, it in enumerate(f["items"], 1):
        c = it["card"]
        print(f"    {i}. {c['headline']}  ·  {c['tag']}")
        if c["product"]:
            tgt = f"  ·  대상 {c['target']}" if c["target"] else ""
            print(f"       추천 상품 {c['product']}{tgt}")
        elif c["action"]:
            print(f"       실행 {c['action']}")
        print(f"       {c['benefit']}")
    if f.get("talking_points"):
        print("\n  [상담 화법]")
        for tp in f["talking_points"]:
            print(f"    · ({tp['title']}) {tp.get('script') or tp['talk']}")
    if f.get("objections"):
        print("\n  [예상 반론 및 대응]")
        for ob in f["objections"]:
            print(f"    · \"{ob['objection']}\" → {ob['response']}")
    if f.get("recommendation"):
        reco = f["recommendation"]
        print("\n  [이런 상품이 적합할 수 있어요]")
        prod = reco["product"]
        print(f"    · 상품: {prod['name']} — {prod['description']} (최근 1년 {prod['return_1y']}%)")
        print(f"      사유: {prod['reason']}")
        if reco.get("portfolio"):
            pf = reco["portfolio"]
            alloc = ", ".join(f"{a['product_name']} {a['weight_pct']}%" for a in pf["allocation"])
            print(f"    · 포트폴리오: {pf['name']} — {alloc}")
            print(f"      사유: {pf['reason']}")
        if reco.get("combined_reason"):
            print(f"    · 종합: {reco['combined_reason']}")
    if f.get("top_holdings"):
        print("\n  [수익률 상위 1% 고객님들이 많이 담은 상품은?]  ※ 이 고객 대상 추천 아님, 참고용")
        for th in f["top_holdings"]:
            print(f"    · {th['product_name']} — {th['description']} (최근 1년 {th['return_1y']}%)")
    outreach = f.get("outreach") or {}
    if outreach.get("event") or outreach.get("seminar"):
        print("\n  [고객님께 안내해보세요]")
        for label, item in (("이벤트", outreach.get("event")), ("세미나", outreach.get("seminar"))):
            if item:
                print(f"    · [{label}] {item['name']} ({item['start_date']}~{item['end_date']})")
    if f.get("consult_history"):
        print("\n  [상담 이력]")
        for line in f["consult_history"]:
            print(f"    · {line}")
    if f["rationale"]:
        print("\n  [판단근거]")
        for s in f["rationale"]:
            print(f"    · {s}")
    if f["source_titles"]:
        print(f"\n  [근거 문서] {'; '.join(f['source_titles'])}")
    if f.get("regulations"):
        print("\n  [근거 규정]")
        for rg in f["regulations"]:
            print(f"    · ({rg['title']}) {rg['regulation']}")
    if f["alternatives"]:
        print("\n  [다른 제안] 수익 개선폭 순")
        for i, a in enumerate(f["alternatives"], 1):
            print(f"    {i}. {a['clause']}  [{engine.effect_label(a['effect_grade'])}]")
    _tier = {"행내전략": "행내 전략 근거", "LLM판단": "⚠ LLM 판단 · 행내 근거 없음(검토 필요)",
             "미매칭": "미매칭 · 제안 없음"}.get(r.get("tier"), r.get("tier", ""))
    print(f"\n  근거 구분: {_tier}")
    print(f"  생성 경로: {r['source']}" + (f" ({r['reason']})" if r["reason"] else ""))
    for b in r["rejected"]:
        print(f"    · 리젝 사유: {b}")
    for label, key in (("고지 필요", "cautions"), ("확인 필요", "needs_confirm"),
                       ("제안 보류", "unverified")):
        for v in f[key]:
            print(f"  [{label}] {v}")


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    name = " ".join(sys.argv[1:]).strip()
    targets = [p for p in PERSONAS if not name or p.nm == name]
    if not targets:
        print(f"'{name}' 에 해당하는 고객이 없습니다. 대상: {', '.join(p.nm for p in PERSONAS)}")
        raise SystemExit(1)
    for p in targets:
        _print(propose(p))
