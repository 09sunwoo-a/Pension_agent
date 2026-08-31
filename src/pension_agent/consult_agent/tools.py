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
from pension_agent.consult_agent import progress
from pension_agent.consult_agent import relations as REL
from pension_agent.consult_agent.nodes import facts_qa, pitch as PITCHMOD, procedure_qa, segment_qa
from pension_agent.consult_agent.prompts import ADEQUACY_PROMPT
from pension_agent.consult_agent.kb import retrieve
from pension_agent.consult_agent.select import llm_pick, pick
from pension_agent.consult_agent.state import KB, AgentState, format_history
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
    atomic, notices, scopes = _procedure_decls([c for _s, c in hits])
    return _ev("procedure", query, procedure_qa.render(hits), KBMOD.sources_of(KB, hits),
               atomic=atomic, notices=notices, scopes=scopes, cards=[c for _s, c in hits])


def _procedure_decls(cards: list[dict]) -> tuple[list[str], list[str], list[dict]]:
    """절차 카드의 원문 스팬·표시 선언. `procedure` 도구와 `playbook`(문제상황 후보에 절차가
    섞일 때)이 함께 쓴다 — 두 곳이 각자 선언하면 한쪽만 화면번호 강제가 빠지는 날이 온다."""
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


def advisory_mark(card: dict) -> str | None:
    """이 재료가 **정보 제공**이라는 표시. `stale_mark` 와 같은 규약 — 카드가 선언했을
    때만 만들고, 문구도 데이터가 들고 있다(`advisory`).

    시황·상품 자료는 직원이 그대로 고객에게 옮기기 가장 쉬운 재료이고, 그 순간 «안내»가
    «권유»가 된다. 원문(05 시황 문서 「유의사항(고지)」)이 그래서 정보 제공 목적임과
    자본시장법·당행 규정 준수 의무를 함께 적어뒀다. 상품 문서에는 같은 고지가 없지만
    폴더가 단위로 선언한다 — 그것이 운영 판단이라는 기록은 CLAUDE.md §8 관리대장에 있다.

    **표시를 코드가 붙이는 이유**는 guard.py 머리말과 같다: 프롬프트로 톤만 잡으면 LLM 이
    무시해도 아무도 모른다. 검증기는 수치·상품명만 보지 톤은 보지 않는다.
    """
    note = (card.get("advisory") or "").strip()
    return f"⚖ {note}" if note else None


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
    notices, scopes = _method_decls([c for _s, c in hits])
    return _ev("method", query, "\n\n".join(_render_method(c) for _, c in hits),
               KBMOD.sources_of(KB, hits),
               notices=notices, scopes=scopes, cards=[c for _s, c in hits])


def _method_decls(cards: list[dict]) -> tuple[list[str], list[dict]]:
    """방법론 카드의 표시 선언. `method` 도구와 `playbook` 이 함께 쓴다.

    방법론에는 값 스팬이 없어 '이 카드를 썼는지'를 가릴 수 없다(keys 빈 묶음) —
    「하지 말 것」은 판단이 안 설 때 유지하는 쪽이 맞다.
    """
    notices = [t for c in cards for t in KBMOD.role_texts(c.get("cautions"), "caution")]
    scopes = [_scope(c.get("title") or c["id"], [], marks)
              for c in cards
              if (marks := KBMOD.role_texts(c.get("cautions"), "caution"))]
    return notices, scopes


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


# ── 시황(market) · 운용 상품(lineup) 기반지식 — 05_시황_상품_기반지식 ──
#
# **둘은 다른 도구다.** 묻는 것이 다르기 때문이다 — market 은 «시장이 어떻게 돌아가나»
# (시황·환율·금리·FOMC), lineup 은 «우리가 뭘 파나»(추천펀드·디폴트옵션 포트폴리오·TDF).
# screen(직원이 단말에서)과 channel(고객이 앱에서)을 같은 원문 표에서 나눈 것과 같은 이유다.
# 하나로 묶여 있던 동안 계획 LLM 은 도구 하나로 둘을 다 받아야 해서 무엇을 부를지 흐렸고,
# 버킷 카탈로그도 시황 문서와 상품 문서를 한 묶음에 세웠다.
#
# 두 도구가 **같은 함수를 쓴다**(_market_like) — 재료의 성격이 같아서다(원문·표 · 기준시점 ·
# 시효 경고). 갈리는 것은 어느 종류를 뒤지는지와 도구 설명뿐이다. 종류마다 렌더·검증을
# 복사하면 한쪽만 고쳐지는 자리가 생긴다.
#
# 「이 자료는 상담 시 근거로 인용할 시장·상품 데이터」라고 폴더가 스스로 규정하고 문서마다
# 검색용 front-matter(trigger_keywords·key_points·as_of)까지 갖춰 저작돼 있었는데, 적재
# 경로가 없어 에이전트에게는 통째로 없는 재료였다(knowledge/CLAUDE.md 적재 감사).
#
# 다른 도구와 갈리는 지점은 **시효**다. 화면번호·제도값과 달리 시황 수치는 주·월 단위로
# 낡는다. 그래서 카드마다 기준시점(as_of)과 원문의 시효 경고(volatile)를 싣고, 그 표시를
# notices 로 강제한다 — 답변에서 빠지면 코드가 채워 넣는다(§9). 붙일지도 문구도 데이터가
# 정한다는 규약은 screen·channel 과 같다(stale_mark).

#: 후보 수. 카드 하나가 표 통째(디폴트옵션 9종 = 약 4천 자)인 경우가 있어 좁게 둔다 —
#: 세 장이면 재료가 1만 자를 넘고, 답이 그 안에서 길을 잃는다.
MARKET_TOP_K = 2


def _render_market(card: dict) -> str:
    # 개요 카드는 제목과 group(문서명)이 같다 — 같은 말을 두 번 세우지 않는다.
    doc_name = card.get("group") if card.get("group") != card.get("title") else None
    scope = " · ".join(x for x in (card.get("category"), doc_name) if x)
    lines = [f"■ {card['title']}" + (f"  ({scope})" if scope else "")]
    if card.get("topic"):
        lines.append(f"· 무엇을 다루나: {card['topic']}")
    lines.append(f"· 기준시점: {card['as_of']}")
    for k in card.get("key_points") or []:
        lines.append(f"· 요점: {k}")
    if card.get("content"):
        lines += ["", card["content"].strip()]
    mark = stale_mark(card)
    if mark:
        lines.append(mark)
    lines.append(f"· 출처 {KBMOD.origin_of(KB, card)}")
    return "\n".join(lines)


def _prefer_sections(hits: list[tuple[float, dict]]) -> list[tuple[float, dict]]:
    """같은 문서의 절이 함께 걸렸으면 그 문서의 **개요 카드는 뺀다.**

    개요 카드는 문서의 front-matter 키워드를 통째로 들고 있어서(주간시황만 24개) 그 문서에
    대한 어떤 질문에나 걸린다. 그런데 답이 든 **표는 절 카드에 있다** — 「지켜드림 금리」의
    답은 디폴트옵션 절에 있고 개요에는 없는데, 둘이 동점이라 개요가 1위로 올라가 후보 두
    자리 중 하나를 먹었다(실측). 개요는 문서의 현관이지 답이 아니다.

    절이 하나도 없으면 개요를 그대로 둔다 — 넓은 질문("요즘 시장 어때")에는 그게 답이다.
    """
    parents = {c.get("parent") for _s, c in hits if c.get("parent")}
    return [(s, c) for s, c in hits if c["id"] not in parents]


def _market_like(kind: str, label: str) -> Callable[[AgentState, str], Evidence | None]:
    """market·lineup 도구 본체. 재료의 성격이 같아 한 함수를 종류만 바꿔 쓴다.

    `fact`(제도 확정값)와 나누는 기준은 **시효**다. 세액공제 한도는 제도가 바뀌기 전까지
    참이고, 이 재료는 다음 회차 자료가 나오면 낡는다 — 그래서 기준시점 표시가 답에 반드시
    따라붙어야 하고(ANSWER_SHAPES), 그 표시는 카드의 선언에서 온다.

    본문은 원문 표·산문이라 atomic 으로 요구하지 않는다. 표를 통째로 원문 강제하면 답변이
    표 덤프가 되고(tools 머리말), 그건 이 재료를 못 쓰게 만드는 것과 같다. 값–조건 오짝은
    `tables` 선언을 relations.table_mispaired() 가 대조해 잡는다.
    """

    def run(state: AgentState, query: str) -> Evidence | None:
        hits = _adopt(state, query, _prefer_sections(
            pick((kind,), query, top_k=MARKET_TOP_K * 3))[:MARKET_TOP_K], label)
        if not hits:
            return None
        notices: list[str] = []
        scopes: list[dict] = []
        for _s, c in hits:
            # 시효 표시(※)와 인용 고지(⚖)는 다른 것을 말한다 — 앞은 «이 수치가 낡을 수
            # 있다», 뒤는 «이건 정보 제공이지 권유가 아니다». 둘 다 카드의 선언에서 온다.
            marks = [m for m in (stale_mark(c), advisory_mark(c)) if m]
            if not marks:
                continue
            notices += [m for m in marks if m not in notices]
            scopes.append(_scope(c["title"], [], marks))
        return _ev(kind, query, "\n\n".join(_render_market(c) for _, c in hits),
                   KBMOD.sources_of(KB, hits), notices=notices, scopes=scopes,
                   cards=[c for _s, c in hits])

    return run


# ─────────────────────────────────────────────────────────────
# 적합성 범위 — "이 고객에게 뭘 추천하지?" 에 답할 수 있는 것
#
# **권유가 아니라 범위다.** 직원이 상품을 물으면 답할 수 있는 것은 «이 고객 투자성향에서
# 어디까지 가능한가»와 «그 안에 무엇이 있는가»이지, 한 상품을 고르는 것이 아니다 —
# 무엇을 권유할지는 자본시장법과 당행 규정에 따라 직원이 정한다(§8 관리대장).
#
# 판정은 **하지 않는다.** strategy_agent 의 적합성 게이트가 이미 계산한 것을 그대로
# 옮긴다(위험등급 상한·거래채널). 같은 판정을 두 번 구현하면 브리핑 화면 ⑤ 「이런 상품이
# 적합할 수 있어요」와 대화형이 다른 목록을 말하게 된다.
#
# 이 도구가 없던 동안, 「이 고객 무슨 상품 추천해주지?」는 lineup 을 세 바퀴 돌고 재료
# 0건으로 끝났다. 계산은 코드가 이미 해뒀는데 **대화형에 그걸 부를 도구가 없었다** —
# 능력 표면은 도구 목록이므로(§3) 없는 도구는 없는 능력이다.
# ─────────────────────────────────────────────────────────────

#: 제외 상품을 몇 건까지 싣나. "왜 이건 없어?" 에 답하려면 사유가 필요하고, 열두 줄이
#: 늘어서면 정작 통과 목록이 묻힌다.
BLOCKED_MAX = 5


def _suitable(state: AgentState, query: str) -> Evidence | None:
    """적합성 게이트가 허용하는 범위와 그 안의 상품. 고객 화면이 닫혀 있으면 없다(§3)."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.strategy_agent import engine  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        pool = engine.candidate_pool_for_recommendation(profile)
        passed = pool["products"]
        # 상한은 게이트가 쓰는 것과 같은 값이어야 한다 — 통과 목록만 옮기고 상한을 따로
        # 셈하면 "다소높은위험까지 됩니다"와 목록이 어긋난다.
        cap = profile.grade
        conds = strategy_customer.conditions(profile)
        if "mis" in conds and strategy_customer.PREF.get(profile.rk):
            cap = strategy_customer.RISK[min(
                strategy_customer.RISK.index(cap),
                strategy_customer.RISK.index(strategy_customer.PREF[profile.rk]))]
        blocked: list[tuple[dict, str]] = []
        for row in engine.query_products(engine.PRODUCTS):
            ok, why = engine.gate_static(row, profile, cap)
            if not ok:
                blocked.append((row, why))
    except Exception:
        return None
    if not passed and not blocked:
        return None
    advice = advisory_mark({"advisory": KBMOD.advisory_note(KB)})

    lines = [f"■ 고객 {customer_id} — 투자성향 {profile.rk} · 위험등급 {profile.grade}",
             f"· 적합성 허용 상한: {cap} (이 등급까지의 상품만 안내할 수 있다)",
             "",
             f"── 안내할 수 있는 상품 {len(passed)}종"]
    for r in passed:
        ret = engine.product_return(r)
        tail = f" · 최근 1년 {ret}%" if ret is not None else ""
        lines.append(f"· {r['name']} — {r['risk']}{tail}"
                     + (f" · {r['category']}" if r.get("category") else ""))
    for pf in pool["portfolios"]:
        lines.append(f"· [포트폴리오] {pf['name']} — {pf.get('description') or ''}".rstrip())
    if blocked:
        lines += ["", f"── 안내할 수 없는 상품 {len(blocked)}종 (왜 목록에 없는지)"]
        lines += [f"· {r['name']} — {why}" for r, why in blocked[:BLOCKED_MAX]]
    else:
        # **0건일 때 침묵하지 않는다.** 재료가 아무 말도 안 하면 답변 형태가 요구하는
        # 「안내할 수 없는 상품」을 LLM 이 통과 목록에서 만들어 채운다(실측: 정민석 —
        # 12종을 11종이라 말하고 하나를 뺐다). 그리고 직원 입장에서도 «없는 것»과
        # «안 알려준 것»은 다르다 — 바로 앞 고객에서는 제외 4종이 나왔기 때문이다.
        lines += ["", "── 안내할 수 없는 상품 없음 "
                      f"(허용 상한이 {cap}이라 카탈로그 전부가 범위 안이다)"]
    return _ev("suitable", query, "\n".join(lines),
               [{"id": f"suitable.{customer_id}",
                 "title": f"{profile.nm} 고객 적합성 판정 (KB-PIN {customer_id})",
                 "doc": "투자성향 적합성 확인 — 위험등급 상한·거래채널 판정 결과 "
                        "(브리핑 화면 ⑤ 와 같은 후보군)",
                 "score": None, "page": None}],
               # 고지 문구를 **여기서 만들지 않는다.** 지식베이스가 선언한 것을 그대로
               # 옮긴다 — 재료 종류마다 코드 상수를 하나씩 두면 §7 이 사실상 없어진다
               # (§12 gap 20 이 그 경고다).
               notices=[advice] if advice else [],
               scopes=[_scope("적합성 판정", [], [advice])] if advice else [])


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

    lines = [f"■ 고객 {customer_id} — 브리핑 자료"]
    lines += [f"· {k} {v}" for k, v in facts["customer"].items()]
    lines += [f"· {k} {v}" for k, v in facts["briefing"].items() if k != "source"]
    # 계좌 상태 — **정상인 항목도 값으로** 싣는다. 화면(briefing)은 요건이 성립한 것만
    # 렌더하는데(그게 맞다 — 한 장짜리 브리핑이다), 그 필터가 그대로 넘어오면 직원이
    # "디폴트옵션 설정돼 있어?" 라고 물었을 때 **미설정 고객에게만** 답이 나갔다. 설정된
    # 고객에게는 "준비된 자료가 없어요" — 정확히 "네, 돼 있습니다" 라고 답해야 하는 자리다.
    # 값이 없어서가 아니라 문제일 때만 실려서였다(engine/render.py::_account_state).
    lines += [f"· {k.replace('_', ' ')} {v}" for k, v in (facts.get("account_state") or {}).items()]
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
    # 화면 ⑥⑦⑧ 이 **이 고객에게** 고른 화법·반론·참고자료. 위 문제상황에서 나온 짝이라
    # 여기 붙는다(REQUIREMENTS.md §2 「문제상황 정의 — ⑥⑦⑧⑨ 의 출발점」).
    #
    # 이 세 줄이 없던 동안 "이 고객한테 뭐라고 말하지"는 `pitch` 도구가 지식베이스 화법
    # 102건을 **고객과 무관하게** 검색해 답했다 — 화면 ⑥에 그 고객 맞춤 화법이 떠 있는데도.
    # 화면과 대화가 같은 질문에 다른 카드를 말하는 상태였고, 그게 이 저장소가 가장
    # 경계하는 실패다(CLAUDE.md §3). allow 에는 이미 facts 전체가 있어 인용은 허용됐지만,
    # 재료 텍스트에 없으면 LLM 은 그 카드를 본 적이 없다.
    #
    # 값은 facts 를 **그대로** 옮긴다. 여기서 다시 고르면 그것이 두 번째 선정 경로가 되고,
    # 화면과 다시 갈린다. 고르는 것은 strategy_agent 몫이다.
    card_sources: list[dict] = []
    card_lines: list[str] = []
    for label, items, keys in (("이렇게 말해보세요", facts.get("talking_points"), ("title", "talk")),
                               ("예상 반론", facts.get("objections"), ("objection", "response")),
                               ("상담 참고", facts.get("consult_resources"), ("title", "snippet"))):
        for item in items or []:
            head, body = (str(item.get(k) or "").strip() for k in keys)
            if not head and not body:
                continue
            mark = " · ".join(x for x in (item.get("card_id"), item.get("situation")) if x)
            card_lines.append(f"· {label}: {head} — {body}" + (f" [{mark}]" if mark else ""))
            # 이 줄들의 출처는 **지식 카드**다(고객 원장이 아니다). 답에 영향을 준 재료는
            # 전부 출처에 실린다(§3) — 안 실으면 직원은 화법이 어디서 나왔는지 모른 채
            # 읽는다. 검색으로 온 재료가 아니므로 관련도(score)는 없다.
            if item.get("card_id"):
                card_sources.append({"id": item["card_id"], "title": head,
                                     "doc": item.get("source") or "출처 미상",
                                     "score": None, "page": None})
    lines += card_lines
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
    # 같은 카드가 ⑥과 ⑧에 함께 뽑힐 수 있다(pitch.k03.038 이 실제로 그렇다) — 첫 등장만 남긴다.
    seen: set[str] = set()
    deduped = [s for s in card_sources if not (s["id"] in seen or seen.add(s["id"]))]
    # 레이블–값 짝을 선언한다. 이 재료의 허용 집합에는 화면 값 말고도 ⑥⑦⑧ 의 화법·반론·
    # 참고자료 수치가 함께 들어 있어서(직원이 그것도 묻는다) 집합 포함 검사만으로는
    # "세액공제 잔여한도는 300만원이에요"(실제 0만원)가 통과한다 — 300 은 화법 문구에 실제로
    # 있는 숫자다. 선언을 relations.labeled_mispaired() 가 대조한다(fact 의 tiers·05 표의
    # tables 와 같은 자리다).
    labeled = [{"label": k.replace("_", " "), "value": str(v)}
               for src in (facts["customer"],
                           {k: v for k, v in facts["briefing"].items() if k != "source"},
                           facts.get("account_state") or {})
               for k, v in src.items()]
    return _ev("customer", query, "\n".join(lines),
               [{"id": f"customer.{customer_id}",
                 "title": f"{profile.nm} 고객 계좌 현황 (KB-PIN {customer_id})",
                 "doc": "고객 정보 — 계좌 원장 조회값 (브리핑 화면과 같은 값)",
                 "score": None, "page": None}, *deduped],
               allow=["\n".join(lines), json.dumps(_citable(facts), ensure_ascii=False, default=str)],
               cards=[{"id": f"customer.{customer_id}", "labeled": labeled,
                       # 재료 전문 — 항목 이름이 다른 자리(문제상황 제목·⑥⑦⑧ 카드 문구
                       # 등)에도 나오면 그 항목은 판정에서 뺀다(relations.checkable).
                       #
                       # **밑줄을 공백으로 펴서 넘긴다.** `labeled` 의 레이블은 정규화된
                       # 이름("당해 납입액")인데 재료 줄은 원장 키 그대로("당해_납입액")라,
                       # 그냥 넘기면 **자기 이름이 한 번도 안 세어진다** — 「재료 어디에든
                       # 제 이름이 다시 나오면 뺀다」가 통째로 작동하지 않는다. 그 상태에서
                       # ⑧ 의 일반 제도 설명("전년·당해 납입액이 세액공제 한도 900만원에
                       # 미달하는 고객")이 이 고객의 「당해 납입액 600만원」과 짝지어져
                       # "600인데 900이라 한다"로 잡혔다 — 카드 원문을 고객 값 주장으로
                       # 오독한 것이다(§6 — 검증기가 옳은 문장을 거부하는 것은 틀린 문장을
                       # 통과시키는 것보다 나쁘다). 이름이 두 자리에 나오면 가릴 수 없으므로
                       # 그 항목만 빠지고, 안 겹치는 항목의 판정은 그대로 남는다.
                       "context": "\n".join(lines).replace("_", " ")}])


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

#: 재료에 싣는 범위(과거 상담 세션 수 · 대화 세션 수 · 세션당 턴 수 · 발췌 길이). 상담 중에
#: 읽을 분량을 넘기면 아무도 안 읽고 원장만 무거워진다.
#:
#: 과거 상담(record)과 에이전트 대화(user/agent)의 예산을 **따로** 둔다. 하나의 최신순
#: 창을 같이 쓰면 graph.ask 가 매 턴 2턴씩 쌓는 대화 세션이 금방 창을 차지해, 정작
#: "지난번에 무슨 얘기 했지"의 지난번(과거 상담)이 밀려난다 — 이 도구를 쓰는 이유가
#: 사라지는 순서다.
HISTORY_SESSIONS, HISTORY_DIALOG_SESSIONS = 3, 1
HISTORY_TURNS, HISTORY_EXCERPT = 8, 120

#: 세션 턴의 역할 → 사람이 읽는 이름. `record` 는 발화가 아니라 «과거 상담 결과 요약»이다
#: (실서비스의 CRM 상담 기록 자리 — scripts/seed_sessions.py 가 목업으로 심는다).
_HISTORY_ROLE = {"user": "직원", "agent": "에이전트", "correction": "수정요청",
                 "tool": "도구실행", "record": "상담기록"}


def _history(state: AgentState, query: str) -> Evidence | None:
    """이 고객과 지난 상담에서 무슨 얘기를 했는지. 고객 화면이 닫혀 있으면 없다(§3).

    에이전트 답변은 **발췌만** 싣는다. 통째로 실으면 원장이 지난 상담의 문장으로 뒤덮여
    이번 질문과 무관한 수치까지 검증을 통과하게 된다. 발췌라도 그 안의 값은 원장에
    들어가므로, 재료가 시효 표시를 달고 나온다(HISTORY_MARK) — 답변이 그 값을 '지금
    기준'으로 말하지 않게 하는 것은 그 표시다.

    계획 루프가 정한 `query` 는 **선별에만** 쓴다: 질의어가 걸리는 과거 상담을 최신순보다
    앞세운다(코드 매칭 — LLM 아님). 매칭 0건은 이력 0건이 아니므로 최신순 그대로 싣는다 —
    "관련 상담이 없습니다"를 도구가 지어내면 그게 근거가 되기 때문이다. 걸러내지 않고
    순서만 바꾸는 이유도 같다: 질의어는 표현이 다를 수 있고, 뺐다가 틀리면 있는 기록을
    없다고 답하게 된다.

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

    # 읽는 곳은 **여기 하나**다. 과거 상담 기록(직원이 고객과 나눈 것)도 세션 저장소에
    # 들어와 있다 — 원장에서 따로 읽는 두 번째 경로를 두면 같은 상담이 두 번 실린다
    # (scripts/seed_sessions.py 가 목업을 심고, 실서비스에서는 CRM 이 같은 자리를 채운다).
    def _turns(session: dict) -> list[dict]:
        return [t for t in (session.get("turns") or []) if (t.get("text") or "").strip()]

    # **지금 진행 중인 세션은 «지난번»이 아니다.** graph.ask 가 턴마다 기록하므로 직전
    # 턴이 이미 이 저장소에 있는데, 그걸 재료로 실으면 30초 전 자기 답변이 «지난 상담
    # 기록»으로 나간다(시연 대본 T5 실측 — "두 번의 상담 기록" 중 하나가 방금 한 T4 였다).
    # 이번 세션의 직전 턴들은 대화 맥락(history)으로 이미 프롬프트에 실려 있어서 재료로
    # 중복할 이유도 없다.
    current = state.get("session_id")
    if current:
        sessions = [s for s in sessions if s.get("session_id") != current]

    recent = sorted(sessions, key=lambda s: s.get("started_at") or "", reverse=True)
    records = [s for s in recent if any(t.get("role") == "record" for t in _turns(s))]
    dialogs = [s for s in recent if s not in records and _turns(s)]

    # 질의어 매칭 — 2자 이상 토큰이 상담 텍스트에 부분일치하면 그 세션을 앞세운다.
    tokens = [w for w in (query or "").split() if len(w) >= 2]

    def _hits(session: dict) -> int:
        joined = " ".join(t.get("text") or "" for t in _turns(session))
        return sum(1 for w in tokens if w in joined)

    if tokens and any(_hits(s) for s in records):
        records.sort(key=_hits, reverse=True)  # 동점은 sort 안정성으로 최신순 유지

    def _render(session: dict, lines: list[str]) -> None:
        turns = _turns(session)
        lines.append(f"· {(session.get('started_at') or '')[:10]} 상담 ({len(turns)}턴)")
        for turn in turns[-HISTORY_TURNS:]:
            text = " ".join((turn.get("text") or "").split())
            if len(text) > HISTORY_EXCERPT:
                text = text[:HISTORY_EXCERPT] + "…"
            role = _HISTORY_ROLE.get(turn.get("role"), turn.get("role") or "?")
            lines.append(f"  - {role}: {text}")

    # 구획을 나눠 싣는다 — 오늘 나눈 대화가 「과거 상담」으로 오독되면 방금 한 말이
    # 지난 상담의 근거처럼 인용된다.
    lines = [f"■ 고객 {customer_id} — 상담 이력 기록"]
    if records:
        lines.append("[과거 상담 기록]")
        for session in records[:HISTORY_SESSIONS]:
            _render(session, lines)
    if dialogs:
        lines.append("[에이전트와 나눈 최근 대화]")
        for session in dialogs[:HISTORY_DIALOG_SESSIONS]:
            _render(session, lines)
    if len(lines) == 1:
        return None

    # 시효 표시는 **과거 상담이 실제로 실렸을 때만** 단다. 방금 나눈 대화에 "지난 상담
    # 기록입니다"를 붙이면 표시가 거짓말을 하고, 매번 붙는 표시는 읽히지 않는다 —
    # 정작 낡은 값이 실린 턴에서도 그냥 지나가게 된다.
    return _ev("history", query, "\n".join(lines),
               [{"id": f"session.{customer_id}", "title": f"고객 {customer_id} 상담 이력",
                 "doc": "상담 이력 기록(과거 상담 + 에이전트가 턴마다 남긴 대화)",
                 "score": None, "page": None}],
               notices=[HISTORY_MARK] if records else [])


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
                  kind: str = "지식", history: list[dict] | None = None,
                  query: str | None = None) -> list[tuple[float, dict]]:
    """질문의 '실제 의도'에 답이 되는 후보만 남긴다(오답 차단). 순서·점수는 그대로 둔다.

    LLM 이 없는 id 를 지어내도 실재 후보와 대조해 걸러낸다 — select.llm_pick 과 같은
    안전장치다. LLM 이 죽으면 예외를 그대로 올린다: 게이트를 못 돌린 턴이 게이트 없이
    답을 만들면 §11 이 막으려는 상태가 된다.

    **이전 대화를 함께 넘긴다.** 후속 질문("1번꺼"·"타행에서요")은 그 말만으로는 어떤
    후보와도 맞지 않아서, 맥락 없이 판정하면 제대로 찾아온 카드까지 전부 탈락한다 —
    계획·작성 프롬프트에 히스토리를 실을 때(§12 지워진 gap 1) 이 프롬프트만 빠져 있었다.

    **계획이 이번에 무엇을 찾는지도 함께 넘긴다**(`query`). 없으면 직원 질문을 그대로
    쓴다. 왜 필요한지는 ADEQUACY_PROMPT 머리말에 적어뒀다 — 고객 특정 질문에서 일반
    자료가 전멸하던 자리다.
    """
    progress.emit("찾은 자료가 질문에 맞는지 확인하고 있어요")
    cards = "\n".join(_headline(c) for _, c in hits)
    raw = generate(ADEQUACY_PROMPT.format(question=question, cards=cards, kind=kind,
                                          query=query or question,
                                          history_block=format_history(history)),
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
    """채택할 후보만 남겨 돌려준다. 0건이면 게이트를 돌리지 않는다(부를 이유가 없다).

    직원 질문과 이번 질의를 **둘 다** 넘긴다. 예전에는 `question or query` 로 하나만
    넘겨서, 계획이 무엇을 찾는 중인지가 게이트에 안 보였다.
    """
    if not hits:
        return []
    return fits_question(state.get("question") or query, hits, kind,
                         history=state.get("history"), query=query)


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
#
# 작성 규약(COMPOSE_SYSTEM 1번)이 «재료에 없는 값은 재료 안 값에서 계산해서 만들어내지도
# 않는다(날짜·차액·비율 전부)»다. 옳은 규약이다 — 그런데 그 결과 **오늘이 며칠인지가 어디에도
# 재료로 없었다.** 그래서 세액공제처럼 연말이 마감인 이야기에서 "며칠 남았다"를 말할 수가
# 없었고, 말하면 원장 밖 수치라 verify 가 잘라냈다.
#
# 답은 «LLM 이 오늘을 알게 하는 것»이 아니다. LLM 의 오늘 감각은 학습 시점이지 실행 시점이
# 아니라, 알게 두면 조용히 몇 달 틀린 날짜를 말한다. 답은 **코드가 오늘을 재료로 싣는 것**
# 이다 — 다른 도구가 지식베이스에서 근거를 길어오는 것과 정확히 같은 자리다.
#
# 세는 법을 둘 다 싣는다. "연말까지 126일"과 "오늘 포함 127일"은 같은 날에 대해 둘 다
# 참이라, 하나만 던지면 어느 쪽인지 몰라 하루짜리 오안내가 된다.
# ─────────────────────────────────────────────────────────────

_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def _date(state: AgentState, query: str) -> Evidence | None:
    """오늘 날짜와 기한까지 남은 일수. 검색이 아니라 시스템 시계에서 온 재료다.

    고객 화면이 열려 있으면 **원장 스냅샷 기준일**도 함께 싣는다. 잔액·수익률은 그날 찍힌
    값이고 잔여일수는 오늘 기준이라, 둘이 며칠 벌어져 있는지를 재료가 말해주지 않으면
    "만기 D-14 인데 왜 잔액은 사흘 전 값이냐"에 답할 수 없다.
    """
    from pension_agent.strategy_agent import customer as CUST  # noqa: PLC0415

    now = CUST.today()
    left = CUST.days_to_year_end(now)
    lines = [
        "■ 오늘 날짜와 기한 (시스템 시계 — 검색 결과가 아니다)",
        f"· 오늘: {now.year}년 {now.month}월 {now.day}일 ({_WEEKDAYS[now.weekday()]}) / {now.isoformat()}",
        f"· 올해: {now.year}년 (세액공제 등 «올해»는 {now.year}년 1월 1일~12월 31일)",
        f"· 연말({now.year}년 12월 31일)까지: {left}일 남음 — 오늘을 세지 않은 값이고, "
        f"오늘부터 12월 31일까지를 세면 {left + 1}일이다",
    ]
    if state.get("customer_id"):
        age = CUST.ledger_age_days()
        lines.append(
            f"· 고객 계좌 원장 기준일: {CUST.AS_OF.isoformat()} — 오늘 기준 {age}일 전 스냅샷이다. "
            "잔액·수익률·납입액은 그날 값이고, 만기까지 며칠·마지막 접촉 이후 며칠 같은 "
            "잔여일수·경과일은 오늘 기준으로 다시 센 값이다")
    # atomic·notices 를 비워 둔다. 여기 있는 것은 값+조건이 붙은 원문 스팬이 아니라 계산값
    # 하나하나라, 문장을 통째로 요구하면 답변이 날짜 덤프가 된다. 수치 집합 검사만 걸린다.
    #
    # 그래도 **틀린 날짜는 걸린다.** 검증기가 날짜를 연·월·일 토큰으로 흩지 않고 통짜
    # 정규형으로 대조하기 때문이다(verify.py 의 _DATE_KO 주석). 그 전에는 원장 어딘가에
    # 2026 이 있다는 이유로 2026년의 아무 달이나 통과했다. 여기 실리는 재료의 형태
    # 요구(ANSWER_SHAPES["date"] — 남은 일수만 쓰고 기준 날짜를 빼지 않는다)와 작성 규약
    # 1번(재료 밖 날짜 계산 금지)이 그 위에 겹쳐 있다.
    return _ev("date", query, "\n".join(lines),
               [{"id": "system.date", "title": f"오늘 날짜 ({now.isoformat()})",
                 "doc": "시스템 시계 — 에이전트 실행 시점", "score": None, "page": None}])


# ─────────────────────────────────────────────────────────────
# 세액공제 환급 예상액 — `date` 와 같은 부류. 검색하지 않고 코드가 계산해 싣는다.
#
# 07/01 ② 가 정한 「계산기」의 첫 조각이다. 그 장이 근거로 든 것이 이것이다 — 직원 두 명이
# 각자 엑셀로 세액공제 계산기를 만들어 배포했다(핫팁 199713·200518). 도구가 없어 사비로
# 만들 만큼 강한 니즈인데, 지금 재료에는 **현재 납입액 기준 한 값**만 있어서
# ("예상 세액공제액 118만원") "300만원 더 넣으면 얼마 더 받아?" 에 답할 수 없었다.
#
# ━━ 입력 수치는 **직원이 친 말**에서 뽑는다 ━━
# 계획 LLM 이 넘기는 `query` 는 직원 질문의 재작성본이라, 줄여 쓰는 과정에서 말을 흘린다
# (`run()` 주석의 화면번호 사례). 단어를 흘릴 수 있으면 숫자도 흘리는데, 검색은 0건으로
# 티가 나는 반면 계산기는 **조용히 다른 답**을 낸다. 게다가 계산 결과는 원장에 실려 인용이
# 허가되므로, 틀린 입력이 그대로 «승인된 숫자»가 된다 — LLM 이 경계를 넓히는 자리다.
# 그래서 금액은 `state["question"]` 에서 코드가 뽑는다(`verify.first_amount`).
#
# ━━ 공제율은 두 구간을 다 낸다 ━━
# 총급여 구간은 원장에 없다(demo_status §4 — 목업 9명 전원 미확인). 코드는 브리핑에서
# 보수적으로 낮은 쪽을 쓰지만(과대산출 회피), 계산기가 그 값 하나만 내놓으면 16.5% 구간
# 고객에게 "왜 적게 나와?" 가 된다. 두 경우를 다 싣고 어느 쪽인지는 직원이 가른다.
# ─────────────────────────────────────────────────────────────

#: 공제율의 근거 카드. 세율·한도·아래 단서가 전부 여기서 온다.
TAX_FACT_ID = "fact.k04.f2"


def _won(v: int) -> str:
    from pension_agent.strategy_agent.engine.text import won  # noqa: PLC0415

    return won(v)


def _tax_credit(state: AgentState, query: str) -> Evidence | None:
    """세액공제 환급 예상액. 계산은 strategy_agent 것을 쓰고 여기서는 재료로 편다."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.strategy_agent import customer as CUST  # noqa: PLC0415
    from pension_agent.verify import first_amount  # noqa: PLC0415

    p = CUST.get_profile(customer_id)
    card = KB.facts.get(TAX_FACT_ID)
    if p is None or card is None:
        return None

    paid, cap = p.pension_paid_ytd, CUST.TAX_CREDIT_CAP_WON
    # 잔여한도는 **원장 값을 쓴다**(`p.room`). 한도에서 IRP 납입액을 빼서 다시 계산하면
    # 안 된다 — 한도 900만원은 연금저축과 **공유**라(fact.k04.f2 "연금저축 세액공제 포함")
    # 연금저축에서 이미 쓴 몫을 IRP 납입액만으로는 알 수 없다. 실제로 당해 납입 0원인데
    # 잔여한도가 0인 고객이 목업에 있다 — 다시 계산하면 그 고객에게 "900만원 더 넣으면
    # 148.5만원" 이라고 말하게 된다(§3 "같은 판정을 두 번 구현하지 않는다").
    room = p.room * 10_000
    # 금액을 안 말했으면 «잔여한도를 채우면» 으로 읽는다. 원장 값이라 지어낸 수가 아니고,
    # 직원이 실제로 묻는 것도 대개 그것이다("얼마나 더 받을 수 있어?").
    said = first_amount(state.get("question") or "")
    extra = said[1] if said else room
    gain_base = min(extra, room)          # 잔여한도를 넘는 납입은 공제 대상이 아니다
    target = paid + gain_base
    gain = CUST.tax_credit(target, 1.0) - CUST.tax_credit(paid, 1.0)

    lines = [f"■ 세액공제 환급 예상액 — {p.nm} 고객 (시스템 계산 — 검색 결과가 아니다)",
             f"· 당해 납입액 {_won(paid)} · 세액공제 한도 {_won(cap)} · 잔여한도 {_won(room)}",
             f"· 계산에 쓴 추가 납입액 {_won(extra)}"
             + ("" if said else " (질문에 금액이 없어 잔여한도로 계산했다)")]

    notices: list[str] = []
    if gain <= 0:
        # 환급 «금액»을 새로 단정하지 않는 갈래다. 아래 결정세액 단서도 붙이지 않는다 —
        # 그 단서는 최대 환급액을 단정할 때 걸리는 것이라 여기서는 무관하다(CLAUDE.md §7).
        lines.append(f"· 세액공제 잔여한도가 {_won(room)}이라 **추가 공제 대상이 없다** — "
                     f"더 납입해도 올해 세액공제로 돌아오는 금액은 늘지 않는다 "
                     f"(연 납입한도 {_won(cap)} 은 연금저축과 함께 쓴다)")
    else:
        lines.append(f"· 공제 대상 {_won(min(paid, cap))} → {_won(min(target, cap))} "
                     f"(잔여한도 {_won(room)}까지)")
        for when, rate in (("총급여 5,500만원 이하", CUST.TAX_CREDIT_RATE["5500이하"]),
                           ("총급여 5,500만원 초과", CUST.TAX_CREDIT_RATE["5500초과"])):
            now, after = CUST.tax_credit(paid, rate), CUST.tax_credit(target, rate)
            lines.append(f"· {when}({rate * 100:.1f}%): 환급 예상 {now:,}원 → {after:,}원 "
                         f"(늘어나는 금액 {after - now:,}원)")
        lines.append("· 이 고객의 총급여 구간은 원장에 없어 두 경우를 다 실었다 — "
                     "어느 구간인지 확인하면 하나로 좁혀진다")
        notices.append(_caveat(card))

    return _ev("tax_credit", query, "\n".join(lines),
               KBMOD.sources_of(KB, [(1.0, card)]), notices=notices,
               scopes=[_scope(card.get("label") or TAX_FACT_ID, [], notices)] if notices else None,
               cards=[card])


#: 환급액에 따라붙는 단서를 카드에서 떼어 오는 표지. 코드가 문장을 갖지 않는다 —
#: 세법이 바뀌면 카드가 바뀌고 답변도 함께 바뀌어야 한다(§7 "표시는 데이터 선언이 정한다").
_CAVEAT_MARK = "단, "


def _caveat(card: dict) -> str:
    """공제율 카드가 못박은 단서. 없으면 카드 원문을 그대로 쓴다(지어내지 않는다)."""
    value = card.get("value") or ""
    at = value.find(_CAVEAT_MARK)
    return value[at:].strip() if at >= 0 else value.strip()

# 문제상황에 걸린 화법 — 화면 ⑥⑦⑧ 과 같은 후보군
# ─────────────────────────────────────────────────────────────

#: 한 번에 올리는 후보 상한. 상담 중에 읽을 수 있는 분량을 넘기면 아무도 안 읽는다
#: (guard.PER_COND 와 같은 이유의 상한이다).
PLAYBOOK_TOP_K = 2

#: 갈래 — 화면 ⑥⑦⑧ 이 쓰는 후보 축 그대로다. pitch 갈래의 세 card_type(proposal ⑥ ·
#: objection ⑦ · guide ⑧)과, ⑧ 이 화법과 번갈아 싣는 방법론·절차(`resource.py`
#: `_situation_resources`). 여기서 축을 새로 정하지 않는다 — 정하면 화면과 갈린다.
PLAYBOOK_LANES = ("pitch", "procedure", "method")

_PLAYBOOK_TYPES = ("proposal", "objection", "guide")


def playbook_hits(state: AgentState, *, lanes: tuple[str, ...] | None = None,
                   exclude: set[str] | None = None,
                   top_k: int = PLAYBOOK_TOP_K) -> list[tuple[float, dict]]:
    """이 고객의 문제상황에 걸린 화법 후보. 도구(`_situation`)와 제안 판정(`act`)이 함께 쓴다.

    **매칭을 여기서 만들지 않는다.** 고객 상태 → 화법 연결은 strategy_agent 가 화면 ⑥⑦⑧
    을 위해 이미 갖고 있고(3단: 카드의 `segments` → 세그먼트·화법 그룹 매핑 → n-gram,
    `docs/REQUIREMENTS.md` §2), 그 함수를 그대로 부른다. 대화형이 자기 매칭을 만들면 같은
    질문에 화면과 다른 카드를 말하게 된다(CLAUDE.md §3 — 같은 재료를 두 경로로 구현하지
    않는다).

    **금지 상속도 같은 이유로 공짜다.** `problem_situations()` 가 세그먼트의 `exclusions`
    선언을 판정하므로(연금수령 개시 계좌에서 납입·세액공제 세그먼트 seg.13·15·16 이 빠진다),
    여기서 따로 막지 않아도 화면이 막은 것은 후보에 애초에 없다 — 따로 막으면 그것이 두 번째
    판정 경로가 되어 언젠가 화면과 갈린다.

    카드 본체는 **대화형 KB**(`state.KB`)에서 id 로 다시 찾는다. strategy_agent 쪽은 KB 를
    따로 적재하는 별도 인스턴스라, 그쪽 카드를 그대로 쓰면 시효성 판정이 노드마다 갈릴 수
    있다(state.py 가 KB 를 한 번만 적재하는 이유와 같다).

    슬롯(«방금 나온 상황»)으로 후보를 좁힌다 — 화면은 고객 상태만 보고 상담 **전에** 고르지만
    여기는 상담 **중**이라, 직원이 방금 말한 상황까지 볼 수 있다. 이것이 화면과 다른 일을
    하는 유일한 근거다. 슬롯 분해가 실패하면(LLM) 좁히지 않고 상태만으로 고른다.

    `lanes` 는 어느 축의 후보를 볼지다(PLAYBOOK_LANES 부분집합). 제안 판정(`act`)은 이번
    턴이 다룬 갈래만 넘긴다 — 절차를 물은 턴에 화법을 제안하면 §3 「묻지 않은 값」의 제안
    버전이 된다. 도구로 직접 불릴 때는 전 갈래를 본다(None) — 그때는 질문 자체가 이
    재료를 향한 것이라 갈래를 좁힐 근거가 질문에 없다.
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return []
    from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.strategy_agent.situations import problem_situations  # noqa: PLC0415
    from pension_agent.strategy_agent.support import matching  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return []
        situations = problem_situations(profile, strategy_customer.conditions(profile))
    except Exception:
        return []
    if not situations:
        return []

    try:
        slots = PITCHMOD.extract_slots(state)
    except LLMError:
        # 슬롯은 후보를 **좁히는** 보조 정보다. 없으면 고객 상태만으로 고르면 되고, LLM 이
        # 정말 죽었다면 뒤이은 작성이 같은 이유로 실패해 턴이 §11 로 끝난다.
        slots = {}

    # 갈래마다 넉넉히 뽑아 슬롯으로 거른 뒤 합쳐서 상위 K 를 고른다 — 갈래별로 K 를
    # 나눠 가지면 이 고객에게 걸린 것이 한 갈래에 몰려 있을 때 그 갈래가 잘린다.
    scored: list[tuple[float, dict, dict]] = []
    for lane in (lanes or PLAYBOOK_LANES):
        if lane == "pitch":
            for card_type in _PLAYBOOK_TYPES:
                scored += matching.scored_situation_cards(situations, card_type, top_k * 3)
        elif lane == "procedure":
            scored += matching.scored_situation_procedures(situations, top_k * 3)
        elif lane == "method":
            scored += matching.scored_situation_methods(situations, top_k * 3)

    by_id = {c["id"]: c for c in KB.cards}
    skip = set(exclude or ())
    best: dict[str, tuple[float, dict]] = {}
    for score, card, _seg in scored:
        local = by_id.get(card["id"])
        if local is None or local["id"] in skip:
            continue
        # 슬롯 스코프는 화법 카드의 축이다(tags.stage·customer_type). 절차·방법론 카드에는
        # 그 축이 없어서 여기 걸면 슬롯이 잡힌 턴마다 전부 탈락한다 — 화법에만 건다.
        if local["_kind"] == "pitch" and not KBMOD.matches_scope(
                local, customer_type=slots.get("customer_type"), stage=slots.get("stage")):
            continue
        if local["id"] not in best or score > best[local["id"]][0]:
            best[local["id"]] = (score, local)
    return sorted(best.values(), key=lambda x: (-x[0], x[1]["id"]))[:top_k]


def _playbook(state: AgentState, query: str) -> Evidence | None:
    """이 고객 상태에 걸린 화법·반론·방법론·절차 참고자료 — 화면 ⑥⑦⑧ 과 같은 후보군에서.

    `customer` 도구가 싣는 것은 화면이 **이미 고른** 2건이고, 여기는 그 **후보군 전체**에서
    이번 대화 상황에 맞는 것을 고른다. 후보군이 같으므로 화면이 자른 것을 꺼내와도 화면과
    어긋나지 않는다 — 다른 매칭이 만든 카드가 아니기 때문이다.

    **후보군 전체를 원장에 싣지 않는다.** ⑦⑧ 후보군은 카드 id·발췌가 통째로 들어와 인용
    허용 수치를 몇 배로 불리고, 그러면 무관한 카드의 숫자가 아무 주장에나 근거를 대준다
    (`_POOL_KEYS` 주석의 사고 — 22개가 110개가 되면서 오답이 통과했다). 여기서는 고른
    것만 올린다.
    """
    hits = playbook_hits(state, exclude=cited_cards(state))
    hits = _adopt(state, query, hits, "고객 상태에 걸린 자료")
    if not hits:
        return None
    return playbook_evidence(query, hits)


def playbook_evidence(query: str, hits: list[tuple[float, dict]]) -> Evidence | None:
    """(관련도, 카드) 목록 → playbook 원장 항목. 도구와 승낙 턴(`act._show_playbook`)이
    함께 쓴다.

    후보에는 종류가 섞인다(화법·방법론·절차 — 화면 ⑧ 이 세 갈래를 번갈아 싣는 것과 같은
    축이다). **종류마다 렌더러와 선언을 그 종류의 도구 것 그대로 쓴다** — 화법 렌더러
    (`kb.build_context`)에 절차 카드를 태우면 두 가지가 깨진다: ① `cautions` 를 역할(role)
    구분 없이 뿌려 저작 메모(authoring)가 직원에게 노출되고(§12 지워진 gap 17 이 고친
    실패의 재발), ② 화면번호가 `atomic` 강제를 받지 않아 LLM 이 옮겨 적다 틀려도 아무도
    못 잡는다.
    """
    if not hits:
        return None
    pitch_hits = [(sc, c) for sc, c in hits if c["_kind"] == "pitch"]
    proc_hits = [(sc, c) for sc, c in hits if c["_kind"] == "procedure"]
    method_hits = [(sc, c) for sc, c in hits if c["_kind"] == "method"]

    blocks: list[str] = []
    atomic: list[str] = []
    notices: list[str] = []
    scopes: list[dict] = []
    if pitch_hits:
        blocks.append(KBMOD.build_context(KB, pitch_hits))
    if proc_hits:
        blocks.append(procedure_qa.render(proc_hits))
        p_atomic, p_notices, p_scopes = _procedure_decls([c for _sc, c in proc_hits])
        atomic += p_atomic
        notices += p_notices
        scopes += p_scopes
    if method_hits:
        blocks.append("\n\n".join(_render_method(c) for _sc, c in method_hits))
        m_notices, m_scopes = _method_decls([c for _sc, c in method_hits])
        notices += m_notices
        scopes += m_scopes
    return _ev("playbook", query, "\n\n".join(blocks), KBMOD.sources_of(KB, hits),
               atomic=atomic, notices=notices, scopes=scopes or None,
               cards=[c for _sc, c in hits])


def cited_cards(state: AgentState) -> set[str]:
    """이번 턴 원장이 이미 들고 있는 카드 id. 같은 카드를 두 번 싣지 않기 위해서다 —
    `customer` 도구가 화면의 2건을 이미 실었다면 그건 여기서 다시 꺼낼 것이 아니다."""
    return {s["id"] for e in (state.get("evidence") or []) for s in e["sources"] if s.get("id")}


# ─────────────────────────────────────────────────────────────
# 레지스트리
# ─────────────────────────────────────────────────────────────

TOOLS: dict[str, Tool] = {
    t.name: t for t in (
        Tool("pitch", "고객에게 실제로 할 말(대사·반론 대응·논거)을 만든다", _pitch,
             progress="상담 화법"),
        Tool("fact", "제도·상품의 확정된 수치를 기준시점·출처와 함께 돌려준다", _fact,
             progress="제도·상품 수치"),
        Tool("procedure", "업무를 어떤 순서·채널로 처리하는지와 걸리는 주의를 돌려준다", _procedure,
             progress="업무 처리 절차"),
        Tool("screen", "단말 화면번호를 찾는다 — '무슨무슨 조회/등록은 몇 번 화면인가'", _screen,
             progress="단말 화면번호"),
        Tool("channel", "고객이 스타뱅킹·인터넷뱅킹에서 직접 처리하는 메뉴 경로를 돌려준다",
             _channel, progress="비대면 채널 경로"),
        Tool("segment", "관리 대상 고객군의 정의와 선정 조건을 설명한다", _segment,
             progress="고객군 정의"),
        Tool("method", "무엇을 어떤 기준으로 판단하는지(관리 방법론)를 돌려준다", _method,
             progress="관리 방법론"),
        Tool("fieldtip", "영업점 현장 관찰(본부 지침 아님)을 돌려준다", _fieldtip,
             progress="영업점 현장 관찰"),
        Tool("market", "시장이 어떻게 돌아가나 — 시황·증시·환율·금리·경제 이벤트와 "
                       "투자전략을 기준시점과 함께 돌려준다",
             _market_like("market", "시황"), progress="시황 자료"),
        Tool("lineup", "우리가 뭘 파나 — 이달의 추천펀드, 디폴트옵션 포트폴리오 구성상품·"
                       "비중·금리, 투자성향별 포트폴리오, TDF 빈티지별 비중을 기준시점과 "
                       "함께 돌려준다",
             _market_like("lineup", "운용 상품"), progress="운용 상품 자료"),
        # "왜 관리 대상(타겟)인가"를 설명에 명시한다 — 재료에 실려 있는데(why_this_customer·
        # 판단근거) 설명이 잔액·수익률만 말하면, 계획이 그 질문을 segment(고객군 일반 정의)로
        # 보내고 이 도구를 안 부른다. 도구 설명이 곧 계획의 판단 재료다.
        # 「이 고객한테 뭘 추천하지」가 이 도구다. 설명에 **권유가 아니라 범위**임을 적는다 —
        # 계획 LLM 이 읽는 유일한 판단 재료라, 여기가 흐리면 그 질문이 lineup 만 세 바퀴
        # 돌다가 재료 0건으로 끝난다(실제로 그랬다).
        Tool("suitable", "이 고객 투자성향으로 **어디까지 안내할 수 있는지** — 적합성 게이트가 "
             "허용하는 위험등급 상한, 그 범위를 통과한 상품·포트폴리오 목록, 제외된 상품과 "
             "그 사유를 돌려준다. 「이 고객한테 뭘 추천하지」·「무슨 상품 있어」가 여기다",
             _suitable),
        Tool("customer", "지금 열려 있는 고객의 브리핑 자료(잔액·수익률·성립 요건, 그리고 이 고객이 "
             "왜 관리 대상(타겟)으로 선정됐는지의 근거)를 돌려준다", _customer,
             progress="고객 브리핑 자료"),
        Tool("history", "이 고객과 지난 상담에서 무슨 얘기를 했는지(날짜·질문·안내 요지) 돌려준다",
             _history, progress="지난 상담 기록"),
        # 시점·기한이 걸린 질문은 재료가 없으면 답이 안 나온다(§8 "지어내지 않는다"가 그대로
        # «말하지 못한다»가 된다). 도구 설명이 곧 계획의 판단 재료이므로, 언제 부르는지를
        # 예시로 박아 둔다 — "얼마 안 남았다"류 문장을 쓰려는 턴이 전부 여기 걸려야 한다.
        # 계산기(07/01 ② 3번)의 첫 조각. 「얼마 더 넣으면 얼마 받나」는 검색으로 답할 수
        # 없고, 재료 밖 계산은 금지라(§5) 코드가 계산해 싣지 않으면 말할 방법이 없다.
        Tool("tax_credit", "«얼마를 더 납입하면 세액공제로 얼마나 돌려받는지»를 계산한다 — "
             "'300만원 더 넣으면 얼마 받아', '한도 채우면 얼마 돌려받아'처럼 **환급액·"
             "납입액을 계산해 달라는** 질문에 쓴다(제도 설명이 아니라 이 고객의 금액)",
             _tax_credit),
        Tool("date", "오늘이 며칠인지와 연말까지 남은 일수를 돌려준다 — '오늘 며칠이야', "
             "'연말까지 얼마 남았어', '언제까지 납입해야 해'처럼 **시점·기한**이 걸린 질문, "
             "그리고 답변에 '며칠 남았다·올해 안에'를 쓰려는 모든 경우에 먼저 부른다", _date,
             progress="오늘 날짜·기한"),
        # `pitch` 와 갈라 두는 이유는 재료가 오는 곳이 다르기 때문이다. pitch 는 질문으로
        # 지식베이스 전체를 찾고, 이쪽은 **이 고객의 문제상황**에 걸린 것만 본다 — 화면
        # ⑥⑦⑧ 과 같은 후보군이다. 설명이 갈리지 않으면 계획이 둘을 구분하지 못한다.
        Tool("playbook", "지금 열려 있는 고객의 상태(문제상황)에 걸린 화법·예상반론·"
             "관리방법론·업무절차 참고자료를 브리핑 화면 ⑥⑦⑧ 과 같은 후보군에서 돌려준다",
             _playbook, progress="이 고객 상태에 걸린 참고자료"),
    )
}

#: 열려 있는 고객이 있어야 성립하는 도구. 어느 고객인지가 재료의 전제다(§3).
_NEEDS_CUSTOMER = frozenset({"customer", "history", "suitable", "tax_credit", "playbook"})


def usable(state: AgentState | None = None) -> list[str]:
    """이 턴에 실제로 부를 수 있는 도구 이름. 고객 화면이 닫혀 있으면 고객 전제 도구는
    빠진다(§3). 카탈로그와 재계획의 '아직 안 써 본 도구'가 같은 목록을 봐야 한다 —
    갈리면 카탈로그에 없는 도구를 다시 시도하라고 말하게 된다."""
    opened = bool((state or {}).get("customer_id"))
    return [t.name for t in TOOLS.values() if opened or t.name not in _NEEDS_CUSTOMER]


def catalog(state: AgentState | None = None) -> str:
    """계획 프롬프트에 실리는 도구 목록. 쓸 수 없는 도구는 애초에 보여주지 않는다 —
    고객 화면이 닫혀 있는데 customer 를 제안하게 두면 한 스텝을 낭비한다."""
    return "\n".join(f"- {TOOLS[n].name}: {TOOLS[n].desc}" for n in usable(state))


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
    if tool.progress:
        # 실제로 이 도구를 돌리기 직전에만 찍는다(progress.py ②). 문구는 도구 선언에서
        # 온다 — LLM 이 만든 질의(query)는 싣지 않는다.
        progress.emit(f"{progress.object_of(tool.progress)} 찾고 있어요")
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


#: 출처의 역할. 답이 **그 재료에서 나온 것**인지, 표현을 **제한만** 한 것인지는 다른
#: 사건이고, 직원에게도 다르게 보여야 한다(§3 · §8). 답을 내보내는 노드가 둘(compose ·
#: clarify)이라 어휘는 한 곳에 둔다 — 갈리면 화면이 한쪽만 갈라 보여준다.
GROUND, CAUTION = "근거", "주의"


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
