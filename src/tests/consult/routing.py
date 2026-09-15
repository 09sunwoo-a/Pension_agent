"""그래프 배선 — 인텐트 라우팅 · 즉답 · 적합성 게이트 · 하지말것 가드.

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import routing, tools
from pension_agent.consult_agent.nodes import pitch, plan, understand
from pension_agent.llm import LLMError

from tests.consult._common import print, _OVERRIDES, stub_understand, stub_plan_pitch  # noqa: A001 — 집계용 print


def check_pitch_stages() -> bool:
    """화법 도구가 LLM → 조건부 n-gram → 조건 완화 3단으로 물러서는지 검증한다.

    예전 그래프의 llm_select → retrieve → broaden 이 이 도구 안으로 접혔다. 접히면서
    단계가 사라졌는지(특히 조건 완화 재검색) 잡는 것이 이 테스트의 목적이다.
    """
    calls: list[dict] = []
    real = tools.KB.pitches[0]
    # 이 검사가 재는 것은 후퇴 단계이지 슬롯 분해가 아니다 — 상태에 심어둔 슬롯을 그대로
    # 돌려주게 해서, 1회차가 조건으로 좁히는지만 본다.
    pitch.extract_slots = lambda st: {k: st.get(k)
                                      for k in ("customer_type", "objection_type", "stage")}

    def spy_retrieve(kb, **kw):
        calls.append(kw)
        # 1회차(조건 있음)는 0건, 2회차(조건 풀림)에서 찾은 것으로 흉내낸다
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
          and calls[0].get("customer_type") == "사업자"        # 1회차는 슬롯으로 좁힌다
          and not any(calls[1].get(k) for k in ("customer_type", "objection_type", "stage"))
          and found is not None and found["sources"][0]["id"] == real["id"])
    print(f"{'✓' if ok else '✗'} 화법 도구 3단 후퇴(n-gram {len(calls)}회 · 2회차 조건 완화)")
    return ok


_ROUTED_INTENTS = ("lms_link", "correction", "confirm_action")


# 예전에는 값·절차·고객군·브리핑 질의도 각자 intent 와 노드를 갖고 있었다. 능력 표면이
# 도구 목록이 되면서(CLAUDE.md §3) 그 넷은 전용 노드를 잃고 계획 루프로 합쳐졌다 —
# 같은 재료를 두 경로로 답하면 프롬프트·검증·표시 규약이 갈리기 때문이다(§12 gap 11).
_PLAN_ROUTED = ("situation", "guide", "fact_lookup", "briefing_qa",
                "objection_drill", "없는_의도")


def check_intent_routing() -> bool:
    """인텐트가 understand 분류에 따라 올바른 노드로 라우팅되는지 확인한다 — 각 노드 자체의
    로직은 해당 파일에서 별도로 검증할 대상이라, 여기서는 그래프 배선만 본다."""
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

    # 지식·고객 재료로 답하는 질문은 전부 계획 루프로 간다. 목록 밖 의도도 마찬가지 —
    # 분류가 어긋났다고 능력이 잘리면 안 된다(무엇으로 답할지는 도구 목록이 정한다).
    for intent in _PLAN_ROUTED:
        this_ok = routing.route_intent({"intent": intent}) == "plan"
        ok = ok and this_ok
        print(f"{'✓' if this_ok else '✗'} intent={intent} → 계획 루프(plan)")

    # LLM 이 죽은 턴은 무엇으로 분류됐든 안내 하나로 끝난다 (§11).
    this_ok = routing.route_intent({"intent": "situation", "llm_error": "X"}) == routing.LLM_DOWN
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} llm_error 가 있으면 → llm_down 노드")

    # understand 가 LLM 실패를 규칙으로 대신 분류하지 않는가 (§11 · gap 9).
    #
    # 예전에는 여기서 키워드 표(guess_intent)로 의도를 어림하고 즉답 노드가 LLM 없이
    # 답을 만들었다. 그 경로가 남아 있으면 "LLM 없이도 절반은 도는" 상태가 굳는다.
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
    """lms_link 는 **보내지 않는다** — 발송 화면 연계를 제안할 뿐이다(§10).

    인용부호 파싱·문구 누락·customer_id 없음을 직접 검증한다(LLM 을 쓰지 않는 노드다).
    """
    from pension_agent.consult_agent.nodes import lms

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
    """값·절차·고객군 재료가 **도구로** 닿고, 근거에 기준시점·출처가 함께 실리는지 본다.

    예전에는 같은 것을 즉답 노드(fact_lookup·procedure·segment_explain)로 쟀다. 그 노드들은
    §11 에 따라 지웠다 — LLM 없이 답을 만드는 경로였기 때문이다. 재료 자체는 그대로 남아
    도구가 쓰므로, 재는 자리를 노드에서 도구로 옮긴다.

    확인하는 것은 두 가지다. ① 질문에 맞는 근거를 실제로 찾는가, ② 근거 블록에 기준시점·
    출처처럼 '이 값을 언제·어디 근거로 말하는지'가 함께 나오는가. ②가 빠지면 숫자만 맞고
    근거가 없는 답이 되어, 직원이 그대로 고객에게 옮길 수 없다.
    """
    from pension_agent.consult_agent.nodes import facts_qa

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

    # 고객 대사를 그대로 던진 질문도 같은 화법 재료로 답한다 — 전용 양식 노드는 없다.
    q = "증권사는 수수료 무료라는데요"
    found = tools.run("pitch", {"question": q}, q)
    this_ok = bool(found) and found["tool"] == "pitch" and bool(found["sources"])
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} 고객 대사 질문도 pitch 재료로 닿는다")

    import importlib
    gone = importlib.util.find_spec("pension_agent.consult_agent.nodes.drill") is None
    ok = ok and gone
    print(f"{'✓' if gone else '✗'} 즉답 카드 양식 노드(drill)가 남아 있지 않다")

    # 지식베이스에 없는 것을 물으면 도구가 근거를 만들어내지 않는다.
    no_invent = not facts_qa.search("오늘 서울 날씨 어때?")
    ok = ok and no_invent
    print(f"{'✓' if no_invent else '✗'} fact 도구: 없는 값은 지어내지 않고 0건으로 답한다")

    # 지운 즉답 노드가 되살아나지 않았는가 (§11 회귀).
    from pension_agent.consult_agent.nodes import procedure_qa, segment_qa
    gone = not any(hasattr(m, fn) for m, fn in
                   ((facts_qa, "fact_lookup"), (procedure_qa, "procedure"),
                    (segment_qa, "segment_explain")))
    ok = ok and gone
    print(f"{'✓' if gone else '✗'} LLM 없이 답을 만들던 즉답 노드 3종이 남아 있지 않다")
    return ok


def check_verify_gate() -> bool:
    """적합성 게이트가 NO 를 내면(의도 불일치) 카드가 검색됐어도 근거로 안 쓰이는지 검증한다.

    게이트가 그래프의 노드에서 화법 도구 안으로 옮겨졌다. 거부되면 도구가 None 을 반환하고,
    원장이 비면 compose 가 정직하게 '근거 없음'으로 답한다.
    """
    G.understand = stub_understand
    G.plan_step = stub_plan_pitch
    agent = G.build_agent()

    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: []
    try:
        out = agent.invoke({"question": "사업자 고객인데 수수료 부담된다고 하시네요"})
    finally:
        tools.fits_question = orig

    # '없다'에는 무엇을 찾아봤는지가 덧붙는다(§5) — 앞부분이 같은지로 본다.
    ok = not out.get("sources") and (out.get("answer") or "").startswith(plan.NO_EVIDENCE)
    print(f"{'✓' if ok else '✗'} 적합성 게이트 NO → 근거 없음 답변")
    return ok


def check_guard() -> int:
    """「하지 말 것」 가드 — 지식베이스에 있는 것만.

    07_에이전트_기능정의/01 ① 필수 구성 요소 6. 규칙을 새로 쓰지 않고 행원들이 정리해둔
    method.cautions(caution 역할)와 민감 응대 화법 카드만 쓴다. 재료가 없으면 만들지 않는다.
    """
    from pension_agent.consult_agent import guard as GD
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

    cases.append((GD.cautions_for(gkb, []) == [], "요건이 없으면 가드도 없다"))
    cases.append((GD.cautions_for(gkb, ["zzz:없는요건"]) == [],
                  "모르는 요건에 가드를 지어내지 않는다"))
    cases.append((GD.prompt_note([], []) == "", "가드가 없으면 프롬프트 지시도 빈다"))

    # procedure.cautions 는 전부 "필자 해석 / 확인 필요" 같은 문서 검증 메모다.
    # 상담 경고로 새면 진짜 경고가 그 사이에 묻힌다.
    every = [g for c in ("low", "dor", "dep", "nod", "mat") for g in GD.cautions_for(gkb, [c])]
    cases.append((not any(m in g["text"] for g in every
                          for m in ("필자", "팀 논의", "확인 필요")),
                  "저자 검증 메모가 상담 경고로 새지 않는다"))

    # 프롬프트 지시와 화면 경고가 같은 재료를 쓴다 — 어긋나면 톤과 표시가 따로 논다.
    note = GD.prompt_note(low, alt)
    cases.append((all(g["text"] in note for g in low),
                  "프롬프트 지시가 화면 경고와 같은 문장"))

    for good, label in cases:
        print(f"{'✓' if good else '✗'} {label}")
    return sum(1 for good, _ in cases if good)
