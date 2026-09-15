"""WorkB 쪽지 — 이번 턴 재료로 쓴 쪽지의 제안·발송.

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

import json

from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import tools
from pension_agent.llm import LLMError

from tests.consult._common import print  # noqa: A001 — 집계용 print


def check_memo() -> int:
    """이번 턴의 재료 → WorkB 쪽지(§3 «이번 상담 대화» 재료 · §10 쪽지 제안·발송).

      ① `transcript` 는 **이번 세션만** 싣고 `history` 는 이번 세션을 뺀다 — 둘이 겹치면 방금
         한 말이 «지난 상담»이 되고, 둘 다 비면 요약할 재료가 없다.
      ② 기록에 붙은 제안 문구·도구 실행 줄은 재료에서 뗀다(안내가 아니라 화면 장치).
      ③ 제안은 «쪽지»를 말한 턴에만 붙는다. **고객 화면은 조건이 아니다** — 화면 유무는
         재료를 가르지(그 고객 / 오늘의 타겟 목록) 쪽지를 막지 않는다.
      ④ 받는 사람은 코드가 정한다 — 기본은 본인이고, **사번을 적었을 때만** 타인이다.
      ⑤ 본문·제목은 LLM 이 가이드라인 안에서 쓰고, 코드가 화면 답변과 **같은 검사**에 건다.
         걸리면 보내지 않고 사유를 말한다(폴백 없음). 꼴(HTML)은 코드가 만든다.
      ⑥ 승낙하면 제안한 턴의 초안 그대로 보내고 기록에 남긴다. 거절하면 보내지 않는다.
      ⑦ 화면에서는 초안을 코드블록으로 감싸고, 보내는 본문과 기록 재료에는 펜스가 없다.
    """
    import os
    import tempfile
    from pathlib import Path

    from pension_agent import note, session_store
    from pension_agent.consult_agent import actions as REG
    from pension_agent.consult_agent import memo, prompts
    from pension_agent.consult_agent.nodes import act
    from pension_agent.consult_agent.nodes import clarify as CL

    def _dead(*a, **kw):
        raise LLMError("no key")

    #: 가짜 작성기 — 규격(JSON)만 맞추고 재료 안 값만 쓴다. 실제 문장은 LLM 이 쓴다.
    def _writer(title: str, body: str):
        return lambda prompt, **kw: json.dumps({"title": title, "body": body},
                                               ensure_ascii=False)

    ok = 0
    orig_gen, orig_sender = memo.generate, note.SENDER
    orig_env = os.environ.get(note.EMP_NO_ENV)
    os.environ[note.EMP_NO_ENV] = "3902172"
    outbox: list[tuple] = []

    async def _send(ids, title, body):
        # 「누구 이름으로」는 발송 함수의 인자가 아니라 블록에 세워진 값이다 — 주입받은
        # 함수라 시그니처를 늘릴 수 없어서다(note 의 «누구 이름으로 나가나»).
        outbox.append((ids, title, body, note.acting_employee()))
        return '{"success": true}'

    note.use_sender(_send)
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = session_store.SESSION_DATA_DIR
        session_store.SESSION_DATA_DIR = Path(tmp)
        try:
            now, old = "s-now", "s-old"
            session_store.append_turn("CM", old, {"role": "user", "text": "지난 세션의 질문"})
            session_store.append_turn("CM", now, {"role": "user", "text": "과세이연 등록은 어떻게 해?"})
            session_store.append_turn("CM", now, {
                "role": "agent",
                "text": "[06-12-501] 후선업무 의뢰등록부터 해요. 60일 내 입금이에요.\n\n"
                        "— 06-12-501 화면 열기, 연계해드릴까요? (네 / 아니오)"})
            session_store.append_turn("CM", now, {"role": "tool", "text": "[발송 화면 연계] 문구"})
            # 앞선 쪽지 제안 턴이 기록에 남은 꼴 — 펜스와 제안 문구는 화면 장치라 재료에서 뗀다.
            session_store.append_turn("CM", now, {
                "role": "agent",
                "text": f"{memo.FENCE}\n앞선 쪽지 본문\n{memo.FENCE}\n\n"
                        "— 이 요약을 쪽지로 보낼까요? 받는 사람은 본인이에요. (네 / 아니오)"})
            state = {"question": "대화 내용 요약해서 쪽지로 보내줘", "customer_id": "CM",
                     "session_id": now}
            found = tools.run("transcript", state, "이번 상담 요약")
            past = tools.run("history", state, "지난 상담")
            closed = tools.run("transcript", {"question": "q", "session_id": now}, "요약")
            nosess = tools.run("transcript", {"question": "q", "customer_id": "CM"}, "요약")
            empty = tools.run("transcript", {"question": "q", "customer_id": "CM",
                                             "session_id": "s-new"}, "요약")

            summary = "직원이 과세이연 등록 절차를 물었고, [06-12-501] 등록부터 60일 내 입금까지 안내했어요."
            turn = {**state, "answer": summary, "evidence": [found]}

            memo.generate = _writer("과세이연 등록 상담 정리",
                                    "과세이연 등록 절차를 확인했어요.\n- 후선업무 의뢰등록부터 시작")
            offered = act.offer(turn)
            pending = offered.get("pending_action")
            plain = act.offer({**turn, "question": "지금까지 대화 내용 요약해줘"})
            shut = act.offer({**turn, "customer_id": None})
            noev = act.offer({**turn, "evidence": []})
            other = act.offer({**turn, "question": "이 내용 사번 3902173한테 쪽지로 보내줘"})
            # 로그인 사번이 넘어온 턴 — 받는 사람이 환경변수가 아니라 그 사번이다.
            login = act.offer({**turn, "employee_id": "3902174"})

            # 검증에 걸리는 초안 — 원장에 없는 수치를 쓴다. 보내지 않고 사유를 말한다.
            memo.generate = _writer("한도 정리", "세액공제 한도는 1,234만원이에요.")
            screened = act.offer(turn)
            # LLM 미연결 — «보냈다»로 접지 않는다.
            memo.generate = _dead
            down = act.offer(turn)
            memo.generate = _writer("과세이연 등록 상담 정리", "확인한 내용을 남겨둡니다.")

            history = [{"question": state["question"], "pending_action": pending}]
            # 승낙 턴에도 로그인 사번이 실려 온다 — 받는 사람은 제안한 턴이 정했고(3902172),
            # 보내는 사람은 이번 턴을 부른 직원(3902174)이다. 두 축이 갈리는 자리다.
            yes = act.confirm_action({"question": "응, 보내줘", "history": history,
                                      "customer_id": "CM", "employee_id": "3902174"})
            sent = [t for s2 in session_store.list_sessions("CM") for t in s2["turns"]
                    if any(c.get("name") == "send_memo" for c in (t.get("tool_calls") or []))]
            no = act.confirm_action({"question": "아니 괜찮아", "history": history, "customer_id": "CM"})
            sent_after_no = [t for s2 in session_store.list_sessions("CM") for t in s2["turns"]
                             if any(c.get("name") == "send_memo" for c in (t.get("tool_calls") or []))]

            # 진입점 → 상태 — 위 두 축(받는 사람·보내는 사람)이 성립하려면 사번이 여기까지
            # 와야 한다. x_client_user 는 사번 뒤에 접미가 붙어 올 수 있어 구분자 앞의
            # 사번을 읽고, 사번이 아닌 값(쿼터 버킷 이름)은 읽지 않는다.
            class _FakeAgent:
                seen: dict = {}

                def invoke(self, st):
                    _FakeAgent.seen = dict(st)
                    return {"answer": "답", "evidence": [], "sources": [], "intent": "situation"}

            orig_agent = G._AGENT
            G._AGENT = _FakeAgent()
            try:
                G.ask("질문", customer_id="CM", session_id="s-emp",
                      x_client_user="3902176-550e8400-e29b-41d4-a716-446655440000")
                by_client = _FakeAgent.seen.get("employee_id")
                G.ask("질문", customer_id="CM", session_id="s-x", x_client_user="pension-agent")
                by_bucket = _FakeAgent.seen.get("employee_id")
                G.ask("질문", customer_id="CM", session_id="s-y",
                      x_client_user="pension-agent", employee_id="3902177")
                by_explicit = _FakeAgent.seen.get("employee_id")
            finally:
                G._AGENT = orig_agent
            logged_emp = next((s2.get("employee_id") for s2 in session_store.list_sessions("CM")
                               if s2["session_id"] == "s-emp"), None)
        finally:
            session_store.SESSION_DATA_DIR = orig_dir
            memo.generate, note.SENDER = orig_gen, orig_sender
            if orig_env is None:
                os.environ.pop(note.EMP_NO_ENV, None)
            else:
                os.environ[note.EMP_NO_ENV] = orig_env

    # ① 시점으로 갈린다.
    hit = (bool(found) and "과세이연 등록은 어떻게 해?" in found["text"]
           and "지난 세션의 질문" not in found["text"]
           and found["sources"][0]["id"] == f"session.CM.{now}")
    print(f"{'✓' if hit else '✗'} transcript 는 이번 세션의 대화만 싣는다")
    ok += hit

    hit = bool(past) and "지난 세션의 질문" in past["text"] and "과세이연" not in past["text"]
    print(f"{'✓' if hit else '✗'} history 는 이번 세션을 빼고 싣는다(둘이 겹치지 않는다)")
    ok += hit

    # ② 화면 장치는 재료가 아니다 — 답변 안의 화면번호·기한은 남는다(요약이 옮길 값).
    hit = (bool(found) and "연계해드릴까요" not in found["text"] and "도구실행" not in found["text"]
           and "[06-12-501]" in found["text"] and "60일" in found["text"]
           and memo.FENCE not in found["text"] and "쪽지로 보낼까요" not in found["text"]
           and "앞선 쪽지 본문" in found["text"])
    print(f"{'✓' if hit else '✗'} 제안 문구·도구 실행 줄·코드블록 펜스는 떼고 답변 본문은 그대로 싣는다")
    ok += hit

    hit = closed is None and nosess is None and bool(empty) and tools.TRANSCRIPT_NONE in empty["text"]
    print(f"{'✓' if hit else '✗'} 고객·세션이 없으면 None, 세션은 있는데 0건이면 «기록 없음» 재료")
    ok += hit

    hit = ("transcript" in tools._NEEDS_CUSTOMER and "transcript" in CL._NO_BRANCH
           and "transcript" in prompts.ANSWER_SHAPES
           and "transcript" not in tools.catalog({}) and "transcript" in tools.catalog({"customer_id": "CM"}))
    print(f"{'✓' if hit else '✗'} 고객 전제 도구이고 갈래가 없으며 답의 형태 요구가 등록돼 있다")
    ok += hit

    # ③ 제안 조건 — «쪽지»를 말한 턴 · 재료가 있는 턴. 고객 화면은 조건이 아니다.
    hit = (bool(pending) and pending["kind"] == "memo"
           and not plain.get("pending_action") and not noev.get("pending_action")
           and bool(shut.get("pending_action")))
    print(f"{'✓' if hit else '✗'} «쪽지»를 말하고 재료가 있는 턴에만 붙는다 — 고객 화면은 조건이 아니다")
    ok += hit

    # ④ 받는 사람 — 코드가 정한다. 사번을 적었을 때만 타인이고, 금액 7자리는 사번이 아니다.
    hit = (act.employee_no("사번 3902172로 보내줘") == "3902172"
           and act.employee_no("3902172한테 쪽지 보내줘") == "3902172"
           and act.employee_no("3902172님께 보내줘") == "3902172"
           and act.employee_no("잔액이 5000000원인 고객 쪽지로 보내줘") is None
           and act.employee_no("쪽지로 보내줘") is None)
    print(f"{'✓' if hit else '✗'} 수신자 사번은 «사번» 이나 사람 조사가 붙었을 때만 읽는다(금액과 갈린다)")
    ok += hit

    hit = (pending["recipients"] == ["3902172"] and pending["to"] == REG.MEMO_DEFAULT_TO
           and (other.get("pending_action") or {}).get("recipients") == ["3902173"]
           and "3902173" in (other.get("pending_action") or {}).get("to", ""))
    print(f"{'✓' if hit else '✗'} 기본은 본인이고, 사번을 적으면 그 사번으로 간다")
    ok += hit

    # 「본인」이 누구인가 — 로그인 사번이 넘어오면 그 사람이고, 없을 때만 환경변수다.
    # 이 값은 진입점의 x_client_user·employee_id 에서 온다(graph.employee_no → 상태).
    hit = ((login.get("pending_action") or {}).get("recipients") == ["3902174"]
           and pending["recipients"] == ["3902172"])
    print(f"{'✓' if hit else '✗'} 「본인」은 로그인 사번이고, 없을 때만 환경변수로 떨어진다")
    ok += hit

    # ⑤ LLM 이 쓰고 코드가 검사한다 — 걸리면 «보내지 않고 사유». 꼴은 코드가 만든다.
    hit = (pending["title"] == "과세이연 등록 상담 정리"
           and "쪽지" not in pending["title"] and not any(c.isdigit() for c in pending["title"])
           and "<br>" in pending["html"] and "<b>" not in pending["text"]
           # 펜스는 화면 장치다 — 초안은 코드블록 안에 서고, 나가는 본문에는 없다.
           and memo.FENCE not in pending["text"] and memo.FENCE not in pending["html"]
           and offered["answer"].startswith(f"{memo.FENCE}\n[제목] 과세이연 등록 상담 정리")
           and f"\n{memo.FENCE}\n\n— " in offered["answer"]
           and offered["answer"].endswith("(네 / 아니오)"))
    print(f"{'✓' if hit else '✗'} 제목·본문은 LLM 이 쓰고 HTML 은 코드가 만든다(화면에는 코드블록 안 평문)")
    ok += hit

    hit = (not screened.get("pending_action") and "보내지 않았어요" in screened["answer"]
           and "1,234" in screened["answer"] and screened["answer"].startswith(summary))
    print(f"{'✓' if hit else '✗'} 근거 밖 수치가 있으면 보내지 않고 걸린 자리를 말한다(폴백 없음)")
    ok += hit

    hit = not down.get("pending_action") and "쓰지 못했어요" in down["answer"]
    print(f"{'✓' if hit else '✗'} LLM 이 죽으면 초안을 «보냈다»로 접지 않는다")
    ok += hit

    # 꼴 변환 — 태그는 이스케이프하고 마크다운 표는 걷어낸다(WorkB 가 렌더하지 않는다).
    made = memo.to_html("[고객 주요 정보]\n  들여쓴 줄\n\n<script>")
    hit = ("<b>[고객 주요 정보]</b>" in made and "&nbsp;&nbsp;들여쓴 줄" in made
           and "<script>" not in made and "&lt;script&gt;" in made
           and made.count("<br>") == 3
           and memo._clean_body("| 항목 | 값 |\n|---|---|\n| 잔액 | 1원 |") == "항목 · 값\n잔액 · 1원")
    print(f"{'✓' if hit else '✗'} 평문 → 쪽지 HTML: 소제목만 굵게 · 들여쓰기 보존 · 태그 이스케이프")
    ok += hit

    # ⑥ 승낙 → 초안 그대로 발송. 답변은 한 줄 — 본문을 반복하지 않는다. 거절 → 보내지 않는다.
    hit = (yes["pending_action"] is None and "쪽지를 보냈어요" in yes["answer"]
           and "\n" not in yes["answer"].strip()
           and len(sent) == 1 and len(outbox) == 1
           and outbox[0][:3] == (["3902172"], pending["title"], pending["html"])
           and sent[0]["tool_calls"][0]["args"]["title"] == pending["title"])
    print(f"{'✓' if hit else '✗'} '응, 보내줘' 면 초안 그대로 WorkB 로 보내고 상담이력에 남긴다")
    ok += hit

    # 받는 사람과 보내는 사람은 다른 축이다 — 받는 사람은 제안한 턴이 정해 초안에 적힌
    # 값이고(직원이 읽고 승낙한 값이라 다시 정하지 않는다), 보내는 사람은 이번 턴을 부른
    # 직원이다(MCP 인증에 들어가고 행내 감사 기록이 그 사번으로 남는다).
    hit = outbox[0][3] == "3902174" and outbox[0][0] == ["3902172"]
    print(f"{'✓' if hit else '✗'} 보내는 사람은 이번 턴의 로그인 사번이다(받는 사람과 다른 축)")
    ok += hit

    hit = by_client == "3902176" and by_bucket is None and by_explicit == "3902177"
    print(f"{'✓' if hit else '✗'} 진입점의 사번이 상태로 실린다 — x_client_user 는 사번 꼴일 때만"
          f" ({by_client} · {by_bucket} · {by_explicit})")
    ok += hit

    hit = logged_emp == "3902176"
    print(f"{'✓' if hit else '✗'} 상담이력 세션에 «누가 상담했나»가 남는다 ({logged_emp})")
    ok += hit

    hit = "취소" in no["answer"] and no["pending_action"] is None and len(sent_after_no) == 1
    print(f"{'✓' if hit else '✗'} '아니' 면 보내지 않는다")
    ok += hit

    # 코드가 붙이는 값 표 — 화면 유무가 무엇을 붙일지 가른다(종류가 아니라 화면이다).
    from pension_agent.strategy_agent import customer as CUST, engine
    who = CUST.PERSONAS[0]
    facts = engine.prepare(who)
    _kt, _kwhat = memo.table_for({"customer_id": who.id}, [])
    _tt, _twhat = memo.table_for({}, [{"tool": "targets"}])
    hit = (memo.KEY_INFO_HEADER in _kt and _kt.count("<tr>") == 6
           and str(facts["customer"]["평가금액"]) in _kt
           and str(facts["account_state"]["세액공제_잔여한도"]) in _kt
           and ":" not in _kt.split("관리 사유")[1].split("</tr>")[0]   # 요건 코드는 뺀다
           and _tt.startswith("<table ") and who.nm not in _twhat
           and not memo.table_for({}, [])[0])                          # 재료가 없으면 안 붙인다
    print(f"{'✓' if hit else '✗'} 값 표는 화면이 가른다 — 고객이 열렸으면 그 고객, 아니면 오늘의 목록")
    ok += hit

    # 꼬리말의 «선정 기준» 줄은 목록 표에만 붙는다 — 고객 한 명을 담은 쪽지에는 고를 목록이 없다.
    _foot_one, _foot_two = memo._footer_html(rule=False), memo._footer_html(rule=True)
    hit = (CUST.AS_OF.isoformat() in _foot_one and memo.today().isoformat() in _foot_one
           and note.FOOTER_RULE not in _foot_one and note.FOOTER_RULE in _foot_two)
    print(f"{'✓' if hit else '✗'} 기준일 안내는 늘 붙고 «선정 기준»은 목록 쪽지에만 붙는다")
    ok += hit

    # 조립식이던 동안은 화면 답변이 곧 쪽지 본문이라, LLM 이 얹은 도입 문장(2026-09-03 실측 —
    # 「쪽지 발송 여부는 시스템이 답변 뒤에 따로 안내해요」)을 코드가 항목 줄만 취해 걸렀다
    # (`memo.items_of`). 지금 화면 답변은 초안의 **출발점**일 뿐이고 본문은 쪽지 가이드라인이
    # 따로 쓰므로 그 거름은 사라졌다 — 대신 그 지시가 가이드라인 쪽에 서 있어야 한다.
    hit = ("쪽지로 보내드립니다" in prompts.MEMO_SYSTEM          # 쪽지 자신에 대해 쓰지 않는다
           and "복사해서 붙여넣으세요" in prompts.MEMO_SYSTEM
           and "인사말·맺음말" in prompts.MEMO_SELF_GUIDE       # 내 기록에는 도입·맺음이 없다
           and "인사" in prompts.MEMO_OTHER_GUIDE               # 남에게 보내는 쪽에는 한 줄 둔다
           and not hasattr(memo, "items_of"))                   # 부르는 곳 없는 거름을 남기지 않는다
    print(f"{'✓' if hit else '✗'} 쪽지 자신을 말하지 말라는 지시가 가이드라인 쪽에 서 있다")
    ok += hit

    shape = prompts.ANSWER_SHAPES["transcript"]
    hit = ("쪽지" in shape and "도입" in shape and "「- 물은 것 — 안내 요지」" in shape
           and "시스템이" not in shape and "답변 뒤에" not in shape)   # 옮겨 적힌 지시 문장(실측)
    print(f"{'✓' if hit else '✗'} 요약 형태 요구가 항목 줄만 쓰게 하고 쪽지·발송 언급을 금지하며, 시스템이 무엇을 하는지는 적지 않는다")
    ok += hit

    hit = ("send_memo" in REG.ACTIONS and "쪽지로 보내줘" in prompts.ROUTE_PROMPT
           and prompts.MEMO_SELF_GUIDE != prompts.MEMO_OTHER_GUIDE)
    print(f"{'✓' if hit else '✗'} 발송이 레지스트리에 있고, 가이드라인이 받는 사람으로 갈린다")
    ok += hit
    return ok
