"""화면 연계 — ⑨ 안내 콘텐츠(outreach) · 발송 화면 제안 · 단말 화면번호.

`test_agent.py::main` 이 호출(스텁 설치·정리 포함). 직접 실행 금지.
"""

from __future__ import annotations

import pathlib

from pension_agent import tools

from tests.agent._common import print  # noqa: A001 — 집계용 print


def check_outreach() -> int:
    """⑨ 안내 콘텐츠 — 대화 재료(outreach 도구)와 발송 화면 제안.

    ① 화면 ⑨ 산출이 대화 재료로 존재(일정·링크 포함)
    ② 따옴표 없이도 LMS 발송 화면 제안
    ③ 제안이 매 턴 붙지 않음
    """
    from pension_agent import tools
    from pension_agent.nodes import act
    from pension_agent.tools.customer_solution.customer import PERSONAS

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

    # 링크는 한 글자만 달라도 무효 — 원문 그대로
    hit = bool(ev["atomic"]) and all(a.startswith("http") for a in ev["atomic"])
    print(f"{'✓' if hit else '✗'} outreach: 안내 링크를 원문 스팬으로 선언한다", )
    ok += hit

    # 개수·열거 번호를 재료에 포함 — 없으면 «원장 밖 수치»로 생성문 폐기
    from pension_agent.evidence.verify import verify_texts
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

    # 넓힌 것은 «코드가 센 개수»뿐 — 재료 밖 수치는 여전히 잘림
    hit = not verify_texts("이 세미나는 연 7.2% 수익을 보장해요.", [ev["text"]])[0]
    print(f"{'✓' if hit else '✗'} outreach: 지어낸 수치는 그대로 걸린다")
    ok += hit

    # 고객 화면 닫힘 시 미성립 재료
    hit = (tools.run("outreach", {"question": "세미나 있어?"}, "세미나") is None
           and "outreach" not in tools.usable({}))
    print(f"{'✓' if hit else '✗'} outreach: 고객 화면이 닫혀 있으면 부를 수 없다")
    ok += hit

    # ② 답변이 콘텐츠를 가리키면 발송 화면 연계 제안
    name = (ev["meta"]["lms"].get("seminar") or ev["meta"]["lms"]["event"])["name"]
    offered = act.offer({**state, "evidence": [ev], "answer": f"«{name}» 를 안내해보세요."})
    pending = offered.get("pending_action")
    hit = (bool(pending) and pending["kind"] == "lms" and name in pending["label"]
           and pending["message"] == (ev["meta"]["lms"].get("seminar")
                                      or ev["meta"]["lms"]["event"])["message"])
    print(f"{'✓' if hit else '✗'} 답변이 가리킨 콘텐츠의 발송 화면을 제안한다(문구는 브리핑 산출 그대로)")
    ok += hit

    # ③ 답변이 아무것도 안 고르면 미부착 — 매 턴 제안 방지
    hit = not act.offer({**state, "evidence": [ev],
                         "answer": "열려 있는 세미나가 몇 건 있어요."}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 콘텐츠를 가리키지 않은 답변에는 제안이 붙지 않는다")
    ok += hit

    # ④ 이름 끝 종류 낱말(«이벤트»·«세미나»)을 떼고 불러도 가리킨 것
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

    # 이름 앞부분만은 미해당 — 넓힌 것은 끝 종류 낱말·공백뿐
    half = _stem[: max(len(_stem) // 2, 1)] if _stem else ""
    hit = bool(half) and not act.offer({**state, "evidence": [ev],
                                        "answer": f"{half}… 같은 게 있어요."}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 이름을 앞부분만 잘라 부른 답변에는 붙지 않는다")
    ok += hit

    # 이름을 바꿔 써도 링크 인용 시 가리킨 것(링크는 원문 스팬)
    url = (event or {}).get("url") or ""
    paraphrased = f"ISA 만기자금 IRP 이전 절세 행사가 있어요. ▶ {url}" if url else ""
    pending = act.offer({**state, "evidence": [ev], "answer": paraphrased}).get("pending_action") \
        if paraphrased else None
    hit = bool(url) and bool(pending) and pending["content_id"] == event["id"]
    print(f"{'✓' if hit else '✗'} 이름을 바꿔 써도 링크를 인용한 답변에는 그 콘텐츠의 제안이 붙는다")
    ok += hit

    # 요건 코드(isa·tax·add) 재료 노출 금지 — 답변이 그대로 옮김
    import re as _re
    _code = _re.compile(r"(?<![A-Za-z])[a-z]{3}:")
    _cust = tools.run("customer", {"customer_id": cid}, "현황")
    hit = not _code.search(text) and bool(_cust) and not _code.search(_cust["text"])
    print(f"{'✓' if hit else '✗'} outreach·customer 재료의 성립 요건에 요건 코드가 실리지 않는다")
    ok += hit

    # 원장에 outreach 근거가 없으면 미부착
    hit = not act.offer({**state, "evidence": [],
                         "answer": f"«{name}» 라는 세미나가 있어요."}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 원장에 안내 콘텐츠 근거가 없으면 제안하지 않는다")
    ok += hit

    # 문구는 화면 ⑨ 와 같은 값 — 대화가 새로 작성 금지
    from pension_agent.tools.customer_solution import solution as strategy_agent
    from pension_agent.tools.customer_solution import customer as strategy_customer
    facts = strategy_agent.facts(strategy_customer.get_profile(cid))
    hit = all(ev["meta"]["lms"][k]["message"] == facts["outreach"][k]["lms_message"]
              for k in ev["meta"]["lms"])
    print(f"{'✓' if hit else '✗'} 대화가 싣는 문구가 브리핑 ⑨ 의 문구와 같다")
    ok += hit

    # ⑤ 요건 일치 콘텐츠와 임박순 폴백 구분(판정: relevant_outreach)
    # 폴백을 «적합»으로 세우면 무관 문자 발송 경로
    from pension_agent.tools.customer_solution import support as strategy_support
    from pension_agent.tools.customer_solution import situations as strategy_situations
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
        # 폴백 문구는 발송 화면 제안 금지(이름을 그대로 불러도)
        fb_name = (facts_none := strategy_agent.facts(strategy_customer.get_profile(none_cid)).get("outreach") or {})
        fb_name = next((v["name"] for v in fb_name.values() if v), "")
        hit = (bool(none_ev) and not none_ev["meta"]["lms"]
               and not act.offer({**none_state, "evidence": [none_ev],
                                  "answer": f"«{fb_name}» 를 안내해보세요."}).get("pending_action"))
        print(f"{'✓' if hit else '✗'} 폴백 콘텐츠에는 발송 화면 제안이 붙지 않는다")
        ok += hit
        del facts_none
    # 요건 일치 고객(PERSONAS[0])은 «요건 일치: <요건 이름>» + 추천 사유
    hit = "요건 일치: " in text and "요건 일치: 없음" not in text and "지금 안내할 것 2건" in text
    print(f"{'✓' if hit else '✗'} 요건에 맞는 콘텐츠에는 «요건 일치: <요건 이름>»이 붙는다")
    ok += hit

    # ⑥ 인용된 발송 문구는 화법 대사와 분리해 `messages` 로(actions/messages.py)
    # 복사 값은 원본(답변이 줄바꿈을 이어 쓰기도 함)
    from pension_agent.actions import messages as lms_messages
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

    # outreach 재료 없는 턴의 문구도 `(광고)` 로 식별 — 복사 값은 본문 그대로
    short = "(광고) 김현수 고객님, KB국민은행입니다. 이벤트 확인해 보세요. 무료수신거부 080-XXX-XXXX"
    got = lms_messages.messages_in(f"줄인 문구예요.\n\"{short}\"", [])
    hit = len(got) == 1 and got[0]["text"] == short and got[0]["copy"] == short
    print(f"{'✓' if hit else '✗'} 재료 없는 턴의 발송 문구도 (광고) 접두로 알아보고 본문 그대로 복사한다")
    ok += hit

    # 폴백 문구 인용도 같은 형태(복사는 허용) — 재료에 원본 필요
    hit = (not none_cid) or bool(none_ev and none_ev["meta"]["messages"])
    print(f"{'✓' if hit else '✗'} 요건 무관 콘텐츠의 발송 문구도 재료가 원본을 싣는다")
    ok += hit

    # ⑦ 링크는 식별자 단위 대조 — 링크끼리 꼬리 번호(`001`) 공유
    from pension_agent.nodes import plan as _plan
    from pension_agent.evidence.verify import numbers as _numbers
    # 꼬리 번호가 겹치는 두 링크를 가진 고객을 로스터에서 탐색
    twin_ev, one, twin = None, "", ""
    for persona in PERSONAS:
        cand = tools.run("outreach", {"customer_id": persona.id, "question": "이벤트 있어?"}, "이벤트")
        urls = [a for a in (cand or {}).get("atomic", []) if a.startswith("http")]
        pair = next(((a, b) for a in urls for b in urls
                     if a != b and _numbers(a) & _numbers(b)), None)
        if pair:
            twin_ev, (one, twin) = cand, pair
            break
    answer = f"이 콘텐츠를 안내해보세요. ▶ {one}"
    hit = (twin_ev is not None and _plan._span_verdict(twin_ev, answer)[0] != _plan.DISCARD
           and _plan._span_verdict(twin_ev, answer + ".")[0] != _plan.DISCARD)
    print(f"{'✓' if hit else '✗'} 링크 하나만 정확히 인용한 답은 번호가 겹치는 다른 링크가 있어도 "
          f"버리지 않는다" + (f" ({one} · {twin})" if twin else " (겹치는 고객 없음)"))
    ok += hit
    urls = [a for a in ev["atomic"] if a.startswith("http")]
    one = urls[-1] if urls else ""
    # 원장에 없는 링크는 여전히 걸림 — 넓힌 것은 «안 부른 링크»뿐
    forged = one[:-1] + ("9" if not one.endswith("9") else "8")
    hit = bool(one) and _plan._span_verdict(ev, f"여기서 확인하세요 {forged}")[0] == _plan.DISCARD
    print(f"{'✓' if hit else '✗'} 원장에 없는 링크를 쓴 답은 버린다")
    ok += hit

    # ⑧ 다른 후보 중 요건 일치는 요건 표시, 요건 무관 폴백은 적합성 근거 금지
    # 요건 성립은 기준일 의존 — 특정 고객·날짜 대신 로스터 전체에서 성질 측정
    import re as _re2
    marked_total, wrong = 0, []
    for persona in PERSONAS:
        p_ev = tools.run("outreach", {"customer_id": persona.id, "question": "다른 건 없어?"}, "다른 후보")
        if not p_ev:
            continue
        p_facts = strategy_agent.facts(strategy_customer.get_profile(persona.id))
        relevant = {r["name"] for r in strategy_support.relevant_outreach(
            p_facts.get("problem_situations") or [], name=persona.nm)}
        cand = [ln.strip()[2:] for ln in p_ev["text"].splitlines()
                if ln.startswith("  · ")]
        marked = [c for c in cand if " · 요건 일치: " in c]
        marked_total += len(marked)
        wrong += [f"{persona.nm}: {c}" for c in cand
                  if (" · 요건 일치: " in c) != (c.split(" — ")[0] in relevant)]
        m = _re2.search(r"다른 후보 중 이 고객 요건에 맞는 것 (\d+)건", p_ev["text"])
        if (int(m.group(1)) if m else 0) != len(marked):
            wrong.append(f"{persona.nm}: 요약 수 {m.group(1) if m else 0} ≠ 표시 {len(marked)}")
    hit = marked_total > 0 and not wrong
    print(f"{'✓' if hit else '✗'} 다른 후보 중 이 고객 요건에 맞는 것만 요건 이름을 달고, 요약 줄이 그 수를 센다"
          + (f" (표시 {marked_total}건)" if not wrong else f" (어긋남: {wrong[:3]})"))
    ok += hit

    # 요건 무관 폴백 — 오세훈은 어느 기준일에도 이벤트 쪽이 폴백
    osh = next((p for p in PERSONAS if p.nm == "오세훈"), None)
    osh_ev = tools.run("outreach", {"customer_id": osh.id, "question": "다른 건 없어?"}, "다른 후보") \
        if osh else None
    osh_text = (osh_ev or {}).get("text", "")
    hit = ("주의: 요건 무관 콘텐츠의 문구다" in osh_text
           and "콘텐츠 대상 키워드(이 고객과 무관)" in osh_text
           and osh_text.count("매칭 키워드:") == 1)
    print(f"{'✓' if hit else '✗'} 요건 무관 폴백은 문구를 적합성 근거로 쓰지 말라고 적고, "
          f"키워드를 «매칭»이라 부르지 않는다")
    ok += hit
    # 요건 일치 콘텐츠(PERSONAS[0])에는 주의 미부착
    hit = "주의: 요건 무관" not in text
    print(f"{'✓' if hit else '✗'} 요건에 맞는 콘텐츠에는 요건 무관 주의가 붙지 않는다")
    ok += hit
    return ok


def check_screen_link() -> int:
    """화면 연계 — 제안 → 확인 → 연계.

    ① 발송 수행 금지 — 화면만 열어줌
    ② 절차 답변의 화면번호는 본문 딥링크(조회 화면엔 버튼 없음)
    ③ 확인 응답은 직전 제안에만 유효
    버튼은 링크로 대체 불가한 연계(LMS 발송 문구·플레이북 카드 내용)만.
    """
    from pension_agent.actions import screens
    from pension_agent.nodes import act
    from pension_agent.state import KB
    from pension_agent.common import session_store

    ok = 0
    talk = '이렇게 말해보세요. "고객님, 남은 세액공제 한도가 264만원 있어요. 연말이 지나면 사라집니다."'

    # ② 답변의 화면번호 → 딥링크, 근거 카드에 있는 번호만
    proc = {"tool": "procedure", "query": "q", "text": "블록", "atomic": ["[75-08-110]"],
            "notices": [], "notice_scopes": [], "allow": ["블록"], "marks": [],
            "sources": [], "meta": {}}
    answer_text = "[75-08-110] 화면에서 처리하시면 돼요."
    links = screens.links_in(answer_text, screens.declared([proc]), screens.names(KB))
    hit = (len(links) == 1 and links[0]["screen"] == "75-08-110"
           and links[0]["url"] == screens.link("75-08-110")
           # `screen` 은 본문 문자열 그대로 — 프론트가 찾아 감쌈
           and links[0]["screen"] in answer_text)
    print(f"{'✓' if hit else '✗'} 절차 화면번호가 실린 답변에 딥링크가 붙는다")
    ok += hit

    # 조회 화면은 «네/아니오» 버튼 없음 — 링크와 같은 일
    hit = not act.offer({"answer": answer_text, "evidence": [proc],
                         "customer_id": "TEST_ACT"}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 조회 화면은 링크로 끝낸다(승낙 버튼을 세우지 않는다)")
    ok += hit

    # 지어낸 번호는 링크 미생성
    hit = not screens.links_in("[99-99-999] 화면에서 처리하세요.",
                               screens.declared([proc]), screens.names(KB))
    print(f"{'✓' if hit else '✗'} 근거에 없는 화면번호로는 링크를 만들지 않는다")
    ok += hit

    # 대괄호 없는 번호도 같은 화면
    hit = [x["screen"] for x in screens.links_in(
        "75-08-110 개인고객용메시지발송등록 에서 보냅니다.",
        screens.declared([proc]), screens.names(KB))] == ["75-08-110"]
    print(f"{'✓' if hit else '✗'} 대괄호 없이 인용한 화면번호도 링크가 된다")
    ok += hit

    # ① 따옴표 대사만으로 발송 화면 제안 금지 — 화법 답변은 거의 항상 따옴표 포함
    hit = not act.offer({"answer": talk, "customer_id": "TEST_ACT"}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 대사가 있다는 이유만으로 발송 화면을 제안하지 않는다")
    ok += hit

    # 발송 요청 시 같은 화면 연계 제안
    from pension_agent.nodes import lms
    lms_pending = lms.lms_link(
        {"question": '"고객님, 남은 세액공제 한도가 264만원 있어요" 이 문구로 LMS 보내줘',
         "customer_id": "TEST_ACT"}).get("pending_action")
    hit = (bool(lms_pending) and lms_pending["kind"] == "lms"
           and lms_pending["screen"] == (screens.lms_screen(KB) or ("",))[0]
           and "264만원" in lms_pending["message"])
    print(f"{'✓' if hit else '✗'} 발송 요청은 발송 '화면'을 제안한다(발송이 아니다)")
    ok += hit

    # 발송 화면번호는 지식베이스 출처 — 하드코딩 금지
    found = screens.lms_screen(KB)
    hit = bool(found) and found[1].startswith("proc.")
    print(f"{'✓' if hit else '✗'} 발송 화면번호를 지식베이스 절차 카드에서 찾는다"
          + (f" — {found[0]} ({found[1]})" if found else ""))
    ok += hit

    # 조건 외 제안 금지 — 매 턴 붙으면 확인이 무의미
    hit = (not act.offer({"answer": talk, "customer_id": None})
           and not act.offer({"answer": "세액공제 한도는 900만원입니다.", "customer_id": "TEST_ACT"}))
    print(f"{'✓' if hit else '✗'} 고객 화면이 없거나 가리키는 화면이 없으면 제안하지 않는다")
    ok += hit

    # 승낙 시 URL 반환 + 연 화면·값 안내
    history = [{"question": "...", "pending_action": lms_pending}]
    yes = act.confirm_action({"question": "네 열어주세요", "history": history,
                              "customer_id": "TEST_ACT"})
    # URL 은 `links` 로 — 본문에 넣으면 프론트에 화면번호→URL 규칙 중복
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

    # 애매한 답은 승낙 아님 — 제안 유지 후 재질문
    maybe = act.confirm_action({"question": "음 글쎄요", "history": history,
                                "customer_id": "TEST_ACT"})
    hit = maybe["pending_action"] == lms_pending and "네' 또는 '아니오" in maybe["answer"]
    print(f"{'✓' if hit else '✗'} 애매하면 제안을 유지한 채 다시 묻는다")
    ok += hit

    # ③ 사이에 다른 질문이 오갔으면 제안 무효
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

    # 더미 문구 차단 게이트 — 등록 자산은 전부 비더미라 검사용 자산을 끼워 확인
    from pension_agent.tools.customer_solution import support
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

    # URL 은 화면번호 있을 때만
    hit = screens.link("") is None and \
        screens.link("[75-08-110]") == f"{screens.SCHEME}scnNo=7508110&mode={screens.MODE}"
    print(f"{'✓' if hit else '✗'} 화면번호가 없으면 링크를 만들지 않는다")
    ok += hit

    # 단말 딥링크 규격 `mystar-link://scnNo=...&mode=...`(구분자 `&`)
    # scnNo 는 화면호출번호 7자리 또는 단말화면번호 11자리 — 그 외 자릿수는 링크 미생성
    url = screens.link("[06-12-604]")
    hit = (url.startswith("mystar-link://scnNo=0612604&mode=")
           and url.split("mode=")[1] in screens.MODES
           and "?" not in url and url.count("&") == 1
           and screens.scn_no("[06-7E-001]") == "067E001"
           and screens.scn_no("06126041234") == "06126041234"
           and screens.link("[99-9]") is None)
    print(f"{'✓' if hit else '✗'} 딥링크는 scnNo(7·11자리)+mode 를 & 로 잇는다 — {url}")
    ok += hit

    # 규격 외 파라미터 금지 — 고객 식별자·문구는 직원이 화면에서 입력
    hit = (set(url.split("://")[1].split("&")) == {"scnNo=0612604", f"mode={screens.MODE}"}
           and "customer_id" not in (act.confirm_action(
               {"question": "네", "history": history, "customer_id": "TEST_ACT"})["answer"]))
    print(f"{'✓' if hit else '✗'} scnNo·mode 밖의 파라미터는 링크에 싣지 않는다")
    ok += hit

    # 정리는 main() 이 이번 실행 생성분만 — rmtree 금지(시연 픽스처 session_data 보존)
    return ok


def check_screen_registry() -> int:
    """단말 화면번호·비대면 채널 경로 재료 적재 — 06/05 화면번호 대응표(표A)·채널 경로(표B)."""
    from pension_agent.tools.kb_search.kb_index import buckets
    from pension_agent.state import KB

    ok = 0
    by_screen = {c["screen"]: c for c in KB.cards if c["_kind"] == "screen"}

    hit = len(by_screen) >= 80
    print(f"{'✓' if hit else '✗'} 화면번호 대응표가 지식베이스에 적재된다 ({len(by_screen)}건)")
    ok += hit

    card = by_screen.get("[06-12-604]")
    hit = bool(card) and card["title"] == "포트폴리오 운용현황 조회"
    print(f"{'✓' if hit else '✗'} 절차 본문이 언급하지 않는 화면도 있다 — [06-12-604]")
    ok += hit

    # 화면번호 질문 → 카드 도달
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

    # 화면명 밖 말로도 도달 — 표A «주요 기능» 칸이 입구
    # LLM 목록 한 줄은 예상질문 앞 2개만(kb_index._card_line) → 그 칸은 둘째 자리 필수
    from pension_agent.tools.kb_search.kb_index import _card_line
    pay = by_screen.get("[02-12-220]")
    line = _card_line(pay, 2) if pay else ""
    hit = (bool(pay) and "해지" in line
           and (pay.get("trigger_examples") or [""] * 2)[1].startswith("해지, 계좌이체"))
    print(f"{'✓' if hit else '✗'} 표A 「주요 기능」 칸이 둘째 검색 예시로 실려 화면명에 없는 말(해지)로도 닿는다")
    ok += hit

    # «같은 말» — 원문(표B) 괄호 병기 이름을 변환기가 파생 필드로(LLM 턴별 판단 차이 방지)
    from pension_agent.tools.adequacy import _headline
    dopt = by_screen.get("[06-12-610]")
    alias = "사전지정운용 = 디폴트옵션"
    hit = (bool(dopt) and alias in (dopt.get("aliases") or [])
           and "디폴트옵션" in _card_line(dopt, 2)
           and alias in tools._render_screen(dopt)
           and alias in _headline(dopt))
    print(f"{'✓' if hit else '✗'} 원문 표기만 있는 화면 카드에 «같은 말»이 붙어 카드 선택·게이트·재료에 보인다")
    ok += hit

    # 이미 직원 호칭을 쓰는 카드엔 미부착([06-12-918]). 원문 summary 수정 금지 — 병기는 검색 예시에만
    idle = by_screen.get("[06-12-918]")
    hit = (bool(idle) and not idle.get("aliases") and bool(dopt)
           and "디폴트옵션" not in (dopt.get("summary") or ""))
    print(f"{'✓' if hit else '✗'} «같은 말»은 이름이 빠진 카드에만, 원문 칸은 그대로 둔다")
    ok += hit

    # 원문 미병기 쌍 금지 — 동의어 생성 금지
    from scripts.kb_build import procedures as _procs
    hit = (_procs._attested_aliases("디폴트옵션(사전지정운용) 등록") == {"사전지정운용": "디폴트옵션"}
           and _procs._attested_aliases("사전지정운용제도 신청 · 디폴트옵션 대기자금") == {})
    print(f"{'✓' if hit else '✗'} 원문에 괄호 병기가 없는 «같은 말»은 붙이지 않는다")
    ok += hit

    # 업무 묶음은 기능 설명 아님(답변이 풀어 씀). 기능 칸이 화면명 반복이어도 줄은 유지
    idle_text = tools._render_screen(idle) if idle else ""
    hit = (bool(idle) and idle_text.splitlines()[0] == f"■ {idle['screen']} {idle['title']}"
           and f"· 표의 업무 묶음: {idle['group']}" in idle_text
           and f"· 무슨 화면인지: {idle['summary']}" in idle_text)
    print(f"{'✓' if hit else '✗'} 화면 재료가 업무 묶음을 기능 설명과 갈라 싣는다")
    ok += hit

    # 화면번호는 원문 그대로 요구
    hit = bool(found) and all(a.startswith("[") for a in found["atomic"])
    print(f"{'✓' if hit else '✗'} 화면번호는 원문 표기 그대로 요구한다(atomic)")
    ok += hit

    # 출처는 원천 문서 — 06/05 는 재배열 정리본
    hit = bool(found) and all(s.get("doc") for s in found["sources"])
    print(f"{'✓' if hit else '✗'} 화면 카드도 원천 문서로 출처를 말한다")
    ok += hit

    # 적재 종류는 전부 버킷에 — 누락 시 LLM 후보에서 사라짐
    bucketed = {c["id"] for b in buckets(KB).values() for c in b["cards"]}
    missing = [c["id"] for c in KB.cards if c["id"] not in bucketed]
    hit = not missing
    print(f"{'✓' if hit else '✗'} 새 종류가 버킷 카탈로그에 빠지지 않는다"
          + ("" if hit else f" — {missing[:3]}"))
    ok += hit

    # 원문 "현행 확인 필요" 표기 그대로
    stale = [c for c in by_screen.values() if c.get("status") == "확인 필요"]
    hit = bool(stale)
    print(f"{'✓' if hit else '✗'} 번호가 낡았을 수 있는 화면은 그 표기를 옮긴다 ({len(stale)}건)")
    ok += hit

    # 표B 비대면 채널 처리 경로 — screen 은 직원·단말, channel 은 고객·앱/웹
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

    # 메뉴명 변경 가능 원문 경고 유지 — 표시 여부는 데이터가 결정(코드 상수 금지)
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

    # 기준시점 하드코딩 금지 — 원문 변경 시 갈림
    src = "".join(f.read_text(encoding="utf-8")
                  for f in pathlib.Path(tools.__file__).parent.glob("*.py"))  # tools/ 패키지 전체
    hit = "2025.03.31" not in src and not hasattr(tools, "CHANNEL_MARK")
    print(f"{'✓' if hit else '✗'} 기준시점·경고 문구가 코드 상수로 남아 있지 않다")
    ok += hit

    # 24시간 원칙 예외(이용 가능 시간)도 같은 재료
    hours = [c for c in channels if c.get("hours")]
    hit = bool(hours) and all(not c.get("starbanking") for c in hours)
    print(f"{'✓' if hit else '✗'} 이용 가능 시간 예외도 함께 적재된다 ({len(hours)}건)")
    ok += hit

    # 이용시간 행에 "채널 목록에 없음" 금지
    hit = "해당 채널 목록에 없음" not in tools._render_channel(hours[0]) if hours else False
    print(f"{'✓' if hit else '✗'} 시간 표에서 온 행을 '채널에 없음'으로 말하지 않는다")
    ok += hit

    # 두 채널 모두 없는 업무는 미적재
    hit = all(c.get("starbanking") or c.get("ibank") or c.get("hours") for c in channels)
    print(f"{'✓' if hit else '✗'} 두 채널 모두 없는 행은 싣지 않는다")
    ok += hit

    # 화면 시효 표시도 channel 과 같은 규약 — 문구는 표A 머리말 ⚠(volatile), "현행 확인 필요" 화면에만
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

    # "확인 필요" 해소 비고를 부분문자열로 오독 금지
    solved = by_screen.get("[04-12-640]")
    hit = bool(solved) and solved.get("status") is None and not solved.get("volatile")
    print(f"{'✓' if hit else '✗'} 확인 필요 '해소' 비고를 경고로 뒤집어 읽지 않는다")
    ok += hit

    # 메뉴명도 검색 단서
    from pension_agent.knowledge.kb import retrieve
    menu_hits = retrieve(KB, kinds=["channel"], utterance="변경관리 퇴직연금 상품변경관리 메뉴",
                         top_k=3)
    hit = any("변경관리" in (c.get("starbanking") or "") for _s, c in menu_hits)
    print(f"{'✓' if hit else '✗'} 메뉴 이름으로도 채널 카드에 닿는다")
    ok += hit
    return ok
