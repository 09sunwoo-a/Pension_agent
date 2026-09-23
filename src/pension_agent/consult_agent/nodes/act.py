"""화면 연계 — 후처리 노드 `offer`(제안)와 확인 응답 노드 `confirm_action`(확인·연계).

답변이 직원이 단말에서 이어서 할 일을 가리키면(업무 절차의 화면번호, 고객에게 보낼 문구의
LMS 발송 화면), 그 화면으로 바로 갈 수 있게 **연계를 제안**한다. 실제 작업은 직원이 그
화면에서 한다 — 에이전트는 화면을 열어줄 뿐 작업을 대신 수행하지 않는다(CLAUDE.md §10).

예전에는 여기서 LMS 를 **발송까지 수행**했다(스텁). 그러면 되돌릴 수 없는 대외 행위를
에이전트가 하는 셈이라 확인 절차 하나에 전부를 걸어야 했다. 지금은 수행하는 것이 없으므로
확인은 "이 화면을 열까요"에 대한 것이고, 보낼지 말지는 직원이 그 화면에서 정한다.

━━ 제안 → 확인 → 연계 ━━
제안 여부는 **규칙이 정한다**(LLM 판단 아님) — 답변이 실제로 화면을 가리키고, 그 화면을
열 정보가 갖춰졌을 때만. 매 턴 "연계해드릴까요?"가 붙으면 직원이 그 문장을 읽지 않게
되고, 그러면 확인 절차 자체가 의미를 잃는다.

━━ 제안은 그 자리에서만 유효하다 ━━
확인 응답은 **직전 턴**의 제안만 실행한다. 예전에는 대화 이력을 거슬러 올라가 '가장 최근의
제안'을 찾았고, 그래서 사이에 다른 질문이 오간 뒤의 "네"도 몇 턴 전 제안을 실행할 수
있었다(§12 gap 14). 직원이 잊은 제안이 뒤늦게 실행되는 것은 승낙이 아니다.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pension_agent import clock, note
from pension_agent import observability
from pension_agent.consult_agent import tools
from pension_agent.consult_agent.effects import memo, schedule, screens
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.consult_agent.effects.actions import ACTIONS, MEMO_DEFAULT_TO
from pension_agent.llm import LLMError

_YES = ("네", "예", "웅", "응", "그래", "좋아", "열어", "연계", "해줘", "해주세요", "부탁", "보내",
        "ok", "yes")
_NO = ("아니", "괜찮", "나중", "취소", "안 열", "안열", "하지마", "no")

#: 제안 갈래마다 다른 동사 — **묻는 문장은 그 갈래가 실제로 하는 일을 말한다.** 「연계」는
#: 단말 화면을 여는 갈래의 말이다. 화법 갈래는 여는 화면이 없고 카드 내용을 그 자리에
#: 보여줄 뿐이라, 「연계해드릴까요」로 물으면 직원은 승낙하고 나서 화면이 열리기를 기다린다.
#: 갈래에 없는 kind 는 화면을 여는 쪽이므로 기본값을 쓴다.
_VERBS = {"pitch": "보여드릴까요", "memo": "보내드릴까요"}
_DEFAULT_VERB = "연계해드릴까요"


def offer_prompt(action: dict[str, Any]) -> str:
    """제안 하나를 직원에게 묻는 문장. **한 제안에 이 문장은 하나뿐이다** — 답변 본문의
    마지막 줄 · 버튼 위 문구(`main._turn_events`) · 애매한 답에 다시 묻는 문장이 전부
    이것이다. 갈래가 스스로 문장을 들고 있으면(`prompt` — 쪽지) 그것을 쓴다."""
    return (action.get("prompt")
            or f"{action.get('label')}, {_verb(action)}? (네 / 아니오)")


def _verb(action: dict[str, Any]) -> str:
    return _VERBS.get(action.get("kind") or "") or _DEFAULT_VERB


def _eul(label: str) -> str:
    """앞말에 맞는 목적격 조사 «을/를».

    조사가 어긋난 문장(「06-12-501 화면 열기**을** 취소했어요」)은 읽는 순간 기계가 쓴
    문장으로 읽힌다 — 제안 이름은 갈래마다 끝 글자가 다르므로(«…2건» · «…열기» ·
    «…나에게)») 한쪽으로 박아 둘 수 없다. 괄호 같은 꼬리는 건너뛰고 마지막 **한글** 글자의
    받침으로 고르고, 한글이 하나도 없으면 «을» 로 둔다.
    """
    for ch in reversed(label or ""):
        if "가" <= ch <= "힣":
            return "을" if (ord(ch) - 0xAC00) % 28 else "를"
        if ch.isalnum():
            break
    return "을"


def _propose(state: AgentState) -> dict[str, Any] | None:
    """이번 답변에 붙일 연계 제안 하나. 조건이 아니면 None.

    **버튼이 뜨는 것은 «문구까지 건네주는» 연계뿐이다**(§10, 2026-09-17 개정). 한때 첫
    갈래가 «답변이 인용한 화면번호를 그냥 열어주는 것»(`kind: "screen"`)이었는데, 답변의
    화면번호가 딥링크로 나가면서(`graph.ask` 의 `links`) 그 버튼은 본문 링크와 **하는 일이
    같아졌다** — 승낙해도 돌아오는 것이 같은 링크 하나여서, 직원은 턴 하나를 더 쓰고 같은
    자리에 도착했다. 매 턴 붙는데 누를 이유가 없는 제안은 §10 이 경계한 바로 그 상태다.

    그래서 남은 둘은 링크가 대체할 수 없는 것들이다: LMS 는 화면과 **함께 발송 문구**를
    건네고(그 문구에 더미 게이트가 걸린다 — `_link`), 플레이북은 **카드 내용**을 보여준다.

    한때 또 하나 있었다: 답변 안에 따옴표로 감싼 15자 이상의 문장이 있으면 LMS 발송 화면을
    제안했다. 그 조건은 **화법 코칭 답변이면 거의 항상 참**이다 — 고객에게 할 말을 큰따옴표로
    쓰라고 작성 프롬프트가 지시하기 때문이다. 그래서 사후관리 방법을 물었을 뿐인 턴에도
    "발송 화면 열까요?"가 붙었다. 문구를 보내려는 직원은 그렇게 말하고("이 문구로 LMS
    보내줘"), 그 요청은 `lms_link` 가 받아 같은 화면 연계를 제안한다.
    """
    return _propose_lms(state) or _propose_playbook(state)


#: 직원이 «쪽지로» 보내 달라고 말했는지의 판정어. 규칙이지 LLM 판단이 아니다(§10).
_MEMO_WORDS = ("쪽지",)

#: 수신자 사번의 꼴 — 자릿수는 `note.EMP_NO_PATTERN` 하나가 정한다(실측: 3902172).
#: 여기서 숫자를 다시 적으면 「사번이 몇 자리인가」의 출처가 둘이 되고, 자릿수가 바뀌는 날
#: 한쪽만 고쳐진다 — 그러면 대화에서는 읽히는 사번이 발송 게이트에서는 안 읽힌다.
_EMP_NO = re.compile(rf"(?<!\d)({note.EMP_NO_PATTERN})(?!\d)")

#: 그 7자리가 **사번으로 불린** 것인지의 단서. 숫자 꼴만으로는 사번과 금액이 갈리지 않는다.
#:
#: 2026-09-22 개정 — 처음에는 「사번」이 바로 앞에 붙거나 「한테·에게·께」가 바로 뒤에 붙은
#: 것만 읽었다. 직원이 실제로 쓰는 말은 그보다 넓다: 「3902173 사번으로 보내줘」·「사번은
#: 3902173이야」·「3902173님 앞으로」·「(3902173)한테」·「3902173으로 전달해줘」 — 전부 사번을
#: 적었는데 본인에게 가서 «타인 사번 쪽지가 안 된다»로 보였다. 넓히는 것은 **단서의
#: 종류**이고 원칙은 그대로다: 단서 없는 맨숫자는 읽지 않고, 금액 단위(원·만·천·억·%)가
#: 붙은 숫자는 어떤 단서가 있어도 읽지 않는다.
#:   앞 단서  「사번」·「직원」·「담당자」·「행원」 + (은/는/이/가/:) 가 숫자 바로 앞에
#:   뒤 단서  (번·님·씨·닫는 괄호 뒤) 사람 조사(한테·에게·께·앞으로) / 「사번」
#:   문장 단서 문장에 「사번」이 있으면 그 숫자
#: 「3902173으로 보내줘」의 «(으)로»는 단서로 삼지 않는다 — 「5000000으로」와 갈리지 않는다.
#: 그 말은 본인에게 가고(되돌릴 수 있다), 직원은 「사번」 한 마디를 더하면 된다.
_EMP_BEFORE = re.compile(r"(?:사번|직원|담당자?|행원)\s*(?:번호)?\s*(?:은|는|이|가|을|를|[:：=])?\s*$")
_EMP_AFTER = re.compile(r"^\s*[)\]]?\s*(?:번)?\s*(?:님|씨)?\s*(?:한테|에게|께|앞으로|앞에|사번)")
#: 금액이다 — 어떤 단서가 있어도 사번으로 읽지 않는다.
_EMP_AMOUNT = re.compile(r"^\s*(?:원|만|천|억|%|,\d|\.\d)")

#: 받는 사람을 모를 때. **묻지 않고 끝낸다** — 받을 사람이 없는 «보낼까요?»는 승낙받을
#: 대상이 없는 제안이다.
NO_RECIPIENT = ("쪽지를 보낼 받는 사람을 알 수 없어요 — 로그인 사번이 넘어오지 않았습니다. "
                "«사번 3902172한테 보내줘»처럼 사번을 알려주시면 그 앞으로 보낼게요.")


def employee_no(question: str) -> str | None:
    """직원이 말한 **수신자 사번**. 없으면 None(= 본인에게 보낸다).

    ━━ 사번이 가장 정확하다 ━━
    사번은 직원이 직접 적은 것이라 그 책임이 갈리지 않는다. 이름으로 보내는 길도 있지만
    (`recipient_name` · §10 「이름으로 보내기」) 1명이면 발송 전에 사번을 확인할 수 없다 —
    그래서 사번과 이름을 함께 적으면 사번을 쓴다(`_recipients`).

    **숫자 꼴만으로 판정하지 않는다.** 7자리 숫자는 금액에도 나온다("5000000원"). 사번이라는
    단서(`_EMP_BEFORE`·`_EMP_AFTER`·문장의 「사번」)가 있는 것만 읽는다 — 못 알아보면
    본인에게 가고, 그건 되돌릴 수 있는 실패다(잘못 보내는 쪽은 아니다).
    """
    text = question or ""
    candidates = [m for m in _EMP_NO.finditer(text) if not _EMP_AMOUNT.match(text[m.end():])]
    # 후보가 둘 이상이면 읽지 않는다 — 「사번 3902173 말고 3902174」에서 어느 쪽인지는 코드가
    # 정할 수 없고, 틀리면 남의 받은편지함에 고객 정보가 남는다(본인에게 가는 쪽이 되돌릴 수 있다).
    if len(candidates) != 1:
        return None
    m = candidates[0]
    before, after = text[:m.start()], text[m.end():]
    if _EMP_BEFORE.search(before) or _EMP_AFTER.match(after) or "사번" in text:
        return m.group(1)
    return None


# ─────────────────────────────────────────────────────────────
# 받는 사람 이름 — 코드가 규칙으로 읽는다(§10 「이름으로 보내기」)
#
# 이름은 사번보다 헷갈리는 말이 많다 — 「고객에게」·「본인에게」·「팀장님께」·「나한테」는
# 사람 이름이 아니고, 「박정호 고객 건 김국민한테」의 박정호는 고객이다. 고객 이름을 받는
# 사람으로 잘못 읽으면 그 고객과 이름이 같은 직원에게 고객 정보가 나간다. 그래서 읽는 꼴을
# 좁힌다: 성씨로 시작하는 한글 2~4자 + (직급·님·씨) + 사람 조사. 이름 바로 앞의
# 「~부·~지점·~센터·~팀·~본부」는 부서로 읽는다. LLM 이 뽑지 않는다(루트 규칙 2).
# ─────────────────────────────────────────────────────────────

_SURNAMES = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문",
             "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신",
             "권", "황", "안", "송", "전", "홍", "유", "고", "문", "양", "손", "배", "백", "허",
             "남", "심", "노", "하", "곽", "성", "차", "주", "우", "구", "민", "류", "나", "진",
             "지", "엄", "채", "원", "천", "방", "공", "현", "함", "변", "염", "여", "추", "도",
             "소", "석", "선", "설", "마", "길", "연", "위", "표", "명", "기", "반", "왕", "금",
             "옥", "육", "인", "맹", "제", "모", "탁", "국", "어", "은", "편", "용", "예", "경",
             "봉", "태", "피", "승", "빈", "라", "견", "당", "화")
_TITLES = ("부센터장", "센터장", "지점장", "본부장", "부부장", "팀장", "실장", "부장", "차장",
           "과장", "대리", "주임", "계장", "사원", "행원", "선임", "책임", "수석", "이사", "상무",
           "전무", "부행장", "행장", "매니저", "프로", "RM", "PB")
#: 이름 자리에 와도 사람 이름이 아닌 말. 성씨로 시작하는 보통명사가 여기 걸린다.
_NOT_NAMES = frozenset((
    "고객", "고객님", "고객분", "본인", "직원", "행원", "담당", "담당자", "모두", "전체", "우리",
    "저희", "여기", "거기", "이분", "그분", "저분", "이사람", "그사람", "사번", "본부", "지점",
    "부서", "기존", "신규", "정리", "내용", "요약", "상담", "이거", "그거", "이번", "다음",
    "전화", "문자", "쪽지", "메모", "사람", "동료", "선배", "후배", "상사", "대상", "신청",
    "전달", "공유", "나에", "제게", "원장", "주인", "고객들", "직원들", "각자", "모든", "전원",
    *_TITLES))
_GROUP_TAIL = r"(?:부|지점|센터|팀|본부|실|사업부|영업점)(?:\([^)]{1,4}\))?"
_NAME = re.compile(
    rf"(?:(?P<g>[가-힣A-Za-z0-9·]{{1,20}}{_GROUP_TAIL})\s+)?"
    rf"(?<![가-힣])(?P<n>(?:{'|'.join(_SURNAMES)})[가-힣]{{1,3}}?)"
    rf"\s*(?:\(\s*{note.EMP_NO_PATTERN}\s*\))?\s*"
    rf"(?:(?:{'|'.join(_TITLES)})\s*)?(?:님|씨)?\s*(?:한테|에게|께|앞으로)")


#: 이름 둘을 이어 말한 꼴(「김국민이랑 이영희한테」) — 이름 발송은 한 사람만 받으므로 고르지 않는다.
_NAME_AND = re.compile(
    rf"(?<![가-힣])(?:{'|'.join(_SURNAMES)})[가-힣]{{1,3}}?\s*(?:님|씨)?\s*(?:이랑|랑|하고|와|과|,|및)\s*"
    rf"(?:{'|'.join(_SURNAMES)})[가-힣]{{1,3}}?\s*(?:님|씨)?\s*(?:한테|에게|께|앞으로)")


class _Many:
    """이름 후보가 둘 이상 — 코드가 고르지 않는다."""


NAME_MANY = _Many()


def recipient_name(text: str) -> tuple[str, str] | _Many | None:
    """직원이 말한 **받는 사람 이름**과 부서. 없으면 None, 둘 이상이면 `NAME_MANY`.

    읽지 않는 것: 보통명사(`_NOT_NAMES`) · 직급만 붙은 호칭(「김대리」) · 시연 고객 명부의
    이름(고객 이름으로 직원을 찾는 일은 없다) · 「고객」이 바로 붙은 이름(조사가 안 붙어
    애초에 꼴에 안 걸린다).
    """
    if _NAME_AND.search(text or ""):
        return NAME_MANY
    hits: dict[str, str] = {}
    for m in _NAME.finditer(text or ""):
        name = m.group("n")
        if name in _NOT_NAMES or name[1:] in _TITLES or name in _customer_names():
            continue
        group = (m.group("g") or "").strip()
        if name not in hits or (group and not hits[name]):
            hits[name] = group
    if len(hits) > 1:
        return NAME_MANY
    return next(iter(hits.items()), None)


def _customer_names() -> frozenset[str]:
    try:
        from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
        return frozenset(getattr(p, "nm", "") for p in strategy_customer.PERSONAS)
    except Exception:          # noqa: BLE001 — 명부를 못 읽어도 이름 읽기를 막지 않는다
        return frozenset()


def name_label(name: str, group: str = "", emp_no: str = "") -> str:
    """화면에 세우는 받는 사람 표기 — 「미아동지점 김국민 님(사번 1631024)」."""
    head = f"{group} {name} 님" if group else f"{name} 님"
    return f"{head}(사번 {emp_no})" if emp_no else head


class Recipient(dict):
    """받는 사람 판정 — ids(사번) · label(표기) · to_self · user_name · group_name · unclear."""


#: 받는 사람 이름을 하나로 정하지 못한 턴의 안내.
NAME_UNCLEAR = ("받는 사람 이름을 하나로 정하지 못했어요. «김국민한테»처럼 한 분만 "
                "말씀해 주세요.")


def _recipients(state: AgentState) -> Recipient:
    """처음 쪽지 요청 턴의 받는 사람. 사번 → 이름 → 본인 순이다.

    **정하는 것은 코드다.** 대화에서 LLM 이 사번을 뽑아내게 두면 문장 하나로 수신자가
    갈릴 수 있다(`memo.draft` 머리말과 같은 자리). 사번과 이름을 함께 적으면(「김국민
    (3902172)한테」) 사번으로 보내고 이름은 표기에만 쓴다 — 이름 검색을 하지 않는다.
    """
    question = state.get("question") or ""
    other, named = employee_no(question), recipient_name(question)
    if other:
        label = (name_label(named[0], named[1], other) if isinstance(named, tuple)
                 else f"사번 {other}")
        return Recipient(ids=[other], label=label, to_self=False)
    if named is NAME_MANY:
        return Recipient(ids=[], label="", to_self=False, unclear=NAME_UNCLEAR)
    if isinstance(named, tuple):
        return Recipient(ids=[], label=name_label(*named), to_self=False,
                         user_name=named[0], group_name=named[1])
    mine = note.employee_id(state.get("employee_id"))
    return Recipient(ids=[mine] if mine else [], label=MEMO_DEFAULT_TO if mine else "",
                     to_self=True)


def _wants_memo(state: AgentState) -> bool:
    """이번 턴이 쪽지 턴인가 — 조건은 셋이고 전부 코드가 확인할 수 있는 사실이다(§10).

      1. 직원이 «쪽지»라고 말했다 — 요약만 부탁한 턴에 "보낼까요?"를 붙이면 묻지 않은
         것을 제안하는 것이다(§3)
      2. 이번 턴이 재료를 **하나라도** 다뤘다(원장이 비어 있지 않다) — 턴마다 갈리는
         게이트다. 재료가 없는 턴은 답변 자체가 「근거 없음」이라 쪽지에 옮길 것이 없다
      3. 화면에 나간 답변이 있다 — 쪽지는 그 답변에서 출발한다

    예전 조건은 여기에 «고객 화면이 열려 있다»와 «원장에 `transcript` 근거가 있다»가
    더 있었다. 그 둘은 쪽지를 **상담 요약 한 종류**로 못 박는 조건이었다 — 고객 창을
    안 띄운 채 오늘의 타겟 목록을 보내려는 요청, 방금 확인한 제도 수치를 옆자리에
    넘기려는 요청이 전부 걸렸다. 무엇을 재료로 쓸지는 이제 화면이 정한다(memo.material).
    """
    if not _said_memo(state):
        return False
    # ③의 예외 — 직전 답변을 코드가 재료로 붙인 턴(`_with_memo_material`)은 화면 답변이 비어
    # 있다(재료 없음 안내문을 비웠다). 그 턴의 출발점은 붙인 재료다.
    answered = bool((state.get("answer") or "").strip()) or bool(state.get("memo_material"))
    return bool(state.get("evidence")) and answered


def _said_memo(state: AgentState) -> bool:
    """직원이 이번 턴에 «쪽지»라고 말했나 — 규칙이지 LLM 판단이 아니다(§10)."""
    return any(w in (state.get("question") or "") for w in _MEMO_WORDS)


def _with_memo_material(state: AgentState) -> AgentState:
    """«쪽지»를 말한 턴인데 원장이 비었으면 **직전 답변을 코드가 재료로 붙인다.**

    「이거 사번 3902173한테 쪽지로 보내줘」는 무엇을 보낼지를 «이거»로만 가리킨다. 그 재료는
    직전 답변(`last_answer`)인데, 계획 LLM 이 그 도구를 안 고르면 원장이 빈 채 턴이 끝나고
    답은 「죄송해요, …」(NO_EVIDENCE)가 되며 쪽지 제안은 붙지 않는다 — 직원에게는 «사번을
    적으면 쪽지가 안 된다»로 보인다. 쪽지를 말했다는 것과 직전 답변이 있다는 것은 둘 다
    코드가 아는 값이므로, 여기서 그 도구를 그대로 불러 붙인다(지워진 gap 30 이 되묻기
    판정에 고객 재료를 코드로 붙인 것과 같은 자리 — LLM 의 도구 선택에 기능을 걸지 않는다).
    붙일 답변이 없으면 아무것도 바꾸지 않는다: 그 턴은 예전처럼 제안 없이 끝난다.

    이때 화면 답변은 재료가 없다는 안내문이라 쪽지의 출발점이 못 된다 — 비운다. 쪽지 본문은
    붙인 직전 답변(재료)에서 쓴다(`memo.draft` 의 context).
    """
    if state.get("evidence") or not _said_memo(state):
        return state
    from pension_agent.consult_agent.nodes import plan  # noqa: PLC0415 — 순환 임포트 회피

    try:
        found = tools.run("last_answer", state, "")
    except (LLMError, tools.ToolFailure):
        found = None
    if found is None:
        return state
    answer = state.get("answer") or ""
    # `memo_material` 은 이 턴 안에서만 쓰는 표지다 — offer 가 돌려주는 값에는 실리지 않는다.
    return {**state, "evidence": [found], "memo_material": True,
            "answer": "" if plan.is_failure_notice(answer) else answer}


def _memo_offer(state: AgentState) -> dict[str, Any]:
    """쪽지 초안을 세우고 «이대로 보낼까요?»를 묻는다(§10 의 제안·확인 형태).

    쪽지는 답변 자리를 **초안 그대로** 바꾼다 — 직원이 화면에서 읽고 승낙하는 것이 곧
    나가는 쪽지여야 한다. 보이는 것은 평문이고 나가는 것은 같은 글의 HTML 이다
    (`memo.Draft` 머리말 — 옮기는 것은 꼴뿐이다).

    **초안을 못 만들면 사유만 말하고 끝낸다.** 화면 답변에는 「걸리면 근거 원문을 내보낸다」는
    폴백이 있지만 쪽지에는 없다 — 근거 원문 덤프를 남의 받은편지함에 넣는 것은 답이 아니고,
    보낸 쪽지는 되돌릴 수 없다(루트 규칙 5).
    """
    who = _recipients(state)
    # 화면 답변이 비어 있으면(직전 답변을 재료로 붙인 턴 — `_with_memo_material`) 사유만 선다.
    head = (state.get("answer") or "").strip()
    if who.get("unclear"):
        return {"answer": f"{head}\n\n— {who['unclear']}" if head else who["unclear"]}
    if not who["ids"] and not who.get("user_name"):
        return {"answer": f"{head}\n\n— {NO_RECIPIENT}" if head else NO_RECIPIENT}
    found, why = memo.draft(state, recipients=who["ids"], to=who["label"], to_self=who["to_self"])
    if found is None:
        return {"answer": f"{head}\n\n— {why}" if head else why}
    if who.get("user_name"):
        found = memo.readdress(found, [], who["label"], who["user_name"], who.get("group_name", ""))
    # 발송 시각 — 단서가 붙은 날짜만 읽는다(effects/schedule.py). 못 가르면 초안은 세우되
    # 승낙으로 보내지 않고 언제 보낼지 묻는다(`when_unclear`).
    timing = schedule.parse(state.get("question") or "", clock.now())
    if timing.kind == "ok" and timing.at is not None:
        found = memo.reschedule(found, timing.at.isoformat(timespec="minutes"), timing.label)
    unclear = timing.kind in ("many", "past")
    return _offer_draft(found, state, head=_WHEN_NOTES.get(timing.kind, ""), unclear=unclear)


#: 발송 시각을 못 가른 턴의 안내. 초안은 세우되 승낙으로 보내지 않는다.
WHEN_MANY = "언제 보낼지 하나로 정하지 못했어요."
WHEN_PAST = "말씀하신 발송 시각이 이미 지났어요."
_WHEN_NOTES = {"many": WHEN_MANY, "past": WHEN_PAST}
#: 이름으로 찾은 직원이 없을 때. `who` 는 「김국민 님」 꼴의 표기다.
NAME_NOT_FOUND = "{who}을 찾지 못했어요. 이름이나 부서를 다시 확인해 주세요."
#: 예약 실행기가 없을 때(`actions.SCHEDULER_ENV`). 보내지 않았다는 사실을 말한다.
SCHEDULE_OFF = "예약 발송이 아직 연결되지 않아 보내지 않았어요. 지금 보내려면 쪽지를 다시 부탁해 주세요."
#: 발송 시각이 정해지지 않은 초안의 제안 문장 — «네»로 보낼 수 없는 제안이다.
WHEN_ASK = ("언제 보낼까요? «10월 5일 오전 9시에 보내줘»처럼 말씀해 주세요. "
            "지금 보내려면 «지금 보내줘»라고 해 주세요.")


def _offer_draft(found: memo.Draft, state: AgentState, head: str = "",
                 unclear: bool = False) -> dict[str, Any]:
    """초안 한 통을 화면에 세우고 «이대로 보낼까요?»를 건다. 처음 초안과 **고친 초안이 같은
    자리를 지난다** — 고칠 때마다 제안을 다시 거는 것이 «직전 한 턴의 제안만 실행한다»(§10)를
    지키면서 초안을 이어 가는 방법이다(발송되는 것은 늘 직전 턴에 화면에 선 초안이다).

    제안에는 초안을 다시 조립할 조각(`body`·`tail_html`·`tail_note`)도 싣는다 — 다음 턴이
    그 초안을 고칠 때 코드가 붙인 값 표는 건드리지 않고 본문만 바꾼다(`memo.revise`).
    """
    label = found.to
    if unclear:
        name, ask = f"이 쪽지 보내기(받는 사람: {label} · 발송 시각 미정)", WHEN_ASK
    elif found.send_label:
        # 예약 쪽지 — 제안 문장이 발송 시각을 그대로 말한다. 잘못 읽었으면 승낙 전에 보인다.
        name = f"이 쪽지 예약하기({found.send_label} · 받는 사람: {label})"
        ask = (f"{found.send_label}에 보내도록 예약할까요? 받는 사람은 {label}이에요. "
               "(네 / 아니오)")
    else:
        name = f"이 쪽지 보내기(받는 사람: {label})"
        ask = f"이대로 쪽지를 보낼까요? 받는 사람은 {label}이에요. (네 / 아니오)"
    action = {"kind": "memo", "label": name, "prompt": ask,
              "title": found.title, "text": found.text, "html": found.html,
              "body": found.body, "tail_html": found.tail_html, "tail_note": found.tail_note,
              "send_at": found.send_at, "send_label": found.send_label,
              "user_name": found.user_name, "group_name": found.group_name,
              "when_unclear": unclear,
              "to": label, "recipients": list(found.recipients),
              "params": {"customer_id": state.get("customer_id") or ""}}
    # 초안은 코드블록으로 감싸 «여기까지가 쪽지»를 화면에서 가른다 — 초안이 답변 자리를
    # 통째로 차지하므로, 표시가 없으면 에이전트가 하는 말과 구별되지 않는다. 펜스는 화면
    # 장치라 나가는 본문에는 없고, 세션 기록에서 재료를 만들 때도 뗀다(tools._strip_devices).
    draft = f"{memo.FENCE}\n[제목] {found.title}\n\n{found.text}\n{memo.FENCE}"
    body = f"{draft}\n\n— {action['prompt']}"
    return {"answer": f"{head}\n\n{body}" if head else body, "pending_action": action}


def _propose_lms(state: AgentState) -> dict[str, Any] | None:
    """이번 턴에 안내하기로 한 콘텐츠의 **발송 화면 연계**를 제안한다(§10 예정 확장의 구현).

    지금까지 LMS 는 직원이 먼저 말해야 시작했다("이 문구로 보내줘" → nodes/lms.py). 그
    갈래를 남겨 둔 채 이쪽을 더하는 이유는, 직원이 «세미나 뭐 있어»를 묻고 나면 다음에 할
    일이 발송 하나뿐인데 그때마다 문구를 따옴표로 옮겨 적게 하는 것이 그 화면의 목적에
    맞지 않기 때문이다.

    **조건은 넷이고 전부 코드가 확인할 수 있는 사실이다**(위 `_propose_playbook` 과 같은
    기준). 특히 ②가 «턴마다 갈리는 게이트»다 — 이게 없으면 고객 화면이 열려 있는 동안 매
    턴 "보낼까요?"가 붙고, 그것이 예전에 따옴표 휴리스틱 갈래를 지운 바로 그 상태다.

      1. 고객 화면이 열려 있고, 이 고객에게 성립한 관리 사유가 있다 — 없으면 관리 대상이
         아니고, 사유를 만들어내지 않는다
      2. **이번 턴이 안내 콘텐츠를 재료로 다뤘다**(원장에 outreach 근거가 있다)
      3. 답변이 그 콘텐츠를 실제로 가리켰다 — 콘텐츠 이름을 인용했다는 뜻이다. 후보를
         늘어놓기만 한 답변에 "보낼까요?"를 붙이면 무엇을 보낸다는 것인지가 없다
      4. 그 콘텐츠에 발송할 문구가 있다(브리핑 ⑨ 가 만들어 둔 값)

    **문구를 여기서 만들지 않는다.** 원장에 실린 브리핑 산출을 그대로 옮긴다 — 화면 ⑨ 가
    어차피 만드는 값이고, 대화가 따로 생성하면 화면에 뜬 것과 다른 문자가 나간다(§10).
    발송 여부는 여전히 직원이 그 화면에서 정하고, 더미 게이트도 `_link` 에 그대로 남는다.
    """
    if not state.get("customer_id") or not _managed_reason(state):
        return None
    answer = state.get("answer") or ""
    for ev in state.get("evidence") or []:
        if ev["tool"] != "outreach":
            continue
        for key, item in _lms_items(ev):
            if not _mentions(answer, item["name"], item.get("url")):
                continue          # 답변이 가리키지 않은 콘텐츠는 제안하지 않는다
            found = screens.lms_screen(KB)
            if not found:
                # 발송 화면번호가 지식베이스에 없으면 링크를 만들지 않는다(§10).
                return None
            number, card = found
            return {"kind": "lms", "label": f"«{item['name']}» 안내 문구로 {number} 발송 화면 열기",
                    "screen": number, "card": card, "message": item["message"],
                    "content_id": item["id"], "content_kind": key,
                    "params": {"customer_id": state.get("customer_id") or ""}}
    return None


#: 콘텐츠 등록 이름의 끝에 붙는 종류 낱말. 답변은 이 낱말을 떼고 부른다.
_CONTENT_KINDS = ("이벤트", "세미나")


def _mentions(answer: str, name: str, url: str | None = None) -> bool:
    """답변이 이 콘텐츠 이름을 불렀는가 — 조건 ③ 「답변이 그 콘텐츠를 실제로 가리켰다」의 판정.

    글자 그대로의 부분문자열 대조였던 동안 **제안이 엉뚱한 콘텐츠에 붙었다**(2026-09-03
    실측, 확정본 E1): 등록 이름은 「IRP 추가입금하고 절세혜택 챙기기 **이벤트**」인데 답변은
    끝의 «이벤트»를 떼고 「…챙기기 (9/30까지)」로 썼고, 같은 답변이 세미나 이름은 그대로
    옮겼다. 그래서 이벤트는 «언급 안 함»으로 탈락하고 발송 제안이 세미나에 붙었으며, 승낙
    턴이 ISA 만기 고객에게 자산배분 세미나 문자를 열었다. 답변이 이름을 부르는 방식(종류
    낱말 생략·공백 차이)은 LLM 이 정하는 표현이라 지시로 고정할 수 없다 — 대조 쪽이 그
    폭을 갖는다. 넓히는 것은 **끝의 종류 낱말과 공백**뿐이다. 이름의 앞부분을 잘라 부르는
    것은 여전히 «가리킨 것»이 아니다(후보를 늘어놓기만 한 답변에 붙이지 않는다는 조건 ③).

    **링크 인용도 «가리킨 것»이다**(2026-09-07 실측, 김서연 SE6). 답변이 등록 이름
    「ISA 만기자금, IRP로 이어가는 절세 이벤트」를 「ISA 만기자금 IRP 이전 절세 이벤트」로
    바꿔 써서 이름 대조가 탈락했고, 제안이 안 붙어 다음 턴 «응, 열어줘»가 «직전에 제안드린
    작업이 없어요»로 끝났다. 그 답변에 안내 링크는 원문 그대로 있었다 — 링크는 원문 스팬
    (atomic)이라 답변이 바꿔 쓸 수 없는 값이고, 콘텐츠마다 다르므로 이름보다 확실한
    식별자다. 이름을 줄여 쓰는 방식은 LLM 이 정하는 표현이라 지시로 못 막지만 링크는
    막을 필요가 없다. 후보를 늘어놓기만 한 답변(이름도 링크도 없음)에는 여전히 안 붙는다.
    """
    stem = name.strip()
    for kind in _CONTENT_KINDS:
        if stem.endswith(kind):
            stem = stem[: -len(kind)].strip()
            break
    squash = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    if bool(stem) and squash(stem) in squash(answer):
        return True
    return bool(url) and url.strip() in answer


def _lms_items(ev: dict) -> list[tuple[str, dict]]:
    """outreach 근거가 들려 보낸 «보낼 수 있는 것» 목록 — (이벤트/세미나, {id·name·message}).

    이름과 문구를 도구가 함께 실어 주므로 제안 노드가 브리핑을 다시 부르지 않는다. 다시
    부르면 그 사이 선정이 달라질 수 있고, 그러면 답변이 말한 것과 다른 콘텐츠를 보내자고
    제안하게 된다.
    """
    out: list[tuple[str, dict]] = []
    for key, item in ((ev["meta"].get("lms") or {})).items():
        if isinstance(item, dict) and item.get("name") and item.get("message"):
            out.append((key, item))
    return out


#: 제안 갈래를 여는 원장 재료. 이번 턴이 이 도구의 근거를 다뤘을 때 그 갈래의 나머지
#: 후보를 제안한다 — 절차를 물은 턴에 화법을 제안하면 §3 「묻지 않은 값」의 제안 버전이다.
_LANE_WORDS = {"pitch": "화법", "procedure": "업무 절차", "method": "관리 방법론"}


def _propose_playbook(state: AgentState) -> dict[str, Any] | None:
    """이 고객 상태에 걸린 재료(화법·방법론·절차)를 더 보여줄지 제안한다(§10 의 제안·확인
    형태를 그대로 쓴다).

    **위 LMS 갈래가 지워진 이유를 그대로 피한다.** 그 갈래의 조건("답변에 따옴표 문장이
    있으면")은 화법 코칭이면 거의 항상 참이라 매 턴 붙었고, 그러면 §10 이 경계한 상태가
    된다. 여기 네 조건은 전부 **코드가 확인할 수 있는 사실**이고, 하나라도 어긋나면 안 붙는다.

      1. 고객 화면이 열려 있다 — 고객 상태가 있어야 성립하는 제안이다(§3)
      2. 이번 턴이 제안 갈래의 재료를 다뤘다(원장에 pitch·procedure·method 근거가 있고,
         제안은 **그 갈래의** 나머지 후보만이다). 조건 ①③④는 관리 대상 고객이 열려 있으면
         거의 항상 참이라, 턴마다 갈리는 게이트는 이것 하나다 — 이게 빠지면 그 고객을 보는
         동안 매 턴 제안이 붙고, LMS 갈래가 죽은 그 상태가 재현된다. 슬롯 분해의 LLM 호출을
         이 갈래 턴에만 쓰게 하는 것도 이 조건이다
      3. 이 고객에게 성립한 문제상황이 있다 — 없으면 관리 사유가 없는 고객이고, 사유를
         만들어내지 않는다
      4. 이번 턴이 **아직 쓰지 않은** 카드가 남아 있다 — 답변이 이미 말한 것을 다시
         보여드릴까요 하고 묻지 않는다

    화면 ⑥⑦⑧ 이 상담 **전에** 고객 상태만으로 2건을 고정하는 것과 달리, 여기는 상담 **중**
    이라 «방금 나온 상황»(슬롯·이번 턴의 갈래)까지 본다 — 그것이 화면의 반복이 아닌 유일한
    근거다.
    """
    if not state.get("customer_id"):
        return None
    used = {e["tool"] for e in (state.get("evidence") or [])}
    lanes = tuple(lane for lane in tools.PLAYBOOK_LANES if lane in used)
    if not lanes:
        return None
    ranked = tools.playbook_ranked(state, lanes=lanes, exclude=tools.cited_cards(state))
    if not ranked:
        return None
    hits = [(score, card) for score, card, _sit in ranked]
    what = "·".join(dict.fromkeys(_LANE_WORDS[c["_kind"]] for _s, c in hits))
    # «상태에 걸린»은 코드 안의 말이다 — 요건에 걸렸다(매칭됐다)는 뜻이지 직원이 쓰는
    # 업무 표현이 아니고, 화면에 그대로 세우면 기계가 지어낸 문장으로 읽힌다(2026-09-17
    # 시연 지적). 밝혀야 하는 것은 **왜 떴는가**이지 매칭의 이름이 아니므로, 같은 사실을
    # 「이 상태의 고객에게 쓰는 화법」으로 말한다.
    reason = _offer_reason(ranked)
    label = (f"«{reason}»에게 쓰는 {what} {len(hits)}건" if reason
             else f"이 고객에게 쓰는 {what} {len(hits)}건")
    # 관련도까지 남긴다 — 승낙 턴은 카드를 다시 고르지 않고 이때 고른 것을 그대로 싣는다(§10).
    return {"kind": "pitch", "label": label,
            "cards": [{"id": c["id"], "score": round(score, 3)} for score, c in hits],
            "params": {"customer_id": state.get("customer_id") or ""}}


#: 문제상황 제목의 꼬리 — 정의를 덧붙인 부분이다(「… 고객 — 적립금 1천만원 & …」·
#: 「… 고객 (이탈 고위험)」). 제안 한 줄에는 대상만 필요하므로 여기서 자른다.
_SIT_TAIL = re.compile(r"\s*(?:—|\(|-\s).*$")

#: 제안 문구에 이름을 대는 문제상황의 최대 개수. 후보가 2건이라(PLAYBOOK_TOP_K) 둘까지다.
_REASON_MAX = 2


def situation_name(sit: dict | None) -> str:
    """문제상황 하나의 «대상» 이름 — 제목에서 정의 꼬리를 뗀 것. 제안 문구와, 제안이 그
    이름을 제대로 대는지 보는 검사가 같은 함수를 쓴다."""
    return _SIT_TAIL.sub("", str((sit or {}).get("title") or "")).strip()


def _offer_reason(ranked: list[tuple[float, dict, dict]]) -> str:
    """제안 문구에 밝히는 «왜 떴는가» — **고른 카드를 실제로 세운 문제상황**의 이름.

    고객의 성립 요건 목록에서 앞 두 개를 끊어 쓰던 자리다. 요건은 카드를 고르지 않는다 —
    카드를 고르는 것은 문제상황(세그먼트)이고, 한 고객에게 요건이 넷이면 그중 카드를 낸
    것은 하나뿐일 수 있다. 그래서 제목이 「원리금보장상품 편중 · 연금개시 요건충족 후
    미개시」인데 밑의 두 카드는 전부 원리금보장 쪽이고 연금개시 화법은 한 장도 없는 일이
    실제로 났다(2026-09-17 실측, 박정호). **제목과 내용이 어긋나면 제목이 거짓말이다.**
    """
    names: list[str] = []
    for _score, _card, sit in ranked:
        title = situation_name(sit)
        if title and title not in names:
            names.append(title)
    return ", ".join(names[:_REASON_MAX])


def _managed_reason(state: AgentState) -> str:
    """이 고객에게 성립한 관리 요건의 이름 — 발송 화면 제안의 **게이트**다(`_propose_lms`
    조건 ①). 관리 사유가 없는 고객에게는 안내 콘텐츠를 보내자고 제안하지 않는다.

    화법 제안의 «왜 떴는가»는 여기가 아니라 `_offer_reason` 이 답한다 — 요건은 카드를
    고르지 않기 때문이다(그 함수 머리말)."""
    try:
        from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
        profile = strategy_customer.get_profile(state.get("customer_id") or "")
        if profile is None:
            return ""
        names = [strategy_customer.CONDS[c] for c in strategy_customer.conditions(profile)
                 if c in strategy_customer.CONDS]
    except Exception:
        return ""
    return " · ".join(names[:2])


def offer(state: AgentState) -> dict[str, Any]:
    """답변 뒤에 붙는 제안. 조건이 아니면 아무것도 바꾸지 않고 통과한다.

    쪽지 턴이 먼저다 — 쪽지는 답변 자리를 초안으로 바꾸므로, 같은 턴에 화면 연계까지
    붙이면 직원이 무엇에 «네»라고 답하는지 갈리지 않는다.
    """
    if state.get("pending_action"):
        observability.step("offer", pending=state["pending_action"].get("label"))
        return {}
    if _said_memo(state):
        state = _with_memo_material(state)
    if _wants_memo(state):
        out = _memo_offer(state)
        observability.step("offer", pending=(out.get("pending_action") or {}).get("label") or "없음")
        return out
    action = _propose(state)
    if not action:
        observability.step("offer", pending="없음")
        return {}
    observability.step("offer", pending=action.get("label"))
    # 끝의 「(네 / 아니오)」는 공통이다(transcript 재료가 기록에서 이 줄을 떼는 표지 —
    # tools._OFFER_TRAILER).
    ask = offer_prompt(action)
    # 조립한 문장을 제안에 남긴다 — 본문 끝 줄 · 버튼 위 문구(`main._turn_events`) ·
    # 애매한 답에 다시 묻는 문장이 **같은 문장**이어야 한다. 폴백을 세 곳에 두면 갈래가
    # 하나 늘 때마다 세 곳이 어긋난다.
    action["prompt"] = ask
    return {"answer": state["answer"] + f"\n\n— {ask}", "pending_action": action}


def _pending(history: list[dict] | None) -> dict | None:
    """직전 턴이 걸어둔 제안. **그 한 턴만** 본다(§10 "제안은 그 자리에서만 유효하다").

    걸어둔 제안을 몇 턴 뒤의 "네"로 실행하지 않는다. 직원이 확인 대신 다른 질문을 하면
    그 턴은 제안 없이 끝나므로, 여기서 자동으로 무효가 된다.
    """
    if not history:
        return None
    return (history[-1] or {}).get("pending_action") or None


def confirm_action(state: AgentState) -> dict[str, Any]:
    """직전 턴이 제안한 화면을 열거나, 물리거나, 애매하면 다시 묻는다.

    무엇을 연계하기로 한 것인지(어느 화면·어느 고객)는 **제안한 턴이 남긴 것**으로 정하고
    이번 턴의 말에서 다시 추측하지 않는다(§10) — 이번 질문에는 "네" 한 글자밖에 없다.
    """
    pending = _pending(state.get("history"))
    if not pending:
        observability.step("confirm", pending="없음")
        return {"answer": "직전에 제안드린 작업이 없어요. 무엇을 도와드릴까요?",
                "sources": [], "pending_action": None}

    if pending.get("kind") == "memo":
        return _memo_reply(pending, state)
    return _reply(pending, state)


def _reply(pending: dict, state: AgentState) -> dict[str, Any]:
    """승낙·거절·애매함 셋으로 가르는 공통 응답 — 화면 연계·화법 제시·쪽지 발송."""
    text = (state.get("question") or "").strip().lower()
    said_no = any(k in text for k in _NO) and not any(text.startswith(k) for k in _YES)
    label = pending["label"]
    if said_no:
        observability.step("confirm", pending=label, reply="reject")
        return {"answer": f"{label}{_eul(label)} 취소했어요.", "sources": [], "pending_action": None}
    if not any(k in text for k in _YES):
        # 애매한 답을 승낙으로 해석하지 않는다 — 제안을 유지한 채 다시 묻는다(§10).
        # 다시 묻는 문장은 제안한 턴이 쓴 문장 그대로다 — 「진행할까요」로 바꿔 물으면
        # 직원이 방금 읽은 제안과 다른 것을 묻는 것으로 읽힌다.
        observability.step("confirm", pending=label, reply="unclear")
        again = offer_prompt(pending).removesuffix("(네 / 아니오)").strip()
        return {"answer": f"{again} '네' 또는 '아니오'로 답해 주세요.",
                "sources": [], "pending_action": pending}

    observability.step("confirm", pending=pending.get("label"), reply="accept")
    kind = pending.get("kind")
    if kind == "pitch":
        return _show_playbook(pending)
    if kind == "memo":
        return _send_memo(pending, state)
    return _link(pending)


# ─────────────────────────────────────────────────────────────
# 쪽지 초안이 걸린 턴 — 승낙 · 거절 · 받는 사람 바꾸기 · 붙여넣기 · 고치기 · 새 질문
# (§10 「쪽지 초안은 고칠 수 있다」)
#
# 다른 제안은 «네 / 아니오 / 애매함» 셋이면 되지만 쪽지 초안은 넷이 더 있다. 직원이 초안을
# 읽고 「앞에 --를 붙여줘」·「사번 3902173한테」·「이대로 보내줘: …」라고 하는 것은 거절도
# 새 질문도 아니다 — 예전에는 그 턴이 제안 없이 끝나 다음 턴의 「웅 쪽지 보내줘」가
# 「직전에 제안드린 작업이 없어요」로 끝났다(2026-09-23 실측).
#
# 갈래를 **코드가 먼저** 가르고, 코드가 못 가르는 것(고치라는 말인가)만 LLM 이 판정한다.
#   ① 거절(짧은 거절 말)                 → 취소
#   ② 받는 사람이 바뀌었나               → 코드(사번 단서 · 본인을 가리키는 말)
#   ③ 짧은 승낙 말이고 ②가 없다          → 그대로 발송
#   ④ «이대로·똑같이» + 붙여넣은 글       → 그 글 그대로 초안(LLM·검사 없음)
#   ⑤ 나머지                            → LLM 이 고치라는 말인지 보고 고친다(memo.revise)
#   ⑥ 고치라는 말이 아니면               → ②면 새 수신자로 다시 제안, 승낙·거절 말이면 그대로,
#                                          아니면 새 질문 — 계획 루프로 넘긴다(초안은 사라진다)
# ─────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"[\s.,!?~…ㅎㅋ^]+", "", text.lower())


#: 짧은 승낙 — **이것만 있는** 말이다. 「보내」가 들어 있다고 승낙으로 읽으면 「앞에 --
#: 붙여서 보내줘」가 고치지 않은 초안을 보낸다(발송은 되돌릴 수 없다).
_PLAIN_YES = re.compile(
    r"^(?:네|넵|예|웅|응|ㅇㅇ|ㅇㅋ|그래|좋아|좋아요|오케이|ok|yes)*"
    r"(?:그대로|이대로|이걸로|그걸로)?(?:쪽지)?(?:로)?"
    r"(?:보내|발송해|전송해|발송|전송|보내줘|보내주세요|보내줘요|부탁해|부탁해요|부탁드려요)?"
    r"(?:줘|주세요|줘요|요)?$")
#: 짧은 거절 — 역시 이것만 있는 말이다. 「아니 앞에 --를 붙여줘」는 거절이 아니라 고치기다.
_PLAIN_NO = re.compile(
    r"^(?:아니|아니요|아니오|아뇨|아냐|no|취소|취소해|취소해줘|취소해주세요|괜찮아|괜찮아요|"
    r"됐어|됐어요|안보내|안보내도돼|보내지마|보내지마요|나중에|나중에할게)+$")

#: 고치라는 말이 아니라고 판정된 뒤, 승낙·거절로 읽는 말머리와 보내라는 동사(⑥).
_LEAD_YES = ("네", "넵", "예", "웅", "응", "그래", "좋아", "오케이", "ok", "yes", "보내")
_LEAD_NO = ("아니", "아뇨", "취소", "괜찮", "됐어", "나중")
_SEND_VERBS = ("보내", "발송", "전송")

#: 받는 사람을 **본인으로** 바꾸는 말. 맨 「본인」은 넣지 않는다 — 「고객 본인이 확인하게
#: 적어줘」가 수신자를 바꾸면 안 된다. 사번처럼 코드가 읽고, 못 읽으면 직전 수신자를 둔다.
_SELF_WORDS = ("나한테", "나에게", "내게", "저한테", "저에게", "내 쪽지함", "내쪽지함",
               "본인한테", "본인에게", "나 한테", "저 한테")

#: 붙여넣은 글을 **그대로** 쓰라는 말과, 그 글을 참고해 **다듬으라는** 말. 둘 다 있으면
#: 다듬는 쪽이다 — 결과가 초안으로 화면에 서고 승낙을 거치므로 「똑같이」 한 마디로 바로잡힌다.
_VERBATIM_WORDS = ("이렇게 보내", "이렇게 쪽지", "이대로", "똑같이", "그대로", "수정 없이",
                   "수정없이", "토씨")
_RESTYLE_WORDS = ("변경해", "바꿔", "다듬", "정리해", "식으로", "처럼", "참고해", "고쳐",
                  "수정해", "스타일", "느낌")
#: 붙여넣은 글로 볼 최소 길이. 「이대로 보내줘: 네」 같은 짧은 꼬리는 본문이 아니다.
_PASTE_MIN = 20


def _pasted(text: str) -> tuple[str, str] | None:
    """(지시, 붙여넣은 글). «이대로·똑같이» 류의 말과 함께 글을 붙여넣었을 때만 돌려준다.

    지시와 글은 첫 콜론(:)이나 줄바꿈에서 가른다 — 앞이 지시·뒤가 글이다. 거꾸로
    (글을 먼저 붙이고 마지막 줄에 「이대로 보내줘」)도 받는다.
    """
    raw = (text or "").strip()
    m = re.search(r"[:：]|\n", raw)
    pairs = []
    if m:
        pairs.append((raw[:m.start()], raw[m.end():]))
    head, _sep, last = raw.rpartition("\n")
    if head:
        pairs.append((last, head))
    for order, body in pairs:
        body = body.strip()
        if (len(body) >= _PASTE_MIN and any(w in order for w in _VERBATIM_WORDS)
                and not any(w in order for w in _RESTYLE_WORDS)):
            return order.strip(), body
    return None


#: 사번과 본인을 함께 말해 받는 사람을 못 가른 턴의 안내. 초안과 받는 사람은 그대로 둔다.
RECIPIENT_UNCLEAR = ("받는 사람을 사번과 본인 중 하나로 정하지 못해서 그대로 뒀어요. "
                     "바꾸시려면 «사번 3902173한테» 또는 «나한테»처럼 한쪽만 말씀해 주세요.")


def _both_named(order: str) -> str:
    """받는 사람을 코드가 못 가르는 말인가 — 그 안내 문장, 아니면 "".

    사번(또는 이름)과 본인을 함께 댔거나, 이름 후보가 둘 이상이면 고르지 않는다(`_readdress`).
    사번과 이름을 함께 댄 것은 여기 해당하지 않는다 — 사번으로 보내고 이름은 표기에 쓴다.
    """
    named = recipient_name(order)
    if named is NAME_MANY:
        return NAME_UNCLEAR
    if (employee_no(order) or named) and any(w in order for w in _SELF_WORDS):
        return RECIPIENT_UNCLEAR
    return ""


def _readdress(order: str, pending: dict, state: AgentState) -> tuple | None:
    """직원의 말이 받는 사람을 바꿨으면 (사번 목록, 표기, 이름, 부서). 안 바꿨거나 못 가르면 None.

    **말이 없으면 직전 수신자를 둔다** — 처음 요청 턴처럼 «사번이 없으면 본인»으로 읽으면
    「앞에 --를 붙여줘」마다 타인 앞으로 걸린 초안이 조용히 본인 앞으로 바뀐다.
    사번과 본인을 함께 말하면(「나한테 말고 사번 3902173한테」) 코드가 고르지 않는다 —
    `employee_no` 가 후보 둘을 읽지 않는 것과 같은 이유다. 제안 문장이 누구 앞인지 밝힌다.
    """
    other, named = employee_no(order), recipient_name(order)
    to_self = any(w in order for w in _SELF_WORDS)
    if (other or named) and to_self or named is NAME_MANY:
        return None
    name, group = named if isinstance(named, tuple) else ("", "")
    if other:
        ids, label, name, group = [other], (name_label(name, group, other) if name
                                            else f"사번 {other}"), "", ""
    elif name:
        ids, label = [], name_label(name, group)
    elif to_self:
        mine = note.employee_id(state.get("employee_id"))
        if not mine:
            return None
        ids, label = [mine], MEMO_DEFAULT_TO
    else:
        return None
    if (ids == list(pending.get("recipients") or []) and name == (pending.get("user_name") or "")
            and group == (pending.get("group_name") or "")):
        return None
    return ids, label, name, group


def _memo_reply(pending: dict, state: AgentState) -> dict[str, Any]:
    """쪽지 초안이 걸린 턴의 응답. 갈래는 위 표(①~⑥)다."""
    question = (state.get("question") or "").strip()
    found = memo.from_pending(pending)
    plain = _norm(question)

    if plain and _PLAIN_NO.match(plain):                                     # ①
        return _reply(pending, state)

    # 이름 발송이 여러 명을 돌려준 뒤의 턴 — 직원이 목록에서 고른다(「1번」·「미아동지점」·
    # 「사번 1631024」). 고르면 그 사번으로 다시 제안한다(이름을 다시 검색하지 않는다).
    cands = pending.get("candidates") or []
    if cands:
        picked = _pick(question, cands)
        if picked is not None:
            c = cands[picked]
            found = memo.readdress(found, [c["user_id"]],
                                   name_label(found.user_name, c.get("group_name", ""), c["user_id"]))
            observability.step("confirm", pending=pending.get("label"), reply="accept")
            return _memo_turn(found, state, unclear=bool(pending.get("when_unclear")))
        if plain and _PLAIN_YES.match(plain):
            return {"answer": _candidates_text(found.user_name, cands), "sources": [],
                    "pending_action": pending}
        # 짧은 답이 어느 후보와도 안 맞으면 고르려던 말이다 — 목록을 다시 보이고 고르게 한다.
        # 받는 사람·발송 시각을 바꾸는 말, 거절은 아래 갈래가 받는다.
        if (len(question) <= PICK_SHORT and not employee_no(question)
                and not recipient_name(question) and not any(w in question for w in _SELF_WORDS)
                and schedule.parse(question, clock.now(), edit=True).kind == "none"):
            return {"answer": f"{PICK_AGAIN}\n{_candidates_text(found.user_name, cands)}",
                    "sources": [], "pending_action": pending}

    paste = _pasted(question)
    order = paste[0] if paste else question
    unclear_who = _both_named(order)
    if unclear_who:
        # 누구 앞인지 코드가 못 가른다 — 바꾸지 않고, 그 사실과 지금 받는 사람을 밝혀 다시 묻는다.
        observability.step("confirm", pending=pending.get("label"), reply="unclear")
        return _memo_turn(found, state, head=unclear_who, unclear=bool(pending.get("when_unclear")))
    moved = _readdress(order, pending, state)                               # ②
    if moved:
        found = memo.readdress(found, *moved)

    # ②′ 발송 시각 — 받는 사람과 같게 다룬다. 말이 없으면 직전 값을 두고, 단서가 있으면 바꾸고,
    # 「지금 보내줘」는 예약을 지운다. 바뀌면 보내지 않고 제안을 다시 건다(무엇이 바뀌었는지 보게).
    unclear = bool(pending.get("when_unclear"))
    timing = schedule.parse(order, clock.now(), edit=True)
    retimed = False
    if timing.kind in ("many", "past"):
        return _memo_turn(found, state, head=_WHEN_NOTES[timing.kind], unclear=True)
    if timing.kind == "ok" and timing.at is not None:
        at = timing.at.isoformat(timespec="minutes")
        retimed = unclear or at != found.send_at
        found = memo.reschedule(found, at, timing.label)
        unclear = False
    elif timing.kind == "clear":
        if unclear or found.send_at:
            retimed = True
            found = memo.reschedule(found, "", "")
            unclear = False
        else:
            return _reply(pending, state)           # 이미 즉시 발송 초안이다 — 「지금 보내줘」는 승낙이다
    changed = bool(moved) or retimed

    if plain and _PLAIN_YES.match(plain) and not changed and not paste:     # ③
        if unclear:
            # 발송 시각이 안 정해진 초안은 «네»로 보내지 않는다 — 언제인지 다시 묻는다.
            return _memo_turn(found, state, unclear=True)
        return _reply(pending, state)

    if paste:                                                               # ④
        made, why = memo.verbatim(found, paste[1])
        observability.step("confirm", pending=pending.get("label"), reply="pasted",
                           status="ok" if made else "blocked", reason=why or None)
        if made is None:
            return _memo_turn(found, state, head=why)
        # 글 전체가 직원이 적은 것이다 — 건수만 남기고 글은 스위치를 따르는 span 에 싣는다.
        _record_staff(pasted=True, added=[], removed=[], text=paste[1])
        return _memo_turn(made, state, unclear=unclear)

    # 받는 사람이 바뀌었고 본문이 옛 받는 사람을 이름으로 부르고 있으면 호칭을 맞추게 알린다
    # (§10 「함께 고친 것」 address). 이름을 모르면(사번·본인) 알리지 않는다 — 코드가 확인할 수
    # 없는 호칭을 LLM 에게 찾게 하면 본문을 더 넓게 건드린다.
    address = _address_change(pending, found) if moved else None
    with observability.span("consult.memo.edit", input={"instruction": question}) as span:
        rev = memo.revise(found, question, state.get("history"), recipient=address)  # ⑤
        span.update(output={"kind": rev.kind, "added": rev.added, "removed": rev.removed})
    observability.step("confirm", pending=pending.get("label"), reply=rev.kind,
                       recipients="변경" if moved else None, reason=rev.reason or None)

    if rev.kind == "edited" and rev.draft is not None:
        _record_staff(pasted=False, added=rev.added, removed=rev.removed)
        unknown = [c for c in rev.also if c not in memo.ALSO_LABELS]
        if rev.also:
            observability.step("confirm", pending=pending.get("label"), reply="edited",
                               reason=f"함께 고친 것 {','.join(rev.also)}"
                                      + (f" (목록 밖 {','.join(unknown)})" if unknown else ""))
        return _memo_turn(rev.draft, state, head=memo.also_line(rev.also), unclear=unclear)
    if rev.kind == "ask":
        # 초안은 그대로 걸어 둔다 — 다음 턴의 「5,300만원으로」가 이 초안을 고치는 말이다.
        # 제안은 다시 조립한다: 이번 말이 받는 사람을 바꿨으면 제안 문장도 그 사람이어야 한다.
        return {"answer": rev.ask, "sources": [],
                "pending_action": _offer_draft(found, state, unclear=unclear)["pending_action"]}
    if rev.kind == "screened":
        return _memo_turn(found, state, head=memo.EDIT_SCREENED.format(faults=rev.reason),
                          unclear=unclear)
    if rev.kind == "down":
        return _memo_turn(found, state, head=memo.EDIT_DOWN.format(reason=rev.reason),
                          unclear=unclear)

    # ⑥ 고치라는 말이 아니다.
    if changed:
        return _memo_turn(found, state, unclear=unclear)
    # 승낙·거절로 읽는 것은 **말머리가 그 말이거나, «쪽지»를 «보내라»고 한** 때뿐이다.
    # `_YES` 의 부분문자열(「해줘」)로 읽으면 「지난 상담 요약해줘」 같은 새 질문이 이 초안을
    # 보낸다. 「저거 쪽지 내용 사번 3902173한테 보내줘」는 받는 사람이 이미 그 사번이면
    # 바뀐 것이 없으니 이 초안을 보내라는 말이다(새 질문으로 넘기면 쪽지를 새로 쓴다).
    text = question.lower()
    if unclear and not text.startswith(_LEAD_NO):
        if text.startswith(_LEAD_YES) or ("쪽지" in text and any(v in text for v in _SEND_VERBS)):
            return _memo_turn(found, state, unclear=True)
    if (text.startswith(_LEAD_YES) or text.startswith(_LEAD_NO)
            or ("쪽지" in text and any(v in text for v in _SEND_VERBS))):
        return _reply(pending, state)
    # 새 질문 — 답은 계획 루프가 쓴다(routing.route_confirm). 초안은 여기서 무효가 된다
    # (§10 「제안은 그 자리에서만 유효하다」). 나가는 것이 없으니 되돌릴 수 있는 쪽이다.
    return {"pending_action": None}


_ADDRESSEE = re.compile(r"([가-힣]{2,5}) 님")


def _address_change(pending: dict, found: memo.Draft) -> tuple[str, str, str] | None:
    """받는 사람이 바뀌었고 본문에 옛 받는 사람의 **이름**이 있으면 (옛 표기, 새 표기, 옛 이름).

    옛 이름은 제안에 적힌 표기(「정석희 님」·「미아동지점 김국민 님(사번 …)」)에서 읽는다.
    사번·본인으로 적힌 받는 사람은 이름을 모르므로 None — 본문의 호칭을 코드가 확인할 수 없다.
    """
    old_label = pending.get("to") or ""
    m = _ADDRESSEE.search(old_label)
    if not m or m.group(1) not in (found.body or "") or found.to == old_label:
        return None
    return old_label, found.to, m.group(1)


_ORDINALS = ("첫", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열")


def _pick(text: str, cands: list[dict]) -> int | None:
    """후보 목록에서 직원이 고른 것의 번호(0부터). 못 고르거나 둘 이상이면 None.

    받는 순서: 사번 → 「N번」·「N번째」·맨숫자 → 「첫 번째」 → 부서 이름. 사번이 목록에 없으면
    고른 것이 아니다(목록 밖 사번은 받는 사람 바꾸기로 처리된다 — `_readdress`).
    """
    raw = text or ""
    ids = [c["user_id"] for c in cands]
    said = [i for i, uid in enumerate(ids) if uid in raw]
    if len(said) == 1:
        return said[0]
    m = re.fullmatch(r"\s*(\d{1,2})(?!\s*(?:시|일|월|주|명|원|만|천|억|%|/|\.|:))\s*(?:번째|번)?\s*(?:분|께|에게|한테|으로|로)?[^\d]*", raw)
    if m and 1 <= int(m.group(1)) <= len(cands):
        return int(m.group(1)) - 1
    for i, word in enumerate(_ORDINALS[:len(cands)]):
        if re.search(rf"{word}\s*번째", raw):
            return i
    squash = re.sub(r"\s+", "", raw)
    groups = [i for i, c in enumerate(cands)
              if c.get("group_name") and (re.sub(r"\s+|\(.*?\)", "", c["group_name"]) in squash)]
    if len(groups) == 1:
        return groups[0]
    # 부서·직급의 **일부**만 말해도 고른 것이다 — 그 말이 든 후보가 한 명뿐이면. 행내 실측
    # (2026-09-23): 「WM플랫폼부(P)」를 고르려고 「WM」이라고 했는데 부서 이름 전체만 인정해서
    # 새 질문으로 넘어갔고, 계획 루프가 «WM»을 검색해 엉뚱한 답을 냈다. 대소문자는 가리지 않는다.
    words = [_untail(w) for w in re.split(r"[\s,./]+", raw.casefold())]
    words = [w for w in words if len(w) >= 2]
    hits = {i for i, c in enumerate(cands) for w in words
            if w in re.sub(r"\s+", "", c.get("group_name", "")).casefold()
            or w in (c.get("dsgt") or "").casefold()}
    return hits.pop() if len(hits) == 1 else None


#: 고르는 말 끝에 붙는 조사·꼬리 — 「WM으로」·「대출센터 분께」·「수석이요」.
_PICK_TAIL = r"(?:으로|로|에게|한테|께|분|님|쪽|꺼|거|이요|요|이|가)$"

def _untail(word: str) -> str:
    """꼬리를 여러 번 뗀다 — 「대리님께」는 «께»와 «님»이 겹쳐 붙는다."""
    while True:
        cut = re.sub(_PICK_TAIL, "", word)
        if cut == word or len(cut) < 2:
            return cut if len(cut) >= 2 else word
        word = cut


#: 목록이 떠 있는데 짧은 답이 어느 후보와도 맞지 않을 때 — 새 질문으로 넘기지 않는다.
PICK_AGAIN = "목록에서 번호나 부서로 골라 주세요."
#: 이보다 짧은 답은 «목록에서 고르려던 말»로 본다(새 질문은 대개 이보다 길다).
PICK_SHORT = 10


def _candidates_text(name: str, cands: list[dict]) -> str:
    """여러 명일 때의 화면 문장 — 합의한 문장 그대로다."""
    lines = [f"{name} 님이 {len(cands)}명 있어요. 어느 분께 보낼까요?"]
    lines += [f"{i}. {c.get('group_name', '')} {c.get('dsgt', '')} · 사번 {c['user_id']}".replace("  ", " ")
              for i, c in enumerate(cands, 1)]
    return "\n".join(lines)


def _memo_turn(found: memo.Draft, state: AgentState, head: str = "",
               unclear: bool = False) -> dict[str, Any]:
    """초안이 걸린 턴에 초안을 (다시) 세운다. 이 턴은 근거를 모으지 않았으므로 출처는 비운다."""
    return {**_offer_draft(found, state, head=head, unclear=unclear), "sources": []}


def _record_staff(*, pasted: bool, added: list[str], removed: list[str], text: str = "") -> None:
    """직원이 지정한 값을 기록에 남긴다(§10 결정 — 원장과 다른 값이 쪽지에 들어가는 길).

    **건수는 늘 남기고, 값은 트레이스 본문 스위치를 따른다.** 값을 싣는 자리는 span 의
    output 이고, 그 길은 `LANGFUSE_CAPTURE_CONTENT=0` 이면 본문 대신 글자 수만 남긴다
    (`observability._payload`). 값을 따로 score·로그로 남기면 그 스위치를 건너뛴다 — 발송
    기록(`_send_memo`)에 제목·본문을 싣지 않는 것과 같은 이유다.
    """
    count = 1 if pasted else len(added)
    observability.score("memo_staff_values", count,
                        comment="붙여넣은 글 그대로" if pasted else "직원 지정 값으로 검사 제외")
    with observability.span("consult.memo.staff_values",
                            input={"pasted": pasted, "count": count}) as span:
        span.update(output={"added": added, "removed": removed, "text": text or None})


def _send_memo(pending: dict, state: AgentState) -> dict[str, Any]:
    """승낙받은 초안을 쪽지로 보내고 결과를 알린다(§10 「연계 결과를 알린다」).

    보내는 것은 제안한 턴이 남긴 것 그대로다 — 여기서 다시 쓰지 않는다. 답변에 본문을 다시
    싣지도 않는다 — 직원이 방금 읽고 승낙한 것이라, 반복하면 같은 글이 화면에 두 번 선다.

    **받는 사람은 제안한 턴이 정했고, 보내는 사람은 이번 턴의 로그인 사번이다.** 둘은 다른
    축이다 — 받는 사람은 초안에 적혀 직원이 읽고 승낙한 값이라 여기서 다시 정하지 않고,
    보내는 사람은 이 요청을 지금 부른 직원이라 이번 턴의 상태에서 온다(MCP 인증에 들어가고
    행내 감사 기록이 그 사번으로 남는다 — `pension_agent/note.py` 의 «누구 이름으로»).

    **판정 못 한 결과를 «보냈다»로 접지 않는다**(note.parse_result). WorkB 는 실패를
    본문에 담아 보내므로, 어댑터가 성공이라고 한 것만 보고 보고하면 거부당한 호출이
    «발송 완료»로 화면에 뜬다.
    """
    markup, title = (pending.get("html") or "").strip(), (pending.get("title") or "").strip()
    ids = [r for r in (pending.get("recipients") or []) if r]
    user_name, group_name = pending.get("user_name") or "", pending.get("group_name") or ""
    if not markup or not title or not (ids or user_name):
        # 초안을 잃었으면 지어내지 않는다 — 무엇을 누구에게 보내기로 했는지 잃은 것이다.
        return {"answer": f"{pending['label']}을 다시 불러오지 못했어요. 한 번 더 부탁해 주세요.",
                "sources": [], "pending_action": None}
    to = pending.get("to") or MEMO_DEFAULT_TO
    if pending.get("when_unclear"):
        # 발송 시각이 안 정해진 초안은 보내지 않는다 — `_memo_reply` 가 먼저 막지만, 버튼으로
        # 곧장 승낙이 들어오는 길(main 의 pending_action)도 여기를 지난다.
        return {"answer": WHEN_ASK, "sources": [], "pending_action": pending}
    send_at, send_label = pending.get("send_at") or "", pending.get("send_label") or ""
    customer_id = (pending.get("params") or {}).get("customer_id") or ""
    sender = note.employee_id(state.get("employee_id"))
    if send_at:
        name = "schedule_memo"
        result = ACTIONS[name](customer_id, markup, send_at=send_at, send_label=send_label,
                               title=title, recipients=ids, to=to, as_employee=sender,
                               user_name=user_name if not ids else "",
                               group_name=group_name if not ids else "")
    elif user_name and not ids:
        # 이름 발송 — 찾은 사람이 1명이면 **이 호출에서 발송된다**(발송 전 사번 확인 불가).
        name = "send_memo_by_name"
        result = ACTIONS[name](customer_id, markup, title=title, user_name=user_name,
                               group_name=group_name, to=to, as_employee=sender)
    else:
        name = "send_memo"
        result = ACTIONS[name](customer_id, markup, title=title, recipients=ids, to=to,
                               as_employee=sender)
    # 되돌릴 수 없는 행위의 결과는 반드시 기록에 남긴다 — 제목·본문·사번은 싣지 않는다.
    # 시연 실행기가 즉시 보낸 예약은 그 사실이 상세(`detail`)로 로그·트레이스에 남는다.
    status = result.get("status") or "unknown"
    if status == "candidates":
        # 여러 명 — 보내지 않았다. 목록을 보여주고 고르게 한다(고르면 사번으로 다시 제안).
        observability.step("action", name, status=status, recipients=f"후보 {len(result['candidates'])}명")
        cands = result["candidates"]
        return {"answer": _candidates_text(user_name, cands), "sources": [],
                "pending_action": {**pending, "candidates": cands,
                                   "prompt": _candidates_text(user_name, cands).split("\n")[0]}}
    if status == "not_found":
        observability.step("action", name, status=status, reason=result.get("detail"))
        # 초안은 그대로 걸어 둔다 — 다음 말로 받는 사람만 바꿀 수 있다.
        return {"answer": NAME_NOT_FOUND.format(who=to), "sources": [],
                "pending_action": {k: v for k, v in pending.items() if k != "candidates"}}
    if user_name and not ids and result.get("recipients"):
        # 이름으로 보낸 것 — 실제로 받은 사람의 사번을 결과 문장에 밝힌다.
        to = name_label(user_name, group_name, ", ".join(result["recipients"]))
    done = status in ("sent", "stubbed", "scheduled")
    observability.score("action_outcome", status,
                        comment=f"{name} · 받는 사람 {len(ids)}명 · {result.get('detail') or ''}")
    observability.step("action", name, status=status,
                       recipients=f"{len(ids) or len(result.get('recipients') or [])}명",
                       reason=result.get("detail") if status != "sent" else None,
                       level=logging.INFO if done else logging.WARNING)
    if status == "scheduled":
        return {"answer": f"{send_label}에 보내도록 예약했어요 — 받는 사람: {to}.",
                "sources": [], "pending_action": None}
    if send_at and status == "not_connected":
        # 조용히 즉시 발송으로 바꾸지 않는다 — 직원은 예약했다고 믿는 쪽지가 이미 나간 상태가 된다.
        return {"answer": SCHEDULE_OFF, "sources": [], "pending_action": None}
    if not done:
        return {"answer": f"쪽지를 보내지 못했어요. {result.get('detail') or ''}".strip(),
                "sources": [], "pending_action": None}
    return {"answer": f"쪽지를 보냈어요 — 받는 사람: {to}.",
            "sources": [], "pending_action": None}


def _show_playbook(pending: dict) -> dict[str, Any]:
    """승낙받은 화법 카드를 **근거 원장에 싣는다** — 답변 문장은 compose 가 쓴다.

    여기서 답변을 직접 만들지 않는 이유는 지식 카드로 답을 쓰는 경로를 둘로 만들지 않기
    위해서다(graph.py "답변을 만드는 경로는 계획 루프 하나다"). 화면 URL 을 돌려주는
    `_link` 가 여기 해당하지 않는 것은 그쪽이 지식 내용이 아니라 링크이기 때문이다 —
    지식 카드를 손으로 렌더하면 §5 형태 요구도 §7 표시도 그 경로만 빠진다.

    **어느 카드인지는 제안한 턴이 남긴 것으로 정한다**(§10). 이번 질문에는 "네" 한 글자
    밖에 없으므로 다시 고르지 않는다.
    """
    picked = [c for c in (pending.get("cards") or []) if isinstance(c, dict) and c.get("id")]
    by_id = {c["id"]: c for c in KB.cards}
    hits = [(float(c.get("score") or 0.0), by_id[c["id"]]) for c in picked if c["id"] in by_id]
    if not hits:
        # 카드를 다시 찾지 못하면 지어내지 않는다 — 무엇을 보여드리기로 했는지 잃은 것이다.
        return {"answer": f"{pending['label']}을 다시 불러오지 못했어요. 한 번 더 물어봐 주세요.",
                "sources": [], "pending_action": None}
    # 종류별 렌더러·선언은 공용 빌더가 안다 — 여기서 화법 렌더러에 절차를 태우면 저작 메모가
    # 새고 화면번호 강제가 빠진다(tools.playbook_evidence 주석).
    ev = tools.playbook_evidence(pending.get("label") or "이 고객에게 쓰는 재료", hits)
    if ev is None:
        return {"answer": f"{pending['label']}을 다시 불러오지 못했어요. 한 번 더 물어봐 주세요.",
                "sources": [], "pending_action": None}
    # answer 를 비워 둔 채 원장만 채운다 — 분기표가 이걸 보고 compose 로 보낸다.
    #
    # 무엇을 승낙받았는지(`accepted`)도 함께 남긴다. 작성 단계가 받는 질문은 "네" 한 마디라
    # 그 말에는 무엇을 쓰라는 것인지가 없고, 알려주지 않으면 LLM 은 <자료> 를 **직전 턴의
    # 질문**에 대고 재서 「그 자료는 없어요」로 답한다(prompts.ACCEPTED_BLOCK 의 실측).
    # 여기서 답변 문장을 만들지 않는 것과 같은 규약이다 — 코드는 «무엇을 보여줄지»만 정하고
    # 문장은 compose 가 쓴다.
    return {"evidence": [ev], "pending_action": None,
            "accepted": pending.get("label") or "이 고객에게 쓰는 재료"}


def _link(pending: dict) -> dict[str, Any]:
    """연계 결과를 알린다 — 어느 화면을 열었는지, 못 열었으면 왜인지(§10).

    **링크는 화면만 연다.** 딥링크가 받는 파라미터는 `scnNo`·`mode` 뿐이라(screens.py) 고객
    식별자도 문구도 URL 로 넘어가지 않는다 — 직원이 열린 화면에서 입력한다. 그러니 여기서
    "무엇을 채웠다"고 말하지 않는다. 채우지 않은 값을 채웠다고 말하는 답변이 링크가 없는
    것보다 나쁘다.

    **URL 문자열은 답변 본문에 넣지 않는다**(2026-09-17). 링크는 `links` 로 나가고 화면이
    그것을 누를 수 있는 링크로 그린다 — 본문에 URL 을 박아 두던 것은 화면이 정규식으로
    긁어낼 수밖에 없던 시절의 잔재다(`app.py` 의 `SCREEN_LINK`). 본문에는 `label` 이 남고
    그 안에 화면번호가 있으므로, 상담이력에는 «무엇을 열었나»가 그대로 기록된다.
    이 턴의 링크는 여기서 확정해 넘긴다 — 승낙 턴은 원장이 비어 있어(`sources: []`)
    답변 본문만으로는 «원장이 아는 화면»을 다시 대조할 수 없다(`graph.ask`).
    """
    message = pending.get("message") or ""
    if pending.get("kind") == "lms":
        # 발송 화면으로 넘기는 문구는 코드가 한 가지를 거부한다 — 아직 실제 콘텐츠로
        # 확정되지 않은 더미 문구다. 화면을 열어 그 문구를 건네면 직원이 그대로 보낼 수
        # 있기 때문이고, 이 판정은 답변에 붙인 경고 문구가 아니라 코드가 한다(§10).
        gate = ACTIONS["open_lms_screen"](
            (pending.get("params") or {}).get("customer_id") or "", message)
        if gate["status"] == "blocked":
            observability.score("action_outcome", "blocked",
                                comment=f"open_lms_screen · {gate.get('detail') or ''}")
            observability.step("action", "open_lms_screen", status="blocked",
                               reason=gate.get("detail"), level=logging.WARNING)
            return {"answer": f"연계하지 않았어요. {gate['detail']}",
                    "sources": [], "pending_action": None, "links": []}

    screen = pending.get("screen") or ""
    url = screens.link(screen)
    if not url:
        # 화면번호가 규격에 맞지 않으면 연계 대신 화면번호만 안내한다(§10).
        observability.score("action_outcome", "failed",
                            comment=f"link · 화면번호 {screen or '미상'} 이 규격에 맞지 않음")
        observability.step("action", "link", status="failed", screen=screen or "미상",
                           reason="화면번호가 규격에 맞지 않음", level=logging.WARNING)
        return {"answer": f"화면 연계를 만들지 못했어요. 화면번호 {screen or '미상'} "
                          "로 직접 이동해 주세요.",
                "sources": [], "pending_action": None, "links": []}
    observability.score("action_outcome", "ok",
                        comment=f"link · {pending.get('kind') or 'screen'} · 화면 {screen}")
    observability.step("action", "link", status="ok", screen=screen, mode=screens.MODE)

    answer = str(pending["label"])
    if pending.get("kind") == "lms" and message:
        # 문구는 링크로 넘어가지 않으므로 직원이 화면에서 붙여넣도록 여기서 다시 준다.
        answer += f'\n화면이 열리면 이 문구를 넣어 주세요 — "{message}"'
    link = {"screen": screens.normalize(screen), "url": url,
            "label": screens.names(KB).get(screens.normalize(screen))
                     or screens.normalize(screen)}
    return {"answer": answer, "sources": [], "pending_action": None, "links": [link]}
