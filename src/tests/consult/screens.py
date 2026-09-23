"""화면 연계 — ⑨ 안내 콘텐츠(outreach) · 발송 화면 제안 · 단말 화면번호.

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

import pathlib

from pension_agent.consult_agent import tools

from tests.consult._common import print  # noqa: A001 — 집계용 print


def check_outreach() -> int:
    """⑨ 안내 콘텐츠 — 대화 재료(outreach 도구)와 발송 화면 제안(§10 예정 확장의 구현).

    회귀 대상:
    ① 화면 ⑨ 는 이벤트·세미나를 골라 두는데 그 산출이 **대화 쪽 재료로 없었다.** "이 고객한테
       보낼 만한 세미나 있어?"·"왜 이거야?"·"다른 건 없어?"가 전부 재료 0건으로 끝났고,
       문구를 다듬어 달라는 요청도 일정·링크가 원장에 없어 검증기에 잘렸다.
    ② LMS 발송 화면은 직원이 문구를 따옴표로 옮겨 적어야만(lms_link) 열렸다.
    ③ 그 제안이 **매 턴 붙지 않는가** — 예전 따옴표 휴리스틱 갈래가 지워진 이유다.
    """
    from pension_agent.consult_agent import tools
    from pension_agent.consult_agent.nodes import act
    from pension_agent.strategy_agent.customer import PERSONAS

    ok = 0
    cid = PERSONAS[0].id
    state = {"customer_id": cid, "question": "이 고객한테 안내할 세미나 있어?"}
    ev = tools.run("outreach", state, "안내할 세미나")

    hit = ev is not None and ev["tool"] == "outreach"
    print(f"{'✓' if hit else '✗'} outreach: 열려 있는 고객의 안내 콘텐츠를 재료로 낸다")
    ok += hit
    if ev is None:
        return ok

    text = ev["text"]
    hit = ("발송 문구:" in text and "다른 세미나 후보 4건:" in text
           and "매칭 키워드:" in text and "안내 링크:" in text
           and "지금 안내할 것 2건" in text)
    print(f"{'✓' if hit else '✗'} outreach: 문구·다른 후보·매칭 키워드·링크가 재료에 함께 실린다")
    ok += hit

    # 링크는 한 글자만 달라도 죽는다 — 답변이 그 값을 말하면 원문 그대로여야 한다.
    hit = bool(ev["atomic"]) and all(a.startswith("http") for a in ev["atomic"])
    print(f"{'✓' if hit else '✗'} outreach: 안내 링크를 원문 스팬으로 선언한다", )
    ok += hit

    # **개수와 열거 번호가 재료에 있어야 답이 살아남는다.**
    #
    # 회귀 대상(실측): 이벤트 1건 + 세미나 1건을 고른 답이 "2건을 추천드려요" 라고 쓰자
    # verify_texts 가 «원장 밖 수치 2» 로 판정해 **생성문을 통째로 폐기**했고, compose 가
    # 이 근거 블록을 그대로 덤프했다 — 직원에게 발송 문구·다른 후보·문제상황이 뒤섞인
    # 내부 블록이 답변으로 나갔다. 세는 것은 코드가 이미 아는 사실이라 재료에 싣는다
    # (`suitable` 이 「안내할 수 있는 상품 N종」을 싣는 것과 같은 처리).
    from pension_agent.verify import verify_texts
    _natural = ["김현수 고객님께는 2건을 추천드려요.",
                "이벤트 1건과 세미나 1건, 총 2건을 안내해보세요.",
                "1. 잠자는 IRP 자금 깨우기 운용 이벤트\n2. 예금만으로 괜찮을까?",
                "다른 이벤트 후보도 3건 더 있어요."]
    _killed = [t for t in _natural
               if not verify_texts(t, [ev["text"]], echoable=[state["question"]])[0]]
    hit = not _killed
    print(f"{'✓' if hit else '✗'} outreach: 개수·열거 번호를 쓴 답이 폐기되지 않는다"
          + (f" (잘린 것: {_killed})" if _killed else ""))
    ok += hit

    # 그렇다고 재료 밖 수치가 통과하면 안 된다 — 넓힌 것은 «코드가 센 개수» 하나뿐이다.
    hit = not verify_texts("이 세미나는 연 7.2% 수익을 보장해요.", [ev["text"]])[0]
    print(f"{'✓' if hit else '✗'} outreach: 지어낸 수치는 그대로 걸린다")
    ok += hit

    # 고객 화면이 닫혀 있으면 성립하지 않는 재료다(§3).
    hit = (tools.run("outreach", {"question": "세미나 있어?"}, "세미나") is None
           and "outreach" not in tools.usable({}))
    print(f"{'✓' if hit else '✗'} outreach: 고객 화면이 닫혀 있으면 부를 수 없다")
    ok += hit

    # ② 답변이 그 콘텐츠를 가리키면 발송 화면 연계를 제안한다.
    name = (ev["meta"]["lms"].get("seminar") or ev["meta"]["lms"]["event"])["name"]
    offered = act.offer({**state, "evidence": [ev], "answer": f"«{name}» 를 안내해보세요."})
    pending = offered.get("pending_action")
    hit = (bool(pending) and pending["kind"] == "lms" and name in pending["label"]
           and pending["message"] == (ev["meta"]["lms"].get("seminar")
                                      or ev["meta"]["lms"]["event"])["message"])
    print(f"{'✓' if hit else '✗'} 답변이 가리킨 콘텐츠의 발송 화면을 제안한다(문구는 브리핑 산출 그대로)")
    ok += hit

    # ③ 재료만 있고 답변이 아무것도 고르지 않았으면 붙지 않는다 — 매 턴 붙는 제안은
    # 직원이 읽지 않게 되고, 그게 §10 이 경계하는 상태다.
    hit = not act.offer({**state, "evidence": [ev],
                         "answer": "열려 있는 세미나가 몇 건 있어요."}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 콘텐츠를 가리키지 않은 답변에는 제안이 붙지 않는다")
    ok += hit

    # ④ 답변이 등록 이름 끝의 종류 낱말(«이벤트»·«세미나»)을 떼고 불러도 그 콘텐츠를
    # 가리킨 것이다. 글자 그대로 대조하던 동안 확정본 E1 에서 「…절세혜택 챙기기 (9/30까지)」가
    # «언급 안 함»으로 탈락하고, 같은 답변이 그대로 옮긴 세미나 이름에 제안이 붙었다 —
    # 승낙 턴이 ISA 만기 고객에게 자산배분 세미나 문자를 열었다(2026-09-03 실측).
    event = ev["meta"]["lms"].get("event")
    seminar = ev["meta"]["lms"].get("seminar")
    _stem = event["name"].removesuffix("이벤트").strip() if event else ""
    both = f"{_stem} (11/25까지)를 안내해보세요. 세미나는 «{seminar['name']}» 가 있어요." \
        if event and seminar else ""
    pending = act.offer({**state, "evidence": [ev], "answer": both}).get("pending_action") \
        if both else None
    hit = bool(pending) and pending["content_id"] == event["id"]
    print(f"{'✓' if hit else '✗'} 종류 낱말을 뗀 이벤트 이름도 가리킨 것으로 보고, "
          f"둘 다 불렀으면 이벤트를 먼저 제안한다")
    ok += hit

    # 이름의 앞부분만 잘라 부른 것은 여전히 «가리킨 것»이 아니다 — 넓힌 것은 끝의 종류
    # 낱말과 공백뿐이다.
    half = _stem[: max(len(_stem) // 2, 1)] if _stem else ""
    hit = bool(half) and not act.offer({**state, "evidence": [ev],
                                        "answer": f"{half}… 같은 게 있어요."}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 이름을 앞부분만 잘라 부른 답변에는 붙지 않는다")
    ok += hit

    # 이름을 바꿔 썼어도 **그 콘텐츠의 링크를 인용했으면** 가리킨 것이다(2026-09-07 실측,
    # 김서연 SE6 — 「ISA 만기자금, IRP로 이어가는 절세 이벤트」를 「…IRP 이전 절세 이벤트」로
    # 써서 제안이 빠졌고 다음 턴 승낙이 공중에 떴다). 링크는 원문 스팬이라 바꿔 쓸 수 없다.
    url = (event or {}).get("url") or ""
    paraphrased = f"ISA 만기자금 IRP 이전 절세 행사가 있어요. ▶ {url}" if url else ""
    pending = act.offer({**state, "evidence": [ev], "answer": paraphrased}).get("pending_action") \
        if paraphrased else None
    hit = bool(url) and bool(pending) and pending["content_id"] == event["id"]
    print(f"{'✓' if hit else '✗'} 이름을 바꿔 써도 링크를 인용한 답변에는 그 콘텐츠의 제안이 붙는다")
    ok += hit

    # 재료에 요건 코드(isa·tax·add)가 실리면 답변이 그대로 옮긴다(§5 「재료에 개발 용어를
    # 쓰지 않는다」) — 실측: 「세액공제 활용 가능(tax)과 추가입금 여력 보유(add) 요건」.
    import re as _re
    _code = _re.compile(r"(?<![A-Za-z])[a-z]{3}:")
    _cust = tools.run("customer", {"customer_id": cid}, "현황")
    hit = not _code.search(text) and bool(_cust) and not _code.search(_cust["text"])
    print(f"{'✓' if hit else '✗'} outreach·customer 재료의 성립 요건에 요건 코드가 실리지 않는다")
    ok += hit

    # 이번 턴이 안내 콘텐츠를 안 다뤘으면(원장에 outreach 근거가 없으면) 붙지 않는다.
    hit = not act.offer({**state, "evidence": [],
                         "answer": f"«{name}» 라는 세미나가 있어요."}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 원장에 안내 콘텐츠 근거가 없으면 제안하지 않는다")
    ok += hit

    # 문구는 대화가 새로 만들지 않는다 — 화면 ⑨ 와 같은 값이어야 같은 문자가 나간다.
    from pension_agent.strategy_agent import agent as strategy_agent
    from pension_agent.strategy_agent import customer as strategy_customer
    facts = strategy_agent.propose(strategy_customer.get_profile(cid))["facts"]
    hit = all(ev["meta"]["lms"][k]["message"] == facts["outreach"][k]["lms_message"]
              for k in ev["meta"]["lms"])
    print(f"{'✓' if hit else '✗'} 대화가 싣는 문구가 브리핑 ⑨ 의 문구와 같다")
    ok += hit

    # ⑤ 요건에 맞는 콘텐츠와 임박 순 폴백을 재료가 가른다.
    #
    # 회귀 대상(2026-09-07 실측, 최서윤): 이 고객 요건에 걸린 콘텐츠가 0건인데 화면 ⑨ 의
    # 임박순 폴백 2건이 요건에 맞는 것과 같은 모양으로 실렸고, 답변이 그것을 «이 고객에게
    # 적합»으로 세우고 사유까지 붙였다. 그 답 끝에 발송 화면 제안이 붙어 고객과 무관한
    # 문자가 나가는 경로가 됐다. 판정은 추천 질문 칩과 같은 함수(relevant_outreach)다.
    from pension_agent.strategy_agent import support as strategy_support
    from pension_agent.strategy_agent import situations as strategy_situations
    none_cid = next((p.id for p in PERSONAS if not strategy_support.relevant_outreach(
        strategy_situations.problem_situations(p))), None)
    hit = none_cid is not None
    print(f"{'✓' if hit else '✗'} 요건에 걸린 콘텐츠가 0건인 고객이 시연 로스터에 있다"
          + (f" ({none_cid})" if none_cid else ""))
    ok += hit
    if none_cid:
        none_state = {"customer_id": none_cid, "question": "이 고객한테 안내할 만한 세미나나 이벤트 있어?"}
        none_ev = tools.run("outreach", none_state, "안내할 세미나 이벤트")
        none_text = (none_ev or {}).get("text", "")
        hit = (bool(none_ev) and "지금 안내할 것 0건" in none_text
               and "요건 일치: 없음" in none_text and "추천 사유:" not in none_text
               and "요건에 맞는 콘텐츠는 없다" in none_text)
        print(f"{'✓' if hit else '✗'} 걸린 콘텐츠 0건이면 재료가 «0건»과 «요건 일치: 없음»을 적고 "
              f"추천 사유를 싣지 않는다")
        ok += hit
        # 폴백 문구로는 발송 화면을 제안하지 않는다 — 답변이 이름을 그대로 불러도.
        fb_name = (facts_none := strategy_agent.propose(
            strategy_customer.get_profile(none_cid))["facts"].get("outreach") or {})
        fb_name = next((v["name"] for v in fb_name.values() if v), "")
        hit = (bool(none_ev) and not none_ev["meta"]["lms"]
               and not act.offer({**none_state, "evidence": [none_ev],
                                  "answer": f"«{fb_name}» 를 안내해보세요."}).get("pending_action"))
        print(f"{'✓' if hit else '✗'} 폴백 콘텐츠에는 발송 화면 제안이 붙지 않는다")
        ok += hit
        del facts_none
    # 요건에 맞는 고객(PERSONAS[0])은 «요건 일치: <요건 이름>»이 붙고 추천 사유가 남는다.
    hit = "요건 일치: " in text and "요건 일치: 없음" not in text and "지금 안내할 것 2건" in text
    print(f"{'✓' if hit else '✗'} 요건에 맞는 콘텐츠에는 «요건 일치: <요건 이름>»이 붙는다")
    ok += hit

    # ⑥ 답변이 인용한 발송 문구는 화법 대사와 갈라 `messages` 로 실린다(effects/messages.py).
    #
    # 회귀 대상(2026-09-23 시연 화면): 발송 문구가 큰따옴표 인용이라 «고객에게 이렇게 말씀해
    # 보세요» 화법 블록으로 섰다 — 직원이 말할 대사와 발송 화면에 붙여 넣을 문자가 구분되지
    # 않았다. 답변은 문구의 줄바꿈을 한 줄로 이어 쓰기도 하므로, 복사 값은 원본으로 돌린다.
    from pension_agent.consult_agent.effects import messages as lms_messages
    original = ev["meta"]["messages"][0]
    flat = " ".join(original.split())
    answer = (f"이 대사로 먼저 말씀해 보세요. “노후 자금 한번 같이 점검해 보시죠.”\n\n"
              f"고객님께 보낼 발송 문구는 다음과 같습니다.\n\n“{flat}”")
    got = lms_messages.messages_in(answer, lms_messages.canonical([ev]))
    hit = (len(got) == 1 and got[0]["kind"] == "lms" and got[0]["text"] == flat
           and got[0]["copy"] == original and "\n" in got[0]["copy"])
    print(f"{'✓' if hit else '✗'} 발송 문구 인용만 messages 로 실리고(화법 대사는 빠진다), "
          f"복사 값은 줄바꿈이 살아 있는 원본이다")
    ok += hit

    # 원장에 outreach 재료가 없는 턴(「더 짧게」로 다시 쓴 턴)의 문구도 `(광고)` 로 알아본다 —
    # 원본이 없으니 복사 값은 본문 그대로다.
    short = "(광고) 김현수 고객님, KB국민은행입니다. 이벤트 확인해 보세요. 무료수신거부 080-XXX-XXXX"
    got = lms_messages.messages_in(f"줄인 문구예요.\n\"{short}\"", [])
    hit = len(got) == 1 and got[0]["text"] == short and got[0]["copy"] == short
    print(f"{'✓' if hit else '✗'} 재료 없는 턴의 발송 문구도 (광고) 접두로 알아보고 본문 그대로 복사한다")
    ok += hit

    # 폴백 콘텐츠의 문구도 인용되면 같은 꼴로 선다(발송 화면 제안과 달리 복사는 막지 않는다 —
    # 본문에 이미 떠 있는 문구다). 재료에 원본이 실려 있어야 한다.
    hit = (not none_cid) or bool(none_ev and none_ev["meta"]["messages"])
    print(f"{'✓' if hit else '✗'} 요건 무관 콘텐츠의 발송 문구도 재료가 원본을 싣는다")
    ok += hit
    return ok


def check_screen_link() -> int:
    """화면 연계 — 제안 → 확인 → 연계 (§10 · gap 14·15).

    회귀 대상:
    ① LMS 를 **발송까지 수행**하는 스텁이었고, 화면 URL·파라미터라는 개념이 없었다.
       에이전트는 화면을 열어줄 뿐 작업을 대신 수행하지 않는다.
    ② 절차 안내로 화면번호를 알려준 답변에는 화면으로 갈 길이 아예 없었다.
    ③ 확인 응답이 대화 이력에서 **가장 최근의** 제안을 찾아 실행했다 — 사이에 다른 질문이
       오간 뒤의 "네"도 몇 턴 전 제안을 실행할 수 있었다. 직원이 잊은 제안이 뒤늦게
       실행되는 것은 승낙이 아니다.

    ②의 답은 2026-09-17 에 «제안 버튼»에서 «본문 링크»로 바뀌었다(§10 개정). 조회 화면은
    딥링크가 하는 일과 버튼이 하는 일이 같아서, 승낙해도 같은 링크 하나가 돌아왔다.
    지금 버튼이 뜨는 것은 링크가 대체할 수 없는 연계뿐이다 — LMS 는 **발송 문구**를,
    플레이북은 **카드 내용**을 함께 건넨다.
    """
    from pension_agent.consult_agent.effects import screens
    from pension_agent.consult_agent.nodes import act
    from pension_agent.consult_agent.state import KB
    from pension_agent import session_store

    ok = 0
    talk = '이렇게 말해보세요. "고객님, 남은 세액공제 한도가 264만원 있어요. 연말이 지나면 사라집니다."'

    # ② 답변이 짚은 화면번호가 딥링크로 나간다 — 단, **근거 카드에 있는 번호만**.
    proc = {"tool": "procedure", "query": "q", "text": "블록", "atomic": ["[75-08-110]"],
            "notices": [], "notice_scopes": [], "allow": ["블록"], "marks": [],
            "sources": [], "meta": {}}
    answer_text = "[75-08-110] 화면에서 처리하시면 돼요."
    links = screens.links_in(answer_text, screens.declared([proc]), screens.names(KB))
    hit = (len(links) == 1 and links[0]["screen"] == "75-08-110"
           and links[0]["url"] == screens.link("75-08-110")
           # `screen` 은 본문에 그대로 있는 문자열이어야 한다 — 프론트가 그것을 찾아 감싼다.
           and links[0]["screen"] in answer_text)
    print(f"{'✓' if hit else '✗'} 절차 화면번호가 실린 답변에 딥링크가 붙는다")
    ok += hit

    # 조회 화면에는 «네/아니오» 버튼을 세우지 않는다 — 링크와 하는 일이 같다(§10 개정).
    hit = not act.offer({"answer": answer_text, "evidence": [proc],
                         "customer_id": "TEST_ACT"}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 조회 화면은 링크로 끝낸다(승낙 버튼을 세우지 않는다)")
    ok += hit

    # 답변이 지어낸 번호로는 링크를 만들지 않는다 — 직원이 엉뚱한 화면에서 작업하게 된다.
    hit = not screens.links_in("[99-99-999] 화면에서 처리하세요.",
                               screens.declared([proc]), screens.names(KB))
    print(f"{'✓' if hit else '✗'} 근거에 없는 화면번호로는 링크를 만들지 않는다")
    ok += hit

    # 대괄호 없이 쓴 번호도 같은 화면이다 — 표기 차이로 링크를 빠뜨리지 않는다.
    hit = [x["screen"] for x in screens.links_in(
        "75-08-110 개인고객용메시지발송등록 에서 보냅니다.",
        screens.declared([proc]), screens.names(KB))] == ["75-08-110"]
    print(f"{'✓' if hit else '✗'} 대괄호 없이 인용한 화면번호도 링크가 된다")
    ok += hit

    # ① 답변에 따옴표 친 대사가 있다는 이유로 발송 화면을 제안하지 않는다.
    #
    # 회귀 대상: 그 조건은 화법 코칭 답변이면 **거의 항상 참**이다 — 고객에게 할 말을
    # 큰따옴표로 쓰라고 작성 프롬프트가 지시하기 때문이다. 그래서 사후관리 방법을 물었을
    # 뿐인 턴에도 "발송 화면 열까요?"가 붙었고, 매 턴 붙는 제안은 직원이 읽지 않게 된다.
    hit = not act.offer({"answer": talk, "customer_id": "TEST_ACT"}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 대사가 있다는 이유만으로 발송 화면을 제안하지 않는다")
    ok += hit

    # 문구를 보내려는 직원은 그렇게 말한다 — 그 요청이 같은 화면 연계를 제안한다.
    from pension_agent.consult_agent.nodes import lms
    lms_pending = lms.lms_link(
        {"question": '"고객님, 남은 세액공제 한도가 264만원 있어요" 이 문구로 LMS 보내줘',
         "customer_id": "TEST_ACT"}).get("pending_action")
    hit = (bool(lms_pending) and lms_pending["kind"] == "lms"
           and lms_pending["screen"] == (screens.lms_screen(KB) or ("",))[0]
           and "264만원" in lms_pending["message"])
    print(f"{'✓' if hit else '✗'} 발송 요청은 발송 '화면'을 제안한다(발송이 아니다)")
    ok += hit

    # 발송 화면번호는 지식베이스에서 온다 — 코드에 박아두지 않는다.
    found = screens.lms_screen(KB)
    hit = bool(found) and found[1].startswith("proc.")
    print(f"{'✓' if hit else '✗'} 발송 화면번호를 지식베이스 절차 카드에서 찾는다"
          + (f" — {found[0]} ({found[1]})" if found else ""))
    ok += hit

    # 조건이 아니면 제안하지 않는다 — 매 턴 "연계해드릴까요?"가 붙으면 확인이 의미를 잃는다.
    hit = (not act.offer({"answer": talk, "customer_id": None})
           and not act.offer({"answer": "세액공제 한도는 900만원입니다.", "customer_id": "TEST_ACT"}))
    print(f"{'✓' if hit else '✗'} 고객 화면이 없거나 가리키는 화면이 없으면 제안하지 않는다")
    ok += hit

    # 확인 — 승낙이면 파라미터를 채운 URL 을 주고, 무엇을 어떤 값으로 열었는지 알린다.
    history = [{"question": "...", "pending_action": lms_pending}]
    yes = act.confirm_action({"question": "네 열어주세요", "history": history,
                              "customer_id": "TEST_ACT"})
    # URL 은 본문이 아니라 `links` 로 나간다 — 본문에 박아 두면 화면이 정규식으로 긁어야
    # 하고, 그러면 「화면번호 → URL」 규칙이 프론트에도 하나 생긴다.
    yes_links = yes.get("links") or []
    hit = (len(yes_links) == 1 and yes_links[0]["url"].startswith(screens.SCHEME)
           and screens.SCHEME not in yes["answer"]
           and "264만원" in yes["answer"]
           and "customer_id" not in yes["answer"] and yes["pending_action"] is None)
    print(f"{'✓' if hit else '✗'} '네' 면 화면 링크를 주고, 링크가 못 싣는 문구는 본문으로 알린다")
    ok += hit

    logged = session_store.list_sessions("TEST_ACT")
    hit = any(t.get("role") == "tool" for sess in logged for t in sess["turns"])
    print(f"{'✓' if hit else '✗'} 연계 호출이 상담이력에 남는다")
    ok += hit

    no = act.confirm_action({"question": "아니요 괜찮아요", "history": history,
                             "customer_id": "TEST_ACT"})
    hit = "취소" in no["answer"] and no["pending_action"] is None
    print(f"{'✓' if hit else '✗'} '아니오' 면 제안을 물린다")
    ok += hit

    # 애매한 답을 승낙으로 해석하지 않는다 — 제안을 유지한 채 다시 묻는다.
    maybe = act.confirm_action({"question": "음 글쎄요", "history": history,
                                "customer_id": "TEST_ACT"})
    hit = maybe["pending_action"] == lms_pending and "네' 또는 '아니오" in maybe["answer"]
    print(f"{'✓' if hit else '✗'} 애매하면 제안을 유지한 채 다시 묻는다")
    ok += hit

    # ③ 제안은 그 자리에서만 유효하다 — 사이에 다른 질문이 오갔으면 무효.
    stale = [{"question": "...", "pending_action": lms_pending},
             {"question": "그건 그렇고 세액공제 한도가 얼마야?"}]
    out = act.confirm_action({"question": "네", "history": stale, "customer_id": "TEST_ACT"})
    hit = "제안드린 작업이 없어요" in out["answer"] and out["pending_action"] is None
    print(f"{'✓' if hit else '✗'} 몇 턴 전 제안을 뒤늦은 '네'로 실행하지 않는다")
    ok += hit

    hit = "제안드린 작업이 없어요" in act.confirm_action(
        {"question": "네", "history": [], "customer_id": "TEST_ACT"})["answer"]
    print(f"{'✓' if hit else '✗'} 제안이 없으면 아무것도 열지 않는다")
    ok += hit

    # 더미 문구는 코드가 막는다 — 화면에 채우면 직원이 그대로 보낼 수 있기 때문이다.
    #
    # 등록된 안내 콘텐츠 9건은 연금사업부 DB 에서 와 전부 dummy 가 아니다. 그래서 검사용
    # 자산을 하나 끼워 넣어 확인한다 — 레지스트리에 더미가 남아 있을 때만 도는 검사였다면
    # 실데이터로 갈아탄 지금 **조용히 사라졌을** 자리다.
    from pension_agent.strategy_agent import support
    probe = {"id": "TEST-DUMMY", "name": "게이트 검사용 더미", "content_type": "이벤트",
             "url": "https://example.invalid/demo/gate-probe", "dummy": True}
    support.ASSETS.append(probe)
    try:
        blocked = act.confirm_action({
            "question": "네",
            "history": [{"question": "...", "pending_action": {
                **lms_pending,
                "message": support.lms_frame("검사", "안내드려요.", probe["url"])}}],
            "customer_id": "TEST_ACT"})
        hit = "연계하지 않았어요" in blocked["answer"] and screens.SCHEME not in blocked["answer"]
    finally:
        support.ASSETS.remove(probe)
    print(f"{'✓' if hit else '✗'} 더미 문구는 화면에 채우지 않는다(코드가 막는다)")
    ok += hit

    # URL 은 화면번호가 있을 때만 만든다.
    hit = screens.link("") is None and \
        screens.link("[75-08-110]") == f"{screens.SCHEME}scnNo=7508110&mode={screens.MODE}"
    print(f"{'✓' if hit else '✗'} 화면번호가 없으면 링크를 만들지 않는다")
    ok += hit

    # 단말 딥링크 규격 — `mystar-link://scnNo=...&mode=...`, 구분자는 `&`(§10).
    # scnNo 는 화면호출번호 7자리(지식베이스 표기에서 구분자를 뺀 것) 또는 단말화면번호
    # 11자리다. 자릿수가 맞지 않으면 링크를 만들지 않는다 — 단말이 엉뚱한 화면을 열거나
    # 아무것도 열지 못하는 링크를 직원에게 주지 않는다.
    url = screens.link("[06-12-604]")
    hit = (url.startswith("mystar-link://scnNo=0612604&mode=")
           and url.split("mode=")[1] in screens.MODES
           and "?" not in url and url.count("&") == 1
           and screens.scn_no("[06-7E-001]") == "067E001"
           and screens.scn_no("06126041234") == "06126041234"
           and screens.link("[99-9]") is None)
    print(f"{'✓' if hit else '✗'} 딥링크는 scnNo(7·11자리)+mode 를 & 로 잇는다 — {url}")
    ok += hit

    # 규격에 없는 파라미터는 싣지 않는다 — 단말이 받지도 않는 키를 붙이면 링크를 통째로
    # 못 읽을 수 있고, 고객 식별자·문구를 "채웠다"고 말하는 답변은 거짓이 된다.
    # 그 값들은 직원이 열린 화면에서 입력한다.
    hit = (set(url.split("://")[1].split("&")) == {"scnNo=0612604", f"mode={screens.MODE}"}
           and "customer_id" not in (act.confirm_action(
               {"question": "네", "history": history, "customer_id": "TEST_ACT"})["answer"]))
    print(f"{'✓' if hit else '✗'} scnNo·mode 밖의 파라미터는 링크에 싣지 않는다")
    ok += hit

    # 정리는 main() 이 «이번 실행이 만든 파일만» 지운다. 예전에는 여기서 디렉터리를
    # 통째로 rmtree 했는데, session_data 가 시연 픽스처를 담게 되면서(과거 상담 기록 —
    # scripts/seed_sessions.py) 테스트 한 번에 그 픽스처가 날아갔다.
    return ok


def check_screen_registry() -> int:
    """단말 화면번호·비대면 채널 경로를 묻는 질문에 답할 재료가 있는가.

    회귀 대상: 변환기가 06/05 의 절차 항목 74건만 읽고 그 위의 **화면번호 대응표 88행을
    통째로 건너뛰었다.** 그래서 절차 항목이 본문에서 언급하지 않는 화면은 지식베이스에
    존재하지 않았고, "포트폴리오 운용현황 조회 화면 번호는?"에 [06-12-604] 가 원문 표에
    버젓이 있는데도 "찾지 못했습니다"로 답했다.

    화면번호는 직원이 가장 자주 묻는 것 중 하나다(07/01 "화면번호·처리 순서까지 담는다").
    옮겨 적기만 하면 되는 재료가 적재되지 않은 채로 있었던 것이다.
    """
    from pension_agent.consult_agent.evidence.kb_index import buckets
    from pension_agent.consult_agent.state import KB

    ok = 0
    by_screen = {c["screen"]: c for c in KB.cards if c["_kind"] == "screen"}

    hit = len(by_screen) >= 80
    print(f"{'✓' if hit else '✗'} 화면번호 대응표가 지식베이스에 적재된다 ({len(by_screen)}건)")
    ok += hit

    card = by_screen.get("[06-12-604]")
    hit = bool(card) and card["title"] == "포트폴리오 운용현황 조회"
    print(f"{'✓' if hit else '✗'} 절차 본문이 언급하지 않는 화면도 있다 — [06-12-604]")
    ok += hit

    # 화면번호 질문이 그 카드에 닿는가.
    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        q = "포트폴리오 운용현황 조회 화면 번호는?"
        found = tools.run("screen", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(found) and "[06-12-604]" in (found.get("atomic") or [])
    print(f"{'✓' if hit else '✗'} screen 도구가 그 화면번호를 근거로 돌려준다")
    ok += hit

    # 화면명에 없는 말로도 닿아야 한다 — 표A «주요 기능» 칸이 검색 입구다(2026-09-16).
    # 「IRP 계좌 해지는 몇 번 화면에서 하지?」에 [02-12-220] 퇴직연금 지급(주요 기능: 해지,
    # 계좌이체, …)이 원문 표에 있는데 예시가 화면명뿐이라 screen 도구가 0건이었다(리허설
    # 케이스 2). LLM 이 보는 카드 목록 한 줄은 예상질문을 앞에서 2개만 싣으므로, 그 칸이
    # **둘째** 자리에 있어야 LLM 카드 선택이 그 말을 본다(kb_index._card_line).
    from pension_agent.consult_agent.evidence.kb_index import _card_line
    pay = by_screen.get("[02-12-220]")
    line = _card_line(pay, 2) if pay else ""
    hit = (bool(pay) and "해지" in line
           and (pay.get("trigger_examples") or [""] * 2)[1].startswith("해지, 계좌이체"))
    print(f"{'✓' if hit else '✗'} 표A 「주요 기능」 칸이 둘째 검색 예시로 실려 화면명에 없는 말(해지)로도 닿는다")
    ok += hit

    # «같은 말» — [06-12-610] 의 주요 기능 칸은 「사전지정운용제도 신청」이라고만 적는다.
    # 직원은 「디폴트옵션 등록 화면」으로 묻고, 그 둘이 같은지를 LLM 이 턴마다 따로 판단해
    # 답이 갈렸다(2026-09-23 — 첫 턴은 화면번호를 답하고, 다음 턴은 «자료로는 확인이
    # 어려워요», 대화가 쌓인 턴은 그 화면을 빼고 되물었다). 원문(표B)이 두 이름을 괄호로
    # 병기하므로 변환기가 파생 필드로 붙이고, 카드 선택·게이트·작성 재료가 전부 그것을 본다.
    from pension_agent.consult_agent.tools.adequacy import _headline
    dopt = by_screen.get("[06-12-610]")
    alias = "사전지정운용 = 디폴트옵션"
    hit = (bool(dopt) and alias in (dopt.get("aliases") or [])
           and "디폴트옵션" in _card_line(dopt, 2)
           and alias in tools._render_screen(dopt)
           and alias in _headline(dopt))
    print(f"{'✓' if hit else '✗'} 원문 표기만 있는 화면 카드에 «같은 말»이 붙어 카드 선택·게이트·재료에 보인다")
    ok += hit

    # 이미 직원이 부르는 이름을 쓰는 카드에는 붙이지 않는다 — [06-12-918] 디폴트옵션 대기자금 관리.
    # 원문 summary 는 손대지 않는다(루트 CLAUDE.md 규칙 1) — 괄호 병기는 검색 예시에만 있다.
    idle = by_screen.get("[06-12-918]")
    hit = (bool(idle) and not idle.get("aliases") and bool(dopt)
           and "디폴트옵션" not in (dopt.get("summary") or ""))
    print(f"{'✓' if hit else '✗'} «같은 말»은 이름이 빠진 카드에만, 원문 칸은 그대로 둔다")
    ok += hit

    # 원문이 병기하지 않은 쌍은 붙이지 않는다 — 코드가 동의어를 지어내지 않는다.
    from scripts.kb_build import procedures as _procs
    hit = (_procs._attested_aliases("디폴트옵션(사전지정운용) 등록") == {"사전지정운용": "디폴트옵션"}
           and _procs._attested_aliases("사전지정운용제도 신청 · 디폴트옵션 대기자금") == {})
    print(f"{'✓' if hit else '✗'} 원문에 괄호 병기가 없는 «같은 말»은 붙이지 않는다")
    ok += hit

    # 화면번호는 한 글자만 틀려도 없는 화면이라 원문 그대로 요구한다.
    hit = bool(found) and all(a.startswith("[") for a in found["atomic"])
    print(f"{'✓' if hit else '✗'} 화면번호는 원문 표기 그대로 요구한다(atomic)")
    ok += hit

    # 출처가 원천 문서를 가리킨다 — 06/05 는 재배열한 정리본이다.
    hit = bool(found) and all(s.get("doc") for s in found["sources"])
    print(f"{'✓' if hit else '✗'} 화면 카드도 원천 문서로 출처를 말한다")
    ok += hit

    # 적재되는 종류는 전부 버킷에 들어가야 한다 — 빠지면 LLM 후보 목록에서 사라진다.
    bucketed = {c["id"] for b in buckets(KB).values() for c in b["cards"]}
    missing = [c["id"] for c in KB.cards if c["id"] not in bucketed]
    hit = not missing
    print(f"{'✓' if hit else '✗'} 새 종류가 버킷 카탈로그에 빠지지 않는다"
          + ("" if hit else f" — {missing[:3]}"))
    ok += hit

    # 원문이 "현행 확인 필요"라 적어둔 화면은 그 표기를 그대로 옮긴다.
    stale = [c for c in by_screen.values() if c.get("status") == "확인 필요"]
    hit = bool(stale)
    print(f"{'✓' if hit else '✗'} 번호가 낡았을 수 있는 화면은 그 표기를 옮긴다 ({len(stale)}건)")
    ok += hit

    # ── 표B. 비대면 채널 처리 경로 — 같은 이유로 빠져 있던 61행 ──
    #
    # screen 과 나누는 기준은 **누가 하는가**다. screen 은 직원이 단말에서, channel 은
    # 고객이 앱·웹에서. 같은 업무라도 답이 다르고 묻는 사람도 다르다.
    channels = [c for c in KB.cards if c["_kind"] == "channel"]
    hit = len(channels) >= 50
    print(f"{'✓' if hit else '✗'} 비대면 채널 처리 경로가 적재된다 ({len(channels)}건)")
    ok += hit

    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        q = "고객이 스타뱅킹에서 직접 상품변경 하려면 어느 메뉴로 가나요"
        found = tools.run("channel", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(found) and "변경관리" in found["text"] and "KB스타뱅킹" in found["text"]
    print(f"{'✓' if hit else '✗'} channel 도구가 고객이 따라갈 메뉴 경로를 돌려준다")
    ok += hit

    # 메뉴명이 바뀔 수 있다는 원문 경고를 빼지 않는다 — 경로를 불러주는 자리다.
    #
    # 그리고 그 표시는 **데이터가 정한다**(§12 gap 16). 한때 코드 상수였는데, 그러면
    # 붙일지를 코드가 정하고(§7) 기준시점이 생성물과 코드 두 곳에 중복돼 갈린다 —
    # 갈리면 답변이 틀린 기준시점을 말한다.
    hit = bool(found) and any("메뉴명이 바뀔 수 있" in n for n in (found.get("notices") or []))
    print(f"{'✓' if hit else '✗'} 앱 개편으로 경로가 바뀔 수 있다는 표시가 함께 나간다")
    ok += hit

    sample = next((c for c in channels if c.get("volatile")), None)
    hit = bool(sample) and sample["volatile"] in tools.stale_mark(sample) \
        and sample.get("as_of") in tools.stale_mark(sample)
    print(f"{'✓' if hit else '✗'} 낡을 수 있다는 경고와 기준시점을 원문에서 읽어 온다")
    ok += hit

    hit = tools.stale_mark({"task": "x"}) is None
    print(f"{'✓' if hit else '✗'} 선언이 없는 재료에는 시효 표시를 붙이지 않는다")
    ok += hit

    # 기준시점이 코드에 박혀 있지 않은가 — 두 곳에 있으면 원문이 바뀔 때 갈린다.
    src = "".join(f.read_text(encoding="utf-8")
                  for f in pathlib.Path(tools.__file__).parent.glob("*.py"))  # tools/ 패키지 전체
    hit = "2025.03.31" not in src and not hasattr(tools, "CHANNEL_MARK")
    print(f"{'✓' if hit else '✗'} 기준시점·경고 문구가 코드 상수로 남아 있지 않다")
    ok += hit

    # 24시간 원칙의 예외(이용 가능 시간)도 같은 재료로 답한다.
    hours = [c for c in channels if c.get("hours")]
    hit = bool(hours) and all(not c.get("starbanking") for c in hours)
    print(f"{'✓' if hit else '✗'} 이용 가능 시간 예외도 함께 적재된다 ({len(hours)}건)")
    ok += hit

    # 이용시간 행에 "채널 목록에 없음"을 붙이지 않는다 — 되는 업무를 안 된다고 말하게 된다.
    hit = "해당 채널 목록에 없음" not in tools._render_channel(hours[0]) if hours else False
    print(f"{'✓' if hit else '✗'} 시간 표에서 온 행을 '채널에 없음'으로 말하지 않는다")
    ok += hit

    # 두 채널 모두 없는 업무는 애초에 싣지 않는다(비대면으로 못 하는 업무다).
    hit = all(c.get("starbanking") or c.get("ibank") or c.get("hours") for c in channels)
    print(f"{'✓' if hit else '✗'} 두 채널 모두 없는 행은 싣지 않는다")
    ok += hit

    # ── 화면 시효 표시도 channel 과 같은 규약이다(§12 지워진 gap 18) ──
    #
    # 문구는 표A 머리말의 ⚠ 에서 오고(volatile), 원문이 "현행 확인 필요"를 표기한 화면에만
    # 붙는다. 코드 상수면 원문 머리말이 바뀔 때 두 곳이 갈린다.
    hit = (bool(stale) and all(c.get("volatile") for c in stale)
           and all(not c.get("volatile") for c in by_screen.values()
                   if c.get("status") != "확인 필요"))
    print(f"{'✓' if hit else '✗'} 화면 시효 경고는 확인 필요 화면에만, 원문에서 읽어 온다")
    ok += hit

    hit = bool(stale) and stale[0]["volatile"] in (tools.stale_mark(stale[0]) or "") \
        and tools.stale_mark(stale[0]) in tools._render_screen(stale[0])
    print(f"{'✓' if hit else '✗'} 화면 렌더의 시효 표시가 volatile 선언에서 만들어진다")
    ok += hit

    hit = "번호가 낡았을" not in src
    print(f"{'✓' if hit else '✗'} 화면 시효 문구가 코드 상수로 남아 있지 않다")
    ok += hit

    # "확인 필요"가 **해소됐다**는 비고를 부분문자열로 뒤집어 읽지 않는다.
    solved = by_screen.get("[04-12-640]")
    hit = bool(solved) and solved.get("status") is None and not solved.get("volatile")
    print(f"{'✓' if hit else '✗'} 확인 필요 '해소' 비고를 경고로 뒤집어 읽지 않는다")
    ok += hit

    # 메뉴 이름도 검색 단서다 — 직원이 업무명이 아니라 메뉴명으로 물을 때가 있다.
    from pension_agent.knowledge.kb import retrieve
    menu_hits = retrieve(KB, kinds=["channel"], utterance="변경관리 퇴직연금 상품변경관리 메뉴",
                         top_k=3)
    hit = any("변경관리" in (c.get("starbanking") or "") for _s, c in menu_hits)
    print(f"{'✓' if hit else '✗'} 메뉴 이름으로도 채널 카드에 닿는다")
    ok += hit
    return ok
