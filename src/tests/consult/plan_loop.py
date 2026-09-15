"""계획 루프 — 도구 결합 · 재계획 · 턴 비용 · 실패 경로(계획 깨짐·LLM 다운·재작성).

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import tools
from pension_agent.consult_agent.nodes import plan, understand
from pension_agent.consult_agent.tools import pitch_slots
from pension_agent.llm import LLMError
from pension_agent.verify import numbers, verify_texts

from tests.consult._common import print, _REAL_EXTRACT_SLOTS  # noqa: A001 — 집계용 print


def check_turn_cost() -> int:
    """값 하나 묻는 턴이 LLM 을 몇 번 부르는가 — 직원은 상담 중에 이 화면을 읽는다.

    회귀 대상: "이 고객 예금 잔액 얼마지" 한 마디가 **순차 LLM 호출 6번**으로 끝났다.
    의도분류 → 화법 슬롯 분해 → 계획(도구 고르기) → 계획(끝났다고 말하기) → 되묻기 판정
    → 답변 작성. 이 중 셋은 이 질문에 아무것도 하지 않는다:

      · 화법 슬롯 분해 — `pitch` 를 부르지도 않는 턴인데 모든 턴 앞에 노드로 있었다.
      · 계획의 두 번째 호출 — 재료 하나로 끝나는데 "이제 됐다"를 따로 말하게 했다.
      · 되묻기 판정 — 열려 있는 고객의 재료에는 갈래가 없다(어느 고객인지가 정해져 있다).

    지연은 정확성과 맞바꾸는 것이 아니다. **하는 일이 없는 호출을 빼는 것**이다.
    """
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    ev_customer = {"tool": "customer", "query": "q", "text": "· 평가금액 2억 3,000만원",
                   "atomic": [], "notices": [], "notice_scopes": [], "marks": [],
                   "related": [], "allow": ["· 평가금액 2억 3,000만원"],
                   "sources": [{"id": "briefing.CX"}], "meta": {}}

    # ① 화법 슬롯 분해가 화법을 안 부르는 턴에서는 아예 안 돈다.
    called: list[str] = []
    orig_extract = pitch_slots.extract_slots
    pitch_slots.extract_slots = lambda st: called.append("slots") or {}
    orig_plan_gen = P.generate
    P.generate = lambda prompt, **kw: '{"tool": "customer", "query": "예금 잔액", "last": true}'
    try:
        state = {"question": "이 고객 예금 잔액 얼마지", "customer_id": "198734-1205842"}
        state.update(P.plan_step(state))
    finally:
        pitch_slots.extract_slots, P.generate = orig_extract, orig_plan_gen
    hit = not called
    print(f"{'✓' if hit else '✗'} 화법을 안 부르는 턴은 슬롯 분해 호출이 없다")
    ok += hit

    # ② 계획이 한 호출로 끝난다("last": true) — 그 도구가 실제로 재료를 내놨을 때만.
    hit = state.get("plan_done") is True and len(state.get("steps") or []) == 1
    print(f"{'✓' if hit else '✗'} 재료 하나로 끝나는 질문은 계획 호출 1번으로 끝난다")
    ok += hit

    # ③ 고객 재료만 있는 턴은 되묻기 판정을 돌리지 않는다.
    called.clear()
    orig_cl_gen = CL.generate
    CL.generate = lambda prompt, **kw: called.append("clarify") or '{"ask": null}'
    try:
        out = CL.clarify({"question": "이 고객 예금 잔액 얼마지", "evidence": [ev_customer]})
    finally:
        CL.generate = orig_cl_gen
    hit = not called and out == {}
    print(f"{'✓' if hit else '✗'} 갈래가 있을 수 없는 재료뿐이면 되묻기 판정을 돌리지 않는다")
    ok += hit

    # 지식 재료가 섞이면 판정은 그대로 돈다 — 아낀 것이 기능을 없앤 것이 아니다.
    ev_proc = {**ev_customer, "tool": "procedure", "text": "절차 블록"}
    called.clear()
    CL.generate = lambda prompt, **kw: called.append("clarify") or '{"ask": null}'
    try:
        CL.clarify({"question": "실물이전 절차", "evidence": [ev_customer, ev_proc]})
    finally:
        CL.generate = orig_cl_gen
    hit = called == ["clarify"]
    print(f"{'✓' if hit else '✗'} 지식 재료가 섞이면 되묻기 판정은 그대로 돈다")
    ok += hit

    # ④ 화법을 부르는 턴에는 슬롯 분해가 살아 있다(n-gram 폴백에 들어갈 때).
    called.clear()
    orig_extract, orig_pick = pitch_slots.extract_slots, tools.llm_pick
    orig_fits = tools.fits_question
    pitch_slots.extract_slots = lambda st: called.append("slots") or {}
    tools.llm_pick = lambda kinds, q: []
    tools.fits_question = lambda question, h, kind="", history=None, query=None, sink=None: h
    try:
        tools.run("pitch", {"question": "수수료 부담된다고 하시네요"}, "수수료 부담")
    finally:
        pitch_slots.extract_slots, tools.llm_pick = orig_extract, orig_pick
        tools.fits_question = orig_fits
    hit = called == ["slots"]
    print(f"{'✓' if hit else '✗'} 화법 도구가 n-gram 으로 물러설 때는 슬롯을 뽑는다")
    ok += hit

    # ⑤ 화법을 안 쓴 답변에 '파악된 상황' 줄을 붙이지 않는다(없는 상담 상황을 상상하게 둔다).
    hit = pitch_slots.situation_line("situation", {}) == "" and \
        "고객유형" in pitch_slots.situation_line("situation", {"customer_type": "사업자"})
    print(f"{'✓' if hit else '✗'} 슬롯이 없으면 '파악된 상황' 줄을 싣지 않는다")
    ok += hit
    return ok


def check_miss_recovery() -> int:
    """계획이 질의·도구를 잘못 골랐다고 **지식베이스에 있는 답이 사라지지 않는가.**

    회귀 대상 셋. 셋 다 "분명 있는 지식인데 못 찾는다"로 나타난다.

    ① `"last": true` 를 재료 없이도 존중했다. 계획이 도구 하나를 고르고 "이걸로 끝"이라고
       말했는데 그 도구가 근거를 못 찾으면, 다른 도구를 써 볼 기회 없이 턴이 '근거 없음'으로
       끝났다. 한 바퀴를 아끼는 것은 재료를 실제로 얻었을 때의 이야기다.
    ② 계획이 만든 질의는 질문을 줄여 쓴 것이라 검색이 기대는 말이 빠질 수 있다 —
       "포트폴리오 운용현황 조회 화면 번호는?"이 "운용현황 조회 화면번호"가 되면 n-gram 이
       0건을 낸다(원문으로는 찾는다). 못 찾으면 원문으로 한 번 더 찾는다.
    ③ '없다'가 무엇을 찾아봤는지 말하지 않았다. 그러면 직원도 우리도 왜 못 찾았는지
       알 수 없다 — 진단이 화면에서 끝나야 한다.
    """
    from pension_agent.consult_agent.nodes import plan as P
    from pension_agent.consult_agent.tools import procedure_qa

    ok = 0

    # ① 근거를 못 찾으면 last 를 존중하지 않는다.
    orig_gen, orig_run = P.generate, P.tools.run
    P.generate = lambda prompt, **kw: '{"tool": "procedure", "query": "없는 절차", "last": true}'
    P.tools.run = lambda name, state, query: None
    try:
        state = {"question": "질문"}
        state.update(P.plan_step(state))
        hit = not state.get("plan_done")
    finally:
        P.generate, P.tools.run = orig_gen, orig_run
    print(f"{'✓' if hit else '✗'} 도구가 근거를 못 찾으면 'last' 로 루프를 끝내지 않는다")
    ok += hit

    # 재료를 얻었으면 그대로 한 바퀴를 아낀다 — 고친 것이 최적화를 없앤 것이 아니다.
    P.generate = lambda prompt, **kw: '{"tool": "procedure", "query": "q", "last": true}'
    P.tools.run = lambda name, state, query: {"tool": name, "query": query, "text": "블록",
                                              "atomic": [], "notices": [], "notice_scopes": [],
                                              "marks": [], "related": [], "allow": ["블록"],
                                              "sources": [], "meta": {}}
    try:
        state = {"question": "질문"}
        state.update(P.plan_step(state))
        hit = state.get("plan_done") is True
    finally:
        P.generate, P.tools.run = orig_gen, orig_run
    print(f"{'✓' if hit else '✗'} 재료를 얻었으면 'last' 로 한 바퀴를 아낀다")
    ok += hit

    # ② 줄여 쓴 질의가 0건이면 직원의 원문 질문으로 한 번 더 찾는다.
    question = "포트폴리오 운용현황 조회 화면 번호는?"
    shrunk = "운용현황 조회 화면번호"
    orig_fits = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        hit = (not procedure_qa.search(shrunk)                       # 줄여 쓰면 0건인데
               and bool(procedure_qa.search(question))               # 원문으로는 찾고
               and tools.run("procedure", {"question": question}, shrunk) is not None)
    finally:
        tools.fits_question = orig_fits
    print(f"{'✓' if hit else '✗'} 계획의 질의가 0건이면 원문 질문으로 한 번 더 찾는다")
    ok += hit

    # 재검색은 **같은 도구·같은 지식베이스**다 — 근거의 경계를 넓히지 않는다.
    tried: list[str] = []
    orig_tool = tools.TOOLS["procedure"]
    tools.TOOLS["procedure"] = tools.Tool(
        "procedure", orig_tool.desc, lambda st, q: tried.append(q) or None)
    try:
        tools.run("procedure", {"question": question}, shrunk)
    finally:
        tools.TOOLS["procedure"] = orig_tool
    hit = tried == [shrunk, question]
    print(f"{'✓' if hit else '✗'} 재시도는 질의 하나만 늘린다(도구·지식베이스는 그대로)")
    ok += hit

    # 원문과 질의가 같으면 두 번 부르지 않는다.
    tried.clear()
    tools.TOOLS["procedure"] = tools.Tool(
        "procedure", orig_tool.desc, lambda st, q: tried.append(q) or None)
    try:
        tools.run("procedure", {"question": question}, question)
    finally:
        tools.TOOLS["procedure"] = orig_tool
    hit = tried == [question]
    print(f"{'✓' if hit else '✗'} 질의가 원문과 같으면 헛되이 두 번 부르지 않는다")
    ok += hit

    # ③ '없다'가 무엇을 찾아봤는지 말한다.
    answer = P._no_evidence({"steps": [{"tool": "procedure",
                                    "query": "운용현황 조회 화면번호", "outcome": "miss"}]})
    hit = "찾아본 곳" in answer and "운용현황 조회 화면번호" in answer
    print(f"{'✓' if hit else '✗'} '근거 없음'이 무엇을 어떤 말로 찾아봤는지 밝힌다")
    ok += hit

    hit = P._no_evidence({}) == P.NO_EVIDENCE
    print(f"{'✓' if hit else '✗'} 아무것도 안 불러본 턴에는 빈 '찾아본 곳'을 붙이지 않는다")
    ok += hit
    return ok


def check_replan_on_empty() -> int:
    """근거 0건인 채 계획이 끝나려 하면 **한 번은 다시 계획하는가**(§5).

    회귀 대상: "이 고객은 왜 타겟이 됐지?"(고객 화면 열림). 계획이 segment 를 골랐고
    (타겟 = 관리 대상 고객군이라는 말은 알아들었다) segment 가 0건을 냈는데, 원장에는
    성공한 재료만 실려서 계획은 자기가 뭘 불러봤는지 몰랐다 — 같은 호출을 반복하다
    반복 차단에 걸려, customer(왜 이 고객인가·판단근거를 들고 있는 도구)를 써 볼 기회
    없이 턴이 '근거 없음'으로 끝났다. 재료가 없는 것이 아니라 고르기를 실패한 것이다.

    고친 것 셋: ① 빗나간 호출이 계획 프롬프트에 실린다 ② 근거 0건인 채 끝내려 하면
    코드가 안 써 본 도구 목록과 함께 한 번 되돌려 보낸다(두 번째 끝내기는 존중 — 정직한
    '없음' 경로를 막지 않는다) ③ customer 도구 설명이 "왜 관리 대상(타겟)인가"를 말한다.
    """
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    ev_customer = {"tool": "customer", "query": "왜 타겟", "text": "· 왜 이 고객인가: 미운용 방치",
                   "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
                   "allow": ["· 왜 이 고객인가: 미운용 방치"],
                   "sources": [{"id": "customer.CX", "title": "고객 계좌 현황"}], "meta": {}}

    # ① 회귀 시나리오 그대로: segment 빗나감 → done → (재계획) → customer 로 답 재료 확보.
    prompts: list[str] = []
    script = ['{"tool": "segment", "query": "타겟 고객군 선정 조건"}',
              '{"done": true}',
              '{"tool": "customer", "query": "왜 타겟이 됐는지", "last": true}']
    orig_gen, orig_run = P.generate, P.tools.run
    P.generate = lambda prompt, **kw: prompts.append(prompt) or script.pop(0)
    P.tools.run = lambda name, state, query: ev_customer if name == "customer" else None
    try:
        state = {"question": "이 고객은 왜 타겟이 됐지?", "customer_id": "188406-7352194"}
        for _ in range(plan.MAX_STEPS + 2):
            state.update(P.plan_step(state))
            if state.get("plan_done"):
                break
    finally:
        P.generate, P.tools.run = orig_gen, orig_run
    used = [e["tool"] for e in state.get("evidence") or []]
    hit = used == ["customer"] and state.get("plan_done") is True
    print(f"{'✓' if hit else '✗'} 첫 도구가 빗나가도 재계획으로 customer 에 닿는다 → 원장 {used}")
    ok += hit

    # 빗나간 호출이 다음 계획 프롬프트에 보인다 — 원장에는 성공한 재료만 실리므로,
    # 이게 없으면 계획은 같은 호출을 반복한다.
    hit = len(prompts) == 3 and "segment:타겟 고객군 선정 조건" in prompts[1] \
        and "반복해도 소용없다" in prompts[1]
    print(f"{'✓' if hit else '✗'} 빗나간 호출이 계획 프롬프트에 실린다")
    ok += hit

    # 재계획 턴의 프롬프트는 아직 안 써 본 도구를 이름으로 보여준다.
    hit = len(prompts) == 3 and "아직 근거가 0건이다" in prompts[2] and "customer" in prompts[2]
    print(f"{'✓' if hit else '✗'} 재계획 지시가 안 써 본 도구(customer 포함)를 보여준다")
    ok += hit

    # ② 두 번째 done 은 존중한다 — 재계획이 정직한 '없음' 경로를 막지 않는다.
    P.generate = lambda prompt, **kw: '{"done": true}'
    try:
        st = {"question": "질문"}
        st.update(P.plan_step(st))
        retried = st.get("plan_retry") is True and not st.get("plan_done")
        st.update(P.plan_step(st))
    finally:
        P.generate = orig_gen
    hit = retried and st.get("plan_done") is True \
        and P.compose(st)["answer"] == P.NO_EVIDENCE
    print(f"{'✓' if hit else '✗'} 두 번째 done 은 존중 → 여전히 정직한 '근거 없음'")
    ok += hit

    # 근거를 모았으면 done 을 바로 존중한다 — 재계획은 0건일 때만이다.
    P.generate = lambda prompt, **kw: '{"done": true}'
    try:
        st2 = {"question": "질문", "evidence": [ev_customer],
               "steps": [{"tool": "customer", "query": "q", "outcome": "found"}]}
        st2.update(P.plan_step(st2))
    finally:
        P.generate = orig_gen
    hit = st2.get("plan_done") is True and not st2.get("plan_retry")
    print(f"{'✓' if hit else '✗'} 근거가 있으면 done 즉시 존중(재계획 없음)")
    ok += hit

    # ③ customer 도구 설명이 "왜 관리 대상(타겟)인가"를 말한다 — 도구 설명이 곧 계획의
    #    판단 재료라, 잔액·수익률만 말하면 이 질문이 segment 로 흘러간다.
    desc = tools.TOOLS["customer"].desc
    hit = "타겟" in desc and "왜" in desc
    print(f"{'✓' if hit else '✗'} customer 도구 설명이 선정 이유(타겟)를 말한다")
    ok += hit
    return ok


def check_progress() -> int:
    """진행 표시 — 실제로 시작한 일만, 코드가 정한 문구로, 상태에 흔적 없이 흘린다.

    답변 스트리밍은 못 한다(생성문이 게이트에서 통째로 폐기될 수 있다). 그래서 흘리는
    것은 진행이고, 규칙은 progress.py 머리말의 셋이다. 여기서는 ① 문구가 코드 소유인지
    (도구 선언 progress 라벨), ② 하지 않은 일을 알리지 않는지(재료 0건 턴에 '작성' 없음),
    ③ 콜백이 죽어도 답변이 사는지를 고정한다.
    """
    from pension_agent.consult_agent import progress as PROG
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    events: list[str] = []

    # ① 도구 실행이 도구 선언의 라벨로 알린다 — LLM 질의가 아니라.
    orig_tool = tools.TOOLS["screen"]
    tools.TOOLS["screen"] = tools.Tool("screen", orig_tool.desc, lambda st, q: None,
                                       progress=orig_tool.progress)
    try:
        with PROG.reporting(events.append):
            tools.run("screen", {"question": "질문"}, "LLM이 만든 질의")
    finally:
        tools.TOOLS["screen"] = orig_tool
    hit = events == ["단말 화면번호를 찾고 있어요"]
    print(f"{'✓' if hit else '✗'} 도구 진행 표시는 코드 선언 라벨로 찍힌다 — {events}")
    ok += hit

    # ①-보조 조사는 받침으로 갈린다 — 병기("을(를)")를 화면에 내보내지 않는다.
    hit = (PROG.object_of("상담 화법") == "상담 화법을"
           and PROG.object_of("업무 처리 절차") == "업무 처리 절차를"
           and PROG.object_of("IRP") == "IRP을(를)")   # 한글 아니면 병기로 물러선다
    print(f"{'✓' if hit else '✗'} 진행 문구의 목적격 조사가 받침에 맞게 붙는다")
    ok += hit

    # ② 재료 0건 턴은 '작성' 을 알리지 않는다 — compose 가 생성 없이 '없음' 으로 답하므로,
    #    알리면 하지 않은 일을 화면이 말하는 것이 된다.
    events.clear()
    with PROG.reporting(events.append):
        out = P.compose({"question": "질문", "evidence": [], "steps": []})
    hit = events == [] and bool(out["answer"])
    print(f"{'✓' if hit else '✗'} 재료 0건 턴은 작성 진행을 알리지 않는다 — {events}")
    ok += hit

    # ③ 콜백이 죽어도 답변 생성은 계속된다 — 진행 표시는 곁가지다.
    def broken(text: str) -> None:
        raise RuntimeError("표시 실패")

    with PROG.reporting(broken):
        out = P.compose({"question": "질문", "evidence": [], "steps": []})
    hit = bool(out["answer"])
    print(f"{'✓' if hit else '✗'} 진행 콜백이 죽어도 답변은 나온다")
    ok += hit

    # ④ 콜백이 없으면(배치·테스트 기본) emit 은 no-op — 켜지 않은 화면에 아무 일도 없다.
    PROG.emit("아무도 안 듣는 진행")   # 예외 없이 지나가면 통과
    print("✓ 콜백 없는 emit 은 no-op 이다")
    ok += 1
    return ok


def check_order_flipped() -> int:
    """카드 선택 1차가 LLM 인지 — 순서가 실제로 뒤집혔는지 검증한다.

    n-gram 은 문자 유사도라 '주제어만 겹치는 확신 있는 오답'을 만든다. 그 오답을 게이트로
    사후에 걸러내는 대신, 애초에 의미로 고르게 한 것이 이 순서의 이유다. n-gram 은 버리지
    않고 LLM 이 0건일 때의 폴백으로 남긴다.
    """
    target = tools.KB.pitches[0]
    visited: list[str] = []

    def spy_retrieve(kb, **kw):
        visited.append("retrieve")
        return []

    orig_pick, orig_retrieve, orig_verify = tools.llm_pick, tools.retrieve, tools.fits_question
    tools.retrieve, tools.fits_question = spy_retrieve, lambda q, h, kind="", history=None, query=None, sink=None: h
    ok = 0
    try:
        # ① LLM 이 골랐으면 n-gram 은 아예 돌지 않는다.
        tools.llm_pick = lambda kinds, query: [(2.0, target)]
        found = tools._pitch({"question": "질문"}, "질문")
        hit = not visited and found is not None and found["sources"][0]["id"] == target["id"]
        print(f"{'✓' if hit else '✗'} LLM 채택 → n-gram 미실행(retrieve {len(visited)}회)")
        ok += hit

        # ② LLM 이 0건이면 n-gram 폴백이 돈다(조건부 → 조건 완화 2회).
        tools.llm_pick = lambda kinds, query: []
        found = tools._pitch({"question": "질문", "stage": "신규"}, "질문")
        hit = len(visited) == 2 and found is None
        print(f"{'✓' if hit else '✗'} LLM 0건 → n-gram 폴백 실행(retrieve {len(visited)}회)")
        ok += hit

        # ③ LLM 의 선택도 게이트를 그대로 통과해야 한다(1차가 됐다고 면제 아님).
        tools.llm_pick = lambda kinds, query: [(2.0, target)]
        tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: []
        hit = tools._pitch({"question": "질문"}, "질문") is None
        print(f"{'✓' if hit else '✗'} LLM 선택도 적합성 게이트 적용")
        ok += hit
    finally:
        tools.llm_pick, tools.retrieve, tools.fits_question = orig_pick, orig_retrieve, orig_verify

    return ok


def check_tool_loop() -> int:
    """계획 루프 — 여러 도구를 한 턴에 부르고 결합하는지, 그리고 경계를 코드가 쥐는지.

    이 스위트의 존재 이유가 여기다. 예전에는 의도 하나 = 노드 하나 = 답변 하나였고, 한 턴에
    재료 하나만 쓸 수 있었다. 결합이 되는지, 그리고 결합하면서 「코드=사실」이 새지 않는지.
    """
    ok = 0
    orig_gen, orig_verify = plan.generate, tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h

    # 절차 카드는 검색 1위가 아니라 **이름으로 고정**한다. 예전에는 "디폴트옵션 변경 화면번호"
    # 의 1위(proc.018)에 기댔는데, 그 카드의 화면번호는 ⚠ 유의 박스에서 잘못 딸려 온 것이라
    # 데이터를 고치며 비었고(build_kb 의 화면번호 추출), 화면을 묻는 질의는 화면번호가 있는
    # 카드를 앞세우므로(procedure_qa.search) 1위가 바뀌었다. 표시 복구를 보는 검사가 검색
    # 순위에 흔들리지 않게 한다 — 이 검사가 보는 것은 검색이 아니라 근거별 선별 복구다.
    from pension_agent.consult_agent.tools import procedure_qa as _proc_qa
    _proc_card = next(c for c in tools.KB.cards if c["id"] == "proc.041")   # 화면번호 + status=확인 필요
    _orig_proc_search = _proc_qa.search
    _proc_qa.search = lambda q, _c=_proc_card: [(2.0, _c)]
    try:
        # ① 두 도구를 부르고 두 근거가 한 답변에 다 들어간다. 절차 질의는 status=확인 필요
        #    카드를 겨냥한다 — ⚠ 유의 텍스트는 이제 역할 선언상 authoring 이라
        #    표시로 강제되지 않고, 절차의 강제 표시는 상충 상태 표기에서만 나온다.
        script = [
            '{"tool": "fact", "query": "세액공제 한도"}',
            '{"tool": "procedure", "query": "디폴트옵션 상품 변경 시 기존 적립금 매도 화면번호"}',
            '{"done": true}',
        ]
        plan.generate = lambda prompt, **kw: script.pop(0) if script else '{"done": true}'
        state = {"question": "세액공제 한도랑 디폴트옵션 변경 화면번호 알려줘"}
        for _ in range(3):
            state.update(plan.plan_step(state))
            if state.get("plan_done"):
                break
        used = [e["tool"] for e in state.get("evidence") or []]
        hit = used == ["fact", "procedure"]
        print(f"{'✓' if hit else '✗'} 한 턴에 도구 2개 호출 → 원장 {used}")
        ok += hit

        # ② 복구는 **근거별**이고, 채우는 것은 빠진 표시뿐이다 — 근거 블록을 통째로 덤프하지
        #    않는다. procedure 는 상충 상태 표시(notices)가 있어 그 표시가 붙고, fact 는 값을
        #    언급하지 않았으므로 원문을 강요받지 않는다. 답변이 표 덤프로 뭉개지지 않는 이유가
        #    이 선별이다.
        plan.generate = lambda prompt, **kw: "두 가지를 함께 보시면 돼요."
        out = plan.compose(state)
        ev_fact = next(e for e in state["evidence"] if e["tool"] == "fact")
        ev_proc = next(e for e in state["evidence"] if e["tool"] == "procedure")
        proc_notice = ev_proc["notices"][0]
        hit = (out["answer"].startswith("두 가지를 함께 보시면 돼요.")
               and proc_notice in out["answer"]          # ⚠ 유의 때문에 복구
               and ev_proc["text"] not in out["answer"]  # 그렇다고 카드 전문을 붙이진 않는다
               and ev_fact["text"] not in out["answer"]  # 값 미언급 → 복구 불필요
               and len(out["sources"]) >= 2)
        print(f"{'✓' if hit else '✗'} 근거별 선별 복구(procedure 붙고 fact 안 붙음) · 근거 {len(out['sources'])}건")
        ok += hit

        # ③ 원장 밖 수치가 있으면 생성문을 통째로 버리고 근거 원문으로 답한다(복구 불가).
        blocks = [e["text"] for e in state["evidence"]]
        plan.generate = lambda prompt, **kw: "한도는 1,234,567원이에요."
        out_bad = plan.compose(state)
        hit = "1,234,567" not in out_bad["answer"] and all(b in out_bad["answer"] for b in blocks)
        print(f"{'✓' if hit else '✗'} 원장 밖 수치 → 생성문 폐기 · 근거 원문으로 답변")
        ok += hit

        # ④ 상한은 코드가 정한다 — LLM 이 계속 도구를 불러도 MAX_STEPS 에서 끊긴다.
        plan.generate = lambda prompt, **kw: '{"tool": "fact", "query": "무한"}'
        st = {"question": "질문"}
        for _ in range(plan.MAX_STEPS + 3):
            st.update(plan.plan_step(st))
            if st.get("plan_done"):
                break
        hit = len(st.get("steps") or []) <= plan.MAX_STEPS
        print(f"{'✓' if hit else '✗'} MAX_STEPS 상한 준수(호출 {len(st.get('steps') or [])}회 ≤ {plan.MAX_STEPS})")
        ok += hit

        # ⑤ 같은 도구를 같은 질의로 다시 부르면 진전이 없으므로 도구를 다시 돌리지 않는다.
        #    근거가 0건이면 바로 끝내는 대신 한 번 재계획으로 되돌리고(check_replan_on_empty),
        #    그 뒤에도 반복이면 끝낸다.
        st2 = {"question": "질문", "steps": [{"tool": "fact", "query": "무한", "outcome": "miss"}]}
        st2.update(plan.plan_step(st2))
        first = st2.get("plan_retry") is True and not st2.get("plan_done")
        st2.update(plan.plan_step(st2))
        hit = first and st2.get("plan_done") is True and len(st2["steps"]) == 1
        print(f"{'✓' if hit else '✗'} 같은 호출 반복 차단(재계획 한 번 뒤 종료)")
        ok += hit

        # 근거를 이미 모은 턴이면 반복은 재계획 없이 바로 끝낸다 — 되돌릴 이유가 없다.
        st2e = {"question": "질문", "steps": [{"tool": "fact", "query": "무한", "outcome": "miss"}],
                "evidence": [{"tool": "fact", "query": "q", "text": "블록", "atomic": [],
                              "notices": [], "notice_scopes": [], "marks": [], "related": [],
                              "allow": ["블록"], "sources": [], "meta": {}}]}
        st2e.update(plan.plan_step(st2e))
        hit = st2e.get("plan_done") is True
        print(f"{'✓' if hit else '✗'} 근거가 있으면 반복 즉시 종료(재계획 없음)")
        ok += hit

        # ⑥ LLM 이 없는 도구 이름을 내놓으면 실행하지 않는다.
        plan.generate = lambda prompt, **kw: '{"tool": "존재하지_않는_도구", "query": "x"}'
        st3 = plan.plan_step({"question": "질문"})
        st3_done = plan.plan_step({"question": "질문", "plan_retry": True})
        hit = "evidence" not in st3 and not st3.get("plan_done") \
            and st3_done.get("plan_done") is True and "evidence" not in st3_done
        print(f"{'✓' if hit else '✗'} 미등록 도구 이름 차단")
        ok += hit

        # ⑦ 근거를 못 모으면 지어내지 않고 없다고 답한다.
        hit = plan.compose({"question": "질문"})["answer"] == plan.NO_EVIDENCE
        print(f"{'✓' if hit else '✗'} 원장 0건 → 정직한 '근거 없음'")
        ok += hit

        # ⑧ 고객 화면이 닫혀 있으면 customer 도구를 아예 보여주지 않는다(스텝 낭비 방지).
        hit = ("customer" not in tools.catalog({})
               and "customer" in tools.catalog({"customer_id": "CX"}))
        print(f"{'✓' if hit else '✗'} 쓸 수 없는 도구는 카탈로그에서 제외")
        ok += hit

        # ⑨ 계획이 **남은 호출 수**를 본다. 상한을 쥔 것은 코드인데, 「한 재료로 답할 수
        #    있으면 last: true 로 한 바퀴를 아껴라」라고 시키면서 몇 바퀴가 남았는지는
        #    안 알려주던 자리다(§5 「형태 요구는 재료에 없는 것을 요구하지 않는다」).
        seen: list[str] = []
        plan.generate = lambda prompt, **kw: (seen.append(prompt) or '{"done": true}')
        plan.plan_step({"question": "질문"})
        plan.plan_step({"question": "질문",
                        "steps": [{"tool": "fact", "query": "q", "outcome": "miss"}]})
        hit = (len(seen) == 2
               and f"남은 호출: {plan.MAX_STEPS}회" in seen[0]
               and f"남은 호출: {plan.MAX_STEPS - 1}회" in seen[1])
        print(f"{'✓' if hit else '✗'} 계획 프롬프트가 남은 호출 수를 싣고 바퀴마다 준다")
        ok += hit
    finally:
        plan.generate, tools.fits_question = orig_gen, orig_verify
        _proc_qa.search = _orig_proc_search

    return ok


def check_atomic_spans() -> int:
    """원문 스팬 집행 — 도구 종류가 아니라 **재료**가 보호 수준을 정한다.

    이게 필요한 이유는 verify_texts 가 수치의 집합 포함 검사라서, 원장에 있는 숫자를
    잘못 짝지은 것을 못 잡기 때문이다. 아래 ③ 이 그 구멍이고, 스팬 집행이 그것을 막는다.
    """
    ok = 0
    VALUE = "총급여 5,500만원 이하 16.5%, 초과 13.2% (지방소득세 포함)"
    ev_num = tools._ev("fact", "q", f"■ 세액공제율\n{VALUE}", [{"id": "f.1", "title": "세액공제율"}],
                       atomic=[VALUE])
    ev_mark = tools._ev("fieldtip", "q", f"■ 팁\n  {tools.FIELDTIP_MARK}\n현장 관찰 요약.",
                        [{"id": "tip.1", "title": "팁"}], notices=[tools.FIELDTIP_MARK])

    orig = plan.generate
    try:
        # ① 값을 언급하지 않으면 원문을 강요하지 않는다 — 모든 답변이 표 덤프가 되지 않는다.
        plan.generate = lambda p, **kw: "세액공제율은 소득에 따라 달라져요."
        out = plan.compose({"question": "q", "evidence": [ev_num]})
        hit = out["answer"] == "세액공제율은 소득에 따라 달라져요." and VALUE not in out["answer"]
        print(f"{'✓' if hit else '✗'} 값 미언급 → 원문 스팬 미요구(산문만)")
        ok += hit

        # ② 값을 원문 그대로 실으면 그대로 통과한다 — 산문 안에 인용이 녹는다.
        plan.generate = lambda p, **kw: f"정리하면 이래요. {VALUE} 라고 안내하시면 돼요."
        out = plan.compose({"question": "q", "evidence": [ev_num]})
        hit = VALUE in out["answer"] and plan.MISSING_NOTICES not in out["answer"]
        print(f"{'✓' if hit else '✗'} 값 원문 인용 → 블록 덧붙임 없이 통과")
        ok += hit

        # ③ 값을 **잘못 짝지으면** 생성문을 폐기한다. 두 숫자가 다 원장에 있으므로
        #    수치 집합 검사만으로는 통과하는 문장이다(그게 스팬 집행이 있는 이유다).
        wrong = "총급여 5,500만원 초과면 16.5% 예요."
        assert verify_texts(wrong, [ev_num["text"]])[0], "수치 검사만으로는 통과해야 한다(구멍 재현)"
        plan.generate = lambda p, **kw: wrong
        out = plan.compose({"question": "q", "evidence": [ev_num]})
        hit = wrong not in out["answer"] and VALUE in out["answer"]
        print(f"{'✓' if hit else '✗'} 값 재조합 → 생성문 폐기 · 원문으로 답변")
        ok += hit

        # ④ 숫자 없는 표시(「본부 지침 아님」)가 빠지면 폐기가 아니라 덧붙여 채운다.
        plan.generate = lambda p, **kw: "현장에서는 KPI부터 본다고 해요."
        out = plan.compose({"question": "q", "evidence": [ev_mark]})
        hit = ("현장에서는" in out["answer"] and tools.FIELDTIP_MARK in out["answer"]
               and plan.MISSING_NOTICES in out["answer"]
               and ev_mark["text"] not in out["answer"])   # 표시만 붙고 카드 전문은 안 붙는다
        print(f"{'✓' if hit else '✗'} 필수 표시 누락 → 생성문 유지 + 빠진 표시만 덧붙임")
        ok += hit

        # ⑤ 화법은 atomic 이 비어 있을 뿐, 처리 경로가 다르지 않다.
        ev_pitch = tools._ev("pitch", "q", "화법 카드 컨텍스트", [{"id": "p.1", "title": "화법"}])
        plan.generate = lambda p, **kw: "고객에게는 이렇게 말해보세요."
        out = plan.compose({"question": "q", "evidence": [ev_pitch]})
        hit = (ev_pitch["atomic"] == [] and ev_pitch["notices"] == []
               and out["answer"] == "고객에게는 이렇게 말해보세요.")
        print(f"{'✓' if hit else '✗'} 화법은 atomic·notices 가 빈 도구일 뿐(경로 동일)")
        ok += hit

        # ⑥ 도구가 실제로 스팬을 선언하는지 — 선언이 비면 집행할 것이 없다. fact 는 관계
        #    선언이 **없는** 카드를 집어 본다 — 선언이 있는 카드의 atomic 이 비는 것은
        #    정상이고(relations 가 대신한다), 그건 check_relations 가 잰다.
        from pension_agent.consult_agent import relations as REL
        from pension_agent.consult_agent.tools import facts_qa as FQ
        from pension_agent.consult_agent.state import KB as _KB
        bare = next(x for x in _KB.facts.values() if not REL.declared(x) and x.get("value"))
        orig_fits, orig_search = tools.fits_question, FQ.search
        tools.fits_question = lambda question, h, kind="", history=None, query=None, sink=None: h
        FQ.search = lambda question: [(2.0, bare)]
        try:
            f = tools.run("fact", {"question": "q"}, "확정값")
        finally:
            tools.fits_question, FQ.search = orig_fits, orig_search
        pr = tools.run("procedure", {"question": "디폴트옵션 변경 화면번호"}, "디폴트옵션 변경 화면번호")
        hit = bool(f and f["atomic"]) and bool(pr and pr["atomic"])
        print(f"{'✓' if hit else '✗'} fact·procedure 가 값 스팬 선언(fact {len(f['atomic']) if f else 0}건 · "
              f"procedure {len(pr['atomic']) if pr else 0}건)")
        ok += hit

        # ⑦ 화면번호를 인용하는 주의 표시(notices)가 값 스팬으로 오판되지 않는다 — 숫자
        #    유무로 종류를 추론했을 때 실제로 났던 오판이다. 절차의 ⚠ 유의가 역할 선언상
        #    authoring 으로 내려간 지금, 화면번호를 인용하는 표시는 화면 비고의 caution 이다
        #    (screen.06-12-501 "당일처리는 17시까지 [06-7A-R51] …").
        by_id = {c["id"]: c for c in tools.KB.cards}
        orig_pick = tools.pick
        tools.pick = lambda kinds, q, **kw: [(2.0, by_id["screen.06-12-501"])]
        try:
            sc = tools.run("screen", {"question": "q"}, "퇴직금 입금 등록 화면번호")
        finally:
            tools.pick = orig_pick
        screens = sc["atomic"][0] if sc and sc["atomic"] else ""
        quoting = any(numbers(n) & numbers(screens) for n in sc["notices"]) if sc else False
        plan.generate = lambda p, **kw: f"등록은 화면 {screens} 에서 하시면 돼요."
        out = plan.compose({"question": "q", "evidence": [sc]})
        hit = quoting and screens in out["answer"] and "화면" in out["answer"].split("──")[0]
        print(f"{'✓' if hit else '✗'} 화면번호 인용 주의 표시를 값 스팬으로 오판하지 않음")
        ok += hit
    finally:
        plan.generate = orig
    return ok


def check_notice_scope() -> int:
    """표시는 **답변이 실제로 쓴 카드**의 것만, 그리고 **표시만** 붙는가.

    회귀 대상: 한 도구가 카드 여러 장을 근거 블록 하나로 돌려주는데, 표시 누락 판정이
    블록 단위였다. 그래서 "디폴트옵션 변경 화면번호"를 물으면 답변이 쓰지도 않은 다른
    절차 카드(교체매매 3경로)의 ⚠ 가 따라 붙고, 그것도 카드 전문 1,000자로 붙었다 —
    답변 3줄에 근거 덤프 2,300자. 정작 관계있는 표시가 그 안에 묻혔다.
    """
    from pension_agent.knowledge.kb import role_texts
    from pension_agent.consult_agent.state import KB

    ok = 0
    orig_gen, orig_pick = plan.generate, tools.pick
    by_id = {c["id"]: c for c in KB.cards}
    # 표시(caution)를 가진 카드 두 장: 답변이 안 쓴 카드(06-10-182 징구 필수) + 쓴 카드
    # (75-08-110 SMS거절 발송 불가). 예전에는 절차의 ⚠ 유의로 재현했는데, 역할 선언이
    # 들어오며 절차 유의는 authoring 으로 내려갔고 카드 단위 표시는 화면 비고의 caution 이
    # 맡는다. 두 화면번호는 숫자 조각이 겹치지 않는 조합이어야 한다 — 겹치면(06·12 등)
    # 값 스팬 검사(_span_verdict 의 DISCARD)가 인용 안 된 쪽 번호로 먼저 걸린다.
    pair = [(2.0, by_id["screen.06-10-182"]), (2.0, by_id["screen.75-08-110"])]
    used_mark = role_texts(by_id["screen.75-08-110"].get("note"), "caution")[0]
    unused_mark = role_texts(by_id["screen.06-10-182"].get("note"), "caution")[0]
    try:
        tools.pick = lambda kinds, q, **kw: pair
        found = tools.run("screen", {"question": "q"}, "연금납입정보 조회랑 상품변경 문자 발송 화면번호")
        tools.pick = orig_pick

        hit = len(found["notice_scopes"]) == 2 and all(s["keys"] for s in found["notice_scopes"])
        print(f"{'✓' if hit else '✗'} 도구가 표시를 카드 단위로 나눠 선언한다"
              f"({len(found['notice_scopes'])}묶음)")
        ok += hit

        # 답변이 [75-08-110] 의 화면번호만 인용했다 → [06-10-182] 의 표시는 붙지 않는다.
        plan.generate = lambda p, **kw: (
            "상품변경 안내 문자는 [75-08-110] 화면에서 발송해요.")
        out = plan.compose({"question": "상품변경 문자 발송 화면번호 알려줘", "evidence": [found]})
        hit = used_mark in out["answer"] and unused_mark not in out["answer"]
        print(f"{'✓' if hit else '✗'} 답변이 쓴 카드의 표시만 붙는다")
        ok += hit

        hit = found["text"] not in out["answer"] and len(out["answer"]) < len(found["text"])
        print(f"{'✓' if hit else '✗'} 카드 전문을 덤프하지 않는다 "
              f"(답변 {len(out['answer'])}자 < 근거 {len(found['text'])}자)")
        ok += hit

        # 답변이 어느 카드를 썼는지 분간이 안 되면(화면번호 미인용) 표시를 다 유지한다 —
        # 잡음을 줄이자고 ⚠ 를 잃지는 않는다.
        plan.generate = lambda p, **kw: "두 화면을 함께 확인하시면 돼요."
        out = plan.compose({"question": "q", "evidence": [found]})
        hit = used_mark in out["answer"] and unused_mark in out["answer"]
        print(f"{'✓' if hit else '✗'} 분간이 안 되면 표시를 잃지 않는다(전부 유지)")
        ok += hit
    finally:
        plan.generate, tools.pick = orig_gen, orig_pick
    return ok


def check_plan_failure() -> int:
    """계획이 깨진 것과 재료가 없는 것을 **다르게 말하는가.**

    회귀 대상: 계획 노드가 LLM 예외를 통째로 삼키고 루프만 끝냈다. 그래서 401·타임아웃·
    모델명 오류·규격 밖 응답이 전부 "그 질문에 쓸 근거를 찾지 못했습니다"로 둔갑했고,
    지식베이스에 멀쩡히 있는 자료를 없다고 답하면서 원인은 화면에서 사라졌다.
    찾아보고 없는 것과 찾아보지도 못한 것은 다른 사건이다.
    """
    ok = 0
    orig_gen, orig_verify = plan.generate, tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    question = "고객이 주식이 더 낫다는데 뭐라고 하지?"
    base = {"question": question, "utterance": question}

    def drive(gen) -> tuple[dict, str]:
        plan.generate = gen
        state = dict(base)
        for _ in range(plan.MAX_STEPS + 1):
            state.update(plan.plan_step(state))
            if state.get("plan_done"):
                break
        plan.generate = lambda p, **kw: "(스텁 답변)"
        return state, plan.compose(state)["answer"]

    try:
        def dead(prompt, **kw):
            # generate() 는 프로바이더 예외를 전부 LLMError 로 모아 올린다 — 삼켜도 되는
            # 예외와 삼키면 안 되는 예외를 호출부가 구분할 수 있어야 하기 때문이다(llm.py).
            raise LLMError("LLM 미설정")

        state, answer = drive(dead)
        hit = bool(state.get("llm_error")) and "지식베이스에 자료가 없다는 뜻이 아니" in answer \
            and plan.NO_EVIDENCE not in answer
        print(f"{'✓' if hit else '✗'} LLM 호출 실패를 '근거 없음'으로 둔갑시키지 않음")
        ok += hit

        # 잘린 JSON — max_tokens 가 빠듯할 때 실제로 나오던 응답 형태다.
        state, answer = drive(lambda p, **kw: '{"tool": "pitch", "query": "고객이 주식이')
        hit = "JSON" in (state.get("llm_error") or "") and plan.NO_EVIDENCE not in answer
        print(f"{'✓' if hit else '✗'} 규격 밖·잘린 응답도 원인을 남긴다")
        ok += hit

        # 계획이 정상이면 llm_error 가 없고, 재료를 못 찾은 것은 그대로 '근거 없음'이다.
        state, answer = drive(lambda p, **kw: '{"done": true}')
        hit = not state.get("llm_error") and answer == plan.NO_EVIDENCE
        print(f"{'✓' if hit else '✗'} 정상 종료 + 재료 0건은 여전히 '근거 없음'")
        ok += hit

        # 질문을 되받아 적어도 잘리지 않을 만큼은 준다(80 토큰에서 잘려 도구를 못 부르던 자리).
        hit = plan.PLAN_MAX_TOKENS >= 200
        print(f"{'✓' if hit else '✗'} 계획 응답 토큰 상한 {plan.PLAN_MAX_TOKENS}")
        ok += hit

        # ── 도구가 **죽은** 턴. 위 LLM 실패와 같은 사고의 세 번째 갈래다(지워진 gap 35).
        # 예전에는 tools.run 이 예외를 삼키고 None 을 돌려줘서, 렌더러에서 난 KeyError 하나가
        # 계획에는 «질의가 빗나갔다»로, 답에는 «지식베이스에서 찾지 못했습니다»로 나갔다.
        orig_run = plan.tools.run

        def crash(name, state, query):
            raise tools.ToolFailure(name, "KeyError: 'as_of'")

        plan.tools.run = crash
        try:
            state, answer = drive(lambda p, **kw: '{"tool": "screen", "query": "운용현황"}')
        finally:
            plan.tools.run = orig_run

        hit = (bool([s for s in state.get("steps") or [] if s["outcome"] == "failed"])
               and plan.NO_EVIDENCE not in answer
               and "지식베이스에 자료가 없다는 뜻이 아니" in answer
               and "KeyError" in answer)
        print(f"{'✓' if hit else '✗'} 도구가 죽은 것을 '근거 없음'으로 둔갑시키지 않는다")
        ok += hit

        # 고장은 LLM 이 죽은 것과 **다르게** 기록된다 — 둘을 한 칸에 접으면 «LLM 을 고쳐라»와
        # «도구를 고쳐라»가 같은 말이 되고, 계획 루프의 처분(루프 유지 vs 중단)도 갈릴 수 없다.
        hit = not state.get("llm_error")
        print(f"{'✓' if hit else '✗'} 도구 고장을 LLM 실패로 기록하지 않는다")
        ok += hit

        # 죽은 도구는 이번 턴의 능력 표면에서 빠진다 — 다시 보여주면 계획이 같은 도구를
        # 다시 골라 바퀴를 버린다(빗나간 호출과 처방이 다른 자리).
        broken = {"steps": [{"tool": "screen", "query": "운용현황",
                             "outcome": "failed", "reason": "KeyError"}]}
        hit = "screen" not in tools.usable(broken) and "screen" in tools.usable({})
        print(f"{'✓' if hit else '✗'} 죽은 도구는 이번 턴 카탈로그에서 빠진다")
        ok += hit

        # 죽은 호출은 '찾아본 곳'에도 서지 않는다 — 지식베이스를 보지도 못했으므로
        # 거기 세우면 «그 재료로 찾아봤는데 없더라»는 거짓 진술이 된다.
        tried = plan._no_evidence({"steps": [
            {"tool": "screen", "query": "운용현황", "outcome": "failed", "reason": "KeyError"},
            {"tool": "fact", "query": "수수료", "outcome": "miss"}]})
        hit = "fact:수수료" in tried and "screen:운용현황" not in tried
        print(f"{'✓' if hit else '✗'} 죽은 호출을 '찾아본 곳'으로 세지 않는다")
        ok += hit

        # 답이 갈리는 것은 **원장이 끝내 비었을 때**다. LLM 실패 안내와 같은 꼴로 끝나야
        # 한다 — 직원이 받는 안내가 실패 지점에 따라 달라지면 그 자체가 진단을 어렵게 한다.
        notice = plan.compose({"question": "q", "evidence": [], "steps": [
            {"tool": "screen", "query": "운용현황", "outcome": "failed", "reason": "x"}]})["answer"]
        hit = (notice.startswith("지금은 답변을 만들 수 없어요")
               and plan.LLM_FAILED.format(reason="x") != notice
               and "단말 화면번호" in notice)     # 도구 선언의 말로 무엇이 실패했는지 밝힌다
        print(f"{'✓' if hit else '✗'} 실패 안내가 무엇을 못 읽었는지 밝힌다")
        ok += hit
    finally:
        plan.generate, tools.fits_question = orig_gen, orig_verify
    return ok


def check_llm_down() -> int:
    """LLM 이 죽었을 때 **어느 단계에서 죽든** 같은 안내로 끝나는가 (CLAUDE.md §11).

    회귀 대상: 슬롯 분해만 예외를 잡지 않아서, 기본 경로인 화법 상황 질문은 답변 대신
    RuntimeError 로 턴이 끝났다. 다른 단계는 전부 잡고 있었으므로 **같은 LLM 미설정이
    질문 종류에 따라 안내가 되기도 하고 크래시가 되기도 했다.** 지금 슬롯 분해는 화법
    도구 안에서 돌고 LLMError 를 그대로 올리는데, 그것을 tools.run → plan_step 이 받아
    같은 안내로 끝낸다 — 잡는 자리가 옮겨졌을 뿐 결과는 같아야 한다.
    """
    ok = 0

    def dead(*a, **kw):
        raise LLMError("LLM 미설정 — PROVIDER=none")

    # ① 슬롯 분해에서 죽어도 계획 루프가 받아 같은 안내로 끝난다.
    orig_pitch, orig_plan = pitch_slots.generate, plan.generate
    orig_extract, orig_pick = pitch_slots.extract_slots, tools.llm_pick
    pitch_slots.generate = dead
    pitch_slots.extract_slots = _REAL_EXTRACT_SLOTS   # 분해 자체를 재는 검사라 원본으로 되돌린다
    plan.generate = lambda p, **kw: '{"tool": "pitch", "query": "수수료"}'
    tools.llm_pick = lambda kinds, q: []        # n-gram 폴백으로 들어가야 슬롯을 뽑는다
    try:
        state = {"question": "사업자 고객인데 수수료 부담된다고 하시네요",
                 "utterance": "수수료 부담"}
        state.update(plan.plan_step(state))
        hit = "LLMError" in (state.get("llm_error") or "") and state.get("plan_done") is True
    except Exception:
        hit = False
    finally:
        pitch_slots.generate, plan.generate = orig_pitch, orig_plan
        pitch_slots.extract_slots, tools.llm_pick = orig_extract, orig_pick
    print(f"{'✓' if hit else '✗'} 슬롯 분해가 죽어도 크래시가 아니라 원인 기록으로 끝난다")
    ok += hit

    # ② 그래프 전체 — 모든 단계가 죽어도 턴은 안내로 끝난다(스텁 없이 진짜 노드로 돈다).
    saved = {n: getattr(G, n) for n in ("understand", "plan_step")}
    origs = (understand.generate, pitch_slots.generate, plan.generate)
    G.understand, G.plan_step = understand.understand, plan.plan_step
    understand.generate = pitch_slots.generate = plan.generate = dead
    try:
        out = G.build_agent().invoke({"question": "사업자 고객인데 수수료 부담된다고 하시네요"})
        answer = out.get("answer", "")
        hit = ("지식베이스에 자료가 없다는 뜻이 아니" in answer
               and plan.NO_EVIDENCE not in answer and "LLMError" in answer)
    except Exception as exc:
        answer, hit = f"({type(exc).__name__})", False
    finally:
        for name, fn in saved.items():
            setattr(G, name, fn)
        understand.generate, pitch_slots.generate, plan.generate = origs
    print(f"{'✓' if hit else '✗'} 화법 상황 질문 + LLM 미설정 → 크래시 없이 안내 — {answer[:38]}")
    ok += hit

    # ③ 재료는 모았는데 문장 작성만 죽은 경우. 근거 원문을 그대로 답으로 내보내면 완성된
    #    답변처럼 보인다 — 다른 단계와 같은 안내로 끝나야 한다.
    orig = plan.generate
    plan.generate = dead
    try:
        evidence = [{"tool": "fact", "query": "한도", "text": "세액공제 한도는 900만원이다.",
                     "atomic": [], "notices": [], "notice_scopes": [], "allow": [],
                     "sources": [{"id": "f1"}], "meta": {}}]
        answer = plan.compose({"question": "한도가 얼마야?", "evidence": evidence})["answer"]
        hit = "지식베이스에 자료가 없다는 뜻이 아니" in answer and "900만원" not in answer
    finally:
        plan.generate = orig
    print(f"{'✓' if hit else '✗'} compose: 문장 작성 실패를 근거 원문 덤프로 덮지 않음")
    ok += hit

    # ④ 뒤집힌 방향의 같은 사고 방지 — 슬롯 분해만 일시적으로 실패하고 계획은 정상이면,
    #    재료를 못 찾은 턴은 여전히 '근거 없음'이다('LLM 실패'로 둔갑시키지 않는다).
    orig = plan.generate
    plan.generate = lambda p, **kw: '{"done": true}'
    try:
        state = {"question": "질문", "llm_error": "LLMError: 일시 실패"}
        state.update(plan.plan_step(state))
        hit = plan.compose(state)["answer"] == plan.NO_EVIDENCE
    finally:
        plan.generate = orig
    print(f"{'✓' if hit else '✗'} 계획이 정상이면 앞 단계의 일시 실패로 답이 바뀌지 않음")
    ok += hit

    return ok


def check_compose_retry() -> int:
    """점검에 걸린 생성문을 **한 번 다시 쓰게** 한다 (§6 — 처분은 구현이 정한다).

    회귀 대상: 처분이 «근거 원문 덤프» 하나뿐이던 동안, 걸린 자리가 한 문장이어도 답이
    통째로 버려지고 화면에는 카드 원문이 답변처럼 떨어졌다(■ 제목 줄 · 「· 기준시점 … ·
    출처 …」 메타 줄). 직원 쪽에서는 에이전트가 갑자기 다른 말투로 말하는 것으로 보인다.

    걸린 자리를 재작성 프롬프트에 실어야 같은 문장이 다시 나오지 않는다 — 「다시 쓰세요」
    만으로는 처분이 한 바퀴 늘 뿐이다.
    """
    from pension_agent.consult_agent.nodes import plan

    ok = 0
    evidence = [{"tool": "fact", "query": "한도", "text": "■ 세액공제 한도\n\n한도는 900만원이다.",
                 "atomic": [], "notices": [], "notice_scopes": [],
                 "allow": ["한도는 900만원이다."], "sources": [{"id": "f1"}],
                 "related": [], "marks": [], "meta": {}}]
    state = {"question": "한도가 얼마야?", "evidence": evidence}

    # ① 첫 생성문이 원장 밖 수치를 말하면, 두 번째 시도의 결과가 답이 된다.
    seen: list[str] = []

    def twice(prompt, **kw):
        seen.append(prompt)
        return ("한도는 1,234만원이에요." if len(seen) == 1 else "한도는 900만원이에요.")

    orig = plan.generate
    plan.generate = twice
    try:
        answer = plan.compose(dict(state))["answer"]
    finally:
        plan.generate = orig
    hit = len(seen) == 2 and answer.startswith("한도는 900만원이에요")
    print(f"{'✓' if hit else '✗'} 걸린 생성문을 한 번 다시 쓰고, 통과하면 그것이 답이다")
    ok += hit

    # ② 재작성 프롬프트가 **무엇이 걸렸는지**를 싣는다. 안 실으면 같은 문장이 다시 나온다.
    hit = len(seen) == 2 and "1,234" in seen[1] and "다시 쓴다" in seen[1]
    print(f"{'✓' if hit else '✗'} 재작성 프롬프트에 걸린 자리가 실린다")
    ok += hit

    # ③ 두 번째도 걸리면 예전 그대로 근거 원문이 답이다 — 틀린 문장이 나가는 선택지는 없다.
    #    무한히 다시 쓰지 않는다는 것도 여기서 잰다(상한은 코드가 쥔다).
    tries: list[str] = []

    def always_bad(prompt, **kw):
        tries.append(prompt)
        return "한도는 1,234만원이에요."

    plan.generate = always_bad
    try:
        answer = plan.compose(dict(state))["answer"]
    finally:
        plan.generate = orig
    hit = (len(tries) == plan.COMPOSE_RETRIES + 1
           and answer.startswith(evidence[0]["text"]) and "1,234" not in answer)
    print(f"{'✓' if hit else '✗'} 계속 걸리면 상한에서 멈추고 근거 원문이 답이다({len(tries)}회)")
    ok += hit

    return ok
