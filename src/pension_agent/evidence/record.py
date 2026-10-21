"""원장 한 항목(Evidence)의 규약과 조립(_ev). evidence/ 는 tools/ 임포트 금지라 여기 위치.

  atomic    값+조건 한 덩이. 답변이 그 숫자를 쓰면 원문 요구, 어기면 생성문 폐기
  notices   항상 있어야 하는 표시. LLM 에 필수 인용으로 넘기지 않고 누락 시 코드가 ※ 로 덧붙임
"""

from __future__ import annotations

from typing import TypedDict

from pension_agent.evidence import marks as MARKS
from pension_agent.evidence import relations as REL
from pension_agent.state import KB


class Evidence(TypedDict):
    """원장 한 항목 — 이번 턴 도구 하나가 내놓은 근거."""

    tool: str
    query: str
    text: str            # 근거 블록 — compose 재료, 복구 시 덧붙이는 원문
    atomic: list[str]    # 값+조건 스팬 — 숫자 사용 시 원문 요구, 어기면 폐기
    notices: list[str]   # 항상 필요한 표시 — 누락 시 덧붙임
    # 카드 단위 표시 묶음 {"label", "keys", "notices"} — 블록 단위면 안 쓴 카드의 ⚠ 까지 붙음
    notice_scopes: list[dict]
    # 출처별 «답변이 썼는지» 판정 스팬(카드 id → 문구). 묶음으로 따라온 재료만 선언, 미선언 출처는 항상 유지
    source_keys: dict[str, list[str]]
    allow: list[str]     # 수치 집합 검사 허용 텍스트 — 화면 밖 재료 포함
    # 관계 선언 카드(relations.py 대조용)
    related: list[dict]
    # 재료 성격 표시 — 신뢰 등급 · 내부용 주의(marks.py)
    marks: list[str]
    sources: list[dict]
    meta: dict           # 도구별 부가 정보(근거 아닌 것만)


def _clean(spans: list[str] | None) -> list[str]:
    return [x for x in (spans or []) if x and x.strip()]


def _scope(label: str, keys: list[str] | None, notices: list[str] | None) -> dict:
    """카드 한 장의 표시 묶음. keys 가 비면 판정 불가 → 표시 유지."""
    return {"label": label, "keys": _clean(keys), "notices": _clean(notices)}


def _ev(tool: str, query: str, text: str, sources: list[dict],
        atomic: list[str] | None = None, notices: list[str] | None = None,
        allow: list[str] | None = None, meta: dict | None = None,
        scopes: list[dict] | None = None, cards: list[dict] | None = None,
        source_keys: dict[str, list[str]] | None = None,
        marks: list[str] | None = None) -> Evidence | None:
    """Evidence 조립. 빈 text 면 None. marks: 카드 밖 재료 성격 표시(카드 표시 뒤에 연결)."""
    if not text.strip():
        return None
    atomic, notices = _clean(atomic), _clean(notices)
    card_marks = MARKS.notes_for(KB, cards or [])
    return {"tool": tool, "query": query, "text": text,
            "marks": card_marks + [m for m in _clean(marks) if m not in card_marks],
            "related": [c for c in (cards or []) if REL.declared(c)],
            "atomic": atomic, "notices": notices,
            # 카드별 선언이 없으면 블록 전체를 한 묶음으로
            "notice_scopes": scopes if scopes is not None else (
                [_scope(sources[0].get("title") or tool if sources else tool, atomic, notices)]
                if notices else []),
            "source_keys": {k: _clean(v) for k, v in (source_keys or {}).items() if _clean(v)},
            "allow": allow if allow is not None else [text], "sources": sources,
            "meta": meta or {}}
