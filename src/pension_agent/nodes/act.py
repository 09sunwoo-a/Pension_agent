"""화면 연계 — 제안 노드 offer 와 확인 노드 confirm_action.

제안 여부는 규칙(LLM 아님) — 링크로 대체 못 하는 연계(LMS 문구·화법 카드·쪽지)만.
확인은 직전 한 턴의 제안만 실행. 에이전트는 화면을 열 뿐, 실제 작업은 직원이 수행(쪽지 발송 제외).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pension_agent.common import clock, note
from pension_agent.common import observability
from pension_agent import tools
from pension_agent.actions import memo, schedule, screens
from pension_agent.state import KB, AgentState
from pension_agent.actions.actions import ACTIONS, MEMO_DEFAULT_TO
from pension_agent.common.llm import LLMError

_YES = ("네", "예", "웅", "응", "그래", "좋아", "열어", "연계", "해줘", "해주세요", "부탁", "보내",
        "ok", "yes")
_NO = ("아니", "괜찮", "나중", "취소", "안 열", "안열", "하지마", "no")

#: 제안 갈래별 동사 — 실제 하는 일을 말함(화법은 화면을 열지 않음). 미등록 kind 는 화면 연계.
_VERBS = {"pitch": "보여드릴까요", "memo": "보내드릴까요"}
_DEFAULT_VERB = "연계해드릴까요"


def offer_prompt(action: dict[str, Any]) -> str:
    """제안을 묻는 문장 — 본문 끝 줄·버튼 위 문구·재질문 공용 단일 출처. action["prompt"] 우선."""
    return (action.get("prompt")
            or f"{action.get('label')}, {_verb(action)}? (네 / 아니오)")


def _verb(action: dict[str, Any]) -> str:
    return _VERBS.get(action.get("kind") or "") or _DEFAULT_VERB


def _eul(label: str) -> str:
    """목적격 조사 «을/를» — 마지막 한글 글자 받침 기준(괄호 꼬리 무시). 한글 없으면 «을»."""
    for ch in reversed(label or ""):
        if "가" <= ch <= "힣":
            return "을" if (ord(ch) - 0xAC00) % 28 else "를"
        if ch.isalnum():
            break
    return "을"


def _propose(state: AgentState) -> dict[str, Any] | None:
    """이번 답변에 붙일 연계 제안 하나(없으면 None) — 링크로 대체 불가한 LMS·플레이북만.

    단순 화면 열기는 본문 딥링크(graph.ask 의 links)가 담당. 따옴표 문장 기반 LMS 제안 금지
    (화법 답변이면 매 턴 붙음).
    """
    return _propose_lms(state) or _propose_playbook(state)


#: 쪽지 요청 판정어(규칙 판정).
_MEMO_WORDS = ("쪽지",)

#: 수신자 사번 꼴 — 자릿수는 note.EMP_NO_PATTERN 단일 출처.
_EMP_NO = re.compile(rf"(?<!\d)({note.EMP_NO_PATTERN})(?!\d)")

#: 사번 단서 — 숫자 꼴만으로는 금액과 구분 불가.
#:   앞 단서  「사번·직원·담당자·행원」(+조사·:) 바로 뒤의 숫자
#:   뒤 단서  (번·님·씨·닫는 괄호 뒤) 사람 조사(한테·에게·께·앞으로) / 「사번」
#:   문장 단서 문장에 「사번」 포함
#: «(으)로»는 단서 아님 — 「5000000으로」와 구분 불가.
_EMP_BEFORE = re.compile(r"(?:사번|직원|담당자?|행원)\s*(?:번호)?\s*(?:은|는|이|가|을|를|[:：=])?\s*$")
_EMP_AFTER = re.compile(r"^\s*[)\]]?\s*(?:번)?\s*(?:님|씨)?\s*(?:한테|에게|께|앞으로|앞에|사번)")
#: 금액 꼬리 — 단서 있어도 사번 아님.
_EMP_AMOUNT = re.compile(r"^\s*(?:원|만|천|억|%|,\d|\.\d)")

#: 받는 사람 미상 시 안내 — 제안 없이 종료.
NO_RECIPIENT = ("쪽지를 보낼 받는 사람을 알 수 없어요 — 로그인 사번이 넘어오지 않았습니다. "
                "«사번 3902172한테 보내줘»처럼 사번을 알려주시면 그 앞으로 보낼게요.")


def employee_no(question: str) -> str | None:
    """직원이 말한 수신자 사번. 없으면 None(본인 발송).

    단서 있는 숫자만 인정 — 못 읽으면 본인에게(되돌릴 수 있는 쪽으로 실패).
    """
    text = question or ""
    candidates = [m for m in _EMP_NO.finditer(text) if not _EMP_AMOUNT.match(text[m.end():])]
    # 후보 둘 이상이면 미판정 — 오발송 방지
    if len(candidates) != 1:
        return None
    m = candidates[0]
    before, after = text[:m.start()], text[m.end():]
    if _EMP_BEFORE.search(before) or _EMP_AFTER.match(after) or "사번" in text:
        return m.group(1)
    return None


# 받는 사람 이름 — 규칙 판독(LLM 미사용): 성씨 + 한글 1~3자 + (직급·님·씨) + 사람 조사.
# 바로 앞의 「~부·~지점·~센터·~팀·~본부」는 부서. 고객 이름 오판 시 동명 직원에게 고객 정보 유출.

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
#: 이름 꼴이지만 사람 이름이 아닌 말(성씨로 시작하는 보통명사).
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


#: 이름 둘을 이어 말한 꼴 — 한 사람만 받으므로 미판정.
#: 접속 조사 자리가 직급으로 시작하면 접속 아님(「과장님」의 «과»를 «A과 B»로 오독 방지).
_TITLE_ALT = "|".join(_TITLES)
_NAME_AND = re.compile(
    rf"(?<![가-힣])(?:{'|'.join(_SURNAMES)})[가-힣]{{1,3}}?\s*(?:(?:{_TITLE_ALT})\s*)?(?:님|씨)?\s*"
    rf"(?!(?:{_TITLE_ALT}))(?:이랑|랑|하고|와|과|,|및)\s*"
    rf"(?:{'|'.join(_SURNAMES)})[가-힣]{{1,3}}?\s*(?:(?:{_TITLE_ALT})\s*)?(?:님|씨)?\s*(?:한테|에게|께|앞으로)")


class _Many:
    """이름 후보가 둘 이상 — 코드가 고르지 않는다."""


NAME_MANY = _Many()


def recipient_name(text: str) -> tuple[str, str] | _Many | None:
    """직원이 말한 받는 사람 (이름, 부서). 없으면 None, 둘 이상이면 NAME_MANY.

    제외: 보통명사 · 직급만 붙은 호칭(「김대리」) · 고객 명부의 이름.
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
        from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
        return frozenset(getattr(p, "nm", "") for p in strategy_customer.PERSONAS)
    except Exception:          # noqa: BLE001 — 명부 실패가 이름 판독을 막지 않음
        return frozenset()


def name_label(name: str, group: str = "", emp_no: str = "") -> str:
    """화면에 세우는 받는 사람 표기 — 「미아동지점 김국민 님(사번 1631024)」."""
    head = f"{group} {name} 님" if group else f"{name} 님"
    return f"{head}(사번 {emp_no})" if emp_no else head


def recipient_title(text: str, name: str) -> str:
    """받는 사람 이름 뒤 직급(「이선우 대리님께」→«대리»). 없으면 "". 명부 직급이 있으면 그쪽 우선."""
    if not name:
        return ""
    m = re.search(rf"(?<![가-힣]){re.escape(name)}\s*(?:\(\s*{note.EMP_NO_PATTERN}\s*\))?\s*"
                  rf"(?P<t>{'|'.join(_TITLES)})", text or "")
    return m.group("t") if m else ""


def addressee(name: str, title: str = "") -> str:
    """본문에서 받는 사람을 부르는 말 — 「이선우 대리님」, 직급을 모르면 「이선우 님」."""
    return f"{name} {title}님" if title else f"{name} 님"


def retitle(found: memo.Draft, name: str, title: str) -> memo.Draft:
    """본문의 받는 사람 호칭을 «이름 + 직급님»으로 교정(코드 처리). 본문에 이름 없으면 그대로."""
    if not (name and title and found.body):
        return found
    body = re.sub(rf"(?<![가-힣]){re.escape(name)}\s*(?:(?:{'|'.join(_TITLES)})\s*)?님",
                  addressee(name, title), found.body)
    if body == found.body:
        return found
    made, _ = memo.assemble(found.title, body, tail_html=found.tail_html, tail_note=found.tail_note,
                            to=found.to, recipients=found.recipients, send_at=found.send_at,
                            send_label=found.send_label, user_name=found.user_name,
                            group_name=found.group_name)
    return made or found


class Recipient(dict):
    """받는 사람 판정 — ids(사번) · label(표기) · to_self · user_name · group_name · unclear."""


#: 받는 사람 이름을 하나로 정하지 못한 턴의 안내.
NAME_UNCLEAR = ("받는 사람 이름을 하나로 정하지 못했어요. «김국민한테»처럼 한 분만 "
                "말씀해 주세요.")


def _recipients(state: AgentState) -> Recipient:
    """첫 쪽지 요청 턴의 받는 사람(코드 판정) — 사번 → 이름 → 본인 순. 사번+이름이면 사번 발송."""
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
    """쪽지 턴 여부 — «쪽지» 발화 · 원장 비어 있지 않음 · 화면 답변 있음.

    고객 화면·쪽지 종류는 조건 아님(재료는 memo.material 이 결정).
    """
    if not _said_memo(state):
        return False
    # 예외 — 직전 답변을 재료로 붙인 턴은 화면 답변이 비어 있음
    answered = bool((state.get("answer") or "").strip()) or bool(state.get("memo_material"))
    return bool(state.get("evidence")) and answered


def _said_memo(state: AgentState) -> bool:
    """이번 턴 «쪽지» 발화 여부(규칙 판정)."""
    return any(w in (state.get("question") or "") for w in _MEMO_WORDS)


def _with_memo_material(state: AgentState) -> AgentState:
    """«쪽지» 턴인데 원장이 비었으면 직전 답변(last_answer)을 코드가 재료로 첨부.

    «이거 쪽지로 보내줘»를 LLM 도구 선택에 걸지 않기 위함. 실패 안내문 답변은 비움.
    """
    if state.get("evidence") or not _said_memo(state):
        return state
    from pension_agent.nodes import plan  # noqa: PLC0415 — 순환 임포트 회피

    try:
        found = tools.run("last_answer", state, "")
    except (LLMError, tools.ToolFailure):
        found = None
    if found is None:
        return state
    answer = state.get("answer") or ""
    # memo_material — 이 턴 내부 표지(offer 반환값에 미포함)
    return {**state, "evidence": [found], "memo_material": True,
            "answer": "" if plan.is_failure_notice(answer) else answer}


def _memo_offer(state: AgentState) -> dict[str, Any]:
    """쪽지 초안으로 답변 자리를 대체하고 «이대로 보낼까요?» 제안. 초안 실패 시 사유만(폴백 없음)."""
    who = _recipients(state)
    head = (state.get("answer") or "").strip()
    if who.get("unclear"):
        return {"answer": f"{head}\n\n— {who['unclear']}" if head else who["unclear"]}
    if not who["ids"] and not who.get("user_name"):
        return {"answer": f"{head}\n\n— {NO_RECIPIENT}" if head else NO_RECIPIENT}
    # 이름을 알 때만 본문 호칭 전달(사번·본인은 이름 미상)
    named = recipient_name(state.get("question") or "")
    name = named[0] if isinstance(named, tuple) and not who["to_self"] else ""
    title = recipient_title(state.get("question") or "", name)
    found, why = memo.draft(state, recipients=who["ids"], to=who["label"], to_self=who["to_self"],
                            addressee=addressee(name, title) if name else "")
    if found is None:
        return {"answer": f"{head}\n\n— {why}" if head else why}
    if who.get("user_name"):
        found = memo.readdress(found, [], who["label"], who["user_name"], who.get("group_name", ""))
    found = retitle(found, name, title)
    # 발송 시각 — 단서 붙은 날짜만. 미확정이면 초안만 세우고 시각을 되물음
    timing = schedule.parse(state.get("question") or "", clock.now())
    if timing.kind == "ok" and timing.at is not None:
        found = memo.reschedule(found, timing.at.isoformat(timespec="minutes"), timing.label)
    unclear = timing.kind in ("many", "past")
    return _offer_draft(found, state, head=_WHEN_NOTES.get(timing.kind, ""), unclear=unclear)


#: 발송 시각 미확정 안내.
WHEN_MANY = "언제 보낼지 하나로 정하지 못했어요."
WHEN_PAST = "말씀하신 발송 시각이 이미 지났어요."
_WHEN_NOTES = {"many": WHEN_MANY, "past": WHEN_PAST}
#: 이름 검색 결과 0명. who 는 「김국민 님」 꼴.
NAME_NOT_FOUND = "{who}을 찾지 못했어요. 이름이나 부서를 다시 확인해 주세요."
#: 예약 실행기 미연결 안내(미발송).
SCHEDULE_OFF = "예약 발송이 아직 연결되지 않아 보내지 않았어요. 지금 보내려면 쪽지를 다시 부탁해 주세요."
#: 발송 시각 미정 초안의 제안 문장 — «네»로 발송 불가.
WHEN_ASK = ("언제 보낼까요? «10월 5일 오전 9시에 보내줘»처럼 말씀해 주세요. "
            "지금 보내려면 «지금 보내줘»라고 해 주세요.")


def _offer_draft(found: memo.Draft, state: AgentState, head: str = "",
                 unclear: bool = False, note: str = "") -> dict[str, Any]:
    """초안 한 통을 세우고 발송 제안을 건다 — 처음·고친 초안 공용(고칠 때마다 제안 재설정).

    제안에 재조립 조각(body·tail_html·tail_note) 포함 — memo.revise 가 본문만 교체.
    head 는 초안 위, note(«함께 고친 것»)는 초안 아래·묻는 문장 앞(첫 문단은 화면에서 굵게 표시).
    """
    label = found.to
    if unclear:
        name, ask = f"이 쪽지 보내기(받는 사람: {label} · 발송 시각 미정)", WHEN_ASK
    elif found.send_label:
        # 예약 쪽지 — 제안 문장에 발송 시각 명시(오독 시 승낙 전 확인 가능)
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
    # 초안은 코드블록으로 구분 — 펜스는 화면 장치(발송 본문·세션 재료에서 제거)
    draft = f"{memo.FENCE}\n[제목] {found.title}\n\n{found.text}\n{memo.FENCE}"
    body = f"{draft}\n\n{note}\n\n— {action['prompt']}" if note else f"{draft}\n\n— {action['prompt']}"
    return {"answer": f"{head}\n\n{body}" if head else body, "pending_action": action}


def _propose_lms(state: AgentState) -> dict[str, Any] | None:
    """이번 턴 안내 콘텐츠의 LMS 발송 화면 연계 제안. 조건(전부 코드 판정):

      1. 고객 화면 열림 + 성립한 관리 사유 있음
      2. 원장에 outreach 근거 있음 — 턴마다 갈리는 게이트
      3. 답변이 그 콘텐츠를 가리킴(이름·링크 인용)
      4. 발송 문구 있음(브리핑 ⑨ 산출 그대로, 여기서 생성 금지)
    """
    if not state.get("customer_id") or not _managed_reason(state):
        return None
    answer = state.get("answer") or ""
    for ev in state.get("evidence") or []:
        if ev["tool"] != "outreach":
            continue
        for key, item in _lms_items(ev):
            if not _mentions(answer, item["name"], item.get("url")):
                continue          # 답변이 가리키지 않은 콘텐츠
            found = screens.lms_screen(KB)
            if not found:
                # 화면번호 미등록 시 링크 미생성
                return None
            number, card = found
            return {"kind": "lms", "label": f"«{item['name']}» 안내 문구로 {number} 발송 화면 열기",
                    "screen": number, "card": card, "message": item["message"],
                    "content_id": item["id"], "content_kind": key,
                    "params": {"customer_id": state.get("customer_id") or ""}}
    return None


#: 콘텐츠 이름 끝 종류 낱말 — 답변은 흔히 생략.
_CONTENT_KINDS = ("이벤트", "세미나")


def _mentions(answer: str, name: str, url: str | None = None) -> bool:
    """답변이 이 콘텐츠를 가리켰는지 — 이름(끝 종류 낱말·공백 무시) 또는 안내 링크 인용.

    허용 폭은 종류 낱말·공백뿐(앞부분 생략은 불인정). 링크는 원문 스팬이라 확실한 식별자.
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
    """outreach 근거의 발송 가능 목록 (이벤트/세미나, {id·name·message}). 브리핑 재호출 금지(선정 변동)."""
    out: list[tuple[str, dict]] = []
    for key, item in ((ev["meta"].get("lms") or {})).items():
        if isinstance(item, dict) and item.get("name") and item.get("message"):
            out.append((key, item))
    return out


#: 제안 갈래를 여는 원장 재료 — 이번 턴에 다룬 갈래의 나머지 후보만 제안.
_LANE_WORDS = {"pitch": "화법", "procedure": "업무 절차", "method": "관리 방법론"}


def _propose_playbook(state: AgentState) -> dict[str, Any] | None:
    """고객 상태에 걸린 재료(화법·방법론·절차) 추가 제시 제안. 조건(전부 코드 판정):

      1. 고객 화면 열림
      2. 원장에 pitch·procedure·method 근거 있음 — 그 갈래의 나머지만. 턴마다 갈리는 유일한 게이트
      3. 성립한 문제상황 있음
      4. 이번 턴이 아직 안 쓴 카드가 남음
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
    # 「~에게 쓰는 화법」 표현 — «걸린» 같은 코드 용어 금지
    reason = _offer_reason(ranked)
    label = (f"«{reason}»에게 쓰는 {what} {len(hits)}건" if reason
             else f"이 고객에게 쓰는 {what} {len(hits)}건")
    # 관련도 포함 — 승낙 턴은 재선정 없이 그대로 적재
    return {"kind": "pitch", "label": label,
            "cards": [{"id": c["id"], "score": round(score, 3)} for score, c in hits],
            "params": {"customer_id": state.get("customer_id") or ""}}


#: 문제상황 제목의 정의 꼬리(« — …»·« (…)») — 제안에는 대상만.
_SIT_TAIL = re.compile(r"\s*(?:—|\(|-\s).*$")

#: 제안 문구에 대는 문제상황 최대 개수(PLAYBOOK_TOP_K 와 맞춤).
_REASON_MAX = 2


def situation_name(sit: dict | None) -> str:
    """문제상황의 대상 이름(정의 꼬리 제거). 제안 문구·검사 공용."""
    return _SIT_TAIL.sub("", str((sit or {}).get("title") or "")).strip()


def _offer_reason(ranked: list[tuple[float, dict, dict]]) -> str:
    """제안 사유 — 고른 카드를 실제로 세운 문제상황 이름(고객 요건 목록 사용 금지: 제목·내용 불일치)."""
    names: list[str] = []
    for _score, _card, sit in ranked:
        title = situation_name(sit)
        if title and title not in names:
            names.append(title)
    return ", ".join(names[:_REASON_MAX])


def _managed_reason(state: AgentState) -> str:
    """성립한 관리 요건 이름 — LMS 제안 게이트용(화법 제안 사유는 _offer_reason)."""
    try:
        from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
        profile = strategy_customer.get_profile(state.get("customer_id") or "")
        if profile is None:
            return ""
        names = [strategy_customer.CONDS[c] for c in strategy_customer.conditions(profile)
                 if c in strategy_customer.CONDS]
    except Exception:
        return ""
    return " · ".join(names[:2])


def offer(state: AgentState) -> dict[str, Any]:
    """답변 뒤 제안. 쪽지 턴 우선(같은 턴 화면 연계 미부착). 조건 불충족 시 상태 불변."""
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
    # 끝의 「(네 / 아니오)」 — transcript 재료에서 이 줄을 떼는 표지(tools._OFFER_TRAILER)
    ask = offer_prompt(action)
    # 본문 끝 줄·버튼 위 문구·재질문이 같은 문장이도록 제안에 저장
    action["prompt"] = ask
    return {"answer": state["answer"] + f"\n\n— {ask}", "pending_action": action}


def _pending(history: list[dict] | None) -> dict | None:
    """직전 한 턴의 제안만 — 몇 턴 뒤의 «네»로 실행 금지."""
    if not history:
        return None
    return (history[-1] or {}).get("pending_action") or None


def confirm_action(state: AgentState) -> dict[str, Any]:
    """직전 제안의 승낙·거절·재질문. 연계 대상은 제안 턴이 남긴 값만 사용."""
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
        # 애매하면 제안 유지 + 같은 문장으로 재질문
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


# 쪽지 초안이 걸린 턴 — 코드가 먼저 가르고, «고치라는 말인가»만 LLM 판정.
#   ① 거절(짧은 거절 말)                 → 취소
#   ② 받는 사람이 바뀌었나               → 코드(사번 단서 · 본인을 가리키는 말)
#   ③ 짧은 승낙 말이고 ②가 없다          → 그대로 발송
#   ④ «이대로·똑같이» + 붙여넣은 글       → 그 글 그대로 초안(LLM·검사 없음)
#   ⑤ 나머지                            → LLM 이 고치라는 말인지 보고 고친다(memo.revise)
#   ⑥ 고치라는 말이 아니면               → ②면 새 수신자로 다시 제안, 승낙·거절 말이면 그대로,
#                                          아니면 새 질문 — 계획 루프로(초안 무효)

def _norm(text: str) -> str:
    return re.sub(r"[\s.,!?~…ㅎㅋ^]+", "", text.lower())


#: 짧은 승낙(이것만 있는 말) — «보내» 포함만으로 승낙 판정 금지(고치기 지시 무시 발송 위험).
_PLAIN_YES = re.compile(
    r"^(?:네|넵|예|웅|응|ㅇㅇ|ㅇㅋ|그래|좋아|좋아요|오케이|ok|yes)*"
    r"(?:그대로|이대로|이걸로|그걸로)?(?:쪽지)?(?:로)?"
    r"(?:보내|발송해|전송해|발송|전송|보내줘|보내주세요|보내줘요|부탁해|부탁해요|부탁드려요)?"
    r"(?:줘|주세요|줘요|요)?$")
#: 짧은 거절(이것만 있는 말) — 「아니 앞에 … 붙여줘」는 고치기.
_PLAIN_NO = re.compile(
    r"^(?:아니|아니요|아니오|아뇨|아냐|no|취소|취소해|취소해줘|취소해주세요|괜찮아|괜찮아요|"
    r"됐어|됐어요|안보내|안보내도돼|보내지마|보내지마요|나중에|나중에할게)+$")

#: ⑥ 승낙·거절 말머리와 발송 동사.
_LEAD_YES = ("네", "넵", "예", "웅", "응", "그래", "좋아", "오케이", "ok", "yes", "보내")
_LEAD_NO = ("아니", "아뇨", "취소", "괜찮", "됐어", "나중")
_SEND_VERBS = ("보내", "발송", "전송")

#: 받는 사람을 본인으로 바꾸는 말. 맨 「본인」 미포함(「고객 본인이」 오판 방지).
_SELF_WORDS = ("나한테", "나에게", "내게", "저한테", "저에게", "내 쪽지함", "내쪽지함",
               "본인한테", "본인에게", "나 한테", "저 한테")

#: 붙여넣은 글 그대로 / 다듬기 지시어. 둘 다 있으면 다듬기.
_VERBATIM_WORDS = ("이렇게 보내", "이렇게 쪽지", "이대로", "똑같이", "그대로", "수정 없이",
                   "수정없이", "토씨")
_RESTYLE_WORDS = ("변경해", "바꿔", "다듬", "정리해", "식으로", "처럼", "참고해", "고쳐",
                  "수정해", "스타일", "느낌")
#: 붙여넣은 글 최소 길이.
_PASTE_MIN = 20


def _pasted(text: str) -> tuple[str, str] | None:
    """(지시, 붙여넣은 글) — «이대로·똑같이» 지시가 있을 때만. 첫 콜론·줄바꿈 기준, 역순(글 뒤 지시)도 허용."""
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


#: 사번과 본인을 함께 말한 턴의 안내(초안·받는 사람 유지).
RECIPIENT_UNCLEAR = ("받는 사람을 사번과 본인 중 하나로 정하지 못해서 그대로 뒀어요. "
                     "바꾸시려면 «사번 3902173한테» 또는 «나한테»처럼 한쪽만 말씀해 주세요.")


def _both_named(order: str) -> str:
    """받는 사람을 못 가르는 말이면 안내 문장, 아니면 "". 사번+이름은 해당 없음(사번 발송)."""
    named = recipient_name(order)
    if named is NAME_MANY:
        return NAME_UNCLEAR
    if (employee_no(order) or named) and any(w in order for w in _SELF_WORDS):
        return RECIPIENT_UNCLEAR
    return ""


def _readdress(order: str, pending: dict, state: AgentState) -> tuple | None:
    """받는 사람 변경 시 (사번 목록, 표기, 이름, 부서), 아니면 None.

    말이 없으면 직전 수신자 유지 — «사번 없으면 본인» 적용 금지(타인 앞 초안이 조용히 본인으로).
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

    # 동명이인 목록에서 고르는 턴 — 고르면 그 사번으로 재제안(이름 재검색 금지)
    cands = pending.get("candidates") or []
    if cands:
        picked = _pick(question, cands)
        if picked is not None:
            c = cands[picked]
            name = found.user_name
            found = memo.readdress(found, [c["user_id"]],
                                   name_label(name, c.get("group_name", ""), c["user_id"]))
            # 명부 직급으로 본문 호칭 교정
            found = retitle(found, name, (c.get("dsgt") or "").strip())
            observability.step("confirm", pending=pending.get("label"), reply="accept")
            return _memo_turn(found, state, unclear=bool(pending.get("when_unclear")))
        if plain and _PLAIN_YES.match(plain):
            return _pick_turn(found.user_name, cands, pending)
        # 짧은 답이 후보와 불일치 → 목록 재표시(받는 사람·시각 변경·거절은 아래 갈래)
        if (len(question) <= PICK_SHORT and not employee_no(question)
                and not recipient_name(question) and not any(w in question for w in _SELF_WORDS)
                and schedule.parse(question, clock.now(), edit=True).kind == "none"):
            return _pick_turn(found.user_name, cands, pending, ask=PICK_AGAIN)

    paste = _pasted(question)
    order = paste[0] if paste else question
    unclear_who = _both_named(order)
    if unclear_who:
        # 받는 사람 미판정 — 유지하고 재질문
        observability.step("confirm", pending=pending.get("label"), reply="unclear")
        return _memo_turn(found, state, head=unclear_who, unclear=bool(pending.get("when_unclear")))
    moved = _readdress(order, pending, state)                               # ②
    if moved:
        found = memo.readdress(found, *moved)

    # ②′ 발송 시각 — 말 없으면 유지, 단서 있으면 변경, 「지금 보내줘」는 예약 해제. 바뀌면 재제안
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
            return _reply(pending, state)           # 이미 즉시 발송 초안 — 승낙
    changed = bool(moved) or retimed

    if plain and _PLAIN_YES.match(plain) and not changed and not paste:     # ③
        if unclear:
            # 발송 시각 미정 — «네»로 발송 금지, 재질문
            return _memo_turn(found, state, unclear=True)
        return _reply(pending, state)

    if paste:                                                               # ④
        made, why = memo.verbatim(found, paste[1])
        observability.step("confirm", pending=pending.get("label"), reply="pasted",
                           status="ok" if made else "blocked", reason=why or None)
        if made is None:
            return _memo_turn(found, state, head=why)
        # 직원 작성 글 — 건수 기록, 본문은 스위치 따르는 span
        _record_staff(pasted=True, added=[], removed=[], text=paste[1])
        return _memo_turn(made, state, unclear=unclear)

    # 받는 사람 변경 + 본문에 옛 이름 → 호칭 교정 요청. 이름 미상(사번·본인)이면 미요청
    address = _address_change(pending, found) if moved else None
    with observability.span("consult.memo.edit", input={"instruction": question}) as span:
        rev = memo.revise(found, question, state.get("history"), recipient=address)  # ⑤
        span.update(output={"kind": rev.kind, "added": rev.added, "removed": rev.removed})
    observability.step("confirm", pending=pending.get("label"), reply=rev.kind,
                       recipients="변경" if moved else None, reason=rev.reason or None)

    if rev.kind == "edited" and rev.draft is not None:
        _record_staff(pasted=False, added=rev.added, removed=rev.removed)
        unknown = [c for c in rev.also if c not in memo.ALSO_LABELS]
        # address 표시는 코드 확인 시만(받는 사람 변경 또는 호칭 소실). 걸러낸 것은 기록만
        shown = [c for c in rev.also
                 if c != "address" or address or _called(found.body) - _called(rev.draft.body)]
        if rev.also:
            dropped = [c for c in rev.also if c not in shown]
            observability.step("confirm", pending=pending.get("label"), reply="edited",
                               reason=f"함께 고친 것 {','.join(rev.also)}"
                                      + (f" (목록 밖 {','.join(unknown)})" if unknown else "")
                                      + (f" (확인 못 해 뺌 {','.join(dropped)})" if dropped else ""))
        edited = rev.draft
        if moved and moved[2]:
            edited = retitle(edited, moved[2], recipient_title(order, moved[2]))
        return _memo_turn(edited, state, note=memo.also_line(shown), unclear=unclear)
    if rev.kind == "echo":
        return _memo_turn(found, state, head=memo.EDIT_ECHO, unclear=unclear)
    if rev.kind == "ask":
        # 초안 유지, 제안은 재조립(받는 사람 변경 반영)
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
    # 승낙·거절은 말머리 또는 «쪽지 + 보내» 일 때만 — _YES 부분문자열(「해줘」)이면 새 질문이 초안을 발송
    text = question.lower()
    if unclear and not text.startswith(_LEAD_NO):
        if text.startswith(_LEAD_YES) or ("쪽지" in text and any(v in text for v in _SEND_VERBS)):
            return _memo_turn(found, state, unclear=True)
    if (text.startswith(_LEAD_YES) or text.startswith(_LEAD_NO)
            or ("쪽지" in text and any(v in text for v in _SEND_VERBS))):
        return _reply(pending, state)
    # 새 질문 — 계획 루프로(routing.route_confirm), 초안 무효
    return {"pending_action": None}


_ADDRESSEE = re.compile(r"([가-힣]{2,5}) 님")


def _address_change(pending: dict, found: memo.Draft) -> tuple[str, str, str] | None:
    """받는 사람 변경 + 본문에 옛 이름 있으면 (옛 표기, 새 표기, 옛 이름). 옛 표기에 이름 없으면 None."""
    old_label = pending.get("to") or ""
    m = _ADDRESSEE.search(old_label)
    if not m or m.group(1) not in (found.body or "") or found.to == old_label:
        return None
    return old_label, found.to, m.group(1)


_ORDINALS = ("첫", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열")


def _pick(text: str, cands: list[dict]) -> int | None:
    """후보 목록에서 고른 번호(0부터). 미선택·복수면 None.

    순서: 사번 → N번 → 서수 → 부서 이름 → 마지막 → 부서·직급 일부 → 부서 줄임말.
    목록 밖 사번은 받는 사람 변경(_readdress).
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
    # 부서·직급 일부(「WM」)도 후보 한 명에만 맞으면 선택. 대소문자 무시
    if re.search(r"마지막|맨\s*끝|맨\s*아래", raw):
        return len(cands) - 1
    words = [_untail(w) for w in re.split(r"[\s,./]+", raw.casefold())]
    words = [w for w in words if len(w) >= 2]
    hits = {i for i, c in enumerate(cands) for w in words
            if w in re.sub(r"\s+", "", c.get("group_name", "")).casefold()
            or w in (c.get("dsgt") or "").casefold()}
    if not hits:
        # 부서 줄임말(「대출센터」→「대출실행센터」) — 3자 이상, 글자 순서 일치
        hits = {i for i, c in enumerate(cands) for w in words
                if len(w) >= 3 and _in_order(w, re.sub(r"\s+", "", c.get("group_name", "")).casefold())}
    return hits.pop() if len(hits) == 1 else None


def _in_order(short: str, full: str) -> bool:
    """short 글자가 full 에 순서대로 포함(첫 글자 일치 필수)."""
    if not short or not full or short[0] != full[0]:
        return False
    it = iter(full)
    return all(ch in it for ch in short)


#: 고르는 말 끝 조사·꼬리.
_PICK_TAIL = r"(?:으로|로|에게|한테|께|분|님|쪽|꺼|거|이요|요|이|가)$"

def _untail(word: str) -> str:
    """겹친 꼬리 반복 제거(「대리님께」)."""
    while True:
        cut = re.sub(_PICK_TAIL, "", word)
        if cut == word or len(cut) < 2:
            return cut if len(cut) >= 2 else word
        word = cut


#: 본문의 사람 호칭(「고객님」 제외).
_CALLED = re.compile(r"[가-힣]{2,4}\s?(?:[가-힣]{1,4})?님")


def _called(body: str) -> set[str]:
    return {m.group() for m in _CALLED.finditer(body or "") if "고객" not in m.group()}


#: 짧은 답이 후보와 불일치 시 재요청 문장.
PICK_AGAIN = "목록에서 번호나 부서로 골라 주세요."
#: 이 길이 이하 답은 목록 선택 시도로 간주.
PICK_SHORT = 10


#: 목록 턴 끝 문장. 이 턴은 action 이벤트 미발행(네/아니오 버튼 방지), 후보는 pending_action 에 보존.
PICK_ASK = "어느 분께 보낼까요? 번호나 부서로 답해 주세요."


def _pick_turn(name: str, cands: list[dict], pending: dict, ask: str = PICK_ASK) -> dict[str, Any]:
    """동명이인 목록 턴(미발송). 첫 문단은 인원 한 줄, 묻는 문장은 목록 뒤 맨 끝."""
    listed = "\n".join(
        f"{i}. " + f"{c.get('group_name', '')} {c.get('dsgt', '')} · 사번 {c['user_id']}".replace("  ", " ").strip()
        for i, c in enumerate(cands, 1))
    text = f"{name} 님이 {len(cands)}명 있어요.\n\n{listed}\n\n{ask}"
    action = {**pending, "candidates": cands, "prompt": ask}
    return {"answer": text, "sources": [], "pending_action": action}


def _memo_turn(found: memo.Draft, state: AgentState, head: str = "",
               unclear: bool = False, note: str = "") -> dict[str, Any]:
    """초안 (재)표시. 출처는 비움."""
    return {**_offer_draft(found, state, head=head, unclear=unclear, note=note), "sources": []}


def _record_staff(*, pasted: bool, added: list[str], removed: list[str], text: str = "") -> None:
    """직원 지정 값 기록 — 건수는 score, 값은 span output 에만(본문 스위치 준수, score·로그에 값 금지)."""
    count = 1 if pasted else len(added)
    observability.score("memo_staff_values", count,
                        comment="붙여넣은 글 그대로" if pasted else "직원 지정 값으로 검사 제외")
    with observability.span("consult.memo.staff_values",
                            input={"pasted": pasted, "count": count}) as span:
        span.update(output={"added": added, "removed": removed, "text": text or None})


def _send_memo(pending: dict, state: AgentState) -> dict[str, Any]:
    """승낙된 초안 발송 및 결과 안내. 초안 재작성·본문 재표시 없음.

    받는 사람 = 제안 턴 값, 보내는 사람 = 이번 턴 로그인 사번. 판정 못 한 결과를 «보냈다»로 처리 금지.
    """
    markup, title = (pending.get("html") or "").strip(), (pending.get("title") or "").strip()
    ids = [r for r in (pending.get("recipients") or []) if r]
    user_name, group_name = pending.get("user_name") or "", pending.get("group_name") or ""
    if not markup or not title or not (ids or user_name):
        # 초안 소실 — 지어내지 않음
        return {"answer": f"{pending['label']}을 다시 불러오지 못했어요. 한 번 더 부탁해 주세요.",
                "sources": [], "pending_action": None}
    to = pending.get("to") or MEMO_DEFAULT_TO
    if pending.get("when_unclear"):
        # 발송 시각 미정 — 버튼 승낙 경로도 여기서 차단
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
        # 이름 발송 — 1명이면 이 호출에서 즉시 발송(사전 사번 확인 불가)
        name = "send_memo_by_name"
        result = ACTIONS[name](customer_id, markup, title=title, user_name=user_name,
                               group_name=group_name, to=to, as_employee=sender)
    else:
        name = "send_memo"
        result = ACTIONS[name](customer_id, markup, title=title, recipients=ids, to=to,
                               as_employee=sender)
    # 결과 기록 필수 — 제목·본문·사번 미포함
    status = result.get("status") or "unknown"
    if status == "candidates":
        # 동명이인 — 미발송, 목록 표시
        observability.step("action", name, status=status, recipients=f"후보 {len(result['candidates'])}명")
        return _pick_turn(user_name, result["candidates"], pending)
    if status == "not_found":
        observability.step("action", name, status=status, reason=result.get("detail"))
        # 초안 유지 — 받는 사람만 변경 가능
        return {"answer": NAME_NOT_FOUND.format(who=to), "sources": [],
                "pending_action": {k: v for k, v in pending.items() if k != "candidates"}}
    if user_name and not ids and result.get("recipients"):
        # 이름 발송 — 실제 수신자 사번 명시
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
        # 즉시 발송으로 대체 금지
        return {"answer": SCHEDULE_OFF, "sources": [], "pending_action": None}
    if not done:
        return {"answer": f"쪽지를 보내지 못했어요. {result.get('detail') or ''}".strip(),
                "sources": [], "pending_action": None}
    return {"answer": f"쪽지를 보냈어요 — 받는 사람: {to}.",
            "sources": [], "pending_action": None}


def _show_playbook(pending: dict) -> dict[str, Any]:
    """승낙된 카드를 원장에만 적재 — 문장은 compose(답변 경로 단일화). 카드는 제안 턴 값 그대로."""
    picked = [c for c in (pending.get("cards") or []) if isinstance(c, dict) and c.get("id")]
    by_id = {c["id"]: c for c in KB.cards}
    hits = [(float(c.get("score") or 0.0), by_id[c["id"]]) for c in picked if c["id"] in by_id]
    if not hits:
        # 카드 소실 — 지어내지 않음
        return {"answer": f"{pending['label']}을 다시 불러오지 못했어요. 한 번 더 물어봐 주세요.",
                "sources": [], "pending_action": None}
    # 종류별 렌더러는 공용 빌더 사용 — 화법 렌더러로 절차를 렌더하면 저작 메모 노출
    ev = tools.playbook_evidence(pending.get("label") or "이 고객에게 쓰는 재료", hits)
    if ev is None:
        return {"answer": f"{pending['label']}을 다시 불러오지 못했어요. 한 번 더 물어봐 주세요.",
                "sources": [], "pending_action": None}
    # answer 비움 → 분기표가 compose 로. accepted 필수 — 없으면 «네»만 보고 «자료 없음»으로 답함
    return {"evidence": [ev], "pending_action": None,
            "accepted": pending.get("label") or "이 고객에게 쓰는 재료"}


def _link(pending: dict) -> dict[str, Any]:
    """화면 연계 결과 안내(연 화면 또는 실패 사유).

    딥링크는 scnNo·mode 뿐 — «채웠다»고 말하지 않음. URL 은 본문 대신 links 로 전달
    (승낙 턴은 원장이 비어 graph.ask 가 재대조 불가 — 여기서 확정).
    """
    message = pending.get("message") or ""
    if pending.get("kind") == "lms":
        # 더미 문구 게이트 — 직원이 그대로 발송할 수 있으므로 코드가 거부
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
        # 규격 외 화면번호 — 번호만 안내
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
        # 문구는 링크 미전달 — 직접 붙여넣도록 재제공
        answer += f'\n화면이 열리면 이 문구를 넣어 주세요 — "{message}"'
    link = {"screen": screens.normalize(screen), "url": url,
            "label": screens.names(KB).get(screens.normalize(screen))
                     or screens.normalize(screen)}
    return {"answer": answer, "sources": [], "pending_action": None, "links": [link]}
