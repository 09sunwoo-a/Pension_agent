"""도구 레지스트리 — 에이전트가 할 수 있는 일의 **능력 표면**.

예전에는 능력 표면이 `routing.INTENTS` 였다. 의도 enum 하나 = 노드 하나 = 답변 하나였고,
새 기능을 붙이려면 enum·분기표·노드를 함께 늘려야 했고, 무엇보다 **한 턴에 하나만** 쓸 수
있었다. "이 고객 수수료 불만인데 우리 IRP 수수료가 얼마고 뭐라고 말해야 하나" 같은 질문은
값·고객·화법 세 재료가 필요한데 그중 하나만 골라졌다.

여기서는 능력이 도구 목록이다. 계획 루프(nodes/plan.py)가 한 턴에 여러 도구를 부르고,
반환된 근거를 **원장**에 쌓고, compose 가 그것만으로 답을 만든다.

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
            블록을 **덧붙여** 채운다.

atomic 이 비어 있는 도구(pitch·customer)는 수치 집합 검사만 걸린다.

━━ 반환 규약 ━━
Evidence 또는 None. None 은 "이 도구로는 근거를 못 찾았다"는 뜻이고, 루프는 다른 도구를
시도하거나 원장이 빈 채로 끝낸다(→ 정직한 '없음' 답변). 도구가 억지로 뭔가 만들어내는
경로는 두지 않는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

from pension_agent.consult_agent import kb as KBMOD
from pension_agent.consult_agent import marks as MARKS
from pension_agent.consult_agent import relations as REL
from pension_agent.consult_agent.nodes import facts_qa, pitch as PITCHMOD, procedure_qa, segment_qa
from pension_agent.consult_agent.prompts import ADEQUACY_PROMPT
from pension_agent.consult_agent.kb import retrieve
from pension_agent.consult_agent.select import llm_pick, pick
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.llm import LLMError, generate

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


# ─────────────────────────────────────────────────────────────
# 도구 — 기존 즉답 노드와 **같은 함수**를 쓴다(중복 구현 금지).
# 각자 자기 재료 중 무엇이 원문 그대로여야 하는지(atomic)를 선언한다.
# ─────────────────────────────────────────────────────────────

def _fact(state: AgentState, query: str) -> Evidence | None:
    """확정값. value 는 '값 + 그 값이 성립하는 조건'이 한 덩이라 쪼갤 수 없다 —
    쪼개면 "5,500만원 초과 16.5%" 같은 재조합이 생기고 검증기가 그것을 못 잡는다.
    기준시점도 원문으로 요구한다(값만 옮기고 시점을 빼면 시효성 표시가 사라진다)."""
    hits = _adopt(state, query, facts_qa.search(query), "제도·상품 확정값")
    if not hits:
        return None
    atomic: list[str] = []
    notices: list[str] = []
    scopes: list[dict] = []
    for _s, f in hits:
        keys = [f.get("value") or ""]
        if f.get("as_of"):
            keys.append(f"기준시점 {f['as_of']}")
        marks = [f"⚠ 상태: {f['status']}"] if f.get("status") and f["status"] != "확정" else []
        # **관계를 선언한 팩트는 원문 강제에서 놓아준다**(knowledge/CLAUDE.md 이행 순서 3).
        # value 를 통째로 답변에 싣게 하던 것은 오짝을 잡는 장치가 그것뿐이어서였다.
        # 이제 그 카드는 relations.py 가 조건–값 쌍으로 대조하므로, LLM 이 질문에 맞게 풀어
        # 써도 된다(§4 "원문을 그대로 옮길 의무는 없다"). 선언이 없는 팩트는 그대로 강제된다.
        if not REL.declared(f):
            atomic += keys
        notices += marks
        if marks:
            scopes.append(_scope(f.get("label") or f["id"], keys, marks))
    return _ev("fact", query, facts_qa.render(hits), KBMOD.sources_of(KB, hits),
               atomic=atomic, notices=notices, scopes=scopes, cards=[f for _s, f in hits])


def _procedure(state: AgentState, query: str) -> Evidence | None:
    """절차. 화면번호는 한 글자만 틀려도 직원이 없는 화면을 찾는다(atomic).
    cautions 중 역할이 caution 으로 선언된 것만 표시로 요구한다(notices) — ⚠ 유의 블록의
    본체는 저작 검증 메모(authoring)라 직원 답변에 실으면 진짜 주의가 그 사이에 묻힌다."""
    hits = _adopt(state, query, procedure_qa.search(query), "업무 처리 절차")
    if not hits:
        return None
    atomic: list[str] = []
    notices: list[str] = []
    scopes: list[dict] = []
    for _s, c in hits:
        keys = list(c.get("screens") or [])
        marks = KBMOD.role_texts(c.get("cautions"), "caution")
        if c.get("status") == "확인 필요":
            marks.append("⚠ 자료 간 표기가 어긋나는 절차입니다")
        atomic += keys
        notices += marks
        if marks:
            scopes.append(_scope(c.get("title") or c["id"], keys, marks))
    return _ev("procedure", query, procedure_qa.render(hits), KBMOD.sources_of(KB, hits),
               atomic=atomic, notices=notices, scopes=scopes, cards=[c for _s, c in hits])


def _render_screen(card: dict) -> str:
    lines = [f"■ {card['screen']} {card['title']}  ({card.get('group')})"]
    if card.get("summary"):
        lines.append(f"· 무슨 화면인지: {card['summary']}")
    if card.get("confidence"):
        lines.append(f"· 근거 신뢰도: {card['confidence']}")
    # 비고는 역할 선언(role)대로만 싣는다 — authoring(저작·검증 메모)은 직원에게 띄우지
    # 않는다. 한때 비고 한 덩이를 통째로 실어서 "화면번호안내PDF 미수록 → 관계 확인 필요"
    # 같은 검증 메모가 답변 재료에 그대로 나갔다(consult CLAUDE.md §12 지워진 gap 17).
    for text in KBMOD.role_texts(card.get("note"), "caution"):
        lines.append(f"⚠ {text}")
    for text in KBMOD.role_texts(card.get("note"), "info"):
        lines.append(f"· 비고: {text}")
    mark = stale_mark(card)
    if mark:
        lines.append(mark)
    lines.append(f"· 출처 {KBMOD.origin_of(KB, card)}")
    return "\n".join(lines)


def _screen(state: AgentState, query: str) -> Evidence | None:
    """단말 화면 레지스트리 — "그 업무는 어느 화면인가"에 화면번호로 답한다.

    `procedure` 와 나뉘어 있는 이유는 묻는 것이 다르기 때문이다. 절차는 "어떻게 처리하나"
    (순서·주의)이고, 이건 "어느 화면인가"(번호·화면명)다. 절차 항목이 본문에서 언급하지
    않는 화면도 표에는 있어서, 절차만으로는 답할 수 없는 질문이 있었다 — "포트폴리오
    운용현황 조회 화면 번호는?"이 그랬다.

    화면번호는 한 글자만 틀려도 직원이 없는 화면을 찾으므로 원문 그대로 요구한다(atomic).
    """
    hits = _adopt(state, query, pick(("screen",), query, top_k=3), "단말 화면번호")
    if not hits:
        return None
    atomic = [c["screen"] for _s, c in hits]
    notices: list[str] = []
    scopes: list[dict] = []
    for _s, c in hits:
        # 시효 표시는 카드의 volatile 선언에서 온다(channel 과 같은 규약) — 한때 이 문구가
        # 코드 상수였는데, 그러면 원문 머리말이 바뀔 때 두 곳이 갈린다(§12 지워진 gap 18).
        marks = KBMOD.role_texts(c.get("note"), "caution")
        stale = stale_mark(c)
        if stale:
            marks.append(stale)
        for m in marks:
            if m not in notices:
                notices.append(m)
        if marks:
            scopes.append(_scope(c["title"], [c["screen"]], marks))
    return _ev("screen", query, "\n\n".join(_render_screen(c) for _, c in hits),
               KBMOD.sources_of(KB, hits), atomic=atomic,
               notices=notices, scopes=scopes,
               cards=[c for _s, c in hits])


def _render_channel(card: dict) -> str:
    lines = [f"■ {card['task']}  ({card.get('group')})"]
    star, ibank = card.get("starbanking"), card.get("ibank")
    # 경로를 가진 행에서만 채널을 말한다. 이용 가능 시간 예외 행은 경로 표가 아니라
    # 시간 표에서 온 것이라, 거기에 "채널 목록에 없음"을 붙이면 되는 업무를 안 된다고 말한다.
    if star or ibank:
        lines.append(f"· KB스타뱅킹: 전체메뉴 > 뱅킹 > 가입상품관리 > 퇴직연금 > {star}"
                     if star else "· KB스타뱅킹: 해당 채널 목록에 없음")
        lines.append(f"· 인터넷뱅킹: KB퇴직연금 > 개인고객 > {ibank}"
                     if ibank else "· 인터넷뱅킹: 해당 채널 목록에 없음")
    if card.get("hours"):
        lines.append(f"· 이용 가능 시간: {card['hours']} (24시간 원칙의 예외)")
    for text in KBMOD.role_texts(card.get("note"), "caution"):
        lines.append(f"⚠ {text}")
    for text in KBMOD.role_texts(card.get("note"), "info"):
        lines.append(f"· 비고: {text}")
    lines.append(f"· 출처 {KBMOD.origin_of(KB, card)}")
    return "\n".join(lines)


def stale_mark(card: dict) -> str | None:
    """이 재료가 낡을 수 있다는 표시. **붙일지도 문구도 데이터가 정한다**(§9).

    카드가 `volatile`(낡을 수 있다는 원문의 경고)을 선언했을 때만 만들고, 기준시점은
    카드의 `as_of` 를 읽는다. 한때 이 문구가 코드 상수였는데, 그러면 ① 데이터가 아니라
    코드가 "무조건 붙는다"를 정하고(§7) ② 기준시점이 생성물과 코드 두 곳에 중복돼서 원문이
    바뀔 때 갈린다 — 갈리면 답변이 틀린 기준시점을 말한다(§12 gap 16).
    """
    warn = (card.get("volatile") or "").strip()
    if not warn:
        return None
    as_of = (card.get("as_of") or "").strip()
    return f"※ {warn}" + (f" — {as_of} 기준 표기입니다." if as_of else ".")


def _channel(state: AgentState, query: str) -> Evidence | None:
    """비대면 채널 처리 경로 — "고객이 스타뱅킹에서 직접 하려면 어느 메뉴인가".

    `screen` 과 나누는 기준은 **누가 하는가**다. screen 은 직원이 단말에서 하는 것이고
    이건 고객이 앱·웹에서 하는 것이다 — 같은 업무라도 답이 다르고, 직원이 고객에게
    전화로 불러주는 경로라 메뉴 이름 한 마디가 곧 답이다.
    """
    hits = _adopt(state, query, pick(("channel",), query, top_k=3), "비대면 채널 경로")
    if not hits:
        return None
    marks: list[str] = []
    for _s, c in hits:
        for mark in (*KBMOD.role_texts(c.get("note"), "caution"), stale_mark(c)):
            if mark and mark not in marks:
                marks.append(mark)
    return _ev("channel", query, "\n\n".join(_render_channel(c) for _, c in hits),
               KBMOD.sources_of(KB, hits), notices=marks,
               scopes=[_scope("비대면 채널 경로", [], marks)] if marks else [],
               cards=[c for _s, c in hits])


def _segment(state: AgentState, query: str) -> Evidence | None:
    """고객군. 선정 조건(condition_text)은 수치 기준이 붙는 자리라 원문으로 요구한다.
    이유(reason_text)는 산문이므로 compose 가 자유롭게 녹여도 된다."""
    hits = _adopt(state, query, segment_qa.search(query), "고객군 정의")
    if not hits:
        return None
    # note 중 역할이 caution 인 것(원문 임계값과 코드 판정이 다르다는 기록 등)만 표시로
    # 요구한다 — info(취지가 같다는 설명)는 렌더에는 실리지만 강제하지 않는다.
    return _ev("segment", query, segment_qa.render(hits, state.get("customer_id")),
               KBMOD.sources_of(KB, hits),
               atomic=[c.get("condition_text") or "" for _s, c in hits],
               notices=[t for _s, c in hits
                        for t in KBMOD.role_texts(c.get("note"), "caution")],
               scopes=[_scope(c.get("title") or c["id"], [c.get("condition_text") or ""],
                              marks)
                       for _s, c in hits
                       if (marks := KBMOD.role_texts(c.get("note"), "caution"))],
               cards=[c for _s, c in hits])


# ── method·fieldtip — 지금까지 답변 근거로 쓰이는 경로가 없던 카드들 ──
# method 131장은 guard.py 가 caution 8건만 쓰고 있었고, fieldtip 10장은 agent_help 의
# 기능 목록에만 등장했다. 판단 기준을 묻는 질문("어떤 고객부터 관리해야 하나")의 답이
# 여기 있는데 닿는 길이 없었다.

def _render_method(card: dict) -> str:
    lines = [f"■ {card['title']}  ({card.get('group')})"]
    if card.get("situation"):
        lines.append(f"· 언제: {card['situation']}")
    if card.get("action"):
        lines.append(f"· 무엇을: {card['action']}")
    if card.get("derivation"):
        lines.append(f"· 도출 근거: {card['derivation']}")
    for text in KBMOD.role_texts(card.get("cautions"), "caution"):
        lines.append(f"⚠ {text}")
    for q in (card.get("quotes") or [])[:1]:
        if q.get("text"):
            lines += ["", f"  {q['text'].strip()}"]
    lines.append(f"· 출처 {KBMOD.origin_of(KB, card)}")
    return "\n".join(lines)


def _method(state: AgentState, query: str) -> Evidence | None:
    """방법론. 본문은 판단 기준을 설명하는 산문이라 compose 가 녹여 쓰는 편이 낫다.
    역할이 caution 인 주의만 원문으로 요구한다 — 「하지 말 것」이 요약되며 날아가면 안 되고,
    저작 메모(authoring — 예: "팀 논의 필요")는 직원에게 띄우지 않는다."""
    hits = _adopt(state, query, pick(("method",), query, top_k=2), "관리 방법론")
    if not hits:
        return None
    return _ev("method", query, "\n\n".join(_render_method(c) for _, c in hits),
               KBMOD.sources_of(KB, hits),
               notices=[t for _s, c in hits
                        for t in KBMOD.role_texts(c.get("cautions"), "caution")],
               # 방법론에는 값 스팬이 없어 '이 카드를 썼는지'를 가릴 수 없다(keys 빈 묶음) —
               # 「하지 말 것」은 판단이 안 설 때 유지하는 쪽이 맞다.
               scopes=[_scope(c.get("title") or c["id"], [], marks)
                       for _s, c in hits
                       if (marks := KBMOD.role_texts(c.get("cautions"), "caution"))],
               cards=[c for _s, c in hits])


def _render_fieldtip(card: dict) -> str:
    # 신뢰 표시를 본문에 남긴다 — 현장팁은 본부 공식 지침이 아니다(kinds.json 의 선언과 같은 말).
    lines = [f"■ {card['title']}", f"  {FIELDTIP_MARK}"]
    if card.get("summary"):
        lines += ["", card["summary"]]
    if card.get("implication"):
        lines.append(f"· 시사점: {card['implication']}")
    for q in (card.get("quotes") or [])[:1]:
        if q.get("text"):
            lines += ["", f"  {q['text'].strip()}"]
    lines.append(f"· 출처 {KBMOD.origin_of(KB, card)}")
    return "\n".join(lines)


#: 현장팁 근거 블록의 머리말. 직원이 블록을 읽을 때 성격이 먼저 보이게 한다. 답변에 붙는
#: 신뢰 표시는 이것이 아니라 문서 레지스트리의 등급이다(marks.py) — 종류마다 전용 문구를
#: 두면 그 종류만 표시되고 나머지는 조용히 빠진다.
FIELDTIP_MARK = "※ 영업점 현장 관찰입니다 — 본부 공식 지침이 아닙니다."


def _fieldtip(state: AgentState, query: str) -> Evidence | None:
    hits = _adopt(state, query, pick(("fieldtip",), query, top_k=2), "영업점 현장 관찰")
    if not hits:
        return None
    # 예전에는 여기만 전용 신뢰 표시(FIELDTIP_MARK)를 notices 로 강제했다. 그 표시는
    # 이제 재료 성격 표시의 한 갈래이고(marks.py), 문서 레지스트리의 등급에서 나온다 —
    # 현장 관찰만 표시되고 본부 공식·대외 공개·교육자료는 안 되던 비대칭을 없앤다.
    return _ev("fieldtip", query, "\n\n".join(_render_fieldtip(c) for _, c in hits),
               KBMOD.sources_of(KB, hits), cards=[c for _s, c in hits])


def _customer(state: AgentState, query: str) -> Evidence | None:
    """지금 열려 있는 고객의 브리핑 재료. 계산은 strategy_agent 가 이미 한 것을 그대로 쓴다
    (같은 판정을 두 번 구현하지 않는다). 화면에 보이는 것과 다른 값을 말하면 안 되기 때문이다.

    **고객 재료로 답하는 경로는 이것 하나다.** 예전에는 `briefing_qa` 노드가 같은
    propose() 를 자기 프롬프트·자기 검증으로 한 번 더 답했다. 두 경로는 규약이 갈렸고
    (한쪽만 「하지 말 것」을 무조건 붙이고, 한쪽만 브리핑 산문을 보여줬다) 같은 질문이
    분류에 따라 다른 답을 받았다(CLAUDE.md §3 · §12 gap 11). 노드를 지우면서 그쪽이
    갖고 있던 재료 — 화면에 실제로 뜬 AI브리핑 문장·근거 해설 — 를 여기로 옮겼다.
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.strategy_agent import agent as strategy_agent  # noqa: PLC0415
    from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        result = strategy_agent.propose(profile)
        facts = result["facts"]
    except Exception:
        return None

    lines = [f"■ 고객 {customer_id} — 브리핑 재료"]
    lines += [f"· {k} {v}" for k, v in facts["customer"].items()]
    lines += [f"· {k} {v}" for k, v in facts["briefing"].items() if k != "source"]
    if facts.get("conditions"):
        lines.append(f"· 성립 요건: {', '.join(facts['conditions'])}")
    # 「왜 이 고객이 관리 대상인가」 — 직원이 실제로 가장 많이 묻는 것인데 통째로 빠져
    # 있었다. 요건 코드(dor·mis…)만 있고 그 요건이 왜 문제인지, 어느 세그먼트에 걸렸는지,
    # 무엇을 근거로 뽑혔는지가 재료에 없어서 LLM 이 요건 이름만 풀어 쓰거나 지어냈다.
    for label, values in (("왜 이 고객인가", facts.get("why_this_customer")),
                          ("판단근거", facts.get("rationale")),
                          ("고지 필요", facts.get("cautions")),
                          ("확인 필요", facts.get("needs_confirm"))):
        for v in values or []:
            lines.append(f"· {label}: {v}")
    for sit in facts.get("problem_situations") or []:
        lines.append(f"· 문제상황 {sit['no']}: {sit['title']} [{sit['group']}]")
    # 화면에 뜬 AI 산문. 직원은 이걸 보면서 묻기 때문에 재료에 없으면 "화면에 저렇게
    # 써 있는데 왜 다르게 말하느냐"가 된다. 값이 아니라 산문이므로 원문 스팬은 아니다.
    for label, text in (("AI브리핑 문장", result.get("sentence")),
                        ("AI브리핑 근거해설", result.get("insight"))):
        if text:
            lines.append(f"· {label}: {text}")
    # allow 에는 facts 전체를 넣는다 — 화면에 안 띄운 값도 '재료'이므로 인용 자체는 이탈이 아니다.
    # atomic·notices 를 비워 둔다. 고객 재료는 항목이 많아 전부 원문으로 요구하면 답변이 표 덤프가
    # 되고, 항목별로 고르면 무엇을 고를지에 근거가 없다. 수치 집합 검사만 걸린다
    # (재조합은 잡지 못한다).
    # 출처를 낸다. 지식 카드가 아니라 **전략제안이 계산한 브리핑 재료**라 카드 id 가 없지만,
    # "이 값이 어디서 왔나"는 답해야 한다(§3 모든 답에 출처를 밝힌다). 출처가 안 실리면
    # 화면에 "근거: 없음"이 뜨고, 직원은 값을 어디서 확인할지 모른 채 답만 받는다.
    # 출처는 **고객 원장**이지 지식 카드가 아니다. 예전 표기("AI브리핑 재료 /
    # briefing.<id>")는 화면에서 카드 id 처럼 읽혀, 계좌에 그냥 들어 있는 값이 어딘가에서
    # 검색해 온 자료처럼 보였다. 검색으로 온 재료가 아니므로 관련도(score)도 없다.
    return _ev("customer", query, "\n".join(lines),
               [{"id": f"customer.{customer_id}",
                 "title": f"{profile.nm} 고객 계좌 현황 (KB-PIN {customer_id})",
                 "doc": "고객 정보 — 계좌 원장 조회값 (브리핑 화면과 같은 값)",
                 "score": None, "page": None}],
               allow=["\n".join(lines), json.dumps(_citable(facts), ensure_ascii=False, default=str)])


#: 인용 허용 집합에서 빼는 facts 가지. 값이 아니라 **선별 전 후보 더미**다.
#:
#: allow 는 "이 답변이 인용해도 되는 값"의 집합이고, verify 는 답변의 수치가 그 안에 있는지만
#: 본다. 그래서 답변이 쓰지도 않을 카드 더미를 넣으면 그 안의 온갖 숫자가 **아무 주장에나
#: 근거를 대주는 꼴**이 된다. pools(⑦⑧ 후보군)는 카드 id·발췌가 통째로 들어와 이준호
#: 케이스에서만 허용 수치를 22개 → 110개로 5배 불렸고, 그 결과 "만기일은 2026년 9월
#: 11일"(오답)이 통과했다 — 9 와 11 이 무관한 카드 어딘가에 있었기 때문이다.
#:
#: 반대로 items·blocked_products·dropped·outreach 는 뺄 수 없다. 직원이 실제로 묻는
#: 것들이다("왜 ELB 는 빠졌어?" → blocked_products 의 최소가입금액).
_POOL_KEYS = ("pools",)


def _citable(facts: dict) -> dict:
    """인용 허용 집합에 실을 facts. 후보 더미만 걷어낸다."""
    return {k: v for k, v in facts.items() if k not in _POOL_KEYS}


# ─────────────────────────────────────────────────────────────
# 상담 이력 — "지난번에 무슨 얘기 했지"
#
# 기록은 이미 턴마다 쌓이고 있었다(graph.ask → session_store.append_turn). 없던 것은
# **읽는 도구**였고, 능력 표면은 도구 목록이므로(§3) 없는 도구는 없는 능력이다. 그래서
# 그 질문은 "제가 도와드릴 수 있는 것" 안내로 끝났고, 다음 턴 답변에는 LLM 이 지어낸
# "이전 대화 내용은 기억하지 못해요"가 따라 나왔다 — 사실도 아니었다. 재료를 주지 않으면
# LLM 은 없는 재료에 대해 말을 만든다.
#
# 지식 카드가 아니라 **운영 기록**이라 kinds.json 에 등록하지 않는다(session_store 참고).
# ─────────────────────────────────────────────────────────────

#: 상담 기록 재료에 항상 붙는 표시. `notices` 라서 답변에서 빠지면 코드가 채워 넣는다.
#: 지난 상담의 안내가 지금도 맞다는 보장은 없다 — 기록은 "그때 무슨 얘기를 했나"의
#: 근거이지 현재 기준 값의 근거가 아니고, 둘을 섞으면 낡은 값이 오늘의 답으로 나간다.
HISTORY_MARK = "※ 지난 상담 기록입니다 — 그때 나눈 이야기이지 지금 기준 값이 아닐 수 있습니다."

#: 재료에 싣는 범위(세션 수 · 세션당 턴 수 · 발췌 길이). 상담 중에 읽을 분량을 넘기면
#: 아무도 안 읽고 원장만 무거워진다.
HISTORY_SESSIONS, HISTORY_TURNS, HISTORY_EXCERPT = 3, 8, 120

_HISTORY_ROLE = {"user": "직원", "agent": "에이전트", "correction": "수정요청", "tool": "도구실행"}


def _history(state: AgentState, query: str) -> Evidence | None:
    """이 고객과 지난 상담에서 무슨 얘기를 했는지. 고객 화면이 닫혀 있으면 없다(§3).

    에이전트 답변은 **발췌만** 싣는다. 통째로 실으면 원장이 지난 상담의 문장으로 뒤덮여
    이번 질문과 무관한 수치까지 검증을 통과하게 된다. 발췌라도 그 안의 값은 원장에
    들어가므로, 재료가 시효 표시를 달고 나온다(HISTORY_MARK) — 답변이 그 값을 '지금
    기준'으로 말하지 않게 하는 것은 그 표시다.

    기록이 없으면 None 이다. "아직 상담 기록이 없습니다" 같은 문장을 도구가 지어내면
    그게 근거가 되고, 원장이 빈 채로 끝나는 정직한 '없음' 경로가 막힌다.
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent import session_store  # noqa: PLC0415
    try:
        sessions = session_store.list_sessions(customer_id)
    except Exception:
        return None

    lines = [f"■ 고객 {customer_id} — 상담 이력 기록"]
    recent = sorted(sessions, key=lambda s: s.get("started_at") or "", reverse=True)
    for session in recent[:HISTORY_SESSIONS]:
        turns = [t for t in (session.get("turns") or []) if (t.get("text") or "").strip()]
        if not turns:
            continue
        lines.append(f"· {(session.get('started_at') or '')[:10]} 상담 ({len(turns)}턴)")
        for turn in turns[-HISTORY_TURNS:]:
            text = " ".join((turn.get("text") or "").split())
            if len(text) > HISTORY_EXCERPT:
                text = text[:HISTORY_EXCERPT] + "…"
            role = _HISTORY_ROLE.get(turn.get("role"), turn.get("role") or "?")
            lines.append(f"  - {role}: {text}")
    if len(lines) == 1:
        return None

    return _ev("history", query, "\n".join(lines),
               [{"id": f"session.{customer_id}", "title": f"고객 {customer_id} 상담 이력",
                 "doc": "상담 이력 기록(에이전트가 턴마다 남긴 것)", "score": None, "page": None}],
               notices=[HISTORY_MARK])


# ─────────────────────────────────────────────────────────────
# 적합성 게이트 — 고른 근거가 질문에 답이 되는가 (CLAUDE.md §5)
#
# **재료 종류를 가리지 않는다.** 예전에는 화법에만 있었다. 이유는 "나머지 도구는 코드가
# 만든 텍스트를 그대로 내보내므로 카드가 어긋나면 직원이 읽고 바로 안다"였는데, 계획
# 루프가 들어오면서 그 전제가 깨졌다 — 값·절차도 이제 LLM 이 풀어 쓰고, 어긋난 카드로
# 쓴 문장은 화법과 똑같이 그럴듯하다. 게다가 §6 의 점검은 전부 "틀린 것을 막는" 검사라
# 적절성을 보증하지 못한다(주제어만 겹친 카드로 쓴 답도 수치는 원장 안에 있다).
#
# 게이트가 도는 자리는 **채택 직전**이고, 판정은 **카드 하나씩**이다. 처음에는 후보 묶음
# 전체를 한 번에 YES/NO 로 물었는데, 그러면 옆에 있는 후보 하나가 빗나갔다는 이유로 맞는
# 카드까지 함께 버려진다 — "디폴트옵션 변경 화면번호"에 맞는 절차 카드가 있는데도 "근거를
# 찾지 못했다"고 답하던 자리다. §5 가 말하는 것도 묶음이 아니라 "그 근거를 버린다"이다.
#
# 남은 후보가 0건이면 그 도구는 근거를 못 내놓은 것이 되고(None), 원장이 비면 compose 가
# 정직하게 '없음'으로 답한다 — 틀린 답을 주느니 없다고 하는 편이 낫다(§5).
# ─────────────────────────────────────────────────────────────

def _headline(card: dict) -> str:
    """후보 한 줄. 종류마다 필드 이름이 다르므로 있는 것 중 앞에서부터 고른다."""
    title = card.get("title") or card.get("label") or card.get("id")
    detail = next((str(card[k]) for k in
                   ("value", "condition_text", "summary", "situation", "action", "content")
                   if card.get(k)), "")
    points = "; ".join(card.get("key_points") or [])[:80]
    tail = (detail or points).replace("\n", " ")[:80]
    return f"- [{card.get('id')}] {title}" + (f" · {tail}" if tail else "")


#: 적합성 판정 응답의 토큰 상한. id 몇 개짜리 JSON 배열 한 줄.
ADEQUACY_MAX_TOKENS = 200


def fits_question(question: str, hits: list[tuple[float, dict]],
                  kind: str = "지식") -> list[tuple[float, dict]]:
    """질문의 '실제 의도'에 답이 되는 후보만 남긴다(오답 차단). 순서·점수는 그대로 둔다.

    LLM 이 없는 id 를 지어내도 실재 후보와 대조해 걸러낸다 — select.llm_pick 과 같은
    안전장치다. LLM 이 죽으면 예외를 그대로 올린다: 게이트를 못 돌린 턴이 게이트 없이
    답을 만들면 §11 이 막으려는 상태가 된다.
    """
    cards = "\n".join(_headline(c) for _, c in hits)
    raw = generate(ADEQUACY_PROMPT.format(question=question, cards=cards, kind=kind),
                   max_tokens=ADEQUACY_MAX_TOKENS)
    m = re.search(r"\[.*\]", raw, re.S)
    try:
        kept = json.loads(m.group()) if m else []
    except ValueError:
        kept = []
    keep = {x for x in kept if isinstance(x, str)} if isinstance(kept, list) else set()
    return [(score, card) for score, card in hits if card.get("id") in keep]


def _adopt(state: AgentState, query: str, hits: list[tuple[float, dict]],
           kind: str) -> list[tuple[float, dict]]:
    """채택할 후보만 남겨 돌려준다. 0건이면 게이트를 돌리지 않는다(부를 이유가 없다)."""
    if not hits:
        return []
    return fits_question(state.get("question") or query, hits, kind)


# ─────────────────────────────────────────────────────────────
# 화법 — atomic·notices 가 비어 있는 도구. 특별하지 않다.
#
# atomic·notices 가 비어 있는 이유는 화법이 **고객에게 말할 문장**이기 때문이다. 원문
# 스팬을 그대로 박으면 대사가 아니라 인용문이 되고, 화법의 쓸모가 사라진다. 화법이
# 기대는 보호는 위 적합성 게이트인데, 이제 그건 모든 도구가 함께 쓴다.
# ─────────────────────────────────────────────────────────────

#: 화법 카드 후보 수. 예전 pitch.TOP_K 와 같다.
PITCH_TOP_K = 3


def _pitch(state: AgentState, query: str) -> Evidence | None:
    """화법 카드 3단 선택 — 예전 그래프의 llm_select → retrieve → broaden 을 그대로 옮긴 것.

    ① LLM 이 버킷→카드로 고른다.
    ② 못 고르면 n-gram 으로 찾는다. 이때는 슬롯(고객유형·거절유형·단계)으로 후보를 좁힌다.
    ③ 그래도 0건이면 슬롯을 다 풀고 다시 찾는다(예전 broaden 1·2차를 한 번에 합친 것 —
       단계를 나눠 두 번 돌려도 결국 전부 푸는 것이 마지막이었고, 중간 단계가 건진 사례가
       회귀 스위트에 없었다).
    """
    hits = llm_pick(("pitch",), query)[:PITCH_TOP_K]
    slots: dict = {}
    if not hits:
        # 슬롯 분해는 **여기서** 한다. 예전에는 계획 루프 앞의 노드가 모든 턴에 대해
        # 미리 뽑았는데, 화법을 부르지도 않는 턴("이 고객 예금 잔액 얼마지")에서 LLM
        # 호출 한 번이 통째로 낭비됐다. n-gram 폴백에만 쓰이므로 폴백에 들어올 때 뽑는다.
        slots = PITCHMOD.extract_slots(state)
        hits = retrieve(KB, top_k=PITCH_TOP_K, kinds=["pitch"], utterance=query, **slots)
    if not hits:
        hits = retrieve(KB, top_k=PITCH_TOP_K, kinds=["pitch"], utterance=query)
    hits = _adopt(state, query, hits, "상담 화법")
    if not hits:
        return None
    # 슬롯을 원장에 남긴다 — compose 의 '파악된 상황' 한 줄이 이걸 읽는다. 화법을 안 부른
    # 턴에는 그 줄이 아예 붙지 않는다(있지도 않은 상담 상황을 상상하게 두지 않는다).
    return _ev("pitch", query, KBMOD.build_context(KB, hits), KBMOD.sources_of(KB, hits),
               cards=[c for _s, c in hits], meta={"slots": slots})


# ─────────────────────────────────────────────────────────────
# 레지스트리
# ─────────────────────────────────────────────────────────────

TOOLS: dict[str, Tool] = {
    t.name: t for t in (
        Tool("pitch", "고객에게 실제로 할 말(대사·반론 대응·논거)을 만든다", _pitch),
        Tool("fact", "제도·상품의 확정된 수치를 기준시점·출처와 함께 돌려준다", _fact),
        Tool("procedure", "업무를 어떤 순서·채널로 처리하는지와 걸리는 주의를 돌려준다", _procedure),
        Tool("screen", "단말 화면번호를 찾는다 — '무슨무슨 조회/등록은 몇 번 화면인가'", _screen),
        Tool("channel", "고객이 스타뱅킹·인터넷뱅킹에서 직접 처리하는 메뉴 경로를 돌려준다",
             _channel),
        Tool("segment", "관리 대상 고객군의 정의와 선정 조건을 설명한다", _segment),
        Tool("method", "무엇을 어떤 기준으로 판단하는지(관리 방법론)를 돌려준다", _method),
        Tool("fieldtip", "영업점 현장 관찰(본부 지침 아님)을 돌려준다", _fieldtip),
        Tool("customer", "지금 열려 있는 고객의 브리핑 재료(잔액·수익률·요건)를 돌려준다", _customer),
        Tool("history", "이 고객과 지난 상담에서 무슨 얘기를 했는지(날짜·질문·안내 요지) 돌려준다",
             _history),
    )
}

#: 열려 있는 고객이 있어야 성립하는 도구. 어느 고객인지가 재료의 전제다(§3).
_NEEDS_CUSTOMER = frozenset({"customer", "history"})


def catalog(state: AgentState | None = None) -> str:
    """계획 프롬프트에 실리는 도구 목록. 쓸 수 없는 도구는 애초에 보여주지 않는다 —
    고객 화면이 닫혀 있는데 customer 를 제안하게 두면 한 스텝을 낭비한다."""
    opened = bool((state or {}).get("customer_id"))
    usable = [t for t in TOOLS.values() if opened or t.name not in _NEEDS_CUSTOMER]
    return "\n".join(f"- {t.name}: {t.desc}" for t in usable)


def run(name: str, state: AgentState, query: str) -> Evidence | None:
    """도구 하나를 부른다. 근거를 못 찾으면 **직원의 원문 질문으로 한 번 더** 찾는다.

    계획이 만든 질의는 질문을 줄여 쓴 것이라, 줄이는 과정에서 검색이 기대는 말이 빠질 수
    있다 — "포트폴리오 운용현황 조회 화면 번호는?"이 "운용현황 조회 화면번호"가 되면
    n-gram 이 0건을 낸다(원문으로는 찾는다). 재검색은 같은 도구·같은 지식베이스이므로
    근거의 경계를 넓히지 않는다. 넓히는 것은 **질의 한 개**뿐이다.

    LLM 이 고른 질의가 항상 낫다고 볼 이유가 없다는 것이 이 재시도의 근거다. 지식베이스에
    답이 있는데 질의를 잘못 골라 "없습니다"로 끝나는 것이 가장 나쁜 실패다.
    """
    tool = TOOLS.get(name)
    if tool is None:
        return None
    question = (state.get("question") or "").strip()
    attempts = [query] + ([question] if question and question != query else [])
    for attempt in attempts:
        try:
            found = tool.run(state, attempt)
        except LLMError:
            # 도구가 죽은 것과 **LLM 이 죽은 것**은 다른 사건이다. 뒤를 앞으로 접으면
            # "찾아봤는데 재료가 없다"로 나가고, 그게 §11 이 막으려는 바로 그 답이다.
            raise
        except Exception:
            return None  # 도구 하나가 죽어도 루프는 다음 도구로 간다
        if found is not None:
            return found
    return None


def ledger_slots(evidence: list[Evidence]) -> dict:
    """화법 도구가 뽑아둔 상담 상황 슬롯. 화법을 안 부른 턴에는 빈 dict."""
    for e in evidence:
        slots = e["meta"].get("slots")
        if slots:
            return slots
    return {}


def ledger_related(evidence: list[Evidence]) -> list[dict]:
    """원장에 실린, 관계를 선언한 카드 전부. compose 가 답변을 이것과 대조한다."""
    out: list[dict] = []
    seen: set[str] = set()
    for e in evidence:
        for card in e.get("related") or []:
            if card.get("id") not in seen:
                seen.add(card.get("id"))
                out.append(card)
    return out


def ledger_marks(evidence: list[Evidence]) -> list[str]:
    """원장에 실린 재료 성격 표시 전부(중복 제거, 등장 순서 유지). compose 가 답변에 붙인다."""
    out: list[str] = []
    for e in evidence:
        for m in e.get("marks") or []:
            if m not in out:
                out.append(m)
    return out


def ledger_texts(evidence: list[Evidence]) -> list[str]:
    """원장의 검증 허용 텍스트 전부. verify_texts 가 이걸 재료로 본다."""
    return [t for e in evidence for t in e["allow"]]


def ledger_sources(evidence: list[Evidence]) -> list[dict]:
    """원장의 근거 목록(중복 id 제거, 등장 순서 유지)."""
    out: list[dict] = []
    seen: set[str] = set()
    for e in evidence:
        for s in e["sources"]:
            if s["id"] not in seen:
                seen.add(s["id"])
                out.append(s)
    return out


def summarize(evidence: list[Evidence]) -> str:
    """계획 프롬프트에 넣는 '지금까지 모은 것' — 본문이 아니라 무엇을 이미 봤는지만."""
    if not evidence:
        return "(아직 없음)"
    return "\n".join(
        f"- {e['tool']}(\"{e['query']}\") → " +
        (", ".join(s["title"] or s["id"] for s in e["sources"][:3]) or "재료 확보")
        for e in evidence
    )
