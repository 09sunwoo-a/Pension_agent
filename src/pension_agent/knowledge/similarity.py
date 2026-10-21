"""문자열 n-gram 유사도 — 검색 채점용. 도메인 중립·결정적."""

from __future__ import annotations

import re

_WORD = re.compile(r"[^0-9a-zA-Z가-힣]")


def normalize(text: str) -> str:
    """검색 비교용 정규화 — 숫자·영문·한글만 유지."""
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
