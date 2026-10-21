"""지식 카드 도구 — fact · procedure · screen · channel · segment · method · fieldtip.

종류마다 원문 강제 스팬(atomic)과 표시(notices)를 선언. *_evidence 는 검색 도구·카드 되싣기 공용.
"""

from __future__ import annotations

from pension_agent.evidence import relations as REL
from pension_agent.tools.kb_search import kb_index
from pension_agent.knowledge import kb as KBMOD
from pension_agent.tools.kb_search import facts_qa, procedure_qa, segment_qa
from pension_agent.state import KB, AgentState
from pension_agent import tools as _T  # noqa: PLC0415 — 후크는 패키지 경유
from pension_agent.tools.adequacy import _adopt
from pension_agent.evidence.record import Evidence, _ev, _scope
from pension_agent.evidence.verify import numbers


def _fact(state: AgentState, query: str) -> Evidence | None:
    """확정값. value(값+조건)·기준시점은 원문 강제 — 쪼개면 값–조건 재조합을 못 잡음."""
    hits = _adopt(state, query, facts_qa.search(query), "제도·상품 확정값")
    if not hits:
        return None
    return fact_evidence(query, hits)


def fact_evidence(query: str, hits: list[tuple[float, dict]], tool: str = "fact") -> Evidence | None:
    """팩트 카드 → 원장 항목."""
    atomic: list[str] = []
    notices: list[str] = []
    scopes: list[dict] = []
    for _s, f in hits:
        keys = [f.get("value") or ""]
        if f.get("as_of"):
            keys.append(f"기준시점 {f['as_of']}")
        marks = [f"⚠ 상태: {f['status']}"] if f.get("status") and f["status"] != "확정" else []
        # 관계 선언 팩트는 relations.py 가 대조 → 원문 강제 해제. 값 없는 스팬도 강제 안 함
        if not REL.declared(f):
            atomic += [k for k in keys if _has_values(k, f)]
        notices += marks
        if marks:
            scopes.append(_scope(f.get("label") or f["id"], keys, marks))
    return _ev(tool, query, facts_qa.render(hits), kb_index.sources_of(KB, hits),
               atomic=atomic, notices=notices, scopes=scopes, cards=[f for _s, f in hits])


def _has_values(span: str, card: dict) -> bool:
    """스팬에 화면번호 외 수치가 있는지."""
    screen_tokens = {t for s in card.get("screens") or [] for t in numbers(s)}
    return bool(numbers(span) - screen_tokens)


def _procedure(state: AgentState, query: str) -> Evidence | None:
    """절차. 화면번호 원문 강제, caution 역할 주의만 표시 요구."""
    hits = _adopt(state, query, procedure_qa.search(query), "업무 처리 절차")
    if not hits:
        return None
    return procedure_evidence(query, hits)


def procedure_evidence(query: str, hits: list[tuple[float, dict]],
                       tool: str = "procedure") -> Evidence | None:
    """절차 카드 → 원장 항목."""
    atomic, notices, scopes = _procedure_decls([c for _s, c in hits])
    return _ev(tool, query, procedure_qa.render(hits), kb_index.sources_of(KB, hits),
               atomic=atomic, notices=notices, scopes=scopes, cards=[c for _s, c in hits])


def _procedure_decls(cards: list[dict]) -> tuple[list[str], list[str], list[dict]]:
    """절차 카드의 원문 스팬·표시 선언(procedure·playbook 공용)."""
    atomic: list[str] = []
    notices: list[str] = []
    scopes: list[dict] = []
    for c in cards:
        keys = list(c.get("screens") or [])
        marks = KBMOD.role_texts(c.get("cautions"), "caution")
        if c.get("status") == "확인 필요":
            marks.append("⚠ 자료 간 표기가 어긋나는 절차입니다")
        atomic += keys
        notices += marks
        if marks:
            scopes.append(_scope(c.get("title") or c["id"], keys, marks))
    return atomic, notices, scopes


def _render_screen(card: dict) -> str:
    lines = [f"■ {card['screen']} {card['title']}"]
    # 화면명 반복이어도 유지 — 빠지면 LLM 이 출처 줄을 기능으로 오독
    if card.get("summary"):
        lines.append(f"· 무슨 화면인지: {card['summary']}")
    # 업무 묶음은 그룹 이름 — 라벨 없이 붙이면 기능으로 오독
    if card.get("group"):
        lines.append(f"· 표의 업무 묶음: {card['group']}")
    # 별칭은 재료로 제공 — 없으면 동의어 판단이 턴마다 갈림
    if card.get("aliases"):
        lines.append(f"· 같은 말: {' · '.join(card['aliases'])}")
    if card.get("confidence"):
        lines.append(f"· 근거 신뢰도: {card['confidence']}")
    # 비고는 역할대로 — authoring 미노출
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
    """단말 화면번호 검색. 화면번호 원문 강제."""
    hits = _adopt(state, query, _T.pick(("screen",), query, top_k=3), "단말 화면번호")
    if not hits:
        return None
    return screen_evidence(query, hits)


def screen_evidence(query: str, hits: list[tuple[float, dict]], tool: str = "screen") -> Evidence | None:
    """화면 카드 → 원장 항목."""
    atomic = [c["screen"] for _s, c in hits]
    notices: list[str] = []
    scopes: list[dict] = []
    for _s, c in hits:
        # 시효 표시는 카드 volatile 선언에서만 — 코드 상수 금지
        marks = KBMOD.role_texts(c.get("note"), "caution")
        stale = stale_mark(c)
        if stale:
            marks.append(stale)
        for m in marks:
            if m not in notices:
                notices.append(m)
        if marks:
            scopes.append(_scope(c["title"], [c["screen"]], marks))
    return _ev(tool, query, "\n\n".join(_render_screen(c) for _, c in hits),
               kb_index.sources_of(KB, hits), atomic=atomic,
               notices=notices, scopes=scopes,
               cards=[c for _s, c in hits])


def _render_channel(card: dict) -> str:
    lines = [f"■ {card['task']}  ({card.get('group')})"]
    star, ibank = card.get("starbanking"), card.get("ibank")
    # 시간 예외 행은 경로 없음 — «목록에 없음» 표기 시 가능한 업무를 불가로 오안내
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
    """시효 표시(※). 카드 volatile·as_of 선언에서만 생성, 없으면 None."""
    warn = (card.get("volatile") or "").strip()
    if not warn:
        return None
    as_of = (card.get("as_of") or "").strip()
    return f"※ {warn}" + (f" — {as_of} 기준 표기입니다." if as_of else ".")


def advisory_mark(card: dict) -> str | None:
    """정보 제공(투자권유 아님) 표시(⚖). 카드 advisory 선언에서만 생성, 없으면 None."""
    note = (card.get("advisory") or "").strip()
    return f"⚖ {note}" if note else None


def _channel(state: AgentState, query: str) -> Evidence | None:
    """비대면 채널(고객 앱·웹) 메뉴 경로 검색."""
    hits = _adopt(state, query, _T.pick(("channel",), query, top_k=3), "비대면 채널 경로")
    if not hits:
        return None
    return channel_evidence(query, hits)


def channel_evidence(query: str, hits: list[tuple[float, dict]], tool: str = "channel") -> Evidence | None:
    """채널 카드 → 원장 항목."""
    marks: list[str] = []
    for _s, c in hits:
        for mark in (*KBMOD.role_texts(c.get("note"), "caution"), stale_mark(c)):
            if mark and mark not in marks:
                marks.append(mark)
    return _ev(tool, query, "\n\n".join(_render_channel(c) for _, c in hits),
               kb_index.sources_of(KB, hits), notices=marks,
               scopes=[_scope("비대면 채널 경로", [], marks)] if marks else [],
               cards=[c for _s, c in hits])


def _segment(state: AgentState, query: str) -> Evidence | None:
    """고객군. 선정 조건은 원문 강제, 이유는 자유 서술."""
    hits = _adopt(state, query, segment_qa.search(query), "고객군 정의")
    if not hits:
        return None
    return segment_evidence(query, hits, state.get("customer_id"))


def segment_evidence(query: str, hits: list[tuple[float, dict]], customer_id: str | None,
                     tool: str = "segment") -> Evidence | None:
    """고객군 카드 → 원장 항목. note 는 caution 만 표시 강제."""
    return _ev(tool, query, segment_qa.render(hits, customer_id),
               kb_index.sources_of(KB, hits),
               atomic=[c.get("condition_text") or "" for _s, c in hits],
               notices=[t for _s, c in hits
                        for t in KBMOD.role_texts(c.get("note"), "caution")],
               scopes=[_scope(c.get("title") or c["id"], [c.get("condition_text") or ""],
                              marks)
                       for _s, c in hits
                       if (marks := KBMOD.role_texts(c.get("note"), "caution"))],
               cards=[c for _s, c in hits])


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
    """방법론. 본문은 자유 서술, caution 주의만 원문 요구(authoring 미노출)."""
    hits = _adopt(state, query, _T.pick(("method",), query, top_k=2), "관리 방법론")
    if not hits:
        return None
    return method_evidence(query, hits)


def method_evidence(query: str, hits: list[tuple[float, dict]], tool: str = "method") -> Evidence | None:
    """방법론 카드 → 원장 항목."""
    notices, scopes = _method_decls([c for _s, c in hits])
    return _ev(tool, query, "\n\n".join(_render_method(c) for _, c in hits),
               kb_index.sources_of(KB, hits),
               notices=notices, scopes=scopes, cards=[c for _s, c in hits])


def _method_decls(cards: list[dict]) -> tuple[list[str], list[dict]]:
    """방법론 카드의 표시 선언(method·playbook 공용). 값 스팬 없음 — 주의는 항상 유지."""
    notices = [t for c in cards for t in KBMOD.role_texts(c.get("cautions"), "caution")]
    scopes = [_scope(c.get("title") or c["id"], [], marks)
              for c in cards
              if (marks := KBMOD.role_texts(c.get("cautions"), "caution"))]
    return notices, scopes


def _render_fieldtip(card: dict) -> str:
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


#: 현장팁 블록 머리말. 답변의 신뢰 표시는 별도(marks.py 문서 등급).
FIELDTIP_MARK = "※ 영업점 현장 관찰입니다 — 본부 공식 지침이 아닙니다."


def _fieldtip(state: AgentState, query: str) -> Evidence | None:
    hits = _adopt(state, query, _T.pick(("fieldtip",), query, top_k=2), "영업점 현장 관찰")
    if not hits:
        return None
    return fieldtip_evidence(query, hits)


def fieldtip_evidence(query: str, hits: list[tuple[float, dict]], tool: str = "fieldtip") -> Evidence | None:
    """현장 관찰 카드 → 원장 항목."""
    return _ev(tool, query, "\n\n".join(_render_fieldtip(c) for _, c in hits),
               kb_index.sources_of(KB, hits), cards=[c for _s, c in hits])
