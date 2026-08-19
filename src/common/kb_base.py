"""지식베이스 공통 기반 — 도메인 중립. 표준 라이브러리만 사용한다.

pitch_agent/kb.py 와 strategy_agent/engine.py 가 함께 쓰는, **도메인과 무관한 부분**만
여기로 모은다:

  · 문자열 n-gram 유사도            (검색 채점의 기초)
  · '_' 접두 파일을 건너뛰는 로딩   (사람 검토 게이트, store.py 가 사용)
  · 범용 검증 (ID 중복 · 깨진 참조 · 사실충돌)

화법 카드의 태그 스코어링(stage·customer_type·objection_type)이나 전략 게이트처럼
도메인에 묶인 로직은 각 에이전트에 그대로 둔다. 여기 있는 것은 어떤 지식베이스에도
공통으로 필요한 뼈대다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

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


# ─────────────────────────────────────────────────────────────
# 로딩 — '_' 접두 파일은 건너뛴다 (= 사람 검토 게이트)
# ─────────────────────────────────────────────────────────────

def iter_knowledge_files(knowledge_dir: Path) -> Iterator[Path]:
    """'_' 로 시작하지 않는 *.json 만 순서대로 돌려준다.

    '_TEMPLATE.json'·'_draft_*.json' 은 미검토 상태이므로 로더가 지나친다.
    """
    for f in sorted(Path(knowledge_dir).glob("*.json")):
        if not f.name.startswith("_"):
            yield f


# ─────────────────────────────────────────────────────────────
# 범용 검증
# ─────────────────────────────────────────────────────────────

def check_duplicate_ids(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    errors: list[str] = []
    for i in ids:
        if i in seen:
            errors.append(f"[중복ID] {i}")
        seen.add(i)
    return errors


def check_broken_refs(refs: Iterable[str], valid_ids: set[str], *, owner: str = "") -> list[str]:
    """참조한 id 가 실재하지 않으면 오류로 보고한다."""
    prefix = f"{owner} → " if owner else ""
    return [f"[깨진참조] {prefix}{r}" for r in refs if r not in valid_ids]


def check_fact_conflicts(facts: Iterable[tuple[str, str]]) -> list[str]:
    """(label, value) 들에서 같은 label 에 서로 다른 value 가 있으면 보고한다.

    문서가 늘어날 때 가장 위험한 케이스 — 세법·금리 개정이 일부 문서에만 반영되어
    같은 항목 값이 문서마다 갈리는 상황을 잡는다.
    """
    by_label: dict[str, set[str]] = {}
    for label, value in facts:
        by_label.setdefault(label.strip(), set()).add(str(value).strip())
    return [
        f"[사실충돌] '{label}' 에 서로 다른 값 {len(vals)}개 — 개정 반영 누락 의심"
        for label, vals in by_label.items()
        if len(vals) > 1
    ]
