"""원장 한 항목(Evidence)의 규약과 조립(_ev). 도구가 내놓는 근거는 전부 이 꼴이다.

이 파일이 `tools/` 가 아니라 여기 있는 이유는 원장 helper(`ledger.py`)가 Evidence 를 쓰기
때문이다 — evidence/ 는 tools/ 를 임포트하지 않는다(`tests/infra/s03_boundaries.py`).

━━ 모든 도구는 같다. 다른 것은 `atomic` 목록 하나다 ━━
도구는 종류로 갈리지 않는다. 전부 근거를 내놓고, compose 가 그 근거로 답을 쓴다. 화법도
예외가 아니다 — 화법은 `atomic` 이 비어 있는 도구일 뿐이다.

`atomic` 은 **원문 그대로여야 하는 스팬** 목록이다. 이게 필요한 이유는 verify_texts 가
수치의 *집합 포함* 검사라서, 원장에 있는 숫자를 **잘못 짝지은 것을 못 잡기** 때문이다.
"총급여 5,500만원 이하 16.5%, 초과 13.2%" 가 원장에 있으면 "초과면 16.5%" 도 통과한다
(두 숫자가 다 원장에 있으므로). 값과 조건이 한 줄에 붙어 있어 분리 검증이 불가능하다.
그래서 그 줄은 **통째로 그대로** 나가야 한다.

원문 요구는 두 종류이고, **도구가 명시적으로 갈라 선언한다**(숫자가 있는지로 추론하지
않는다 — ⚠ 경고문이 화면번호를 인용하고 있어서 수치 주장으로 오판되는 일이 실제로 있었다).

  atomic    값 + 그 값이 성립하는 조건이 붙은 한 덩이. 답변이 그 숫자를 쓸 때만 원문을
            요구한다(값을 언급하지 않는 답변까지 강요하면 전부 표 덤프가 된다).
            어기면 생성문을 **폐기**한다 — 틀린 짝을 옳은 블록 옆에 남겨둘 수 없다.
  notices   빠지면 안 되는 표시(⚠ 유의 · 「하지 말 것」 · 「본부 지침 아님」). 언급 여부와
            무관하게 항상 요구한다. 누락은 답변이 틀린 게 아니라 덜 갖춰진 것이므로
            블록을 **덧붙여** 채운다. **LLM 에 필수 인용으로 넘기지 않는다**(2026-09-22) —
            넘기면 카드 원문(「-다」체)이 본문 한가운데 절반만 베껴지고 코드가 전문을 또
            붙여 같은 단서가 겹쳐 선다. 코드가 답변 아래에 ※ 로 한 번 세운다.

atomic 이 비어 있는 도구(pitch·customer)는 수치 집합 검사만 걸린다.
"""

from __future__ import annotations

from typing import TypedDict

from pension_agent.consult_agent.evidence import marks as MARKS
from pension_agent.consult_agent.evidence import relations as REL
from pension_agent.consult_agent.state import KB


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
    # 출처 한 건이 «답변이 실제로 썼는지» 가릴 스팬. 카드 id → 그 카드에서만 나오는 문구.
    # **선언한 출처만 가려낸다** — 선언이 없는 출처는 판정 대상이 아니라 늘 남는다.
    # 검색으로 찾아온 재료는 선언하지 않는다(그 질문에 답하려고 부른 것이라 전부 근거다).
    # 선언하는 것은 **묶음으로 따라온** 재료다 — `customer` 도구는 원장 값과 함께 화면
    # ⑥⑦⑧ 이 이 고객에게 고른 카드를 싣는데, 「디폴트옵션 등록됐어?」처럼 원장 한 줄로
    # 끝나는 답에도 그 카드 다섯 장이 «근거»로 따라 섰다(2026-09-17 실측, 박정호).
    source_keys: dict[str, list[str]]
    allow: list[str]     # 수치 집합 검사에 허용할 텍스트 — 화면에 안 보이는 재료도 포함
    # 관계 선언을 가진 카드들(knowledge/CLAUDE.md §1·§2). compose 가 답변을 이것과 대조해
    # 값–조건 오짝·알려진 오답을 잡는다(relations.py). 선언이 없는 카드는 여기 없다.
    related: list[dict]
    # 재료 성격 표시 — 신뢰 등급 · 내부용 주의(marks.py). 답변이 그 문장을 인용했는지와
    # 무관하게, 이 재료를 근거로 쓴 답변에는 관련 있는 것으로 본다(§7).
    marks: list[str]
    sources: list[dict]
    meta: dict           # 도구별 부가 정보. 근거 자체가 아닌 것만 담는다


def _clean(spans: list[str] | None) -> list[str]:
    return [x for x in (spans or []) if x and x.strip()]


def _scope(label: str, keys: list[str] | None, notices: list[str] | None) -> dict:
    """카드 한 장의 표시 묶음. `keys` 는 '이 카드가 답변에 쓰였는지'를 가리는 값 스팬이고,
    비어 있으면 판단할 수 없다는 뜻이라 표시를 유지한다(잃는 쪽으로 기울지 않는다)."""
    return {"label": label, "keys": _clean(keys), "notices": _clean(notices)}


def _ev(tool: str, query: str, text: str, sources: list[dict],
        atomic: list[str] | None = None, notices: list[str] | None = None,
        allow: list[str] | None = None, meta: dict | None = None,
        scopes: list[dict] | None = None, cards: list[dict] | None = None,
        source_keys: dict[str, list[str]] | None = None,
        marks: list[str] | None = None) -> Evidence | None:
    """`marks` — 카드 선언에서 오지 않는 재료 성격 표시(§7). 카드가 없는 재료(상담 기록)가
    «이 재료가 어떤 성격인가»를 밝히는 자리다. 카드에서 온 표시 뒤에 잇는다 — 표시는 답변
    끝 「── 참고한 자료」 블록에 코드가 세우고, `notices` 와 달리 LLM 이 본문에 쓰지 않는다."""
    if not text.strip():
        return None
    atomic, notices = _clean(atomic), _clean(notices)
    card_marks = MARKS.notes_for(KB, cards or [])
    return {"tool": tool, "query": query, "text": text,
            "marks": card_marks + [m for m in _clean(marks) if m not in card_marks],
            "related": [c for c in (cards or []) if REL.declared(c)],
            "atomic": atomic, "notices": notices,
            # 카드별로 나눠 선언하지 않은 도구는 블록 하나를 통째로 한 묶음으로 본다.
            "notice_scopes": scopes if scopes is not None else (
                [_scope(sources[0].get("title") or tool if sources else tool, atomic, notices)]
                if notices else []),
            "source_keys": {k: _clean(v) for k, v in (source_keys or {}).items() if _clean(v)},
            "allow": allow if allow is not None else [text], "sources": sources,
            "meta": meta or {}}
