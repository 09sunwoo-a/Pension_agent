"""전략 제안 에이전트 — LLM 단계 및 최종 조립.

    propose(profile) -> {sentence, insight, source, rejected, facts}

파이프라인
    engine.prepare()      결정적 재료 산출 (요건 판정·적합성 게이트·금액·기대효과)
    LLM                   실행 순서 판단 · 인과 해석 · 문장 작성
    engine.verify()       재료 이탈 검증. 실패 시 sentence 를 비우고 reason 만 남긴다

LLM 미설정, 호출 실패, 응답 파싱 실패, 항목 임의 변경, 재료 이탈 중 어느 경우에도 응답은
반환되지만, 실행 항목(facts["items"])이 있는 한(Tier1) sentence 는 규칙으로 지어내지 않고
비운다 — 'AI 브리핑' 이라는 이름으로 규칙 문장을 AI 산출로 오인시키지 않기 위함이다. 실행
항목 자체가 0건(Tier2/미매칭)일 때만 engine.compose_rule() 의 "실행 항목 없음" 상태 메시지를
쓴다. 어느 경우든 source·reason 필드로 생성 경로와 사유를 확인한다.

미구현 확장 지점
    Tier2 전략 선택. 행내 가이드가 없는 요건 조합에 대해 LLM 이 지식베이스 후보 중에서
    전략을 선택하는 경로이며, 현재는 Tier1(strategies.json 에 정의된 플레이북)만 사용한다.

실행: python agent.py [고객명]
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.strategy_agent import engine
from pension_agent import llm
from pension_agent.strategy_agent import sections
from pension_agent.strategy_agent.customer import PERSONAS, Profile
from pension_agent.strategy_agent.prompts import (
    COACH_PROMPT,
    COACH_SYSTEM,
    FALLBACK_PROMPT,
    FALLBACK_SYSTEM,
    LMS_PROMPT,
    LMS_SYSTEM,
    SELECT_PROMPT,
    SELECT_SYSTEM,
    TOP_HOLDINGS_PROMPT,
    TOP_HOLDINGS_SYSTEM,
    RECOMMEND_PROMPT,
    RECOMMEND_SYSTEM,
    SYSTEM,
    TALK_PROMPT,
    TALK_SYSTEM,
    WHY_CUSTOMER_PROMPT,
    WHY_CUSTOMER_SYSTEM,
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


# 대화형 브리핑 수정(consult_agent.correction)이 건드릴 수 있는 필드 — 전부 LLM 이 쓴 산문
# (의견)이지, engine.py 가 계산한 숫자·상품명·조건 판정이 아니다. 이 밖의 요청은 "시스템이
# 계산한 사실이라 대화로 못 고친다"고 거절해야 한다. 이번 범위는 검토 기록(감사로그)까지만—
# 승인된 수정이 다음 propose() 호출에 자동 반영되는 재적용 루프는 아직 없다.
EDITABLE_FIELDS = {
    "ai_briefing_sentence": "propose() 반환의 sentence",
    "ai_briefing_insight": "propose() 반환의 insight",
    "card_benefit": "facts['items'][i]['card']['benefit'] — 전략 카드의 한 줄 혜택",
}


def _write_talking_scripts(facts: dict) -> None:
    """talking_points 각 항목에 고객 맞춤 대고객 화법 스크립트(script)를 채운다(REQUIREMENTS.md ⑥).

    실패(LLM 미가용·호출 실패·파싱 실패·재료 이탈)해도 조용히 건너뛴다 — 이 경우 talk(화법
    카드 핵심포인트 나열)를 그대로 쓰면 되므로 propose() 전체를 막을 이유가 없다.
    """
    points = facts.get("talking_points") or []
    if not points:
        return
    if not llm.available():
        facts["llm_skipped"]["talking_scripts"] = "LLM 미설정 — 화법 카드 원문만 표시됨"
        return
    payload = [{"title": tp["title"], "화법_핵심": tp["talk"],
                "금액": tp.get("amount"), "상품": tp.get("products")} for tp in points]
    try:
        raw = llm.generate(
            TALK_PROMPT.format(points=json.dumps(payload, ensure_ascii=False, indent=1)),
            system=TALK_SYSTEM, max_tokens=500,
        )
    except Exception as e:  # 게이트웨이 장애·DNS 실패·타임아웃 등
        facts["llm_skipped"]["talking_scripts"] = (
            f"LLM 호출 실패 ({type(e).__name__}) — 화법 카드 원문만 표시됨")
        return
    data = _parse(raw)
    if not isinstance(data, dict):
        facts["llm_skipped"]["talking_scripts"] = "LLM 응답 파싱 실패 — 화법 카드 원문만 표시됨"
        return
    for tp in points:
        script = str(data.get(tp["title"]) or "").strip()
        if script and engine.verify(script, facts)[0]:
            tp["script"] = script


def _write_why_this_customer(facts: dict) -> None:
    """② '왜 이 고객님인가요' 관리 근거를 LLM이 해석해 쓴다(REQUIREMENTS.md §15 — Rule(수치) + LLM(해석)).

    실패하면 engine.prepare() 가 채운 규칙 문장(정량 나열)이 그대로 남는다 — 이 표 항목은
    Rule ● 이 함께 표시된 접점이라, ③ 코칭과 달리 비우지 않고 폴백을 유지한다.
    """
    if not llm.available():
        facts["llm_skipped"]["why_this_customer"] = "LLM 미설정 — 규칙 문장 그대로 표시됨"
        return
    briefing = {k: v for k, v in facts["briefing"].items() if k != "source"}
    try:
        raw = llm.generate(
            WHY_CUSTOMER_PROMPT.format(
                customer=json.dumps(facts["customer"], ensure_ascii=False),
                briefing=json.dumps(briefing, ensure_ascii=False),
                conditions=", ".join(facts["conditions"]) or "없음",
            ),
            system=WHY_CUSTOMER_SYSTEM, max_tokens=300,
        )
    except Exception as e:  # 게이트웨이 장애·DNS 실패·타임아웃 등
        facts["llm_skipped"]["why_this_customer"] = (
            f"LLM 호출 실패 ({type(e).__name__}) — 규칙 문장 그대로 표시됨")
        return
    data = _parse(raw)
    lines = [str(x).strip() for x in (data or {}).get("lines", []) if str(x).strip()]
    if not lines:
        facts["llm_skipped"]["why_this_customer"] = "LLM 응답 파싱 실패 — 규칙 문장 그대로 표시됨"
        return
    bad = [b for line in lines for b in engine.verify(line, facts)[1]]
    if bad:
        facts["llm_skipped"]["why_this_customer"] = (
            f"재료에 없는 값이 포함됨 — 규칙 문장 그대로 표시됨 ({'; '.join(bad)})")
        return
    facts["why_this_customer"] = lines[:3]


def _write_coaching(facts: dict) -> None:
    """③ 현재 운용상태의 AI 코칭 2문장을 생성한다(REQUIREMENTS.md §6.1).

    REQUIREMENTS.md §15 가 이 섹션을 LLM 전용으로 지정했으므로 규칙 폴백을 두지 않는다. 실패하면
    facts["coaching"] 은 None 으로 남고 사유만 llm_skipped 에 적힌다 — 규칙으로 쓴 문장을
    'AI 코칭' 이라는 이름으로 내보내면 직원이 LLM 산출로 오인하기 때문이다.

    두 문장 중 하나라도 검증에 걸리면 통째로 버린다 — 짝이 맞지 않는 코칭을 남기느니 비운다.
    """
    if not llm.available():
        facts["llm_skipped"]["coaching"] = "LLM 미설정 — LLM_BASE_URL / LLM_API_KEY 환경변수 없음"
        return
    briefing = {k: v for k, v in facts["briefing"].items() if k != "source"}
    try:
        raw = llm.generate(
            COACH_PROMPT.format(
                customer=json.dumps(facts["customer"], ensure_ascii=False),
                briefing=json.dumps(briefing, ensure_ascii=False),
                conditions=", ".join(facts["conditions"]) or "없음",
            ),
            system=COACH_SYSTEM, max_tokens=400,
        )
    except Exception as e:  # 게이트웨이 장애·DNS 실패·타임아웃 등
        facts["llm_skipped"]["coaching"] = f"LLM 호출 실패 ({type(e).__name__})"
        return
    data = _parse(raw)
    if not isinstance(data, dict):
        facts["llm_skipped"]["coaching"] = "LLM 응답 파싱 실패 — JSON 객체를 찾을 수 없음"
        return
    headline = str(data.get("headline") or "").strip()
    detail = str(data.get("detail") or "").strip()
    if not headline or not detail:
        facts["llm_skipped"]["coaching"] = "LLM 응답에 headline/detail 없음"
        return
    bad = engine.verify(headline, facts)[1] + engine.verify(detail, facts)[1]
    if bad:
        facts["llm_skipped"]["coaching"] = f"재료에 없는 값이 포함됨 — {'; '.join(bad)}"
        return
    facts["coaching"] = {"headline": headline, "detail": detail}


def _customer_state(p: Profile) -> dict:
    """LLM 에 넘기는 고객 상태 스냅샷(REQUIREMENTS.md §9). 선별·생성 프롬프트가 공유한다."""
    return {
        "투자성향": p.rk, "연령": p.ag, "평가금액": engine.won(p.bal),
        "포트폴리오": dict(zip(engine.PORT_LABELS, p.port, strict=True)),
        "수익률": engine._return_label(p, long=True),
        "투자기간": f"{p.invest_period_years}년" if p.invest_period_years is not None else "미확인",
        "연금수령여부": "수령 중" if p.pension_started else "미개시",
        "운용이력": f"최종 운용변경 이후 {p.nchM}개월 경과",
    }


def _select(p: Profile, facts: dict, key: str, label: str,
            candidates: list[dict], k: int) -> list[dict] | None:
    """후보 중 k개를 LLM 이 고른다(REQUIREMENTS.md §15 — DB 는 Rule, 선별은 LLM).

    LLM 은 후보 '번호'만 돌려준다. 표시 내용은 DB 원문 그대로라 창작이 불가능하므로 verify()
    를 태우지 않는다 — 검증해야 할 자유 문장 자체가 없다.

    실패하면 None 을 돌려주고 사유만 남긴다. 호출부는 규칙 순서(임박 순·id 순) 상위 k개를
    그대로 쓰되, 화면에 'AI 선별 안 됨'을 표시한다 — ⑦⑧⑨ 는 ③ 코칭과 달리 DB 자체가 요건상
    Rule 담당이라, 비우는 것보다 규칙 순서를 보여주고 출처를 밝히는 쪽이 맞다.
    """
    skipped = facts["llm_skipped"]
    if not candidates:
        return None
    if len(candidates) <= k:
        # 후보가 뽑을 개수 이하면 고를 여지가 없다 — 전부 그대로 쓰고 LLM 호출은 아낀다.
        # (콘텐츠가 쌓이면 자연히 이 분기를 벗어나 아래 선별 경로를 탄다)
        return candidates
    listing = "\n".join(
        f"{i}. {json.dumps(c, ensure_ascii=False)}" for i, c in enumerate(candidates))
    try:
        raw = llm.generate(
            SELECT_PROMPT.format(
                customer_state=json.dumps(_customer_state(p), ensure_ascii=False),
                conditions=", ".join(facts["conditions"]) or "없음",
                label=label, candidates=listing, k=k,
            ),
            system=SELECT_SYSTEM, max_tokens=200,
        )
    except Exception as e:  # 게이트웨이 장애·DNS 실패·타임아웃 등
        skipped[key] = f"LLM 호출 실패 ({type(e).__name__}) — 규칙 순서로 표시됨"
        return None
    data = _parse(raw)
    picks = (data or {}).get("pick")
    if not isinstance(picks, list):
        skipped[key] = "LLM 응답 파싱 실패 — 규칙 순서로 표시됨"
        return None
    chosen: list[dict] = []
    for i in picks:
        if isinstance(i, int) and 0 <= i < len(candidates) and candidates[i] not in chosen:
            chosen.append(candidates[i])
    if not chosen:
        skipped[key] = "LLM 이 후보 밖 번호를 지목함 — 규칙 순서로 표시됨"
        return None
    return chosen[:k]


def _select_db_sections(p: Profile, facts: dict) -> None:
    """⑦·⑧·⑨ — DB 가 만든 넓은 후보군에서 LLM 이 이 고객에게 맞는 것만 고른다(REQUIREMENTS.md §15).

    선별 전에는 어느 고객에게나 같은 반론·같은 가이드·같은 세미나가 나갔다. 규칙이 뽑는 순서
    (저작 순·id 순·임박 순)가 고객과 무관하기 때문이다. 후보를 넓게 뽑아 LLM 에 고르게 하는
    것이 이 함수의 전부이며, 실패하면 기존 규칙 순서 상위 n개가 그대로 남는다.
    """
    pools = facts["pools"]
    if obj := _select(p, facts, "objections", "예상 반론", pools["objections"], 2):
        facts["objections"] = obj
    if res := _select(p, facts, "consult_resources", "상담 참고 리소스",
                      pools["consult_resources"], 2):
        facts["consult_resources"] = res

    outreach = facts.get("outreach") or {}
    for key, label in (("event", "이벤트"), ("seminar", "세미나")):
        if picked := _select(p, facts, f"outreach_{key}", label, pools["outreach"][key], 1):
            outreach[key] = picked[0]
    facts["outreach"] = outreach


def _write_top_holdings_insight(p: Profile, facts: dict) -> None:
    """④ 상위 1% 상품 사례에 이 고객 관점의 해석 한 줄을 붙인다(REQUIREMENTS.md §15 — Rule + LLM 해석)."""
    holdings = facts.get("top_holdings") or []
    if not holdings:
        return
    try:
        raw = llm.generate(
            TOP_HOLDINGS_PROMPT.format(
                customer_state=json.dumps(_customer_state(p), ensure_ascii=False),
                holdings=json.dumps(holdings, ensure_ascii=False, indent=1),
            ),
            system=TOP_HOLDINGS_SYSTEM, max_tokens=250,
        )
    except Exception as e:
        facts["llm_skipped"]["top_holdings_insight"] = f"LLM 호출 실패 ({type(e).__name__})"
        return
    insight = str((_parse(raw) or {}).get("insight") or "").strip()
    if not insight:
        facts["llm_skipped"]["top_holdings_insight"] = "LLM 응답에 insight 없음"
        return
    if not engine.verify(insight, facts)[0]:
        facts["llm_skipped"]["top_holdings_insight"] = "재료에 없는 값이 포함됨"
        return
    facts["top_holdings_insight"] = insight


def _write_lms_messages(p: Profile, facts: dict) -> None:
    """⑨ 이벤트·세미나의 LMS 문구를 고객별로 생성한다(REQUIREMENTS.md §15 — LMS 문구는 LLM).

    실패하면 콘텐츠 DB(assets.json)의 고정 문구가 그대로 남는다. 그 경우 전 고객 동일 문구가
    나가므로, 사유를 남겨 화면이 '고객별 생성 아님'을 밝히게 한다.
    """
    outreach = facts.get("outreach") or {}
    state = json.dumps(_customer_state(p), ensure_ascii=False)
    for key in ("event", "seminar"):
        item = outreach.get(key)
        if not item:
            continue
        try:
            raw = llm.generate(
                LMS_PROMPT.format(customer_state=state,
                                  content=json.dumps(item, ensure_ascii=False)),
                system=LMS_SYSTEM, max_tokens=250,
            )
        except Exception as e:
            facts["llm_skipped"]["lms_message"] = (
                f"LLM 호출 실패 ({type(e).__name__}) — 콘텐츠 DB 의 고정 문구가 표시됨")
            return
        message = str((_parse(raw) or {}).get("message") or "").strip()
        if message and engine.verify(message, facts)[0]:
            # 예전에는 여기서 '[더미] ' 접두를 코드가 다시 붙였다. 지금은 붙이지 않는다 —
            # 발송문도 데모 산출물이라 딱지가 없어야 한다는 결정. 대신 보호막을 텍스트가
            # 아니라 게이트로 옮겼다: pension_agent.tools.open_lms_screen() 이 dummy 자산의
            # 문구를 발송 화면에 채우는 것을 거부한다. 접두는 LLM 이 지울 수 있지만
            # 게이트는 못 지운다.
            item["lms_message"] = message
            item["lms_generated"] = True
        else:
            facts["llm_skipped"]["lms_message"] = "생성 문구가 재료를 벗어남 — 고정 문구가 표시됨"


def _recommend(p: Profile, facts: dict) -> dict | None:
    """⑤ '이런 상품이 적합할 수 있어요' — 상품 1개 + 포트폴리오 1개를 LLM 이 폐쇄 후보군에서
    고른다(REQUIREMENTS.md ⑤). 실패(후보 없음·LLM 미가용·파싱 실패·목록 밖 id·재료 이탈)하면
    None 을 반환해 섹션을 노출하지 않는다 — 근거 없는 추천을 보여주느니 비우는 쪽을 택한다.

    비운 사유는 facts["llm_skipped"] 에 남긴다. 화면이 '후보가 없어서 빈 것'과 'LLM 이 죽어서
    빈 것'을 구분해 보여줘야 직원이 판단할 수 있다.
    """
    skipped = facts["llm_skipped"]
    pool = engine.candidate_pool_for_recommendation(p)
    if not pool["products"]:
        skipped["recommendation"] = "적합성 게이트를 통과한 후보 상품이 없음"
        return None
    if not llm.available():
        skipped["recommendation"] = "LLM 미설정 — LLM_BASE_URL / LLM_API_KEY 환경변수 없음"
        return None

    # REQUIREMENTS.md §9 가 상품추천 LLM 입력으로 명시한 항목을 빠짐없이 싣는다. 수익률·운용이력이
    # 빠지면 §17 의 "추천 결과에는 고객 상태 근거가 존재해야 한다"가 구조적으로 성립하지 않는다.
    customer_state = {
        "투자성향": p.rk, "연령": p.ag, "평가금액": engine.won(p.bal),
        "포트폴리오": dict(zip(engine.PORT_LABELS, p.port, strict=True)),
        "수익률": engine._return_label(p, long=True),
        "투자기간": f"{p.invest_period_years}년" if p.invest_period_years is not None else "미확인",
        "연금수령여부": "수령 중" if p.pension_started else "미개시",
        "운용이력": f"최종 운용변경 이후 {p.nchM}개월 경과",
    }
    products_payload = [{
        "id": r["id"], "name": r["name"], "category": r["category"], "risk": r["risk"],
        "return_1y": engine.product_return(r), "region": r.get("region"),
        "asset_class": r.get("asset_class"), "strategy": r.get("strategy_desc"),
        "features": r.get("features"),
        # §9 상품 입력 — TDF 는 위험자산 비중이 성향 적합성 판단의 핵심 변수이고,
        # 디폴트옵션 편입 여부는 미설정 고객(nod 요건) 추천의 근거가 된다.
        "tdf_risk_asset_pct": r.get("tdf_risk_asset_pct"),
        "default_option": r.get("default_option"),
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
    except Exception as e:  # 게이트웨이 장애·DNS 실패·타임아웃 등
        skipped["recommendation"] = f"LLM 호출 실패 ({type(e).__name__})"
        return None

    data = _parse(raw)
    if not isinstance(data, dict):
        skipped["recommendation"] = "LLM 응답 파싱 실패 — JSON 객체를 찾을 수 없음"
        return None

    by_pid = {r["id"]: r for r in pool["products"]}
    product = by_pid.get(str(data.get("product_id") or ""))
    if product is None:
        skipped["recommendation"] = "LLM 이 후보 목록 밖의 상품 id 를 지목함"
        return None
    by_fid = {pf["id"]: pf for pf in pool["portfolios"]}
    portfolio = by_fid.get(str(data.get("portfolio_id") or "")) if data.get("portfolio_id") else None

    verify_facts = engine.recommendation_facts(p, product, portfolio)
    product_reason = str(data.get("product_reason") or "").strip()
    if not product_reason or not engine.verify(product_reason, verify_facts)[0]:
        skipped["recommendation"] = "추천 사유가 비었거나 재료에 없는 값을 포함함"
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

    facts["summaries"] 는 화면이 각 섹션을 접어둘 때 쓰는 요약 2줄이다. _propose() 의 탈출
    경로가 여러 갈래(LLM 미설정·호출 실패·파싱 실패·재료 이탈…)라 각 return 마다 붙이지 않고
    여기서 한 번에 채운다 — 어느 경로로 빠져나오든 요약이 비지 않는다.
    """
    out = _propose(p, use_llm=use_llm, top_n=top_n)
    out["facts"]["summaries"] = engine.section_summaries(
        out["facts"], out["sentence"], out["insight"])
    return out


def _propose(p: Profile, *, use_llm: bool, top_n: int) -> dict[str, Any]:
    facts = engine.prepare(p, top_n=top_n)
    facts["recommendation"] = None
    if not use_llm:
        # LLM 전용 섹션은 규칙으로 대신 채우지 않는다 — 비워두고 사유만 남긴다.
        for key in ("coaching", "recommendation", "talking_scripts", "why_this_customer"):
            facts["llm_skipped"][key] = "LLM 미사용 설정(use_llm=False)"
    else:
        _write_why_this_customer(facts)
        _write_coaching(facts)
        _write_talking_scripts(facts)
        _select_db_sections(p, facts)
        _write_top_holdings_insight(p, facts)
        _write_lms_messages(p, facts)
        facts["recommendation"] = _recommend(p, facts)
    out: dict[str, Any] = {
        "customer": p.nm, "facts": facts, "sentence": "",
        "insight": "", "source": "미생성", "tier": "행내전략", "reason": "", "rejected": [],
    }

    # Tier2 — 매칭 전략이 없으면 LLM 이 대신 참고 의견을 작성한다. 근거 계층은 tier 로 표시한다.
    # "실행 항목이 없다"는 규칙 문장은 LLM 성패와 무관하게 참인 사실 진술이므로 그대로 쓴다 —
    # _fallback() 이 Tier2 LLM 참고의견으로 덮어쓸 수 있으면 덮어쓴다.
    if not facts["items"]:
        out["sentence"] = engine.compose_rule(facts)
        out["source"] = "규칙"
        return _fallback(facts, out, use_llm)

    # Tier1 — 매칭 전략의 확정 재료를 LLM 이 문장으로 옮긴다. 실패하면(아래 각 분기) sentence 를
    # 비운 채 reason 만 남긴다 — 절을 기계적으로 이어붙인 규칙 문장을 'AI 브리핑'으로 오인시키지
    # 않기 위함이다(③ 코칭이 LLM 실패 시 결과를 비우는 것과 같은 원칙).
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
    print(f"  {engine.customer_header_line(f['customer'])}")  # 상단 항목(REQUIREMENTS.md §3.1)
    print("  " + ", ".join(c.split(":", 1)[1] for c in f["conditions"]))
    if f.get("why_this_customer"):
        print(f"\n  [{sections.title('why_this_customer')}]")
        for line in f["why_this_customer"]:
            print(f"    · {line}")
    bf = f["briefing"]
    src = "; ".join(engine.format_sources([bf["source"]]))
    print(f"  [보유현황] {' | '.join(f'{k} {v}' for k, v in bf.items() if k != 'source')}"
          f"  (근거 {src})")
    if f.get("coaching"):
        print(f"\n  [{sections.title('current_state')} — AI 코칭]")
        print(f"    ▷ {f['coaching']['headline']}")
        print(f"      {f['coaching']['detail']}")
    for key, why in (f.get("llm_skipped") or {}).items():
        print(f"  ⚠ AI 생성 안 됨 [{key}] — {why}")
    # ① AI 브리핑 — 긴 지시 문장 대신 항목별 카드로 제시한다.
    print(f"\n  [{sections.title('ai_briefing')}]")
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
    if f.get("problem_situations"):
        names = ", ".join(s["title"] for s in f["problem_situations"][:3])
        more = len(f["problem_situations"]) - 3
        print(f"\n  이 고객의 문제상황: {names}" + (f" 외 {more}건" if more > 0 else ""))
    if f.get("talking_points"):
        print(f"\n  [{sections.title('talking_points')}]")
        for tp in f["talking_points"]:
            print(f"    · ({tp['title']}) {tp.get('script') or tp['talk']}")
            if tp.get("source"):
                print(f"      — 출처 {tp['source']}")
    if f.get("objections"):
        print(f"\n  [{sections.title('objections')}]")
        for ob in f["objections"]:
            print(f"    · \"{ob['objection']}\" → {ob['response']}")
            if ob.get("source"):
                print(f"      — 출처 {ob['source']}")
    if f.get("recommendation"):
        reco = f["recommendation"]
        print(f"\n  [{sections.title('recommendation')}]")
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
    if f.get("consult_resources"):
        print(f"\n  [{sections.title('consult_resources')}]")
        for res in f["consult_resources"]:
            print(f"    · {res['title']} — {res['snippet']}")
            if res.get("screens"):
                print(f"      확인 화면 {' '.join(res['screens'])}")
            if res.get("source"):
                print(f"      — 출처 {res['source']}")
    if f.get("top_holdings"):
        th_sec = sections.BY_KEY["top_holdings"]
        print(f"\n  [{th_sec.full_title}]  ※ {th_sec.note}")
        for th in f["top_holdings"]:
            # 값이 있는 칸만 그린다. 원장의 동연령 비교는 상품명만 주고 설명·수익률이
            # 없는데, 빈 값을 그대로 끼우면 "— (최근 1년 None%)" 이 화면에 나간다.
            bits = [f"    · {th['product_name']}"]
            if th.get("description"):
                bits.append(f"— {th['description']}")
            if th.get("return_1y") is not None:
                bits.append(f"(최근 1년 {th['return_1y']}%)")
            elif th.get("peer_top1_return") is not None:
                bits.append(f"(동연령 상위 1% 평균 수익률 {th['peer_top1_return']}%)")
            print(" ".join(bits))
    outreach = f.get("outreach") or {}
    if outreach.get("event") or outreach.get("seminar"):
        print(f"\n  [{sections.title('outreach')}]")
        for label, item in (("이벤트", outreach.get("event")), ("세미나", outreach.get("seminar"))):
            if item:
                print(f"    · [{label}] {item['name']} ({item['start_date']}~{item['end_date']})")
                if item.get("lms_message"):
                    print(f"      LMS 문구: {item['lms_message']}")
    if f.get("consult_history"):
        print(f"\n  [{sections.CONSULT_HISTORY_TITLE}]")
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
    if not PERSONAS:
        print("등록된 고객이 없습니다. 시연용 고객 데이터가 정해지면 "
              "pension_agent/strategy_agent/customer.py 의 PERSONAS 에 채웁니다.")
        raise SystemExit(1)
    targets = [p for p in PERSONAS if not name or p.nm == name]
    if not targets:
        print(f"'{name}' 에 해당하는 고객이 없습니다. 대상: {', '.join(p.nm for p in PERSONAS)}")
        raise SystemExit(1)
    for p in targets:
        _print(propose(p))
