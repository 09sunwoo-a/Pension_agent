"""LLM 산출물이 재료 밖으로 나갔는지 판정하는 공용 검증기.

원래 strategy_agent/engine.py 전용이었으나, 대화형 에이전트(consult_agent)가 브리핑 질의·
브리핑 수정 등에서 LLM으로 문장을 쓰는 접점이 늘면서 "숫자·상품명은 재료 밖으로 못 나간다"는
같은 가드가 여러 곳에 필요해져 공용화했다. strategy_agent.engine.verify()는 이 함수를
PRODUCTS로 얇게 감싸 호출한다 — 기존 호출부(agent.py·test_engine.py)는 무수정.

━━ 재료를 무엇으로 보나 ━━
처음에는 재료가 브리핑 facts dict 하나뿐이어서 allowed_facts() 가 그 스키마
(customer·conditions·briefing·items)를 직접 꺼내 읽었다. 도구 루프가 들어오면서 재료가
"이번 턴에 도구들이 반환한 근거 원장"으로 넓어졌고, 그 원장은 dict 가 아니라 텍스트 묶음이다.
그래서 판정의 알맹이를 allowed_from_texts()/verify_texts() 로 내리고, 기존 두 함수는 facts
스키마를 텍스트로 펴서 그것을 부르는 얇은 껍데기로 남겼다 — 호출부는 무수정이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_PROD = re.compile(r"KB\s[^\s,·)]+(?:\s[^\s,·)]+)*")


def numbers(text: str) -> set[str]:
    """텍스트에 등장하는 수치. 스팬 안에 값이 있는지 판정할 때 호출부가 쓴다
    (compose 가 '값을 말했는데 원문 스팬을 안 실었다'를 잡는 데 필요하다)."""
    return set(_NUM.findall(text))


def allowed_from_texts(texts: Iterable[str]) -> tuple[set[str], set[str]]:
    """텍스트 묶음에서 인용 가능한 숫자를 걷는다. 상품명은 텍스트만으로는 판별할 수 없어
    비워 둔다 — 상품 목록을 아는 호출부가 known_products 로 넘긴다.

    도구 루프의 근거 원장이 이 형태다(도구가 만든 근거 블록들). 원장 밖 숫자가 답변에
    있으면 그 도구가 내놓지 않은 값이므로 지어낸 것이다.
    """
    nums: set[str] = set()
    for t in texts:
        nums.update(_NUM.findall(t))
    return nums, set()


def allowed_facts(facts: dict) -> tuple[set[str], set[str]]:
    """LLM 이 인용할 수 있는 숫자·상품명 집합. 이 범위를 벗어난 표현은 환각으로 판정한다."""
    prods: set[str] = set()
    blob = [str(v) for v in facts["customer"].values()] + list(facts["conditions"])
    blob += [str(v) for k, v in facts["briefing"].items() if k != "source"]
    for it in facts["items"]:
        blob += [it["clause"], it["evidence"], it["amount"] or "", it["formula"], it["talk"]]
        blob += it["evidence_extra"]
        for name in it["products"].values():
            prods.add(name.split("(")[0].strip())
            blob.append(name)
    nums, _ = allowed_from_texts(blob)
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
    return _judge(sentence, nums, prods, known_products)


def verify_texts(
    sentence: str, texts: Iterable[str], known_products: set[str] = frozenset()
) -> tuple[bool, list[str]]:
    """근거 원장(텍스트 묶음) 대조판. 원장에 없는 수치·상품명이 있으면 거부한다.

    상품명은 원장 텍스트에서 뽑지 않고 known_products 만 허용한다 — 텍스트에서 "KB ○○"
    패턴을 긁어 허용 집합에 넣으면, LLM 이 지어낸 상품명이 답변과 원장에 함께 실려 있을 때
    서로를 근거로 통과해버린다.
    """
    nums, _ = allowed_from_texts(texts)
    return _judge(sentence, nums, known_products, known_products)


def _judge(
    sentence: str, nums: set[str], prods: set[str], known_products: set[str]
) -> tuple[bool, list[str]]:
    bad = [f"수치 '{n}'" for n in _NUM.findall(sentence) if n not in nums]
    for m in (x.strip() for x in _PROD.findall(sentence)):
        if any(m.startswith(x) or x.startswith(m) for x in prods):
            continue
        bad.append(f"상품명 '{m}' ({'게이트 미통과' if m in known_products else '미등록'})")
    return (not bad), bad
