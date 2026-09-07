"""지식 카드 도구 — fact · procedure · screen · channel · segment · method · fieldtip.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from pension_agent.consult_agent import kb as KBMOD, relations as REL
from pension_agent.consult_agent.nodes import facts_qa, procedure_qa, segment_qa
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.consult_agent import tools as _T  # noqa: PLC0415 — 후크는 패키지를 거쳐 부른다(머리말)
from pension_agent.consult_agent.tools.adequacy import _adopt
from pension_agent.consult_agent.tools.base import Evidence, _ev, _scope


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
    hits = _adopt(state, query, _T.pick(("screen",), query, top_k=3), "단말 화면번호")
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
    hits = _adopt(state, query, _T.pick(("channel",), query, top_k=3), "비대면 채널 경로")
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
    hits = _adopt(state, query, _T.pick(("method",), query, top_k=2), "관리 방법론")
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
    hits = _adopt(state, query, _T.pick(("fieldtip",), query, top_k=2), "영업점 현장 관찰")
    if not hits:
        return None
    # 예전에는 여기만 전용 신뢰 표시(FIELDTIP_MARK)를 notices 로 강제했다. 그 표시는
    # 이제 재료 성격 표시의 한 갈래이고(marks.py), 문서 레지스트리의 등급에서 나온다 —
    # 현장 관찰만 표시되고 본부 공식·대외 공개·교육자료는 안 되던 비대칭을 없앤다.
    return _ev("fieldtip", query, "\n\n".join(_render_fieldtip(c) for _, c in hits),
               KBMOD.sources_of(KB, hits), cards=[c for _s, c in hits])
