"""문자열 n-gram 유사도 — 검색 채점의 기초. 도메인 중립·결정적·표준 라이브러리만.

화법 카드의 태그 스코어링(stage·customer_type·objection_type)이나 전략 게이트처럼
도메인에 묶인 로직은 각 에이전트에 있다. 여기 있는 것은 어떤 지식베이스에도 필요한
"두 문장이 얼마나 닮았나" 하나뿐이다.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[^0-9a-zA-Z가-힣]")


# ─────────────────────────────────────────────────────────────
# 문자열 유사도 (LLM 없음 · 결정적)
# ─────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """검색 비교용 정규화 — 숫자·영문·한글만 남긴다."""
    return _WORD.sub("", text or "")


def ngrams(text: str, n: int = 2) -> set[str]:
    t = normalize(text)
    if len(t) < n:
        return {t} if t else set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def ngram_sim(a: str, b: str, n: int = 2) -> float:
    """두 문자열의 n-gram Jaccard 유사도 (0.0~1.0)."""
    ga, gb = ngrams(a, n), ngrams(b, n)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0
