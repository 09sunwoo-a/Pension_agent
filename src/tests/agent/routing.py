"""그래프 배선 — 인텐트 라우팅 · 즉답 · 적합성 게이트 · 하지말것 가드.

`test_agent.py::main` 이 호출(스텁 설치·정리 포함). 직접 실행 금지.
"""

from __future__ import annotations

from pension_agent import graph as G
from pension_agent import routing, tools
from pension_agent.nodes import plan, understand
from pension_agent.tools.kb_search import pitch_slots
from pension_agent.common.llm import LLMError

from tests.agent._common import print, _OVERRIDES, stub_understand, stub_plan_pitch  # noqa: A001 — 집계용 print


def check_pitch_stages() -> bool:
    """화법 도구 3단 후퇴(LLM → 조건부 n-gram → 조건 완화) — 조건 완화 재검색 누락 방지."""
    calls: list[dict] = []
    real = tools.KB.pitches[0]
    # 슬롯 분해는 측정 대상 아님 — 상태의 슬롯을 그대로 반환
    pitch_slots.extract_slots = lambda st: {k: st.get(k)
                                      for k in ("customer_type", "objection_type", "stage")}

    def spy_retrieve(kb, **kw):
        calls.append(kw)
        # 1회차(조건 있음) 0건, 2회차(조건 완화) 적중
        return [] if any(kw.get(k) for k in ("customer_type", "objection_type", "stage")) else [(0.5, real)]

    orig_retrieve, orig_verify = tools.retrieve, tools.fits_question
    tools.retrieve, tools.fits_question = spy_retrieve, lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        found = tools._pitch(
            {"question": "질문", "customer_type": "사업자", "stage": "이탈방어", "objection_type": None},
            "질문",
        )
    finally:
        tools.retrieve, tools.fits_question = orig_retrieve, orig_verify

    ok = (len(calls) == 2                                     # 조건부 → 조건 완화
          and calls[0].get("customer_type") == "사업자"        # 1회차는 슬롯으로 좁힘
          and not any(calls[1].get(k) for k in ("customer_type", "objection_type", "stage"))
          and found is not None and found["sources"][0]["id"] == real["id"])
    print(f"{'✓' if ok else '✗'} 화법 도구 3단 후퇴(n-gram {len(calls)}회 · 2회차 조건 완화)")
    return ok


_ROUTED_INTENTS = ("lms_link", "correction", "confirm_action")


# 계획 루프로 가는 의도 — 전용 노드 금지(두 경로면 프롬프트·검증·표시 규약이 갈림)
_PLAN_ROUTED = ("situation", "guide", "fact_lookup", "briefing_qa",
                "objection_drill", "없는_의도")


def check_intent_routing() -> bool:
    """인텐트별 노드 라우팅(그래프 배선만)."""
    orig = {name: getattr(G, name) for name in _ROUTED_INTENTS}

    def make_probe(name):
        def probe(state):
            return {"answer": f"({name} 응답)", "sources": []}
        return probe

    for name in orig:
        setattr(G, name, make_probe(name))

    ok = True
    for intent in _ROUTED_INTENTS:
        question = f"{intent}-라우팅-테스트"
        _OVERRIDES[question] = {"intent": intent}
        agent = G.build_agent()
        out = agent.invoke({"question": question, "customer_id": "CX"})
        this_ok = out.get("answer") == f"({intent} 응답)"
        ok = ok and this_ok
        print(f"{'✓' if this_ok else '✗'} intent={intent} → {intent} 노드로 라우팅")
        del _OVERRIDES[question]

    # 목록 밖 의도도 계획 루프 — 분류 오류로 능력이 잘리면 안 됨
    for intent in _PLAN_ROUTED:
        this_ok = routing.route_intent({"intent": intent}) == "plan"
        ok = ok and this_ok
        print(f"{'✓' if this_ok else '✗'} intent={intent} → 계획 루프(plan)")

    # LLM 실패 턴은 분류와 무관하게 llm_down
    this_ok = routing.route_intent({"intent": "situation", "llm_error": "X"}) == routing.LLM_DOWN
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} llm_error 가 있으면 → llm_down 노드")

    # LLM 실패를 규칙(guess_intent)으로 대신 분류 금지
    def _dead_llm(*a, **kw):
        raise LLMError("LLM 없음")

    orig_gen = understand.generate
    understand.generate = _dead_llm
    try:
        out = understand.understand({"question": "세액공제 한도"})
    finally:
        understand.generate = orig_gen
    this_ok = out.get("intent") == routing.LLM_DOWN and "LLM 없음" in out.get("llm_error", "")
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} understand: LLM 실패를 규칙으로 대신 분류하지 않는다")

    this_ok = not hasattr(understand, "guess_intent")
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} 규칙 라우팅 폴백(guess_intent)이 남아 있지 않다")

    for name, fn in orig.items():
        setattr(G, name, fn)
    return ok


def check_lms_link_parsing() -> bool:
    """lms_link — 발송 없이 화면 연계만 제안. 인용부호 파싱·문구 누락·customer_id 없음."""
    from pension_agent.nodes import lms

    out = lms.lms_link({"question": '"안내 문구입니다" 로 LMS 보내줘', "customer_id": "CX"})
    pending = out.get("pending_action")
    ok1 = (bool(pending) and pending["kind"] == "lms" and pending["screen"]
           and pending["message"] == "안내 문구입니다"
           and "보낼지는 그 화면에서" in out["answer"])
    ok2 = "큰따옴표" in lms.lms_link({"question": "그냥 보내줘", "customer_id": "CX"})["answer"]
    ok3 = "찾을 수 없어요" in lms.lms_link({"question": '"문구" 보내줘', "customer_id": None})["answer"]
    ok = ok1 and ok2 and ok3
    print(f"{'✓' if ok else '✗'} lms_link: 발송이 아니라 화면 연계를 제안한다")
    return ok


def check_knowledge_intents() -> bool:
    """값·절차·고객군 재료가 도구로 닿고 근거에 기준시점·출처가 함께 실리는지."""
    from pension_agent.tools.kb_search import facts_qa

    checks = [
        ("fact", "세액공제 한도가 얼마야?", ("만원", "출처")),
        ("procedure", "적립금 수익률 조회 화면번호 알려줘", ("화면번호", "출처")),
        ("segment", "현금성자산 편중 고객은 왜 관리 대상이야?", ("골라내나", "출처")),
    ]
    ok = True
    for name, question, expected in checks:
        found = tools.run(name, {"question": question}, question)
        text = (found or {}).get("text", "")
        this_ok = bool(found) and bool(found["sources"]) and all(t in text for t in expected)
        ok = ok and this_ok
        detail = "" if this_ok else f" — {text[:70]!r}"
        print(f"{'✓' if this_ok else '✗'} {name} 도구: 근거를 찾고 출처·기준을 함께 싣는다{detail}")

    # 고객 대사 질문도 pitch 재료로 — 전용 노드 없음
    q = "증권사는 수수료 무료라는데요"
    found = tools.run("pitch", {"question": q}, q)
    this_ok = bool(found) and found["tool"] == "pitch" and bool(found["sources"])
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} 고객 대사 질문도 pitch 재료로 닿는다")

    import importlib
    gone = importlib.util.find_spec("pension_agent.nodes.drill") is None
    ok = ok and gone
    print(f"{'✓' if gone else '✗'} 즉답 카드 양식 노드(drill)가 남아 있지 않다")

    no_invent = not facts_qa.search("오늘 서울 날씨 어때?")
    ok = ok and no_invent
    print(f"{'✓' if no_invent else '✗'} fact 도구: 없는 값은 지어내지 않고 0건으로 답한다")

    # LLM 없는 즉답 노드 부활 방지
    from pension_agent.tools.kb_search import procedure_qa, segment_qa
    gone = not any(hasattr(m, fn) for m, fn in
                   ((facts_qa, "fact_lookup"), (procedure_qa, "procedure"),
                    (segment_qa, "segment_explain")))
    ok = ok and gone
    print(f"{'✓' if gone else '✗'} LLM 없이 답을 만들던 즉답 노드 3종이 남아 있지 않다")
    return ok


def check_verify_gate() -> bool:
    """적합성 게이트 NO → 검색된 카드 미사용, '근거 없음' 답변."""
    G.understand = stub_understand
    G.plan_step = stub_plan_pitch
    agent = G.build_agent()

    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: []
    try:
        out = agent.invoke({"question": "사업자 고객인데 수수료 부담된다고 하시네요"})
    finally:
        tools.fits_question = orig

    # 뒤에 찾아본 범위가 붙음 — 접두만 비교
    ok = not out.get("sources") and (out.get("answer") or "").startswith(plan.NO_EVIDENCE)
    print(f"{'✓' if ok else '✗'} 적합성 게이트 NO → 근거 없음 답변")
    return ok


def check_guard() -> int:
    """「하지 말 것」 가드 — method.cautions·민감 응대 화법 카드만, 재료 없으면 미생성."""
    from pension_agent.tools.customer_solution import guard as GD
    from pension_agent.knowledge import kb as KBM

    gkb = KBM.load_kb()
    cases: list[tuple[bool, str]] = []

    low = GD.cautions_for(gkb, ["low:수익률 하위 30%"])
    cases.append((any("지적" in g["text"] and "개선" in g["text"] for g in low),
                  "low: '지적 대신 개선안'이 지식베이스에서 나온다"))
    cases.append((bool(low) and all(g["card"].startswith("m.") for g in low),
                  "가드 근거는 method 카드"))

    alt = GD.sensitive_cards(gkb, ["low:수익률 하위 30%"])
    cases.append((any(a["card"] == "pitch.k03.028" for a in alt),
                  "low: 민감 응대 대안 화법을 함께 제시"))

    dep = GD.cautions_for(gkb, ["dep:원리금보장상품 편중(80% 이상)"])
    cases.append((any("현금성자산" in g["text"] for g in dep),
                  "dep: 용어 주의(고유계정대→현금성자산)도 지식베이스에서"))

    # idl·hlt·mis 도 caution 재료 있음(m.1-1·m.006 · m.023 · m.005) — 요건 코드 연결 확인
    idl = GD.cautions_for(gkb, ["idl:미운용 현금성자산"])
    cases.append((any("현금성자산" in g["text"] for g in idl)
                  and any("사용계획" in g["text"] for g in idl),
                  "idl: 사용계획 자금 선별·용어 주의가 지식베이스에서 나온다"))
    hlt = GD.cautions_for(gkb, ["hlt:판매중단·환매추천 펀드 보유"])
    cases.append((any(g["card"] == "m.023" and "약속하지 않는다" in g["text"] for g in hlt),
                  "hlt: '갈아탄 상품이 더 좋다고 약속하지 않는다'가 지식베이스에서 나온다"))
    mis = GD.cautions_for(gkb, ["mis:투자성향 불일치"])
    cases.append((any(g["card"] == "m.005" for g in mis),
                  "mis: 이중 접촉 방지 주의가 지식베이스에서 나온다"))
    dy = GD.cautions_for(gkb, ["dor:장기 미접촉", "hlt:판매중단·환매추천 펀드 보유",
                               "idl:미운용 현금성자산", "nod:디폴트옵션 미설정",
                               "isa:ISA 만기자금 보유"])
    cases.append((bool(dy) and {g["cond"] for g in dy} <= {"hlt", "idl"},
                  "송도윤 요건 묶음에 가드가 붙고, 재료 없는 요건(dor·nod·isa)은 비어 있다"))

    cases.append((GD.cautions_for(gkb, []) == [], "요건이 없으면 가드도 없다"))
    cases.append((GD.cautions_for(gkb, ["zzz:없는요건"]) == [],
                  "모르는 요건에 가드를 지어내지 않는다"))
    cases.append((GD.prompt_note([], []) == "", "가드가 없으면 프롬프트 지시도 빈다"))

    # procedure.cautions 는 저자 검증 메모 — 상담 경고 유입 금지
    every = [g for c in GD.TRIGGERS for g in GD.cautions_for(gkb, [c])]
    cases.append((not any(m in g["text"] for g in every
                          for m in ("필자", "팀 논의", "확인 필요")),
                  "저자 검증 메모가 상담 경고로 새지 않는다"))

    # 프롬프트 지시·화면 경고 동일 재료
    note = GD.prompt_note(low, alt)
    cases.append((all(g["text"] in note for g in low),
                  "프롬프트 지시가 화면 경고와 같은 문장"))

    for good, label in cases:
        print(f"{'✓' if good else '✗'} {label}")
    return sum(1 for good, _ in cases if good)
