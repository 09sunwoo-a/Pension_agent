"""규칙 기반 문장 합성과 섹션 요약 — LLM 폴백이자 검증 기준선.

LLM 이 없거나(망분리·장애) 산출물이 재료를 벗어나면 여기서 만든 문장이 그대로 화면에
올라간다. 그래서 이 문장은 "임시방편"이 아니라 언제든 고객 앞에 나갈 수 있는 품질이어야
한다. `verify()` 가 LLM 문장을 재료와 대조하는 것도 이 모듈의 몫이다.
"""

from __future__ import annotations

import re

from pension_agent.strategy_agent.engine.catalog import ACTOR_SUFFIX, PRODUCTS
from pension_agent.verify import verify as _verify


# ─────────────────────────────────────────────────────────────

def final_clause(item: dict) -> str:
    """행위 주체를 반영한 최종 절.

    산출 문장은 직원이 읽고 실행하는 문서이다. 재예치·전환·납입·설정은 고객의 운용지시
    사항이므로 직원에 대한 지시로 그대로 쓸 수 없고, 제안·안내 행위로 감싼다.
    """
    if item["actor"] == "직원":
        return item["clause"]
    return item["clause"] + ACTOR_SUFFIX.get(item["kind"], "하도록 제안")


def compose_rule(facts: dict) -> str:
    """절을 연결해 단일 문장을 구성한다.

    절은 명사형 동작으로 종결되므로 접속 규칙만으로 안전하게 결합된다.
    LLM 미가용 또는 검증 실패 시 본 결과를 최종 응답으로 사용한다.
    """
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
    """앞 단어의 받침 유무에 맞는 조사를 고른다('상품을' / '가이드를').

    끝 글자가 한글이 아니면(영문·기호로 끝나는 상품명) 받침 없음으로 본다 — 'CP2-E와'
    처럼 모음으로 읽히는 경우가 대부분이라 이쪽이 덜 틀린다.
    """
    ch = next((c for c in reversed(word) if c.isalnum()), "")
    has = "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28
    return with_batchim if has else without_batchim


def _join_ko(names: list[str]) -> str:
    """항목 이름을 '와/과'로 잇는다. 조사는 바로 앞 이름의 받침을 따른다."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    head = ", ".join(names[:-1])
    return f"{head}{_josa(names[-2], '과', '와')} {names[-1]}"


def _sentences(text: str) -> list[str]:
    """문장 단위로 쪼갠다. 종결부호 뒤 공백만 경계로 본다."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def section_summaries(facts: dict, sentence: str, insight: str) -> dict[str, dict[str, str]]:
    """섹션별 '접힘 상태' 요약 2줄 — 굵은 요약 한 줄 + 부연 한 줄.

    화면은 ①~⑨ 를 모두 접어두고 이 두 줄만 보여준 뒤 펼치게 한다
    (docs/REQUIREMENTS.md §2 — 섹션 정의와 접힘 규칙).

    전부 코드 합성이다 — 재료(facts)에 이미 있는 문장·이름·개수만 재배열하므로 LLM 을 태우지
    않고, 따라서 verify() 도 불필요하다(재료 이탈이 원천적으로 불가능하다). 상품명은 축약하지
    않고 원문 그대로 쓴다 — 금융상품명을 코드가 임의로 줄이면 다른 상품으로 읽힐 수 있다.
    내용이 없는 섹션은 키 자체를 만들지 않으므로, 화면은 키 존재 여부로 노출을 판단하면 된다.
    """
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


# ─────────────────────────────────────────────────────────────
# 검증 — LLM 산출물의 재료 이탈 여부 판정 (common/verify.py 로 공용화됨)
# ─────────────────────────────────────────────────────────────


def verify(sentence: str, facts: dict) -> tuple[bool, list[str]]:
    """재료에 없는 수치 또는 게이트 미통과·미등록 상품명이 포함되었는지 검사한다."""
    return _verify(sentence, facts, known_products={r["name"] for r in PRODUCTS})
