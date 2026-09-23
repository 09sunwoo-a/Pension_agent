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
    from pension_agent.consult_agent.effects import actions as REG
    from pension_agent.consult_agent import prompts
    from pension_agent.consult_agent.effects import memo
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
            # 원장이 빈 쪽지 턴 — 「이거 사번 …한테 쪽지로 보내줘」에서 계획이 last_answer 를
            # 안 골라 재료 0건·NO_EVIDENCE 로 끝난 꼴. 직전 답변이 있으면 코드가 붙인다.
            from pension_agent.consult_agent.nodes import plan as PL
            bare = act.offer({"question": "이거 사번 3902173한테 쪽지로 보내줘", "customer_id": "CM",
                              "session_id": now, "evidence": [], "answer": PL.NO_EVIDENCE,
                              "history": [{"question": "과세이연 등록은 어떻게 해?",
                                           "answer": "[06-12-501] 후선업무 의뢰등록부터 해요."}]})
            # 직전 답변도 없으면 예전처럼 제안 없이 끝난다.
            bare_none = act.offer({"question": "이거 사번 3902173한테 쪽지로 보내줘",
                                   "customer_id": "CM", "session_id": now, "evidence": [],
                                   "answer": PL.NO_EVIDENCE, "history": []})

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
           and found["sources"][0]["id"] == f"session.{now}")
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

    # 직원이 실제로 쓰는 말 — 사번을 적었는데 본인에게 가던 꼴들(2026-09-22 개정).
    read = {
        "3902173 사번으로 보내줘": "3902173", "사번은 3902173이야, 쪽지로": "3902173",
        "3902173님 앞으로 쪽지 보내줘": "3902173", "김대리(3902173)한테 쪽지로": "3902173",
        "직원 3902173에게 쪽지": "3902173", "3902173번한테 쪽지 부탁해": "3902173",
        "담당자가 3902173인데 쪽지로 넘겨줘": "3902173",
        # 읽지 않는 것 — 단서 없는 맨숫자(«(으)로»는 금액과 안 갈린다) · 금액 · 사번이 둘
        "3902173 쪽지로 보내줘": None, "3902173으로 전달해줘": None,
        "5000000으로 보내줘": None, "사번 3902173 말고 3902174 둘 다": None,
        "사번 있는 고객, 잔액 5000000원 쪽지로": None, "3902173원으로 보내줘": None,
    }
    misses = {q: act.employee_no(q) for q, want in read.items() if act.employee_no(q) != want}
    hit = not misses
    print(f"{'✓' if hit else '✗'} 사번 단서를 직원 말대로 넓게 읽되 금액·맨숫자·둘 이상은 읽지 않는다"
          + (f" — {misses}" if misses else ""))
    ok += hit

    # 원장이 빈 쪽지 턴 — 직전 답변이 있으면 코드가 붙여 제안이 선다(사번 그대로). 없으면 예전대로.
    hit = ((bare.get("pending_action") or {}).get("recipients") == ["3902173"]
           and bare["answer"].startswith(memo.FENCE) and PL.NO_EVIDENCE not in bare["answer"]
           and not bare_none.get("pending_action"))
    print(f"{'✓' if hit else '✗'} 원장이 빈 쪽지 턴은 직전 답변을 코드가 재료로 붙인다(계획이 안 골라도)")
    ok += hit

    # 쪽지 요청이 lms_link 로 오분류돼도 그 노드로 가지 않는다 — LMS·문자를 말한 턴만 존중.
    from pension_agent.consult_agent import routing as RT
    hit = (RT.route_intent({"intent": "lms_link", "question": "사번 3902173한테 쪽지로 보내줘"}) == "plan"
           and RT.route_intent({"intent": "lms_link", "question": '"문구" 로 LMS 보내줘'}) == "lms_link"
           and RT.route_intent({"intent": "lms_link", "question": "이 문구 문자로 보내줘"}) == "lms_link")
    print(f"{'✓' if hit else '✗'} «쪽지» 턴은 lms_link 로 분류돼도 계획 루프로 간다")
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


def check_memo_edit() -> int:
    """쪽지 초안은 고칠 수 있다(§10 「쪽지 초안은 고칠 수 있다」 · 2026-09-23 실측).

    실측: 초안이 선 뒤 「앞에 --를 붙여줘」·「줄 바꿈하고 잘 정리해서」 → 「웅 쪽지 보내줘」가
    「직전에 제안드린 작업이 없어요」로 끝났고, 이어서 「저거 쪽지 내용 사번 …한테 쪽지 보내줘」는
    고친 글이 아니라 새로 쓴 글을 내밀었다.

      ① 초안이 걸려 있으면 분류와 무관하게 확인 노드로 간다
      ② 고치라는 말이면 **그 초안을** 고치고 제안을 다시 건다 — 발송되는 것은 직전 턴의 초안
      ③ 받는 사람은 말이 있을 때만 바뀐다(사번 · 본인을 가리키는 말). 본문은 그대로다
      ④ 직원이 적은 값은 원장과 달라도 들어가고, 아무도 안 적은 값은 걸린다 — 걸리면 직전 초안을 둔다
      ⑤ «이대로·똑같이» + 붙여넣은 글은 LLM·검사 없이 그대로 초안이 된다. «~식으로»는 다듬는다
      ⑥ 새 질문이면 초안을 무효로 하고 계획 루프로 넘긴다. 「요약해줘」의 «해줘»를 승낙으로 읽지 않는다
      ⑦ 짧은 승낙·거절은 LLM 을 부르지 않는다. 「아니 앞에 --」는 거절이 아니다
    """
    import os
    import tempfile
    from pathlib import Path

    from pension_agent import note, session_store
    from pension_agent.consult_agent import routing as RT
    from pension_agent.consult_agent.effects import memo
    from pension_agent.consult_agent.nodes import act

    ok = 0
    orig_gen, orig_sender = memo.generate, note.SENDER
    orig_env = os.environ.get(note.EMP_NO_ENV)
    os.environ[note.EMP_NO_ENV] = "3902172"
    outbox: list[tuple] = []
    calls: list[str] = []

    async def _send(ids, title, body):
        outbox.append((ids, title, body))
        return '{"success": true}'

    def _llm(obj: dict):
        def gen(prompt, **kw):
            calls.append(prompt)
            return json.dumps(obj, ensure_ascii=False)
        memo.generate = gen

    def _say(question: str, history: list[dict]) -> dict:
        return act.confirm_action({"question": question, "history": history, "customer_id": "CM"})

    note.use_sender(_send)
    # 발송 기록이 실제 상담이력 저장소에 남지 않게 한다(check_memo 와 같은 처리).
    tmp = tempfile.TemporaryDirectory()
    orig_dir = session_store.SESSION_DATA_DIR
    session_store.SESSION_DATA_DIR = Path(tmp.name)
    try:
        table = "<table><tr><td>평가금액</td><td>5,200만원</td></tr></table>"
        first, _ = memo.assemble("과세이연 상담 정리", "과세이연 등록 절차를 확인했어요.\n60일 내 입금",
                                 tail_html=table, tail_note="(아래에 고객 주요 정보 표가 붙습니다)",
                                 to="사번 3902173", recipients=["3902173"])
        t1 = act._offer_draft(first, {"customer_id": "CM"})
        hist = [{"question": "요약해서 사번 3902173한테 쪽지 보내줘", "pending_action": t1["pending_action"]}]

        routed = RT.route_intent({"intent": "correction", "question": "앞에 --를 붙여줘", "history": hist})

        _llm({"edit": True, "title": "과세이연 상담 정리",
              "body": "-- 과세이연 등록 절차를 확인했어요.\n-- 60일 내 입금"})
        t2 = _say("앞에 --를 붙여줘", hist)
        hist2 = [*hist, {"question": "앞에 --를 붙여줘", "pending_action": t2.get("pending_action")}]
        _llm({"edit": True, "title": "과세이연 상담 정리",
              "body": "-- 과세이연 등록 절차를 확인했어요.\n\n-- 60일 내 입금"})
        t3 = _say("줄 바꿈하고 잘 정리해서", hist2)
        hist3 = [*hist2, {"question": "줄 바꿈하고 잘 정리해서", "pending_action": t3.get("pending_action")}]
        # 「아니」로 시작해도 고치라는 말이면 고친다(짧은 거절만 거절이다).
        t_no_edit = _say("아니 앞에 --를 붙여줘", hist)

        before = len(calls)
        t4 = _say("웅 쪽지 보내줘", hist3)
        yes_calls = len(calls) - before
        sent_after_yes = list(outbox)

        _llm({"edit": False})
        t5 = _say("저거 쪽지 내용 사번 3902175한테 쪽지 보내줘", hist3)
        n_before_same = len(outbox)
        t6 = _say("저거 쪽지 내용 사번 3902173한테 쪽지 보내줘", hist3)
        sent_same = len(outbox) - n_before_same
        t7 = _say("그냥 나한테 보내줘", hist3)
        t_both = _say("나한테 말고 사번 3902175한테", hist3)

        _llm({"edit": True, "title": "과세이연 상담 정리",
              "body": "-- 과세이연 등록 절차를 확인했어요.\n\n-- 90일 내 입금"})
        t8 = _say("60일 말고 90일로 고쳐줘", hist3)
        _llm({"edit": True, "title": "과세이연 상담 정리",
              "body": "-- 과세이연 등록 절차를 확인했어요.\n\n-- 70일 내 입금"})
        t9 = _say("줄 간격 좀 넓혀줘", hist3)
        _llm({"edit": True, "ask": "기한을 며칠로 고칠까요?"})
        t10 = _say("기한 틀렸어 고쳐줘", hist3)

        before = len(calls)
        pasted = "오늘 과세이연 등록 건 확인 부탁드립니다. 60일 안에 입금돼야 해요."
        t11 = _say(f"이렇게 보내줘: {pasted}", hist3)
        paste_calls = len(calls) - before
        _llm({"edit": True, "title": "과세이연 상담 정리", "body": "확인 부탁드려요. 60일 안에 입금돼야 해요."})
        before = len(calls)
        t12 = _say(f"이런 식으로 변경해서: {pasted}", hist3)
        restyle_calls = len(calls) - before

        _llm({"edit": False})
        n_before_q = len(outbox)
        t13 = _say("지난 상담 요약해줘", hist3)
        sent_by_question = len(outbox) - n_before_q
        t14 = _say("취소", hist3)
    finally:
        session_store.SESSION_DATA_DIR = orig_dir
        tmp.cleanup()
        memo.generate, note.SENDER = orig_gen, orig_sender
        if orig_env is None:
            os.environ.pop(note.EMP_NO_ENV, None)
        else:
            os.environ[note.EMP_NO_ENV] = orig_env

    hit = routed == "confirm_action"
    print(f"{'✓' if hit else '✗'} 쪽지 초안이 걸려 있으면 분류와 무관하게 확인 노드로 간다")
    ok += hit

    p2, p3 = t2.get("pending_action") or {}, t3.get("pending_action") or {}
    hit = (p2.get("kind") == "memo" and p2["body"].startswith("-- ") and p3["body"].count("\n\n") == 1
           and t2["answer"].startswith(memo.FENCE) and t2["answer"].endswith("(네 / 아니오)")
           and table in p3["html"] and p3["tail_note"] == first.tail_note          # 값 표는 그대로
           and (t_no_edit.get("pending_action") or {}).get("body", "").startswith("-- "))
    print(f"{'✓' if hit else '✗'} 고치라는 말이면 그 초안을 고쳐 제안을 다시 건다(값 표는 그대로)")
    ok += hit

    hit = (yes_calls == 0 and "쪽지를 보냈어요" in t4["answer"] and len(sent_after_yes) == 1
           and sent_after_yes[0][0] == ["3902173"] and sent_after_yes[0][2] == p3["html"])
    print(f"{'✓' if hit else '✗'} 고친 뒤 「웅 쪽지 보내줘」는 LLM 없이 **고친 초안**을 보낸다")
    ok += hit

    p5, p7 = t5.get("pending_action") or {}, t7.get("pending_action") or {}
    hit = (p5.get("recipients") == ["3902175"] and p5.get("body") == p3["body"]
           and "사번 3902175" in p5.get("prompt", "") and sent_same == 1
           and p7.get("recipients") == ["3902172"] and p7.get("body") == p3["body"]
           and (t_both.get("pending_action") or {}).get("recipients") == ["3902173"]
           and (p2.get("recipients") == ["3902173"]))            # 말이 없으면 직전 수신자를 둔다
    print(f"{'✓' if hit else '✗'} 받는 사람만 바꾸면 고친 본문 그대로 다시 제안한다(같은 사번이면 보낸다 · 본인 전환 · 섞이면 안 바꾼다)")
    ok += hit

    p8, p9 = t8.get("pending_action") or {}, t9.get("pending_action") or {}
    hit = ("90일" in p8.get("body", "") and "60일" not in p8.get("body", "")
           and "반영하지 않았어요" in t9["answer"] and "70" in t9["answer"]
           and p9.get("body") == p3["body"])
    print(f"{'✓' if hit else '✗'} 직원이 적은 값은 들어가고, 아무도 안 적은 값은 걸려 직전 초안이 남는다")
    ok += hit

    hit = (t10["answer"] == "기한을 며칠로 고칠까요?"
           and (t10.get("pending_action") or {}).get("body") == p3["body"])
    print(f"{'✓' if hit else '✗'} 새 값 없이 고치라면 되묻고, 초안은 걸어 둔다")
    ok += hit

    p11, p12 = t11.get("pending_action") or {}, t12.get("pending_action") or {}
    hit = (paste_calls == 0 and p11.get("body") == pasted and p11.get("title") == p3["title"]
           and table in p11.get("html", "")
           and restyle_calls == 1 and p12.get("body") == "확인 부탁드려요. 60일 안에 입금돼야 해요.")
    print(f"{'✓' if hit else '✗'} «이렇게 보내줘: …»는 그대로, «이런 식으로 변경해서: …»는 다듬는다")
    ok += hit

    hit = (t13 == {"pending_action": None} and sent_by_question == 0
           and RT.route_confirm(t13) == "plan"
           and "취소" in t14["answer"] and t14["pending_action"] is None)
    print(f"{'✓' if hit else '✗'} 새 질문이면 초안을 무효로 하고 계획 루프로 넘긴다 · 「취소」는 취소")
    ok += hit

    # 짧은 승낙·거절 판정 — 「보내」가 들어 있다고 승낙이 아니다.
    yes = [act._norm(q) for q in ("네", "웅 쪽지 보내줘", "응, 보내줘", "이대로 보내줘", "좋아요", "보내")]
    not_yes = [act._norm(q) for q in ("앞에 --붙여서 보내줘", "사번 3902173한테 보내줘", "지난 상담 요약해줘")]
    no = [act._norm(q) for q in ("아니", "취소", "아니 괜찮아", "안 보내")]
    hit = (all(act._PLAIN_YES.match(q) for q in yes) and not any(act._PLAIN_YES.match(q) for q in not_yes)
           and all(act._PLAIN_NO.match(q) for q in no)
           and not act._PLAIN_NO.match(act._norm("아니 앞에 --를 붙여줘")))
    print(f"{'✓' if hit else '✗'} 짧은 승낙·거절만 코드가 바로 가른다(「앞에 --붙여서 보내줘」는 승낙이 아니다)")
    ok += hit
    return ok


def check_memo_schedule() -> int:
    """예약 쪽지(§10 「예약 발송」) — 발송 시각은 코드가 단서로 읽고, 맨 끝 실행기만 바뀐다.

      ① 단서가 붙은 날짜만 발송일이다 — 「10월 5일 만기 고객」은 쪽지 내용이다
      ② 후보가 둘이거나 지난 시각이면 초안은 세우되 «네»로 보내지 않고 언제인지 묻는다
      ③ 발송 시각은 받는 사람처럼 수정 턴을 지나도 남고, 단서로 바뀌고, 「지금 보내줘」로 지워진다
      ④ 실행기가 없으면 보내지 않는다(즉시 발송으로 접지 않는다)
      ⑤ 시연 실행기는 접수와 동시에 한 통을 보내고 «예약했어요»라고 답한다. 보내지 못하면
         «예약했어요»라고 말하지 않는다. 쪽지 본문에는 예약 표시가 없다
    """
    import os
    import tempfile
    from datetime import datetime
    from pathlib import Path

    from pension_agent import note, session_store
    from pension_agent.consult_agent.effects import actions as REG
    from pension_agent.consult_agent.effects import memo, schedule as S
    from pension_agent.consult_agent.nodes import act

    ok = 0
    now = datetime(2026, 9, 23, 14, 0)     # 수요일 오후 2시
    table = {
        "10월 5일에 쪽지 보내줘": ("ok", "10월 5일(월) 오전 9시"),
        "사번 3902173한테 10월 5일 오후 2시 반에 보내줘": ("ok", "10월 5일(월) 오후 2시 30분"),
        "10/5 오전 10시에 발송해줘": ("ok", "10월 5일(월) 오전 10시"),
        "내일 쪽지로 보내줘": ("ok", "9월 24일(목) 오전 9시"),
        "다음 주 월요일에 보내줘": ("ok", "9월 28일(월) 오전 9시"),
        "오후 3시에 보내줘": ("ok", "9월 23일(수) 오후 3시"),
        "예약은 10월 7일": ("ok", "10월 7일(수) 오전 9시"),
        "1월 5일에 보내줘": ("ok", "1월 5일(화) 오전 9시"),          # 지난 지 오래면 내년
        "10월 5일 만기 고객 정리해서 쪽지 보내줘": ("none", ""),     # 쪽지 내용의 날짜
        "쪽지 보내줘": ("none", ""),
        "10월 5일이나 6일에 보내줘": ("many", ""),
        "15일에 보내줘": ("many", ""),                                # 월 없는 날은 못 읽었다
        "오후 1시에 보내줘": ("past", ""),
        "9월 1일에 보내줘": ("past", ""),
    }
    misses = {q: (S.parse(q, now).kind, S.parse(q, now).label) for q, want in table.items()
              if (S.parse(q, now).kind, S.parse(q, now).label) != want}
    edit_ok = (S.parse("10월 6일로 바꿔줘", now, edit=True).label == "10월 6일(화) 오전 9시"
               and S.parse("10월 6일로 바꿔줘", now).kind == "none"
               and S.parse("그냥 지금 보내줘", now, edit=True).kind == "clear")
    hit = not misses and edit_ok
    print(f"{'✓' if hit else '✗'} 발송일은 단서가 붙은 날짜만 읽는다(내용 속 날짜·못 가르는 날·지난 시각)"
          + (f" — {misses}" if misses else ""))
    ok += hit

    orig_gen, orig_sender, orig_draft = memo.generate, note.SENDER, memo.draft
    orig_env = {k: os.environ.get(k) for k in (note.EMP_NO_ENV, REG.SCHEDULER_ENV)}
    os.environ[note.EMP_NO_ENV] = "3902172"
    os.environ.pop(REG.SCHEDULER_ENV, None)
    outbox: list[tuple] = []
    fail = {"on": False}

    async def _send(ids, title, body):
        outbox.append((ids, title, body))
        return '{"success": false, "error": "끊김"}' if fail["on"] else '{"success": true}'

    base, _ = memo.assemble("과세이연 상담 정리", "과세이연 등록 절차를 확인했어요.", tail_html="",
                            tail_note="", to="사번 3902173", recipients=["3902173"])
    memo.draft = lambda state, **kw: (memo.readdress(base, kw["recipients"], kw["to"]), "")
    note.use_sender(_send)
    tmp = tempfile.TemporaryDirectory()
    orig_dir = session_store.SESSION_DATA_DIR
    session_store.SESSION_DATA_DIR = Path(tmp.name)

    def _offer(q: str) -> dict:
        return act._memo_offer({"question": q, "customer_id": "CM", "answer": "요약", "evidence": [{}]})

    def _say(q: str, pending: dict) -> dict:
        return act.confirm_action({"question": q, "customer_id": "CM",
                                   "history": [{"question": "q", "pending_action": pending}]})

    try:
        o1 = _offer("사번 3902173한테 10월 5일에 쪽지 보내줘")
        o2 = _offer("10월 5일 만기 고객 정리해서 쪽지 보내줘")
        o3 = _offer("15일에 쪽지 보내줘")
        p1, p3 = o1["pending_action"], o3["pending_action"]
        yes_unclear = _say("네", p3)
        sent_unclear = len(outbox)

        memo.generate = lambda prompt, **kw: json.dumps({"edit": False})
        r_move = _say("10월 6일로 바꿔줘", p1)
        memo.generate = lambda prompt, **kw: json.dumps(
            {"edit": True, "title": "과세이연 상담 정리", "body": "-- 과세이연 등록 절차를 확인했어요."},
            ensure_ascii=False)
        r_edit = _say("앞에 --를 붙여줘", p1)
        r_now = _say("그냥 지금 보내줘", p1)
        r_set = _say("10월 7일에 보내줘", p3)

        sent_before = len(outbox)
        off = _say("네", p1)
        sent_off = len(outbox) - sent_before
        os.environ[REG.SCHEDULER_ENV] = "demo"
        on = _say("네", p1)
        sent_on = outbox[sent_before:]
        logged = [c for s2 in session_store.list_sessions("CM") for t in s2["turns"]
                  for c in (t.get("tool_calls") or []) if c.get("name") == "schedule_memo"]
        fail["on"] = True
        broke = _say("네", p1)
        fail["on"] = False
        plain_send = _say("네", o2["pending_action"])
    finally:
        session_store.SESSION_DATA_DIR = orig_dir
        tmp.cleanup()
        memo.generate, note.SENDER, memo.draft = orig_gen, orig_sender, orig_draft
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    hit = (p1["send_at"] == "2026-10-05T09:00" and p1["recipients"] == ["3902173"]
           and "10월 5일(월) 오전 9시에 보내도록 예약할까요" in o1["answer"]
           and not o2["pending_action"]["send_at"] and "이대로 쪽지를 보낼까요" in o2["answer"]
           and "10월 5일" not in p1["html"] and "예약" not in p1["html"])      # 본문에 예약 표시 없음
    print(f"{'✓' if hit else '✗'} 발송일이 읽히면 예약 제안이 서고, 내용 속 날짜는 즉시 발송 그대로다")
    ok += hit

    hit = (p3["when_unclear"] and act.WHEN_MANY in o3["answer"] and act.WHEN_ASK in o3["answer"]
           and yes_unclear["pending_action"]["when_unclear"] and sent_unclear == 0
           and (r_set["pending_action"] or {}).get("send_at") == "2026-10-07T09:00"
           and not r_set["pending_action"]["when_unclear"])
    print(f"{'✓' if hit else '✗'} 발송일을 못 가르면 «네»로 보내지 않고 묻고, 날짜를 말하면 예약 제안이 선다")
    ok += hit

    pm, pe, pn = r_move["pending_action"], r_edit["pending_action"], r_now["pending_action"]
    hit = (pm["send_at"] == "2026-10-06T09:00" and pm["body"] == p1["body"]
           and pe["send_at"] == p1["send_at"] and pe["body"].startswith("-- ")
           and pn["send_at"] == "" and "이대로 쪽지를 보낼까요" in r_now["answer"])
    print(f"{'✓' if hit else '✗'} 발송 시각은 수정 턴을 지나도 남고, 단서로 바뀌고, 「지금 보내줘」로 지워진다(보내지 않고 다시 제안)")
    ok += hit

    hit = (off["answer"] == act.SCHEDULE_OFF and sent_off == 0
           and on["answer"] == "10월 5일(월) 오전 9시에 보내도록 예약했어요 — 받는 사람: 사번 3902173."
           and len(sent_on) == 1 and sent_on[0][0] == ["3902173"] and sent_on[0][2] == p1["html"]
           and any((c["args"].get("executor") == "demo" and c["args"]["send_at"] == p1["send_at"])
                   for c in logged)
           and "예약했어요" not in broke["answer"] and "보내지 못했어요" in broke["answer"]
           and "쪽지를 보냈어요" in plain_send["answer"])
    print(f"{'✓' if hit else '✗'} 실행기가 없으면 보내지 않고, 시연 실행기는 즉시 한 통을 보내며 «예약했어요»·기록을 남긴다(실패면 말하지 않는다)")
    ok += hit
    return ok


def check_memo_by_name() -> int:
    """이름으로 보내기(§10 「이름으로 보내기」 · WorkB `search_emp_and_send_memo`).

      ① 받는 사람 이름·부서는 코드가 꼴로 읽는다 — 고객 이름·호칭·보통명사는 읽지 않는다
      ② 결과 판정은 셋이다 — 1명(이미 발송) · 여러 명(목록) · 0명. success 만 보고 접지 않는다
      ③ 1명이면 결과 문장에 응답의 사번을 밝힌다
      ④ 여러 명이면 보내지 않고 목록을 보여주며, 고른 사번으로 `send_memo` 를 쓴다(재검색 없음)
      ⑤ 0명이면 찾지 못했다고 말하고 초안을 걸어 둔다
      ⑥ 사번을 함께 적으면 사번으로 보낸다 · 시연 실행기는 이름 예약도 받는다
    """
    import os
    import tempfile
    from pathlib import Path

    from pension_agent import note, session_store
    from pension_agent.consult_agent.effects import actions as REG
    from pension_agent.consult_agent.effects import memo
    from pension_agent.consult_agent.nodes import act
    from pension_agent.strategy_agent import customer as CUST

    ok = 0
    customer_nm = CUST.PERSONAS[0].nm
    table = {
        "김국민에게 쪽지 보내줘": ("김국민", ""),
        "데이터시스템부(P) 김국민한테": ("김국민", "데이터시스템부(P)"),
        "미아동지점 김국민 차장님께 보내줘": ("김국민", "미아동지점"),
        "김국민 대리한테 전달해줘": ("김국민", ""),
        "박정호 고객 건 김국민한테 보내줘": ("김국민", ""),
        "남궁민수님께": ("남궁민수", ""),
        "고객에게 보내줘": None, "본인에게": None, "팀장님께 보내줘": None, "나한테 보내줘": None,
        "김대리한테 보내줘": None, "정리해서 쪽지 보내줘": None,
        f"{customer_nm}님께 보내줘": None,                                 # 시연 고객 이름
        "김국민이랑 이영희한테": act.NAME_MANY, "김국민한테, 이영희에게도": act.NAME_MANY,
    }
    misses = {q: act.recipient_name(q) for q, want in table.items() if act.recipient_name(q) != want}
    hit = not misses
    print(f"{'✓' if hit else '✗'} 받는 사람 이름·부서를 꼴로 읽고 고객·호칭·보통명사는 읽지 않는다"
          + (f" — {misses}" if misses else ""))
    ok += hit

    one = ('{"success": true, "data": {"message": "Successfully sent memo to 1 recipients.", '
           '"recipients": ["3901182"], "api_response": "0;OK"}}')
    many = ('{"success": true,"data": {"resultCode": 200,"resultMessage": "SUCCESS","resultData": ['
            '{"user_id": "5905382","group_name": "데이터시스템부(P)","dsgt": "대리"},'
            '{"user_id": "1631024","group_name": "미아동지점","dsgt": "차장"}]}}')
    none = '{"success": true,"data": {"resultCode": 204,"resultMessage": "NO CONTENT","resultData": null}}'
    parsed = [note.parse_name_result(x) for x in (one, many, none, '{"success": false, "error": "x"}', "?")]
    hit = ([r["status"] for r in parsed] == ["sent", "candidates", "not_found", "failed", "unknown"]
           and parsed[0]["recipients"] == ["3901182"] and len(parsed[1]["candidates"]) == 2)
    print(f"{'✓' if hit else '✗'} 이름 발송 결과는 1명(발송)·여러 명(목록)·0명으로 갈린다 — success 만 보지 않는다")
    ok += hit

    orig = (note.SENDER, note.NAME_SENDER, memo.draft)
    orig_env = {k: os.environ.get(k) for k in (note.EMP_NO_ENV, REG.SCHEDULER_ENV)}
    os.environ[note.EMP_NO_ENV] = "3902172"
    os.environ.pop(REG.SCHEDULER_ENV, None)
    calls: list[tuple] = []
    answer = {"raw": one}

    async def _by_name(name, group, title, body):
        calls.append(("name", name, group))
        return answer["raw"]

    async def _by_id(ids, title, body):
        calls.append(("id", ids))
        return '{"success": true}'

    note.use_name_sender(_by_name)
    note.use_sender(_by_id)
    base, _ = memo.assemble("상담 정리", "본문", tail_html="", tail_note="", to="x", recipients=[])
    memo.draft = lambda state, **kw: (memo.readdress(base, kw["recipients"], kw["to"]), "")
    tmp = tempfile.TemporaryDirectory()
    orig_dir = session_store.SESSION_DATA_DIR
    session_store.SESSION_DATA_DIR = Path(tmp.name)

    def _offer(q: str) -> dict:
        return act._memo_offer({"question": q, "customer_id": "CM", "answer": "요약", "evidence": [{}]})

    def _say(q: str, pending: dict) -> dict:
        return act.confirm_action({"question": q, "customer_id": "CM",
                                   "history": [{"question": "q", "pending_action": pending}]})

    try:
        o1 = _offer("김국민에게 쪽지 보내줘")
        p1 = o1["pending_action"]
        before_yes = len(calls)
        sent = _say("네", p1)
        answer["raw"] = many
        listed = _say("네", p1)
        pc = listed["pending_action"]
        yes_again = _say("네", pc)
        picked = _say("2번", pc)
        by_group = _say("데이터시스템부", pc)
        by_id = _say("사번 1631024로", pc)
        not_hour = act._pick("2시에 보내줘", pc["candidates"])
        n_before = len(calls)
        final = _say("네", picked["pending_action"])
        final_calls = calls[n_before:]
        answer["raw"] = none
        missing = _say("네", p1)
        with_id = _offer("김국민(3902172)한테 보내줘")
        os.environ[REG.SCHEDULER_ENV] = "demo"
        answer["raw"] = one
        sched = _offer("미아동지점 김국민 차장님께 10월 5일에 보내줘")
        sched_done = _say("네", sched["pending_action"])
    finally:
        session_store.SESSION_DATA_DIR = orig_dir
        tmp.cleanup()
        note.SENDER, note.NAME_SENDER, memo.draft = orig
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    hit = (p1["user_name"] == "김국민" and p1["recipients"] == []
           and o1["answer"].endswith("이대로 쪽지를 보낼까요? 받는 사람은 김국민 님이에요. (네 / 아니오)")
           and before_yes == 0                                            # 제안 턴에는 부르지 않는다
           and sent["answer"] == "쪽지를 보냈어요 — 받는 사람: 김국민 님(사번 3901182).")
    print(f"{'✓' if hit else '✗'} 승낙한 뒤에만 이름 발송을 부르고, 1명이면 받은 사람의 사번을 밝힌다")
    ok += hit

    hit = (listed["answer"] == ("김국민 님이 2명 있어요. 어느 분께 보낼까요?\n"
                                "1. 데이터시스템부(P) 대리 · 사번 5905382\n"
                                "2. 미아동지점 차장 · 사번 1631024")
           and yes_again["answer"] == listed["answer"]                    # 「네」로는 고른 게 아니다
           and picked["pending_action"]["recipients"] == ["1631024"]
           and "받는 사람은 미아동지점 김국민 님(사번 1631024)이에요" in picked["answer"]
           and by_group["pending_action"]["recipients"] == ["5905382"]
           and by_id["pending_action"]["recipients"] == ["1631024"] and not_hour is None
           and final_calls == [("id", ["1631024"])]                         # 재검색 없이 사번 발송
           and "미아동지점 김국민 님(사번 1631024)" in final["answer"])
    print(f"{'✓' if hit else '✗'} 여러 명이면 목록을 보여주고, 고른 사번으로 다시 제안해 사번으로 보낸다")
    ok += hit

    hit = (missing["answer"] == "김국민 님을 찾지 못했어요. 이름이나 부서를 다시 확인해 주세요."
           and (missing["pending_action"] or {}).get("user_name") == "김국민"
           and with_id["pending_action"]["recipients"] == ["3902172"]
           and not with_id["pending_action"]["user_name"]
           and "받는 사람은 김국민 님(사번 3902172)이에요" in with_id["answer"]
           and sched["pending_action"]["group_name"] == "미아동지점"
           and sched_done["answer"] == ("10월 5일(월) 오전 9시에 보내도록 예약했어요 — "
                                        "받는 사람: 미아동지점 김국민 님(사번 3901182)."))
    print(f"{'✓' if hit else '✗'} 0명이면 초안을 걸어 두고, 사번을 함께 적으면 사번으로, 시연 실행기는 이름 예약도 받는다")
    ok += hit
    return ok
