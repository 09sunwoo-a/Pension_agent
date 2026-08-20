"""LLM 산출물이 재료(facts) 밖으로 나갔는지 판정하는 공용 검증기.

원래 strategy_agent/engine.py 전용이었으나, 대화형 에이전트(consult_agent)가 브리핑 질의·
브리핑 수정 등에서 LLM으로 문장을 쓰는 접점이 늘면서 "숫자·상품명은 재료 밖으로 못 나간다"는
같은 가드가 여러 곳에 필요해져 공용화했다. strategy_agent.engine.verify()는 이 함수를
PRODUCTS로 얇게 감싸 호출한다 — 기존 호출부(agent.py·test_engine.py)는 무수정.
"""

from __future__ import annotations

import re

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_PROD = re.compile(r"KB\s[^\s,·)]+(?:\s[^\s,·)]+)*")


def allowed_facts(facts: dict) -> tuple[set[str], set[str]]:
    """LLM 이 인용할 수 있는 숫자·상품명 집합. 이 범위를 벗어난 표현은 환각으로 판정한다."""
    nums: set[str] = set()
    prods: set[str] = set()
    blob = [str(v) for v in facts["customer"].values()] + list(facts["conditions"])
    blob += [str(v) for k, v in facts["briefing"].items() if k != "source"]
    for it in facts["items"]:
        blob += [it["clause"], it["evidence"], it["amount"] or "", it["formula"], it["talk"]]
        blob += it["evidence_extra"]
        for name in it["products"].values():
            prods.add(name.split("(")[0].strip())
            blob.append(name)
    for t in blob:
        nums.update(_NUM.findall(t))
    return nums, prods


def verify(
    sentence: str, facts: dict, known_products: set[str] = frozenset()
) -> tuple[bool, list[str]]:
    """재료에 없는 수치 또는 게이트 미통과·미등록 상품명이 포함되었는지 검사한다.

    known_products 는 "게이트 미통과"(재료엔 없지만 시스템엔 등록된 상품)와 "미등록"(시스템에도
    없는 상품)을 구분해 거부 사유를 더 정확히 남기기 위한 선택 인자다. 호출부가 넘기지 않으면
    모두 "미등록"으로 보고한다.
    """
    nums, prods = allowed_facts(facts)
    bad = [f"수치 '{n}'" for n in _NUM.findall(sentence) if n not in nums]
    for m in (x.strip() for x in _PROD.findall(sentence)):
        if any(m.startswith(x) or x.startswith(m) for x in prods):
            continue
        bad.append(f"상품명 '{m}' ({'게이트 미통과' if m in known_products else '미등록'})")
    return (not bad), bad
