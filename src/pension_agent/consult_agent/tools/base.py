"""근거(Evidence) 규약 · Tool 선언 · 근거 블록 조립 헬퍼(_ev). 모든 도구 모듈이 여기에 기댄다.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict
from pension_agent.consult_agent import marks as MARKS, relations as REL
from pension_agent.consult_agent.state import KB, AgentState


class Evidence(TypedDict):
    """원장 한 항목. 이번 턴에 어떤 도구가 무엇을 근거로 내놓았는지의 기록."""

    tool: str
    query: str
    text: str            # 근거 블록. compose 의 재료이고, 복구할 때 그대로 덧붙이는 원문이다
    atomic: list[str]    # 값+조건이 붙은 스팬. 그 숫자를 쓰면 원문 요구, 어기면 생성문 폐기
    notices: list[str]   # 항상 답변에 있어야 하는 표시. 누락하면 빠진 표시를 덧붙여 채운다
    # 표시를 **카드 단위로** 묶은 것. 한 도구가 카드 여러 장을 한 블록으로 돌려주기 때문에
    # 블록 단위로만 보면 "답변이 쓴 카드"와 "안 쓴 카드"의 ⚠ 가 구분되지 않는다 — 화면번호
    # 하나를 물었는데 답변이 쓰지도 않은 다른 절차의 주의사항이 따라 붙던 자리다.
    # 항목: {"label": 카드 제목, "keys": 그 카드의 값 스팬, "notices": 그 카드의 표시}
    notice_scopes: list[dict]
    allow: list[str]     # 수치 집합 검사에 허용할 텍스트 — 화면에 안 보이는 재료도 포함
    # 관계 선언을 가진 카드들(knowledge/CLAUDE.md §1·§2). compose 가 답변을 이것과 대조해
    # 값–조건 오짝·알려진 오답을 잡는다(relations.py). 선언이 없는 카드는 여기 없다.
    related: list[dict]
    # 재료 성격 표시 — 신뢰 등급 · 내부용 주의(marks.py). 답변이 그 문장을 인용했는지와
    # 무관하게, 이 재료를 근거로 쓴 답변에는 관련 있는 것으로 본다(§7).
    marks: list[str]
    sources: list[dict]
    meta: dict           # 도구별 부가 정보. 근거 자체가 아닌 것만 담는다


@dataclass(frozen=True)
class Tool:
    name: str
    desc: str            # 계획 프롬프트에 실리는 한 줄 설명
    run: Callable[[AgentState, str], Evidence | None]
    # 진행 표시에 찍히는 재료 이름("단말 화면번호"…). 문구는 코드가 정한다는 규칙
    # (progress.py ①)이 도구에 적용된 자리다 — LLM 이 만든 질의는 진행 표시에 싣지
    # 않는다(질의가 곧 지어낸 문장일 수 있다). 비어 있으면 그 도구는 진행을 알리지 않는다.
    progress: str = ""


def _clean(spans: list[str] | None) -> list[str]:
    return [x for x in (spans or []) if x and x.strip()]


def _scope(label: str, keys: list[str] | None, notices: list[str] | None) -> dict:
    """카드 한 장의 표시 묶음. `keys` 는 '이 카드가 답변에 쓰였는지'를 가리는 값 스팬이고,
    비어 있으면 판단할 수 없다는 뜻이라 표시를 유지한다(잃는 쪽으로 기울지 않는다)."""
    return {"label": label, "keys": _clean(keys), "notices": _clean(notices)}


def _ev(tool: str, query: str, text: str, sources: list[dict],
        atomic: list[str] | None = None, notices: list[str] | None = None,
        allow: list[str] | None = None, meta: dict | None = None,
        scopes: list[dict] | None = None, cards: list[dict] | None = None) -> Evidence | None:
    if not text.strip():
        return None
    atomic, notices = _clean(atomic), _clean(notices)
    return {"tool": tool, "query": query, "text": text,
            "marks": MARKS.notes_for(KB, cards or []),
            "related": [c for c in (cards or []) if REL.declared(c)],
            "atomic": atomic, "notices": notices,
            # 카드별로 나눠 선언하지 않은 도구는 블록 하나를 통째로 한 묶음으로 본다.
            "notice_scopes": scopes if scopes is not None else (
                [_scope(sources[0].get("title") or tool if sources else tool, atomic, notices)]
                if notices else []),
            "allow": allow if allow is not None else [text], "sources": sources,
            "meta": meta or {}}
