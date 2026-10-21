"""규칙 기반 문장 합성·섹션 요약·verify() — LLM 미가용·검증 실패 시 그대로 화면 노출되는 폴백."""

from __future__ import annotations

import re

from pension_agent.tools.customer_solution.engine.catalog import ACTOR_SUFFIX, PRODUCTS
from pension_agent.evidence.verify import verify as _verify


def final_clause(item: dict) -> str:
    """행위 주체 반영 최종 절 — 고객 운용지시 사항은 제안·안내로 감쌈."""
    if item["actor"] == "직원":
        return item["clause"]
    return item["clause"] + ACTOR_SUFFIX.get(item["kind"], "하도록 제안")


def compose_rule(facts: dict) -> str:
    """절 연결 단일 문장(LLM 폴백)."""
    clauses = [final_clause(it) for it in facts["items"]]
    if not clauses:
        return "제안 가능한 실행 항목이 없습니다. (해당 요건 없음 또는 적합성 게이트에서 전량 제외)"
    if len(clauses) == 1:
        return clauses[0] + "하세요."
    if len(clauses) == 2:
        return f"{clauses[0]}하고, {clauses[1]}하세요."
    mid = ", ".join(c + "하고" for c in clauses[1:-1])
    return f"{clauses[0]}한 뒤, {mid}, {clauses[-1]}하세요."


def _josa(word: str, with_batchim: str, without_batchim: str) -> str:
    """받침 유무별 조사 선택. 끝 글자가 한글이 아니면 받침 없음."""
    ch = next((c for c in reversed(word) if c.isalnum()), "")
    has = "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28
    return with_batchim if has else without_batchim


def _join_ko(names: list[str]) -> str:
    """이름을 '와/과'로 연결."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    head = ", ".join(names[:-1])
    return f"{head}{_josa(names[-2], '과', '와')} {names[-1]}"


def _sentences(text: str) -> list[str]:
    """문장 분리(종결부호 뒤 공백 기준)."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def section_summaries(facts: dict, sentence: str, insight: str) -> dict[str, dict[str, str]]:
    """섹션별 접힘 요약(headline + detail) — 코드 합성. 상품명 축약 금지, 빈 섹션은 키 미생성."""
    out: dict[str, dict[str, str]] = {}

    lines = _sentences(sentence)
    if lines:
        out["ai_briefing"] = {"headline": lines[0],
                              "detail": " ".join(lines[1:]) or insight}

    why = facts.get("why_this_customer") or []
    if why:
        out["why_this_customer"] = {"headline": why[0], "detail": " ".join(why[1:])}

    if facts.get("coaching"):
        out["current_state"] = dict(facts["coaching"])

    top = facts.get("top_holdings") or []
    if top:
        names = [t["product_name"] for t in top]
        out["top_holdings"] = {
            "headline": f"{_join_ko(names)}{_josa(names[-1], '을', '를')} 많이 담고 있어요.",
            "detail": "이 고객 대상 추천이 아니라, 고성과 고객의 운용 사례로 참고해보세요.",
        }

    reco = facts.get("recommendation")
    if reco:
        names = [reco["product"]["name"]]
        if reco.get("portfolio"):
            names.append(reco["portfolio"]["name"])
        out["recommendation"] = {
            "headline": f"{_join_ko(names)}{_josa(names[-1], '을', '를')} 추천해요.",
            "detail": reco.get("combined_reason") or reco["product"]["reason"],
        }

    points = facts.get("talking_points") or []
    if points:
        title = points[0]["title"]
        out["talking_points"] = {
            "headline": f"{title}{_josa(title, '을', '를')} 짚어 이렇게 말해볼까요?",
            "detail": points[0].get("script") or points[0]["talk"],
        }

    obj = facts.get("objections") or []
    if obj:
        out["objections"] = {
            "headline": f"“{obj[0]['objection']}” 는 고객에게 이렇게 대응해보세요.",
            "detail": obj[0]["response"],
        }

    res = facts.get("consult_resources") or []
    if res:
        names = [r["title"] for r in res]
        out["consult_resources"] = {
            "headline": f"{_join_ko(names)}{_josa(names[-1], '을', '를')} 확인해보세요.",
            "detail": "상담의 설득력을 높이는 당행 노하우·가이드예요.",
        }

    outreach = facts.get("outreach") or {}
    picks = [(lbl, outreach[k]) for lbl, k in (("이벤트", "event"), ("세미나", "seminar"))
             if outreach.get(k)]
    if picks:
        labelled = [f"{it['name']} {lbl}" for lbl, it in picks]
        out["outreach"] = {
            "headline": f"{_join_ko(labelled)}{_josa(labelled[-1], '을', '를')} 안내해보세요.",
            "detail": "고객의 관심도를 높이고 상담 후속 접점으로 활용하세요.",
        }

    return out


def verify(sentence: str, facts: dict, *, extra=()) -> tuple[bool, list[str]]:
    """재료 밖 수치·미등록 상품명 검사. `extra` 는 facts 밖 재료(⑨ 일정·링크 등)."""
    return _verify(sentence, facts, known_products={r["name"] for r in PRODUCTS}, extra=extra)
