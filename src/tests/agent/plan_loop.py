"""계획 루프 검사 — 도구 결합 · 재계획 · 턴 비용 · 실패 경로(계획 깨짐·LLM 다운·재작성).

`tests/test_agent.py::main` 에서 호출(스텁 설치·정리 포함). 직접 실행 금지.
"""

from __future__ import annotations

from pension_agent import graph as G
from pension_agent import tools
from pension_agent.nodes import plan, understand
from pension_agent.tools.kb_search import pitch_slots
from pension_agent.common.llm import LLMError
from pension_agent.evidence.verify import numbers, verify_texts

from tests.agent._common import print, _REAL_EXTRACT_SLOTS  # noqa: A001 — 집계용 print


def check_turn_cost() -> int:
    """값 하나 묻는 턴의 LLM 호출 수 — 하는 일 없는 호출(슬롯 분해·두 번째 계획·되묻기) 제외."""
    from pension_agent.nodes import clarify as CL
    from pension_agent.nodes import plan as P

    ok = 0
    ev_customer = {"tool": "customer", "query": "q", "text": "· 평가금액 2억 3,000만원",
                   "atomic": [], "notices": [], "notice_scopes": [], "marks": [],
                   "related": [], "allow": ["· 평가금액 2억 3,000만원"],
                   "sources": [{"id": "briefing.CX"}], "meta": {}}

    # 화법 미호출 턴은 슬롯 분해 없음
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

    # "last": true 는 도구가 실제로 재료를 냈을 때만
    hit = state.get("plan_done") is True and len(state.get("steps") or []) == 1
    print(f"{'✓' if hit else '✗'} 재료 하나로 끝나는 질문은 계획 호출 1번으로 끝난다")
    ok += hit

    # 고객 재료만 있는 턴은 되묻기 판정 생략
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

    # 지식 재료가 섞이면 판정 유지 — 생략이 기능 제거가 아님
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

    # 화법 턴의 n-gram 폴백에서는 슬롯 분해 유지
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

    # 화법 미사용 답변엔 '파악된 상황' 줄 미첨부
    hit = pitch_slots.situation_line("situation", {}) == "" and \
        "고객유형" in pitch_slots.situation_line("situation", {"customer_type": "사업자"})
    print(f"{'✓' if hit else '✗'} 슬롯이 없으면 '파악된 상황' 줄을 싣지 않는다")
    ok += hit
    return ok


def check_miss_recovery() -> int:
    """계획의 질의·도구 오선택에도 지식베이스의 답이 사라지지 않는지 — last 존중 조건·원문 재검색·'없음' 문구."""
    from pension_agent.nodes import plan as P
    from pension_agent.tools.kb_search import procedure_qa

    ok = 0

    # 근거 없으면 last 무시
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

    # 재료를 얻었으면 last 로 한 바퀴 절약
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

    # 줄여 쓴 질의가 0건이면 원문 질문으로 재검색
    question = "포트폴리오 운용현황 조회 화면 번호는?"
    shrunk = "운용현황 조회 화면번호"
    orig_fits = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        hit = (not procedure_qa.search(shrunk)                       # 줄여 쓰면 0건
               and bool(procedure_qa.search(question))               # 원문으로는 찾음
               and tools.run("procedure", {"question": question}, shrunk) is not None)
    finally:
        tools.fits_question = orig_fits
    print(f"{'✓' if hit else '✗'} 계획의 질의가 0건이면 원문 질문으로 한 번 더 찾는다")
    ok += hit

    # 재검색은 같은 도구·같은 지식베이스 — 근거 경계 확대 금지
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

    # '없다'만 말함 — 찾아본 질의는 화면 미표시(steps·트레이스에만)
    answer = P._no_evidence({"steps": [{"tool": "procedure",
                                    "query": "운용현황 조회 화면번호", "outcome": "miss"}]})
    hit = answer == P.NO_EVIDENCE and "운용현황 조회 화면번호" not in answer
    print(f"{'✓' if hit else '✗'} '근거 없음'이 찾아본 질의를 화면에 싣지 않는다")
    ok += hit

    hit = P._no_evidence({}) == P.NO_EVIDENCE
    print(f"{'✓' if hit else '✗'} 아무것도 안 불러본 턴도 같은 한 문장이다")
    ok += hit
    return ok


def check_no_material_tone() -> int:
    """재료 없는 턴도 공손한 말투인지 — 코드 안내문(NO_EVIDENCE)과 LLM 프롬프트 예시 양쪽."""
    from pension_agent.prompts import COMPOSE_MISSING_BLOCK, COMPOSE_SYSTEM

    ok = 0
    hit = "죄송" in plan.NO_EVIDENCE and plan.NO_EVIDENCE.rstrip().endswith("요.")
    print(f"{'✓' if hit else '✗'} '근거 없음' 안내문이 답변과 같은 공손한 해요체다")
    ok += hit

    hit = (plan.NO_EVIDENCE.count(".") == 1
           and not any(w in plan.NO_EVIDENCE for w in ("지식베이스", "화법·", "찾아본 곳", "다시 물어")))
    print(f"{'✓' if hit else '✗'} 안내문은 한 문장이고 개발 용어·재료 나열·찾아본 곳·되묻기 안내가 없다")
    ok += hit

    # 다른 안내문(LLM 장애·도구 고장·검증 폴백)에도 «지식베이스» 금지
    hit = not any("지식베이스" in t for t in (plan.LLM_FAILED, plan.TOOL_FAILED, plan.RAW_EVIDENCE))
    print(f"{'✓' if hit else '✗'} 장애·고장·폴백 안내문에도 «지식베이스»가 없다")
    ok += hit

    # 옛 예시가 지시문에 있으면 LLM 이 베낌 — 금지 예시 인용은 허용
    hit = '"그 자료는 없어요"라고 답한다' not in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 작성 프롬프트가 「그 자료는 없어요」를 답변 예시로 들지 않는다")
    ok += hit

    hit = "공손" in COMPOSE_SYSTEM and "공손" in COMPOSE_MISSING_BLOCK
    print(f"{'✓' if hit else '✗'} 자료가 없는 턴의 말투를 작성 프롬프트가 지시한다(둘 다)")
    ok += hit

    # «대신 답하지 마라»만 있으면 물은 대상의 상태 값까지 삼킴 — 상태 값은 함께 알릴 사실로 명시
    hit = "대신 답해 주지 마라" in COMPOSE_MISSING_BLOCK and "함께 알려줄 사실" in COMPOSE_MISSING_BLOCK
    print(f"{'✓' if hit else '✗'} 핵심 대상이 없는 턴도 물은 대상의 원장 상태 값은 함께 적게 한다")
    ok += hit
    return ok


def check_replan_on_empty() -> int:
    """근거 0건인 채 끝내려 하면 한 번 재계획 — 빗나간 호출 노출 · 안 써 본 도구 안내 · 두 번째 done 존중."""
    from pension_agent.nodes import plan as P

    ok = 0
    ev_customer = {"tool": "customer", "query": "왜 타겟", "text": "· 왜 이 고객인가: 미운용 방치",
                   "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
                   "allow": ["· 왜 이 고객인가: 미운용 방치"],
                   "sources": [{"id": "customer.CX", "title": "고객 계좌 현황"}], "meta": {}}

    # 회귀 시나리오: segment 빗나감 → done → 재계획 → customer
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

    # 원장엔 성공 재료만 실림 — 빗나간 호출이 프롬프트에 없으면 같은 호출 반복
    hit = len(prompts) == 3 and "segment:타겟 고객군 선정 조건" in prompts[1] \
        and "반복해도 소용없다" in prompts[1]
    print(f"{'✓' if hit else '✗'} 빗나간 호출이 계획 프롬프트에 실린다")
    ok += hit

    hit = len(prompts) == 3 and "아직 근거가 0건이다" in prompts[2] and "customer" in prompts[2]
    print(f"{'✓' if hit else '✗'} 재계획 지시가 안 써 본 도구(customer 포함)를 보여준다")
    ok += hit

    # 두 번째 done 은 존중 — 정직한 '없음' 경로 유지
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

    # 재계획은 근거 0건일 때만
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

    # 도구 설명이 곧 계획 판단 재료 — 잔액·수익률만 말하면 이 질문이 segment 로 흐름
    desc = tools.TOOLS["customer"].desc
    hit = "타겟" in desc and "왜" in desc
    print(f"{'✓' if hit else '✗'} customer 도구 설명이 선정 이유(타겟)를 말한다")
    ok += hit
    return ok


def check_progress() -> int:
    """진행 표시 — 코드 선언 라벨만, 하지 않은 일 미표시, 콜백 실패에도 답변 유지."""
    from pension_agent.common import progress as PROG
    from pension_agent.nodes import plan as P

    ok = 0
    events: list[str] = []

    # 도구 진행은 도구 선언 라벨로(LLM 질의 아님)
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

    # 조사는 받침으로 — 병기("을(를)") 미노출
    hit = (PROG.object_of("상담 화법") == "상담 화법을"
           and PROG.object_of("업무 처리 절차") == "업무 처리 절차를"
           and PROG.object_of("IRP") == "IRP을(를)")   # 한글 아니면 병기
    print(f"{'✓' if hit else '✗'} 진행 문구의 목적격 조사가 받침에 맞게 붙는다")
    ok += hit

    # 재료 0건 턴은 compose 가 생성 없이 답함 — '작성' 진행 미표시
    events.clear()
    with PROG.reporting(events.append):
        out = P.compose({"question": "질문", "evidence": [], "steps": []})
    hit = events == [] and bool(out["answer"])
    print(f"{'✓' if hit else '✗'} 재료 0건 턴은 작성 진행을 알리지 않는다 — {events}")
    ok += hit

    # 진행 표시는 곁가지 — 콜백 실패에도 답변 생성
    def broken(text: str) -> None:
        raise RuntimeError("표시 실패")

    with PROG.reporting(broken):
        out = P.compose({"question": "질문", "evidence": [], "steps": []})
    hit = bool(out["answer"])
    print(f"{'✓' if hit else '✗'} 진행 콜백이 죽어도 답변은 나온다")
    ok += hit

    # 콜백 없으면(배치·테스트 기본) emit 은 no-op
    PROG.emit("아무도 안 듣는 진행")   # 예외 없으면 통과
    print("✓ 콜백 없는 emit 은 no-op 이다")
    ok += 1
    return ok


def check_order_flipped() -> int:
    """카드 선택 1차가 LLM 인지 — n-gram 은 LLM 0건 시 폴백."""
    target = tools.KB.pitches[0]
    visited: list[str] = []

    def spy_retrieve(kb, **kw):
        visited.append("retrieve")
        return []

    orig_pick, orig_retrieve, orig_verify = tools.llm_pick, tools.retrieve, tools.fits_question
    tools.retrieve, tools.fits_question = spy_retrieve, lambda q, h, kind="", history=None, query=None, sink=None: h
    ok = 0
    try:
        # LLM 이 골랐으면 n-gram 미실행
        tools.llm_pick = lambda kinds, query: [(2.0, target)]
        found = tools._pitch({"question": "질문"}, "질문")
        hit = not visited and found is not None and found["sources"][0]["id"] == target["id"]
        print(f"{'✓' if hit else '✗'} LLM 채택 → n-gram 미실행(retrieve {len(visited)}회)")
        ok += hit

        # LLM 0건이면 n-gram 폴백(조건부 → 조건 완화 2회)
        tools.llm_pick = lambda kinds, query: []
        found = tools._pitch({"question": "질문", "stage": "신규"}, "질문")
        hit = len(visited) == 2 and found is None
        print(f"{'✓' if hit else '✗'} LLM 0건 → n-gram 폴백 실행(retrieve {len(visited)}회)")
        ok += hit

        # LLM 선택도 게이트 적용
        tools.llm_pick = lambda kinds, query: [(2.0, target)]
        tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: []
        hit = tools._pitch({"question": "질문"}, "질문") is None
        print(f"{'✓' if hit else '✗'} LLM 선택도 적합성 게이트 적용")
        ok += hit
    finally:
        tools.llm_pick, tools.retrieve, tools.fits_question = orig_pick, orig_retrieve, orig_verify

    return ok


def check_tool_loop() -> int:
    """계획 루프 — 한 턴 다중 도구 결합과 코드가 쥔 경계(상한·반복·카탈로그)."""
    ok = 0
    orig_gen, orig_verify = plan.generate, tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h

    # 절차 카드는 검색 1위가 아닌 이름으로 고정 — 검색 순위 변동에 흔들리지 않게(보는 것은 선별 복구)
    from pension_agent.tools.kb_search import procedure_qa as _proc_qa
    _proc_card = next(c for c in tools.KB.cards if c["id"] == "proc.041")   # 화면번호 + status=확인 필요
    _orig_proc_search = _proc_qa.search
    _proc_qa.search = lambda q, _c=_proc_card: [(2.0, _c)]
    try:
        # 두 도구 → 두 근거가 한 답변에. 절차 질의는 status=확인 필요 카드 겨냥(강제 표시는 상충 상태 표기뿐)
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

        # 복구는 근거별 · 빠진 표시만 — 블록 통째 덤프 금지(procedure 는 notices 첨부, fact 는 값 미언급이라 없음)
        plan.generate = lambda prompt, **kw: "두 가지를 함께 보시면 돼요."
        out = plan.compose(state)
        ev_fact = next(e for e in state["evidence"] if e["tool"] == "fact")
        ev_proc = next(e for e in state["evidence"] if e["tool"] == "procedure")
        proc_notice = ev_proc["notices"][0]
        hit = (out["answer"].startswith("두 가지를 함께 보시면 돼요.")
               and proc_notice in out["answer"]          # ⚠ 유의 때문에 복구
               and ev_proc["text"] not in out["answer"]  # 카드 전문 미첨부
               and ev_fact["text"] not in out["answer"]  # 값 미언급 → 복구 불필요
               and len(out["sources"]) >= 2)
        print(f"{'✓' if hit else '✗'} 근거별 선별 복구(procedure 붙고 fact 안 붙음) · 근거 {len(out['sources'])}건")
        ok += hit

        # 원장 밖 수치 → 생성문 폐기 · 근거 원문으로 답변
        blocks = [e["text"] for e in state["evidence"]]
        plan.generate = lambda prompt, **kw: "한도는 1,234,567원이에요."
        out_bad = plan.compose(state)
        hit = ("1,234,567" not in out_bad["answer"]
               and all(b in out_bad["answer"] for b in blocks)
               # 폴백은 «내가 쓴 문장 아님»과 사유를 먼저 밝힘 — 안 밝히면 카드 덤프를 답변으로 읽음
               and out_bad["answer"].startswith(plan.RAW_EVIDENCE.split("{", 1)[0])
               and "수치" in out_bad["answer"].split("\n\n", 1)[0])
        print(f"{'✓' if hit else '✗'} 원장 밖 수치 → 생성문 폐기 · 근거 원문으로 답변(사유 표기 포함)")
        ok += hit

        # 상한은 코드 — LLM 이 계속 불러도 MAX_STEPS 에서 끊김
        plan.generate = lambda prompt, **kw: '{"tool": "fact", "query": "무한"}'
        st = {"question": "질문"}
        for _ in range(plan.MAX_STEPS + 3):
            st.update(plan.plan_step(st))
            if st.get("plan_done"):
                break
        hit = len(st.get("steps") or []) <= plan.MAX_STEPS
        print(f"{'✓' if hit else '✗'} MAX_STEPS 상한 준수(호출 {len(st.get('steps') or [])}회 ≤ {plan.MAX_STEPS})")
        ok += hit

        # 같은 도구·같은 질의 재호출 차단 — 근거 0건이면 재계획 한 번 뒤 종료
        st2 = {"question": "질문", "steps": [{"tool": "fact", "query": "무한", "outcome": "miss"}]}
        st2.update(plan.plan_step(st2))
        first = st2.get("plan_retry") is True and not st2.get("plan_done")
        st2.update(plan.plan_step(st2))
        hit = first and st2.get("plan_done") is True and len(st2["steps"]) == 1
        print(f"{'✓' if hit else '✗'} 같은 호출 반복 차단(재계획 한 번 뒤 종료)")
        ok += hit

        # 근거가 이미 있으면 반복 시 재계획 없이 종료
        st2e = {"question": "질문", "steps": [{"tool": "fact", "query": "무한", "outcome": "miss"}],
                "evidence": [{"tool": "fact", "query": "q", "text": "블록", "atomic": [],
                              "notices": [], "notice_scopes": [], "marks": [], "related": [],
                              "allow": ["블록"], "sources": [], "meta": {}}]}
        st2e.update(plan.plan_step(st2e))
        hit = st2e.get("plan_done") is True
        print(f"{'✓' if hit else '✗'} 근거가 있으면 반복 즉시 종료(재계획 없음)")
        ok += hit

        # 미등록 도구 이름은 미실행
        plan.generate = lambda prompt, **kw: '{"tool": "존재하지_않는_도구", "query": "x"}'
        st3 = plan.plan_step({"question": "질문"})
        st3_done = plan.plan_step({"question": "질문", "plan_retry": True})
        hit = "evidence" not in st3 and not st3.get("plan_done") \
            and st3_done.get("plan_done") is True and "evidence" not in st3_done
        print(f"{'✓' if hit else '✗'} 미등록 도구 이름 차단")
        ok += hit

        # 등록됐지만 이번 턴 카탈로그 밖 도구도 미실행 — 계획 프롬프트 규칙에 도구 이름이 있어 LLM 이 고름
        hidden = []
        for name, st_in in (("last_answer", {"question": "짧게 줄여줘", "history": []}),
                            ("customer", {"question": "이 고객 왜 관리 대상이야?"}),
                            ("screen", {"question": "화면번호",
                                        "steps": [{"tool": "screen", "query": "x",
                                                   "outcome": "failed", "reason": "KeyError"}]})):
            plan.generate = lambda prompt, _n=name, **kw: f'{{"tool": "{_n}", "query": "[1]"}}'
            out = plan.plan_step(st_in)
            ran = any(s.get("tool") == name for s in out.get("steps") or [])
            hidden.append(name in tools.TOOLS and not ran and "evidence" not in out)
        hit = all(hidden)
        print(f"{'✓' if hit else '✗'} 카탈로그 밖 도구(등록은 됨)를 부르면 실행하지 않는다")
        ok += hit

        hit = plan.compose({"question": "질문"})["answer"] == plan.NO_EVIDENCE
        print(f"{'✓' if hit else '✗'} 원장 0건 → 정직한 '근거 없음'")
        ok += hit

        # 고객 화면이 닫혀 있으면 customer 도구 미노출(스텝 낭비 방지)
        hit = ("customer" not in tools.catalog({})
               and "customer" in tools.catalog({"customer_id": "CX"}))
        print(f"{'✓' if hit else '✗'} 쓸 수 없는 도구는 카탈로그에서 제외")
        ok += hit

        # 계획 프롬프트에 남은 호출 수 표시 — last: true 판단 재료
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


def check_tone_and_marks() -> int:
    """답변 말투 「-다」체 누출 방지 · 표시(※)는 제 줄에 — 값 없는 미선언 팩트는 원문 미강제."""
    from pension_agent.evidence import relations as REL
    from pension_agent.prompts import COMPOSE_SYSTEM
    from pension_agent.state import KB as _KB
    from pension_agent.tools.kb_search.cards import fact_evidence

    ok = 0
    f65 = _KB.facts["fact.k04.f65"]
    ev = fact_evidence("q", [(1.0, f65)])
    hit = ev is not None and not REL.declared(f65) and ev["atomic"] == []
    print(f"{'✓' if hit else '✗'} 숫자가 화면번호뿐인 미선언 팩트(F65)는 원문을 강제하지 않는다"
          + ("" if hit else f" — {ev and ev['atomic']}"))
    ok += hit

    # 값이 있는 미선언 팩트는 여전히 강제 — 종류 단위 해제 아님
    valued = next(f for f in _KB.facts.values()
                  if not REL.declared(f) and (numbers(f.get("value") or "")
                                              - {t for s in f.get("screens") or [] for t in numbers(s)}))
    ev = fact_evidence("q", [(1.0, valued)])
    hit = ev is not None and valued["value"] in ev["atomic"]
    print(f"{'✓' if hit else '✗'} 값이 있는 미선언 팩트({valued['id']})는 그대로 원문을 강제한다")
    ok += hit

    hit = "「-다」로 끝나는 문어체여도" in COMPOSE_SYSTEM and "줄을 바꿔 따로 세운다" in COMPOSE_SYSTEM \
        and "직접 쓰지 않는다 — 시스템이 답변 아래에 세운다" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 작성 규칙이 해요체 유지·표시는 코드가 아래에 세움·줄바꿈을 시킨다")
    ok += hit

    # 표시 예시는 notices 로 남는 종류(⚠ 유의) — HISTORY_MARK 는 본문에 안 섬(material.check_history_material)
    mark = "⚠ 유의 — 기록의 값은 그때 기준이라 지금과 다를 수 있어요."
    inline = f"밝히신 적이 있어요. {mark}"
    hit = plan._break_marks(inline) == f"밝히신 적이 있어요.\n{mark}" \
        and plan._break_marks(f"앞 문장.\n{mark}") == f"앞 문장.\n{mark}" \
        and plan._break_marks("금리 3.5% ※표시 아님") == "금리 3.5% ※표시 아님"
    print(f"{'✓' if hit else '✗'} 앞 문장에 이어 붙은 표시를 제 줄로 내린다(이미 제 줄이면 그대로)")
    ok += hit

    # compose 통과 답변에도 적용
    ev_h = tools._ev("history", "q", "■ 지난 상담\n2025-10-06 재투자 의사", [{"id": "h", "title": "h"}],
                     notices=[mark])
    orig = plan.generate
    try:
        plan.generate = lambda p, **kw: inline
        out = plan.compose({"question": "q", "evidence": [ev_h]})
    finally:
        plan.generate = orig
    hit = f"있어요.\n{mark}" in out["answer"] and out["answer"].count(mark) == 1
    print(f"{'✓' if hit else '✗'} 통과한 답변의 표시가 제 줄에 서고 중복 덧붙임은 없다")
    ok += hit
    return ok


def check_atomic_spans() -> int:
    """원문 스팬 집행 — 도구 종류가 아닌 재료가 보호 수준 결정(verify_texts 가 못 잡는 값 재조합 차단)."""
    ok = 0
    VALUE = "총급여 5,500만원 이하 16.5%, 초과 13.2% (지방소득세 포함)"
    ev_num = tools._ev("fact", "q", f"■ 세액공제율\n{VALUE}", [{"id": "f.1", "title": "세액공제율"}],
                       atomic=[VALUE])
    ev_mark = tools._ev("fieldtip", "q", f"■ 팁\n  {tools.FIELDTIP_MARK}\n현장 관찰 요약.",
                        [{"id": "tip.1", "title": "팁"}], notices=[tools.FIELDTIP_MARK])

    orig = plan.generate
    try:
        # 값 미언급이면 원문 미요구 — 표 덤프 방지
        plan.generate = lambda p, **kw: "세액공제율은 소득에 따라 달라져요."
        out = plan.compose({"question": "q", "evidence": [ev_num]})
        hit = out["answer"] == "세액공제율은 소득에 따라 달라져요." and VALUE not in out["answer"]
        print(f"{'✓' if hit else '✗'} 값 미언급 → 원문 스팬 미요구(산문만)")
        ok += hit

        # 값 원문 인용은 그대로 통과
        plan.generate = lambda p, **kw: f"정리하면 이래요. {VALUE} 라고 안내하시면 돼요."
        out = plan.compose({"question": "q", "evidence": [ev_num]})
        hit = out["answer"].count(VALUE) == 1 and "빠뜨리면" not in out["answer"]
        print(f"{'✓' if hit else '✗'} 값 원문 인용 → 블록 덧붙임 없이 통과")
        ok += hit

        # 값 재조합은 생성문 폐기 — 두 숫자 모두 원장에 있어 수치 집합 검사로는 통과하는 문장
        wrong = "총급여 5,500만원 초과면 16.5% 예요."
        assert verify_texts(wrong, [ev_num["text"]])[0], "수치 검사만으로는 통과해야 한다(구멍 재현)"
        plan.generate = lambda p, **kw: wrong
        out = plan.compose({"question": "q", "evidence": [ev_num]})
        hit = wrong not in out["answer"] and VALUE in out["answer"]
        print(f"{'✓' if hit else '✗'} 값 재조합 → 생성문 폐기 · 원문으로 답변")
        ok += hit

        # 숫자 없는 표시(「본부 지침 아님」) 누락은 폐기 아닌 덧붙임
        plan.generate = lambda p, **kw: "현장에서는 KPI부터 본다고 해요."
        out = plan.compose({"question": "q", "evidence": [ev_mark]})
        hit = ("현장에서는" in out["answer"] and tools.FIELDTIP_MARK in out["answer"]
               and "빠뜨리면" not in out["answer"]         # 머리말 없음
               and ev_mark["text"] not in out["answer"])   # 표시만, 카드 전문 미첨부
        print(f"{'✓' if hit else '✗'} 필수 표시 누락 → 생성문 유지 + 빠진 표시만 덧붙임(머리말 없이)")
        ok += hit

        # 표지 없는 표시는 ※ 를 앞세워 첨부 — 본문 이어진 문단으로 읽히지 않게
        caveat = "단, 세액공제 전 결정세액이 공제액보다 적으면 최대 환급액을 받지 못한다."
        ev_plain = tools._ev("tax_credit", "q", "■ 환급 계산\n300만원 추가 납입 시 495,000원",
                             [{"id": "t.1", "title": "환급"}], notices=[caveat])
        prompts_seen: list[str] = []

        def gen_capture(p, **kw):
            prompts_seen.append(p)
            return "300만원 더 넣으면 495,000원을 돌려받을 수 있어요."

        plan.generate = gen_capture
        out = plan.compose({"question": "q", "evidence": [ev_plain]})
        hit = f"\n※ {caveat}" in out["answer"] and out["answer"].count(caveat) == 1
        print(f"{'✓' if hit else '✗'} 표지 없는 표시는 ※ 를 앞세워 제 줄에 붙는다")
        ok += hit

        # 표시는 <필수 인용> 미포함 — 넣으면 LLM 이 일부만 베끼고 코드가 또 붙여 중복. atomic 은 필수 인용 유지
        hit = bool(prompts_seen) and "<필수 인용" not in prompts_seen[0] and caveat not in prompts_seen[0]
        print(f"{'✓' if hit else '✗'} 표시(notices)는 필수 인용에 넣지 않는다(코드가 아래에 세운다)")
        ok += hit
        prompts_seen.clear()
        plan.generate = gen_capture
        plan.compose({"question": "q", "evidence": [ev_num]})
        hit = bool(prompts_seen) and "<필수 인용" in prompts_seen[0] and VALUE in prompts_seen[0]
        print(f"{'✓' if hit else '✗'} 값+조건 스팬(atomic)은 여전히 필수 인용이다")
        ok += hit

        # 화법은 atomic 이 빈 도구일 뿐 경로 동일
        ev_pitch = tools._ev("pitch", "q", "화법 카드 컨텍스트", [{"id": "p.1", "title": "화법"}])
        plan.generate = lambda p, **kw: "고객에게는 이렇게 말해보세요."
        out = plan.compose({"question": "q", "evidence": [ev_pitch]})
        hit = (ev_pitch["atomic"] == [] and ev_pitch["notices"] == []
               and out["answer"] == "고객에게는 이렇게 말해보세요.")
        print(f"{'✓' if hit else '✗'} 화법은 atomic·notices 가 빈 도구일 뿐(경로 동일)")
        ok += hit

        # 도구가 실제로 스팬을 선언하는지 — fact 는 관계 미선언 카드로(선언 카드는 check_relations)
        from pension_agent.evidence import relations as REL
        from pension_agent.tools.kb_search import facts_qa as FQ
        from pension_agent.state import KB as _KB
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

        # 화면번호를 인용하는 주의 표시(screen 비고 caution)를 값 스팬으로 오판 금지
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
    """표시는 답변이 실제로 쓴 카드의 것만, 표시만 첨부 — 카드 전문 덤프 금지."""
    from pension_agent.knowledge.kb import role_texts
    from pension_agent.state import KB

    ok = 0
    orig_gen, orig_pick = plan.generate, tools.pick
    by_id = {c["id"]: c for c in KB.cards}
    # caution 가진 카드 두 장(안 쓴 06-10-182 · 쓴 75-08-110). 두 번호는 숫자 조각이 겹치면 안 됨 — 겹치면 _span_verdict DISCARD 가 먼저 걸림
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

        # [75-08-110] 만 인용 → [06-10-182] 표시 미첨부
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

        # 쓴 카드 분간 불가(화면번호 미인용)면 표시 전부 유지
        plan.generate = lambda p, **kw: "두 화면을 함께 확인하시면 돼요."
        out = plan.compose({"question": "q", "evidence": [found]})
        hit = used_mark in out["answer"] and unused_mark in out["answer"]
        print(f"{'✓' if hit else '✗'} 분간이 안 되면 표시를 잃지 않는다(전부 유지)")
        ok += hit
    finally:
        plan.generate, tools.pick = orig_gen, orig_pick
    return ok


def check_plan_failure() -> int:
    """계획 깨짐(LLM 예외·규격 밖 응답·도구 고장)과 재료 없음을 다르게 안내하는지."""
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
            # generate() 는 프로바이더 예외를 LLMError 로 통일(llm.py)
            raise LLMError("LLM 미설정")

        state, answer = drive(dead)
        hit = bool(state.get("llm_error")) and "자료가 없다는 뜻이 아니" in answer \
            and plan.NO_EVIDENCE not in answer
        print(f"{'✓' if hit else '✗'} LLM 호출 실패를 '근거 없음'으로 둔갑시키지 않음")
        ok += hit

        # 잘린 JSON — max_tokens 빠듯할 때의 응답 형태
        state, answer = drive(lambda p, **kw: '{"tool": "pitch", "query": "고객이 주식이')
        hit = "JSON" in (state.get("llm_error") or "") and plan.NO_EVIDENCE not in answer
        print(f"{'✓' if hit else '✗'} 규격 밖·잘린 응답도 원인을 남긴다")
        ok += hit

        # 계획 정상 + 재료 0건은 '근거 없음'
        state, answer = drive(lambda p, **kw: '{"done": true}')
        hit = not state.get("llm_error") and answer == plan.NO_EVIDENCE
        print(f"{'✓' if hit else '✗'} 정상 종료 + 재료 0건은 여전히 '근거 없음'")
        ok += hit

        # 질문을 되받아 적어도 안 잘릴 토큰 상한
        hit = plan.PLAN_MAX_TOKENS >= 200
        print(f"{'✓' if hit else '✗'} 계획 응답 토큰 상한 {plan.PLAN_MAX_TOKENS}")
        ok += hit

        # 도구가 죽은 턴 — tools.run 이 예외를 삼키면 «근거 없음»으로 둔갑
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
               and "자료가 없다는 뜻이 아니" in answer
               and "KeyError" in answer)
        print(f"{'✓' if hit else '✗'} 도구가 죽은 것을 '근거 없음'으로 둔갑시키지 않는다")
        ok += hit

        # 도구 고장은 LLM 실패와 별도 기록 — 처분(루프 유지 vs 중단)이 갈림
        hit = not state.get("llm_error")
        print(f"{'✓' if hit else '✗'} 도구 고장을 LLM 실패로 기록하지 않는다")
        ok += hit

        # 죽은 도구는 이번 턴 카탈로그에서 제외 — 다시 골라 바퀴 낭비 방지
        broken = {"steps": [{"tool": "screen", "query": "운용현황",
                             "outcome": "failed", "reason": "KeyError"}]}
        hit = "screen" not in tools.usable(broken) and "screen" in tools.usable({})
        print(f"{'✓' if hit else '✗'} 죽은 도구는 이번 턴 카탈로그에서 빠진다")
        ok += hit

        # 근거 0건 안내문에 호출 내역(도구 이름·질의) 미표시
        tried = plan._no_evidence({"steps": [
            {"tool": "screen", "query": "운용현황", "outcome": "failed", "reason": "KeyError"},
            {"tool": "fact", "query": "수수료", "outcome": "miss"}]})
        hit = (tried == plan.NO_EVIDENCE
               and not any(w in tried for w in ("수수료", "운용현황", "fact", "screen", "찾아본 곳")))
        print(f"{'✓' if hit else '✗'} 근거 0건 안내문에 호출 내역(도구 이름·질의)이 서지 않는다")
        ok += hit

        # 계획 프롬프트 서명은 도구 이름 그대로 — 같은 호출 재선택 방지 좌표
        sig = plan._label({"tool": "fact", "query": "수수료"})
        hit = sig == "fact:수수료"
        print(f"{'✓' if hit else '✗'} 계획 프롬프트의 서명은 도구 이름을 그대로 쓴다 ({sig})")
        ok += hit

        # 사유는 str(예외) 그대로라 길이 무제한 — 도구마다 한 줄로 자름
        long_reason = "JSONDecodeError: " + "x" * 400 + "\n  File \"tools/kb_search/market.py\", line 91"
        notice = plan.compose({"question": "q", "evidence": [], "steps": [
            {"tool": "screen", "query": "운용현황", "outcome": "failed", "reason": long_reason},
            {"tool": "fact", "query": "수수료", "outcome": "failed", "reason": "KeyError: 'as_of'"},
        ]})["answer"]
        hit = ("x" * 400 not in notice and "JSONDecodeError" in notice
               and "tools/kb_search/market.py" not in notice      # 둘째 줄 미표시
               and "KeyError: 'as_of'" in notice        # 뒤쪽 도구 사유 유지
               and len(notice) < 400)
        print(f"{'✓' if hit else '✗'} 도구 고장 사유도 한 줄로 자른다(도구마다 · {len(notice)}자)")
        ok += hit

        # 원장이 끝내 빈 턴 — LLM 실패 안내와 같은 꼴
        notice = plan.compose({"question": "q", "evidence": [], "steps": [
            {"tool": "screen", "query": "운용현황", "outcome": "failed", "reason": "x"}]})["answer"]
        hit = (notice.startswith("지금은 답변을 만들 수 없어요")
               and plan.LLM_FAILED.format(reason="x") != notice
               and "단말 화면번호" in notice)     # 도구 선언 라벨로 실패 대상 표시
        print(f"{'✓' if hit else '✗'} 실패 안내가 무엇을 못 읽었는지 밝힌다")
        ok += hit
    finally:
        plan.generate, tools.fits_question = orig_gen, orig_verify
    return ok


def check_llm_down() -> int:
    """LLM 이 어느 단계에서 죽든 같은 안내로 종료(슬롯 분해 LLMError 는 tools.run → plan_step 이 처리)."""
    ok = 0

    def dead(*a, **kw):
        raise LLMError("LLM 미설정 — PROVIDER=none")

    # 슬롯 분해에서 죽어도 계획 루프가 받아 같은 안내
    orig_pitch, orig_plan = pitch_slots.generate, plan.generate
    orig_extract, orig_pick = pitch_slots.extract_slots, tools.llm_pick
    pitch_slots.generate = dead
    pitch_slots.extract_slots = _REAL_EXTRACT_SLOTS   # 분해 자체를 재므로 원본
    plan.generate = lambda p, **kw: '{"tool": "pitch", "query": "수수료"}'
    tools.llm_pick = lambda kinds, q: []        # n-gram 폴백이어야 슬롯 추출
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

    # 그래프 전체 — 모든 단계가 죽어도 안내로 종료(진짜 노드)
    saved = {n: getattr(G, n) for n in ("understand", "plan_step")}
    origs = (understand.generate, pitch_slots.generate, plan.generate)
    G.understand, G.plan_step = understand.understand, plan.plan_step
    understand.generate = pitch_slots.generate = plan.generate = dead
    try:
        out = G.build_agent().invoke({"question": "사업자 고객인데 수수료 부담된다고 하시네요"})
        answer = out.get("answer", "")
        hit = ("자료가 없다는 뜻이 아니" in answer
               and plan.NO_EVIDENCE not in answer and "LLMError" in answer)
    except Exception as exc:
        answer, hit = f"({type(exc).__name__})", False
    finally:
        for name, fn in saved.items():
            setattr(G, name, fn)
        understand.generate, pitch_slots.generate, plan.generate = origs
    print(f"{'✓' if hit else '✗'} 화법 상황 질문 + LLM 미설정 → 크래시 없이 안내 — {answer[:38]}")
    ok += hit

    # 문장 작성만 죽은 경우 — 근거 원문 덤프는 완성 답변처럼 보여 금지
    orig = plan.generate
    plan.generate = dead
    try:
        evidence = [{"tool": "fact", "query": "한도", "text": "세액공제 한도는 900만원이다.",
                     "atomic": [], "notices": [], "notice_scopes": [], "allow": [],
                     "sources": [{"id": "f1"}], "meta": {}}]
        answer = plan.compose({"question": "한도가 얼마야?", "evidence": evidence})["answer"]
        hit = "자료가 없다는 뜻이 아니" in answer and "900만원" not in answer
    finally:
        plan.generate = orig
    print(f"{'✓' if hit else '✗'} compose: 문장 작성 실패를 근거 원문 덤프로 덮지 않음")
    ok += hit

    # 역방향 — 앞 단계 일시 실패여도 계획 정상이면 '근거 없음' 유지
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
    """점검에 걸린 생성문을 한 번 재작성 — 걸린 자리를 프롬프트에 실음, 상한 후 근거 원문."""
    from pension_agent.nodes import plan

    ok = 0
    evidence = [{"tool": "fact", "query": "한도", "text": "■ 세액공제 한도\n\n한도는 900만원이다.",
                 "atomic": [], "notices": [], "notice_scopes": [],
                 "allow": ["한도는 900만원이다."], "sources": [{"id": "f1"}],
                 "related": [], "marks": [], "meta": {}}]
    state = {"question": "한도가 얼마야?", "evidence": evidence}

    # 첫 생성문이 원장 밖 수치면 두 번째 결과가 답
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

    # 재작성 프롬프트에 걸린 자리 포함 — 없으면 같은 문장 반복
    hit = len(seen) == 2 and "1,234" in seen[1] and "다시 쓴다" in seen[1]
    print(f"{'✓' if hit else '✗'} 재작성 프롬프트에 걸린 자리가 실린다")
    ok += hit

    # 계속 걸리면 상한(COMPOSE_RETRIES)에서 근거 원문 — 틀린 문장 출력 금지
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
           and answer.startswith(plan.RAW_EVIDENCE.split("{", 1)[0])
           and evidence[0]["text"] in answer and "1,234만" not in answer)
    print(f"{'✓' if hit else '✗'} 계속 걸리면 상한에서 멈추고 근거 원문이 답이다({len(tries)}회)")
    ok += hit

    return ok
