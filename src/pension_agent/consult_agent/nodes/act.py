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

import re
from typing import Any

from pension_agent import workb
from pension_agent.consult_agent import memo, screens, tools
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.tools import MEMO_DEFAULT_TO, TOOL_REGISTRY

#: 근거 카드의 화면번호 표기. 답변이 이 표기를 그대로 인용했을 때만 그 화면을 가리킨 것으로 본다.
_SCREEN_IN_ANSWER = re.compile(r"\[\s*[0-9A-Za-z]{2}-[0-9A-Za-z]{2}-[0-9A-Za-z]{3}\s*\]")

_YES = ("네", "예", "웅", "응", "그래", "좋아", "열어", "연계", "해줘", "해주세요", "부탁", "보내",
        "ok", "yes")
_NO = ("아니", "괜찮", "나중", "취소", "안 열", "안열", "하지마", "no")


def _answer_screens(state: AgentState) -> list[str]:
    """답변이 실제로 인용한 화면번호 — **근거 카드에 있는 것만**(§10).

    답변 텍스트에서 화면번호 꼴을 찾은 뒤 원장의 값과 대조한다. 답변에서만 찾으면 LLM 이
    지어낸 번호로 링크를 만들게 되고, 원장에서만 찾으면 답변이 언급하지도 않은 다른
    절차의 화면을 열자고 제안하게 된다.
    """
    answer = state.get("answer") or ""
    known = {screens.normalize(s)
             for e in (state.get("evidence") or []) for s in e["atomic"]
             if _SCREEN_IN_ANSWER.fullmatch(s.strip())}
    seen: list[str] = []
    for m in _SCREEN_IN_ANSWER.finditer(answer):
        number = screens.normalize(m.group())
        if number in known and number not in seen:
            seen.append(number)
    return seen


def _propose(state: AgentState) -> dict[str, Any] | None:
    """이번 답변에 붙일 화면 연계 제안 하나. 조건이 아니면 None.

    **답변이 화면번호를 인용했을 때만이다.** 그것이 §10 의 "답변이 실제로 화면을 가리킨다"에
    해당하는 유일하게 확인 가능한 신호다 — 직원이 그 번호를 읽고 있다는 뜻이고, 열 화면도
    파라미터도 근거에서 나온다.

    한때 여기에 두 번째 갈래가 있었다: 답변 안에 따옴표로 감싼 15자 이상의 문장이 있으면
    LMS 발송 화면을 제안했다. 그 조건은 **화법 코칭 답변이면 거의 항상 참**이다 — 고객에게
    할 말을 큰따옴표로 쓰라고 작성 프롬프트가 지시하기 때문이다. 그래서 사후관리 방법을
    물었을 뿐인 턴에도 "발송 화면 열까요?"가 붙었고, 그러면 §10 이 경계한 바로 그 상태가
    된다 — 매 턴 붙는 제안은 직원이 읽지 않게 되고 확인 절차가 의미를 잃는다.

    문구를 보내려는 직원은 그렇게 말한다("이 문구로 LMS 보내줘"). 그 요청은 `lms_link` 가
    받아 같은 화면 연계를 제안한다 — 기능이 사라진 것이 아니라, **추측이 아니라 요청으로**
    시작하게 바뀐 것이다.
    """
    for number in _answer_screens(state):
        return {"kind": "screen", "label": f"{number} 화면 열기", "screen": number,
                "params": {"customer_id": state.get("customer_id") or ""}}
    return _propose_lms(state) or _propose_playbook(state)


#: 직원이 «쪽지로» 보내 달라고 말했는지의 판정어. 규칙이지 LLM 판단이 아니다(§10).
_MEMO_WORDS = ("쪽지",)

#: 수신자 사번의 꼴 — WorkB 사번은 7자리다(실측: 개발자 사번 3902172).
_EMP_NO = re.compile(r"(?<!\d)(\d{7})(?!\d)")

#: 그 7자리가 **사번으로 불린** 것인지의 단서. 숫자 꼴만으로는 사번과 금액이 갈리지 않는다.
#: 앞에 「사번」이 붙었거나, 뒤에 사람에게 붙는 조사가 붙은 경우만 사번으로 읽는다.
_EMP_BEFORE = re.compile(r"사번\s*[:：]?\s*$")
_EMP_AFTER = re.compile(r"^\s*(?:님)?\s*(?:한테|에게|께)")

#: 받는 사람을 모를 때. **묻지 않고 끝낸다** — 받을 사람이 없는 «보낼까요?»는 승낙받을
#: 대상이 없는 제안이다.
NO_RECIPIENT = ("쪽지를 보낼 받는 사람을 알 수 없어요 — 로그인 사번이 넘어오지 않았습니다. "
                "«사번 3902172한테 보내줘»처럼 사번을 알려주시면 그 앞으로 보낼게요.")


def employee_no(question: str) -> str | None:
    """직원이 말한 **수신자 사번**. 없으면 None(= 본인에게 보낸다).

    ━━ 사번을 받을 때만 타인 전송이다 ━━
    이름·부서로 사람을 찾아 보내는 WorkB 도구(`search_emp_and_send_memo`)가 있지만 쓰지
    않는다. 동명이인이 갈리는 자리를 검색에 맡기면 **엉뚱한 사람의 받은편지함에 고객 정보가
    남고**, 그건 확인 절차로도 못 막는다 — 직원은 자기가 승낙한 게 누구 앞인지 안 읽는다.
    사번은 직원이 직접 적은 것이라 그 책임이 갈리지 않는다.

    **숫자 꼴만으로 판정하지 않는다.** 7자리 숫자는 금액에도 나온다("5000000원"). 앞에
    「사번」이 붙었거나 뒤에 사람 조사(한테·에게·께)가 붙은 것만 사번으로 읽는다 — 못
    알아보면 본인에게 가고, 그건 되돌릴 수 있는 실패다(잘못 보내는 쪽은 아니다).
    """
    for m in _EMP_NO.finditer(question or ""):
        if _EMP_BEFORE.search(question[:m.start()]) or _EMP_AFTER.match(question[m.end():]):
            return m.group(1)
    return None


def _recipients(state: AgentState) -> tuple[list[str], str, bool]:
    """(받는 사람 사번, 화면에 밝힐 표기, 본인인가). 사번이 없으면 빈 목록이다.

    **정하는 것은 코드다.** 대화에서 LLM 이 사번을 뽑아내게 두면 문장 하나로 수신자가
    갈릴 수 있다(`memo.draft` 머리말과 같은 자리).
    """
    other = employee_no(state.get("question") or "")
    if other:
        return [other], f"사번 {other}", False
    mine = workb.employee_id(state.get("employee_id"))
    return ([mine], MEMO_DEFAULT_TO, True) if mine else ([], "", True)


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
    if not any(w in (state.get("question") or "") for w in _MEMO_WORDS):
        return False
    return bool(state.get("evidence")) and bool((state.get("answer") or "").strip())


def _memo_offer(state: AgentState) -> dict[str, Any]:
    """쪽지 초안을 세우고 «이대로 보낼까요?»를 묻는다(§10 의 제안·확인 형태).

    쪽지는 답변 자리를 **초안 그대로** 바꾼다 — 직원이 화면에서 읽고 승낙하는 것이 곧
    나가는 쪽지여야 한다. 보이는 것은 평문이고 나가는 것은 같은 글의 HTML 이다
    (`memo.Draft` 머리말 — 옮기는 것은 꼴뿐이다).

    **초안을 못 만들면 사유만 말하고 끝낸다.** 화면 답변에는 「걸리면 근거 원문을 내보낸다」는
    폴백이 있지만 쪽지에는 없다 — 근거 원문 덤프를 남의 받은편지함에 넣는 것은 답이 아니고,
    보낸 쪽지는 되돌릴 수 없다(루트 규칙 5).
    """
    ids, label, to_self = _recipients(state)
    if not ids:
        return {"answer": f"{state['answer']}\n\n— {NO_RECIPIENT}"}
    found, why = memo.draft(state, recipients=ids, to=label, to_self=to_self)
    if found is None:
        return {"answer": f"{state['answer']}\n\n— {why}"}
    action = {"kind": "memo", "label": f"이 쪽지 보내기(받는 사람: {label})",
              "prompt": f"이대로 쪽지를 보낼까요? 받는 사람은 {label}이에요. (네 / 아니오)",
              "title": found.title, "text": found.text, "html": found.html,
              "to": label, "recipients": list(found.recipients),
              "params": {"customer_id": state.get("customer_id") or ""}}
    return {"answer": f"[제목] {found.title}\n\n{found.text}\n\n— {action['prompt']}",
            "pending_action": action}


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
    if not state.get("customer_id") or not _playbook_reason(state):
        return None
    answer = state.get("answer") or ""
    for ev in state.get("evidence") or []:
        if ev["tool"] != "outreach":
            continue
        for key, item in _lms_items(ev):
            if not _mentions(answer, item["name"]):
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


def _mentions(answer: str, name: str) -> bool:
    """답변이 이 콘텐츠 이름을 불렀는가 — 조건 ③ 「답변이 그 콘텐츠를 실제로 가리켰다」의 판정.

    글자 그대로의 부분문자열 대조였던 동안 **제안이 엉뚱한 콘텐츠에 붙었다**(2026-09-03
    실측, 확정본 E1): 등록 이름은 「IRP 추가입금하고 절세혜택 챙기기 **이벤트**」인데 답변은
    끝의 «이벤트»를 떼고 「…챙기기 (9/30까지)」로 썼고, 같은 답변이 세미나 이름은 그대로
    옮겼다. 그래서 이벤트는 «언급 안 함»으로 탈락하고 발송 제안이 세미나에 붙었으며, 승낙
    턴이 ISA 만기 고객에게 자산배분 세미나 문자를 열었다. 답변이 이름을 부르는 방식(종류
    낱말 생략·공백 차이)은 LLM 이 정하는 표현이라 지시로 고정할 수 없다 — 대조 쪽이 그
    폭을 갖는다. 넓히는 것은 **끝의 종류 낱말과 공백**뿐이다. 이름의 앞부분을 잘라 부르는
    것은 여전히 «가리킨 것»이 아니다(후보를 늘어놓기만 한 답변에 붙이지 않는다는 조건 ③).
    """
    stem = name.strip()
    for kind in _CONTENT_KINDS:
        if stem.endswith(kind):
            stem = stem[: -len(kind)].strip()
            break
    squash = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    return bool(stem) and squash(stem) in squash(answer)


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
    hits = tools.playbook_hits(state, lanes=lanes, exclude=tools.cited_cards(state))
    if not hits:
        return None
    reason = _playbook_reason(state)
    what = "·".join(dict.fromkeys(_LANE_WORDS[c["_kind"]] for _s, c in hits))
    label = (f"이 고객 «{reason}» 상태에 걸린 {what} {len(hits)}건" if reason
             else f"이 고객 상태에 걸린 {what} {len(hits)}건")
    # 관련도까지 남긴다 — 승낙 턴은 카드를 다시 고르지 않고 이때 고른 것을 그대로 싣는다(§10).
    return {"kind": "pitch", "label": label,
            "cards": [{"id": c["id"], "score": round(score, 3)} for score, c in hits],
            "params": {"customer_id": state.get("customer_id") or ""}}


def _playbook_reason(state: AgentState) -> str:
    """제안 문구에 밝히는 «무엇에 걸렸는가». 요건 이름은 코드가 이미 아는 값이라 지어내지
    않는다 — 무엇 때문에 이 제안이 붙었는지 모르면 직원은 매번 열어봐야 한다."""
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
        return {}
    if _wants_memo(state):
        return _memo_offer(state)
    action = _propose(state)
    if not action:
        return {}
    # 끝의 「(네 / 아니오)」는 공통이다(transcript 재료가 기록에서 이 줄을 떼는 표지 —
    # tools._OFFER_TRAILER).
    ask = action.get("prompt") or f"{action['label']}, 연계해드릴까요? (네 / 아니오)"
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
        return {"answer": "직전에 제안드린 작업이 없어요. 무엇을 도와드릴까요?",
                "sources": [], "pending_action": None}

    text = (state.get("question") or "").strip().lower()
    said_no = any(k in text for k in _NO) and not any(text.startswith(k) for k in _YES)
    if said_no:
        return {"answer": f"{pending['label']}을 취소했어요.", "sources": [], "pending_action": None}
    if not any(k in text for k in _YES):
        # 애매한 답을 승낙으로 해석하지 않는다 — 제안을 유지한 채 다시 묻는다(§10).
        return {"answer": f"{pending['label']}을 진행할까요? '네' 또는 '아니오'로 답해 주세요.",
                "sources": [], "pending_action": pending}

    kind = pending.get("kind")
    if kind == "pitch":
        return _show_playbook(pending)
    if kind == "memo":
        return _send_memo(pending)
    return _link(pending)


def _send_memo(pending: dict) -> dict[str, Any]:
    """승낙받은 초안을 쪽지로 보내고 결과를 알린다(§10 「연계 결과를 알린다」).

    보내는 것은 제안한 턴이 남긴 것 그대로다 — 여기서 다시 쓰지 않는다. 답변에 본문을 다시
    싣지도 않는다 — 직원이 방금 읽고 승낙한 것이라, 반복하면 같은 글이 화면에 두 번 선다.

    **판정 못 한 결과를 «보냈다»로 접지 않는다**(workb.parse_result). WorkB 는 실패를
    본문에 담아 보내므로, 어댑터가 성공이라고 한 것만 보고 보고하면 거부당한 호출이
    «발송 완료»로 화면에 뜬다.
    """
    markup, title = (pending.get("html") or "").strip(), (pending.get("title") or "").strip()
    ids = [r for r in (pending.get("recipients") or []) if r]
    if not markup or not title or not ids:
        # 초안을 잃었으면 지어내지 않는다 — 무엇을 누구에게 보내기로 했는지 잃은 것이다.
        return {"answer": f"{pending['label']}을 다시 불러오지 못했어요. 한 번 더 부탁해 주세요.",
                "sources": [], "pending_action": None}
    to = pending.get("to") or MEMO_DEFAULT_TO
    result = TOOL_REGISTRY["send_memo"](
        (pending.get("params") or {}).get("customer_id") or "", markup,
        title=title, recipients=ids, to=to)
    if result.get("status") not in ("sent", "stubbed"):
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
    ev = tools.playbook_evidence(pending.get("label") or "고객 상태에 걸린 재료", hits)
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
            "accepted": pending.get("label") or "고객 상태에 걸린 재료"}


def _link(pending: dict) -> dict[str, Any]:
    """연계 결과를 알린다 — 어느 화면을 열었는지, 못 열었으면 왜인지(§10).

    **링크는 화면만 연다.** 딥링크가 받는 파라미터는 `scnNo`·`mode` 뿐이라(screens.py) 고객
    식별자도 문구도 URL 로 넘어가지 않는다 — 직원이 열린 화면에서 입력한다. 그러니 여기서
    "무엇을 채웠다"고 말하지 않는다. 채우지 않은 값을 채웠다고 말하는 답변이 링크가 없는
    것보다 나쁘다.
    """
    message = pending.get("message") or ""
    if pending.get("kind") == "lms":
        # 발송 화면으로 넘기는 문구는 코드가 한 가지를 거부한다 — 아직 실제 콘텐츠로
        # 확정되지 않은 더미 문구다. 화면을 열어 그 문구를 건네면 직원이 그대로 보낼 수
        # 있기 때문이고, 이 판정은 답변에 붙인 경고 문구가 아니라 코드가 한다(§10).
        gate = TOOL_REGISTRY["open_lms_screen"](
            (pending.get("params") or {}).get("customer_id") or "", message)
        if gate["status"] == "blocked":
            return {"answer": f"연계하지 않았어요. {gate['detail']}",
                    "sources": [], "pending_action": None}

    url = screens.link(pending.get("screen") or "")
    if not url:
        # 화면번호가 규격에 맞지 않으면 연계 대신 화면번호만 안내한다(§10).
        return {"answer": f"화면 연계를 만들지 못했어요. 화면번호 {pending.get('screen') or '미상'} "
                          "로 직접 이동해 주세요.",
                "sources": [], "pending_action": None}

    answer = f"{pending['label']} — {url}"
    if pending.get("kind") == "lms" and message:
        # 문구는 링크로 넘어가지 않으므로 직원이 화면에서 붙여넣도록 여기서 다시 준다.
        answer += f'\n화면이 열리면 이 문구를 넣어 주세요 — "{message}"'
    return {"answer": answer, "sources": [], "pending_action": None}
