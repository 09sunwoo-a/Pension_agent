"""되묻기 검사 — 맥락 이어받기 · 근거 충분성 · 판정 골든셋 · 등급형 판정.

`tests/test_agent.py::main` 에서 호출(스텁 설치·정리 포함). 직접 실행 금지.
"""

from __future__ import annotations

import json

from pension_agent import graph as G
from pension_agent import routing, tools
from pension_agent.common.llm import LLMError

from tests.agent._common import print, _REAL_FITS  # noqa: A001 — 집계용 print


def check_context_and_clarify() -> int:
    """후속 질문의 맥락 이어받기와 모호할 때 되묻기 — 함께여야 성립(되물은 답을 이전 질문과 이어 해석)."""
    from pension_agent.nodes import clarify as CL
    from pension_agent.nodes import plan as P
    from pension_agent.state import KB, format_history

    ok = 0
    history = [{"question": "실물이전 어떻게 처리해?", "stage": "계약이전",
                "pending_clarify": {"question": "어느 방향인가요?",
                                    "options": ["타행 → 당행", "당행 → 타행"]}}]

    # 대화 맥락이 계획·작성 프롬프트에 실리는지
    seen: dict[str, str] = {}
    orig_gen = P.generate
    P.generate = lambda prompt, **kw: seen.setdefault(
        "plan" if "쓸 수 있는 도구" in prompt else "compose", prompt) and '{"done": true}'
    try:
        P.plan_step({"question": "타행에서요", "history": history})
        evidence = [{"tool": "procedure", "query": "q", "text": "계약이전 절차입니다.",
                     "atomic": [], "notices": [], "notice_scopes": [],
                     "allow": ["계약이전 절차입니다."], "sources": [], "meta": {}}]
        P.compose({"question": "타행에서요", "history": history, "evidence": evidence})
    finally:
        P.generate = orig_gen
    hit = all("실물이전 어떻게 처리해?" in seen.get(k, "") for k in ("plan", "compose"))
    print(f"{'✓' if hit else '✗'} 이전 대화가 계획·작성 프롬프트에 실린다")
    ok += hit

    hit = "되물음" in format_history(history) and "타행 → 당행" in format_history(history)
    print(f"{'✓' if hit else '✗'} 되물은 내용이 다음 턴의 맥락으로 남는다")
    ok += hit

    # 갈래가 있으면 답변 대신 질문으로 종료
    evidence = [{"tool": "procedure", "query": "실물이전", "text": "타행→당행 절차 / 당행→타행 절차",
                 "atomic": [], "notices": [], "notice_scopes": [], "allow": [],
                 "sources": [], "meta": {}}]
    orig_gen = CL.generate
    CL.generate = lambda prompt, **kw: (
        '{"ask": "타행에서 가져오는 건가요, 당행에서 내보내는 건가요?",'
        ' "options": ["타행 → 당행", "당행 → 타행"]}')
    try:
        out = CL.clarify({"question": "실물이전 어떻게 처리해?", "evidence": evidence})
        hit = bool(out.get("clarify")) and "타행 → 당행" in out["answer"]
        print(f"{'✓' if hit else '✗'} 갈래가 갈리면 답변 대신 선택지를 보여주고 되묻는다")
        ok += hit

        # 연속 되묻기 금지 — 상한은 코드
        again = CL.clarify({"question": "실물이전", "evidence": evidence,
                            "history": [{"question": "q", "pending_clarify": {"question": "?"}}]})
        hit = not again.get("clarify")
        print(f"{'✓' if hit else '✗'} 직전 턴이 되묻기였으면 다시 되묻지 않는다")
        ok += hit

        # 근거 0건은 모호함 아님
        hit = not CL.clarify({"question": "실물이전", "evidence": []}).get("clarify")
        print(f"{'✓' if hit else '✗'} 근거가 0건이면 되묻지 않는다(없다고 답할 일이다)")
        ok += hit
    finally:
        CL.generate = orig_gen

    # 선택지 2개 미만이면 되묻지 않음
    CL.generate = lambda prompt, **kw: '{"ask": "무엇을 원하세요?", "options": ["하나"]}'
    try:
        hit = not CL.clarify({"question": "실물이전", "evidence": evidence}).get("clarify")
    finally:
        CL.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 갈래를 2개 이상 못 보여주면 되묻지 않는다")
    ok += hit

    CL.generate = lambda prompt, **kw: '{"ask": null}'
    try:
        # 등급은 계측용으로 남음 — 되묻기·재작성은 없음
        out = CL.clarify({"question": "한도 얼마야?", "evidence": evidence})
        hit = not out.get("clarify") and not out.get("judge_note") \
            and out.get("judge_verdict") == CL.ANSWER
    finally:
        CL.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 되묻지 않기로 하면 답변 경로를 막지 않는다")
    ok += hit

    # 되묻기 턴엔 화면 연계 제안 없음 — 배선으로 차단
    hit = routing.route_answer({"clarify": {"question": "?"}}) == "__end__" \
        and routing.route_answer({}) == "offer"
    print(f"{'✓' if hit else '✗'} 되묻기 턴은 offer 를 거치지 않고 끝난다")
    ok += hit

    # 되묻기 답(「2번째꺼」)이 confirm_action 으로 오분류돼도 막다른 안내 금지 — 제안 유무는 코드가 판단
    hit = routing.route_intent({"intent": "confirm_action", "history": history}) == "plan"
    print(f"{'✓' if hit else '✗'} 되묻기 직후의 확인 응답 오분류는 계획 루프로 돌아간다")
    ok += hit

    # 제안이 걸려 있으면 정상 confirm_action
    hit = routing.route_intent(
        {"intent": "confirm_action",
         "history": [{"question": "q", "pending_action": {"label": "x"}}]}) == "confirm_action"
    print(f"{'✓' if hit else '✗'} 제안이 걸린 확인 응답은 그대로 confirm_action")
    ok += hit

    # 제안·되묻기 모두 없으면 '제안 없음' 안내 유지(빈 히스토리 포함)
    hit = (routing.route_intent({"intent": "confirm_action",
                                 "history": [{"question": "q"}]}) == "confirm_action"
           and routing.route_intent({"intent": "confirm_action"}) == "confirm_action")
    print(f"{'✓' if hit else '✗'} 제안도 되묻기도 없으면 '제안 없음' 안내를 유지한다")
    ok += hit

    # 적합성 게이트도 이전 대화를 봄 — 「1번꺼」만으로는 어떤 후보와도 안 맞음
    seen: dict[str, str] = {}
    orig_gen, orig_fits = tools.generate, tools.fits_question
    tools.fits_question = _REAL_FITS          # 게이트 본체 검사 — 스텁 해제
    tools.generate = lambda p, **kw: seen.setdefault("p", p) and "[]"
    try:
        card = next(c for c in KB.cards if c["_kind"] == "screen")
        tools._adopt({"question": "1번꺼", "history": history}, "화면번호", [(2.0, card)], "화면")
    finally:
        tools.generate, tools.fits_question = orig_gen, orig_fits
    hit = "실물이전 어떻게 처리해?" in seen.get("p", "") and "타행 → 당행" in seen.get("p", "")
    print(f"{'✓' if hit else '✗'} 적합성 게이트 프롬프트에 이전 대화·되물은 선택지가 실린다")
    ok += hit

    # 되묻기 턴도 선택지를 만든 근거를 출처로 표시
    ev = tools._ev("screen", "q", "■ [04-12-179] 퇴직연금 상품 조회",
                   [{"id": "screen.04-12-179", "title": "퇴직연금 상품 조회", "doc": "화면번호 안내"}])
    CL.generate = lambda p, **kw: ('{"ask": "어느 쪽인가요?", '
                                   '"options": ["[04-12-179] 상품 조회", "[04-12-17A] NEW"]}')
    try:
        out = CL.clarify({"question": "퇴직연금 상품 조회 화면번호", "evidence": [ev]})
    finally:
        CL.generate = orig_gen
    hit = ([s["id"] for s in out["sources"]] == ["screen.04-12-179"]
           and all(s["role"] == tools.GROUND for s in out["sources"]))
    print(f"{'✓' if hit else '✗'} 되묻기 턴이 선택지를 만든 근거를 출처로 싣는다")
    ok += hit

    # 출처 역할 어휘는 한 곳에서 — 답 내는 노드가 둘이라 갈리면 화면이 한쪽만 구분
    hit = (P.GROUND, P.CAUTION) == (tools.GROUND, tools.CAUTION)
    print(f"{'✓' if hit else '✗'} compose·clarify 가 같은 출처 역할 어휘를 쓴다")
    ok += hit
    return ok


def check_adequacy_and_shape() -> int:
    """근거가 질문에 답이 되는지(적합성 게이트) · 답의 형태가 재료 유형에 맞는지."""
    from pension_agent.nodes import plan as P
    from pension_agent.tools.kb_search import procedure_qa
    from pension_agent.prompts import ANSWER_SHAPES, COMPOSE_SYSTEM

    ok = 0

    # 게이트는 재료 종류 무관 적용 — 전부 버리면 어느 도구도 근거 없음
    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: []
    try:
        blocked = [name for name in ("fact", "procedure", "segment", "method", "fieldtip", "pitch")
                   if tools.run(name, {"question": "세액공제 한도가 얼마야?"},
                                "세액공제 한도가 얼마야?") is not None]
    finally:
        tools.fits_question = orig
    hit = not blocked
    print(f"{'✓' if hit else '✗'} 적합성 게이트가 모든 재료 종류에 걸린다"
          + ("" if hit else f" — 통과해버린 도구 {blocked}"))
    ok += hit

    # 후보 0건이면 게이트 미호출
    called: list[str] = []
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: (called.append(kind), h)[1]
    try:
        tools.run("fact", {"question": "오늘 서울 날씨 어때?"}, "오늘 서울 날씨 어때?")
    finally:
        tools.fits_question = orig
    hit = not called
    print(f"{'✓' if hit else '✗'} 후보가 0건이면 게이트를 돌리지 않는다")
    ok += hit

    # 후보 한 줄은 종류 무관(팩트는 title 없이 label)
    head = tools._headline({"id": "f.1", "label": "세액공제 한도", "value": "연 900만원"})
    hit = "세액공제 한도" in head and "연 900만원" in head and "조건별 값" not in head
    print(f"{'✓' if hit else '✗'} 후보 요약이 종류마다 다른 필드 이름을 흡수한다")
    ok += hit

    # tiers 선언 카드는 후보 줄에 조건만 — 한 장 안의 갈래(대면/비대면)를 게이트가 보게. 값은 원장 소유
    tiered = tools._headline({"id": "f.53", "label": "수수료 면제", "value": "면제된다",
                              "tiers": [{"when": "퇴직금 5천만원 이상 · 비대면 계좌", "value": "면제"},
                                        {"when": "퇴직금 5천만원 이상 · 대면 계좌", "value": "면제 아님"},
                                        {"when": "퇴직금 5천만원 이상 · 대면 계좌", "value": "중복"}]})
    hit = ("조건별 값: 퇴직금 5천만원 이상 · 비대면 계좌 / 퇴직금 5천만원 이상 · 대면 계좌" in tiered
           and tiered.count("대면 계좌") == 2 and "면제 아님" not in tiered)
    print(f"{'✓' if hit else '✗'} 조건별 값을 선언한 카드는 후보 줄에 조건이 실린다(값은 아니다)")
    ok += hit

    # 대사 카드는 후보 줄에 행원 대사 — summary 가 저작 메모인 카드가 있음, 고객 발화는 검색 입구 부적합
    spoken = tools._headline({"id": "p.1", "title": "방치 고객에게", "summary": "세 자료가 …",
                              "dialogue": [{"speaker": "고객", "text": "아, 그래요?"},
                                           {"speaker": "행원", "text": "운용지시가 안된 현금성자산이 있습니다"},
                                           {"speaker": "행원", "text": "두 번째 행원 대사"}]})
    hit = ("대사: 운용지시가 안된 현금성자산이 있습니다" in spoken
           and "아, 그래요?" not in spoken and "두 번째" not in spoken
           and "세 자료가" in spoken)
    print(f"{'✓' if hit else '✗'} 대사를 든 카드는 후보 줄에 행원 대사가 실린다(고객 발화 아님)")
    ok += hit

    # 대사 없는 카드엔 칸 자체 없음(`조건별 값:` 과 같은 규약)
    hit = "대사:" not in head
    print(f"{'✓' if hit else '✗'} 대사가 없는 카드에는 대사 칸이 붙지 않는다")
    ok += hit

    # 게이트는 카드 하나씩 판정 — 옆 후보가 빗나가도 맞는 카드는 유지
    q = "디폴트옵션 변경 화면번호 알려줘"
    candidates = procedure_qa.search(q)
    keep = candidates[-1][1]["id"] if candidates else ""
    tools.fits_question = lambda question, h, kind="", history=None, query=None, sink=None: [x for x in h if x[1]["id"] == keep]
    try:
        found = tools.run("procedure", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(candidates) and bool(found) and found["sources"][0]["id"] == keep \
        and len(found["sources"]) == 1
    print(f"{'✓' if hit else '✗'} 후보 일부만 맞으면 그것만 남긴다(전부 버리지 않는다)")
    ok += hit

    tools.fits_question = lambda question, h, kind="", history=None, query=None, sink=None: []
    try:
        hit = tools.run("procedure", {"question": q}, q) is None
    finally:
        tools.fits_question = orig
    print(f"{'✓' if hit else '✗'} 맞는 후보가 하나도 없을 때만 근거를 내놓지 않는다")
    ok += hit

    # LLM 이 지어낸 id 는 실재 후보와 대조해 제거
    orig_gen = tools.generate
    tools.generate = lambda prompt, **kw: '["없는카드id"]'
    try:
        hit = _REAL_FITS(q, candidates, "업무 처리 절차") == []
    finally:
        tools.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 지어낸 id 는 실재 후보와 대조해 걸러낸다")
    ok += hit

    # 형태 요구는 원장에 실린 재료의 것만
    def ev(tool):
        return {"tool": tool, "query": "q", "text": f"{tool} 근거", "atomic": [], "notices": [],
                "notice_scopes": [], "allow": [f"{tool} 근거"], "sources": [], "meta": {}}

    block = P._shape_block([ev("fact"), ev("pitch")])
    hit = (ANSWER_SHAPES["fact"] in block and ANSWER_SHAPES["pitch"] in block
           and ANSWER_SHAPES["procedure"] not in block)
    print(f"{'✓' if hit else '✗'} 쓴 재료의 형태 요구만 싣는다(안 쓴 재료의 것은 빼고)")
    ok += hit

    hit = P._shape_block([]) == ""
    print(f"{'✓' if hit else '✗'} 재료가 없으면 형태 요구도 비운다")
    ok += hit

    # 모든 도구에 형태 요구 필수 — 빠지면 그 재료만 형태 기준 없이 답해짐
    missing = [n for n in tools.TOOLS if n not in ANSWER_SHAPES]
    hit = not missing
    print(f"{'✓' if hit else '✗'} 모든 도구에 형태 요구가 선언돼 있다"
          + ("" if hit else f" — 빠진 도구 {missing}"))
    ok += hit

    # 출처는 화면 근거 목록 전담 — 형태 요구가 출처를 요구하면 LLM 이 메타 줄을 복사. 기준시점은 시효성이라 유지
    hit = ("출처" not in ANSWER_SHAPES["fact"] and "기준시점" in ANSWER_SHAPES["fact"]
           and "재료 블록의 형식은 옮기지 않는다" in COMPOSE_SYSTEM)
    print(f"{'✓' if hit else '✗'} 출처는 형태 요구가 아니라 근거 목록이 전담한다")
    ok += hit

    seen: dict[str, str] = {}
    orig_gen = P.generate
    P.generate = lambda prompt, **kw: seen.setdefault("compose", prompt) and "(스텁)"
    try:
        P.compose({"question": "한도 얼마야?", "evidence": [ev("fact")]})
    finally:
        P.generate = orig_gen
    hit = ANSWER_SHAPES["fact"] in seen.get("compose", "")
    print(f"{'✓' if hit else '✗'} 형태 요구가 작성 프롬프트에 실린다")
    ok += hit
    return ok


def check_clarify_settled() -> int:
    """되묻기 판정이 열린 고객의 코드 계산 상태를 갈래가 아닌 «정해진 것»으로 보는지."""
    from pension_agent.nodes import clarify as CL

    ok = 0
    cid = "198734-1205842"          # 이준호 — 성립 요건 mat(만기예금 보유) 하나, 디폴트옵션 설정
    playbook = [{"tool": "playbook", "query": "q",
                 "text": "만기 임박+디폴트옵션 미등록 고객에게 → … / 원리금보장 100% 운용 고객에게 → …",
                 "atomic": [], "notices": [], "notice_scopes": [], "allow": [], "sources": [], "meta": {}}]
    fact = [{"tool": "fact", "query": "q", "text": "세액공제 한도 900만원 / 연금저축 단독 600만원",
             "atomic": [], "notices": [], "notice_scopes": [], "allow": [], "sources": [], "meta": {}}]
    customer = [{"tool": "customer", "query": "q", "text": "■ 고객 — 퇴직급여 5.2억 · 개인부담금 0원",
                 "atomic": [], "notices": [], "notice_scopes": [], "allow": [], "sources": [], "meta": {}}]

    # 고객 상태 카드(playbook)만 있으면 판정 생략 — 고른 기준은 코드가 이미 정함
    hit = not CL.applicable({"question": "뭐라고 말하면 좋아?", "customer_id": cid, "evidence": playbook})
    print(f"{'✓' if hit else '✗'} playbook 근거만 있는 턴은 되묻기 판정을 돌리지 않는다")
    ok += hit

    # 고객이 열려 있으면 코드가 아는 상태가 <근거> 밖 «이미 정해진 것»으로(고객 도구 미호출 턴 포함)
    seen: list[str] = []
    orig = CL.generate
    CL.generate = lambda prompt, **kw: (seen.append(prompt), '{"ask": null}')[1]
    try:
        CL.clarify({"question": "이 고객 세액공제 얼마나 더 받아?", "customer_id": cid, "evidence": fact})
        prompt = seen[-1] if seen else ""
        settled = prompt.split("<이미 정해진 것>")[-1].split("</이미 정해진 것>")[0] if "<이미 정해진 것>" in prompt else ""
        hit = ("만기예금 보유" in settled and "디폴트옵션 설정" in settled
               and "거래채널 대면" in settled and "소득구간" in settled)
        print(f"{'✓' if hit else '✗'} 판정 프롬프트에 성립 요건·계좌 상태·거래채널·소득구간이"
              " «정해진 것»으로 실린다")
        ok += hit

        # 소득구간은 양쪽 다 검사 — 실데이터에 컬럼이 없을 때 «미확인»이 정해진 것으로 실리면 그 축을 되묻지 않음
        import dataclasses  # noqa: PLC0415
        from pension_agent.tools.customer_solution import customer as _SC  # noqa: PLC0415
        _orig_get = _SC.get_profile
        _SC.get_profile = lambda c: dataclasses.replace(_orig_get(c), income_bracket=None)
        try:
            seen.clear()
            CL.clarify({"question": "이 고객 세액공제 얼마나 더 받아?", "customer_id": cid,
                        "evidence": fact})
            p2 = seen[-1] if seen else ""
            s2 = p2.split("<이미 정해진 것>")[-1].split("</이미 정해진 것>")[0] if "<이미 정해진 것>" in p2 else ""
        finally:
            _SC.get_profile = _orig_get
        hit = bool(s2) and "소득구간" not in s2
        print(f"{'✓' if hit else '✗'} 소득구간이 미확인이면 «정해진 것»에 싣지 않는다(갈래로 남긴다)")
        ok += hit
        hit = bool(settled) and "만기예금 보유" not in prompt.split("<근거>")[-1].split("</근거>")[0]
        print(f"{'✓' if hit else '✗'} 정해진 것은 <근거>(갈래 후보) 밖에 실린다")
        ok += hit

        # 부담금 종류도 «정해진 것» — 수수료율표(fact.k04.f50)의 갈림 축이 원장 값. 재료가 fact 뿐인 턴도 포함
        hit = "사용자부담금" in settled and "가입자부담금" in settled
        print(f"{'✓' if hit else '✗'} 부담금 종류(사용자/가입자)가 «정해진 것»에 실린다")
        ok += hit

        # 원장의 고객 재료 본문도 같은 블록에
        seen.clear()
        CL.clarify({"question": "수수료 얼마야?", "customer_id": cid, "evidence": customer + fact})
        prompt = seen[-1] if seen else ""
        settled = prompt.split("<이미 정해진 것>")[-1].split("</이미 정해진 것>")[0] if "<이미 정해진 것>" in prompt else ""
        hit = "개인부담금 0원" in settled
        print(f"{'✓' if hit else '✗'} 원장의 고객 재료 본문이 «정해진 것»에 실린다")
        ok += hit

        # 고객이 없으면 블록 없음
        seen.clear()
        CL.clarify({"question": "실물이전 어떻게 해?", "evidence": fact})
        hit = bool(seen) and "<이미 정해진 것>" not in seen[-1]
        print(f"{'✓' if hit else '✗'} 고객이 열려 있지 않으면 «정해진 것» 블록이 없다")
        ok += hit
    finally:
        CL.generate = orig

    # 블록은 LLM 없이 생성 — 프로파일 없는 id 면 빈 문자열, 예외 없음
    hit = CL.settled_block({"customer_id": "000000-0000000", "evidence": []}) == ""
    print(f"{'✓' if hit else '✗'} 없는 고객 id 는 빈 블록이고 예외가 아니다")
    ok += hit
    return ok


#: 되묻기 판정 골든셋 — (질문, 도구, 판정, 되묻기로 끝나야 하나, 근거에 있어야 할 갈래 표시)
_CLARIFY_GOLDEN = (
    # 진짜 갈래 — 근거에 신청 경로가 셋
    ("계약이전 어떻게 신청해?", "procedure",
     {"ask": "어느 경로로 신청하시나요?", "options": ["후선 의뢰", "스타뱅킹", "인터넷뱅킹"]},
     True, "3경로"),
    # 가짜 갈래 — 카드는 여러 장이지만 답은 화면번호 하나(카드 수 ≠ 갈래)
    ("실물이전 가능여부 조회 화면번호?", "screen", {"ask": None}, False, "06-AD-020"),
)


def check_clarify_golden() -> int:
    """되묻기 판정 골든셋 — 판정 뒤 턴이 어떻게 끝나는지를 그래프 전체로 고정(판정 자체는 미검사)."""
    from pension_agent.nodes import clarify as CL
    from pension_agent.nodes import plan as P

    ok = 0
    for question, tool, verdict, should_ask, marker in _CLARIFY_GOLDEN:
        found = tools.run(tool, {"question": question}, question)
        if not found:
            print(f"✗ 골든셋 재료 확보: {tool}({question}) → 근거 없음")
            continue

        # 재료에 갈래(또는 확정 답)가 실제로 있는지
        hit = marker in found["text"]
        print(f"{'✓' if hit else '✗'} 골든셋 재료에 «{marker}» 가 실려 있다 — {question}")
        ok += hit

        seen: list[str] = []
        orig_cl, orig_plan_node, orig_gen = CL.generate, G.plan_step, P.generate
        CL.generate = lambda prompt, **kw: (seen.append(prompt), json.dumps(verdict))[1]
        # 계획은 재료를 넣어 둔 채 종료 — 재는 것은 판정 뒤 배선(stub_plan_pitch 덮어쓰기 방지)
        G.plan_step = lambda state: {"plan_done": True}
        P.generate = lambda prompt, **kw: "안내드릴게요."
        try:
            agent = G.build_agent()
            out = agent.invoke({"question": question, "evidence": [found],
                                "steps": [{"tool": tool, "query": question,
                                           "outcome": "found"}]})
        finally:
            CL.generate, G.plan_step, P.generate = orig_cl, orig_plan_node, orig_gen

        asked = bool(out.get("clarify"))
        hit = asked == should_ask
        print(f"{'✓' if hit else '✗'} {'되묻고 끝난다' if should_ask else '답변으로 흘러간다'}"
              f" — {question}")
        ok += hit

        if should_ask:
            # 되묻기 턴 — 선택지·출처는 나가고 화면 연계 제안은 없음
            hit = all(o in out["answer"] for o in verdict["options"]) \
                and bool(out.get("sources")) and not out.get("pending_action")
            print(f"{'✓' if hit else '✗'} 되묻기 턴: 선택지+출처가 나가고 연계 제안은 없다")
        else:
            hit = bool(out.get("answer")) and not out.get("clarify")
            print(f"{'✓' if hit else '✗'} 되묻지 않은 턴: 답변이 그대로 나간다")
        ok += hit

        # 판정 프롬프트가 그 재료를 봤는지
        hit = bool(seen) and marker in seen[0]
        print(f"{'✓' if hit else '✗'} 판정 프롬프트에 근거가 실린다 — {question}")
        ok += hit

    # 맥락으로 갈래가 정해진 후속 질문 — 판정 프롬프트에 이전 대화 포함
    history = [{"question": "타행에서 퇴직금 가져오려는 고객인데 뭐라고 말하지",
                "stage": "계약이전"}]
    evidence = [{"tool": "procedure", "query": "계약이전", "text": "전입 절차 / 전출 절차",
                 "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
                 "allow": [], "sources": [], "meta": {}}]
    seen = []
    orig_cl = CL.generate
    CL.generate = lambda prompt, **kw: (seen.append(prompt), '{"ask": null}')[1]
    try:
        out = CL.clarify({"question": "그럼 계약이전은 어떻게 신청해?",
                          "history": history, "evidence": evidence})
    finally:
        CL.generate = orig_cl
    hit = bool(seen) and "타행에서 퇴직금 가져오려는 고객" in seen[0] \
        and not out.get("clarify") and not out.get("judge_note")
    print(f"{'✓' if hit else '✗'} 판정 프롬프트가 이전 대화를 본다(맥락으로 갈래가 정해진 후속 질문)")
    ok += hit

    # 갈래 불가 재료뿐이면 판정 생략(check_turn_cost 는 호출 절약 쪽에서 검사)
    called: list[str] = []
    CL.generate = lambda prompt, **kw: (called.append("clarify"), '{"ask": null}')[1]
    try:
        for tool_name in ("customer", "history", "date"):
            CL.clarify({"question": "이 고객 평가금액 얼마야",
                        "evidence": [{**evidence[0], "tool": tool_name}]})
    finally:
        CL.generate = orig_cl
    hit = not called
    print(f"{'✓' if hit else '✗'} 고객·상담기록·날짜 재료뿐이면 되묻기 판정을 돌리지 않는다")
    ok += hit
    return ok


def check_answer_parallel() -> int:
    """되묻기 판정·답변 작성 동시 실행이 답을 바꾸지 않는지(nodes/answer.py) — 되묻기 시 써 둔 답 폐기 · 진행 표시 스레드 전달."""
    from pension_agent.common import progress as PROG
    from pension_agent.nodes import answer as A
    from pension_agent.nodes import clarify as CL
    from pension_agent.nodes import plan as P

    ok = 0
    evidence = [{"tool": "procedure", "query": "q", "text": "전입 절차 / 전출 절차",
                 "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
                 "allow": ["전입 절차 / 전출 절차"], "sources": [{"id": "proc.x"}], "meta": {}}]
    state = {"question": "계약이전 어떻게 신청해?", "evidence": evidence}

    # 되묻기로 결정되면 써 둔 답 폐기
    orig_cl, orig_gen = CL.generate, P.generate
    CL.generate = lambda prompt, **kw: '{"ask": "어느 방향인가요?", "options": ["전입", "전출"]}'
    P.generate = lambda prompt, **kw: "전입 절차는 이렇습니다."
    try:
        out = A.answer(dict(state))
    finally:
        CL.generate, P.generate = orig_cl, orig_gen
    hit = bool(out.get("clarify")) and "전입 절차는 이렇습니다." not in out["answer"]
    print(f"{'✓' if hit else '✗'} 되묻기로 끝나면 동시에 써 둔 답변은 나가지 않는다")
    ok += hit

    # 판정이 죽어도 작성이 성공했으면 그 답 — 직렬과 같은 규약
    def _dead(prompt, **kw):
        raise LLMError("timeout")

    CL.generate, P.generate = _dead, (lambda prompt, **kw: "전입 절차는 이렇습니다.")
    try:
        out = A.answer(dict(state))
    finally:
        CL.generate, P.generate = orig_cl, orig_gen
    hit = not out.get("clarify") and "전입 절차는 이렇습니다." in out["answer"] \
        and "LLMError" in (out.get("llm_error") or "")
    print(f"{'✓' if hit else '✗'} 판정이 죽어도 작성이 됐으면 그 답이 나가고 원인이 남는다")
    ok += hit

    # 판정 없는 턴은 판정 LLM·스레드 없음
    called: list[str] = []
    CL.generate = lambda prompt, **kw: (called.append("clarify"), '{"ask": null}')[1]
    P.generate = lambda prompt, **kw: "고객 재료로 답합니다."
    try:
        out = A.answer({"question": "이 고객 평가금액 얼마야",
                        "evidence": [{**evidence[0], "tool": "customer"}]})
    finally:
        CL.generate, P.generate = orig_cl, orig_gen
    hit = not called and bool(out.get("answer"))
    print(f"{'✓' if hit else '✗'} 갈래가 없는 재료뿐이면 판정 없이 바로 답을 쓴다")
    ok += hit

    # 진행 표시가 스레드를 건너감 — ContextVar 는 복사 안 하면 안 따라감(progress.py)
    events: list[str] = []
    CL.generate = lambda prompt, **kw: '{"ask": null}'
    P.generate = lambda prompt, **kw: "전입 절차는 이렇습니다."
    try:
        with PROG.reporting(events.append):
            A.answer(dict(state))
    finally:
        CL.generate, P.generate = orig_cl, orig_gen
    hit = any("작성" in e for e in events) and any("검증" in e for e in events)
    print(f"{'✓' if hit else '✗'} 작성·검증 진행 표시가 스레드를 건너 전달된다 — {events}")
    ok += hit
    return ok


def check_branch_answer_amount() -> int:
    """되묻기 다음 턴은 원래 질문의 금액으로 계산 · 화면번호 일부 인용 허용(span 게이트)."""
    from pension_agent import tools
    from pension_agent.nodes.plan import _span_verdict
    from pension_agent.tools.customer_solution.customer import PERSONAS

    ok = 0
    cid = next((p.id for p in PERSONAS if p.room > 0), PERSONAS[0].id)

    # 되묻기 다음 턴 — 갈래를 고른 말의 금액(5,500만원)이 아닌 원래 질문의 금액
    clarified = [{"question": "300만원 더 넣으면 얼마 돌려받아?",
                  "pending_clarify": {"question": "총급여 구간을 확인해 주세요",
                                      "options": ["5,500만원 이하", "5,500만원 초과"]}}]
    ev = tools._tax_credit({"customer_id": cid, "question": "5,500만원 이하야",
                            "history": clarified}, "")
    hit = ev is not None and "추가 납입액 300만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 되묻기 답: 갈래를 고른 말이 아니라 원래 질문의 금액으로 계산한다")
    ok += hit

    # 되묻기 아니면 이번 질문에서 그대로
    ev = tools._tax_credit({"customer_id": cid, "question": "500만원 더 넣으면?",
                            "history": []}, "")
    hit = ev is not None and "추가 납입액 500만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 되묻기 답: 평범한 턴은 이번 질문의 금액을 그대로 쓴다")
    ok += hit

    # 화면번호 — 번호끼리 앞 마디 공유(04-12-…), 일부 인용은 통과·근거에 없는 번호는 폐기
    atomic = ["[06-12-501]", "[01-12-213]", "[04-12-641]", "[04-12-648]",
              "[04-12-644]", "[06-12-626]", "[04-12-646]"]
    found = {"atomic": atomic, "notices": [], "notice_scopes": [], "text": "", "allow": []}
    passing = [a for a in (
        "[06-12-501] 등록 후 [01-12-213] 로 입금하고 [04-12-646] 로 발굴합니다.",
        "06-12-501 등록 후 01-12-213 으로 입금합니다.",          # 대괄호 없어도 같은 화면
        "과세이연정보를 먼저 등록하고 60일 안에 입금합니다.",       # 미인용은 위반 아님
    ) if _span_verdict(found, a)[0] != "discard"]
    hit = len(passing) == 3
    print(f"{'✓' if hit else '✗'} 화면번호: 일부만 인용하거나 대괄호를 빼도 폐기되지 않는다")
    ok += hit

    blocked = [a for a in ("[04-12-640] 화면에서 조회하세요.",
                           "[06-12-502] 후선 업무의뢰로 등록합니다.")
               if _span_verdict(found, a)[0] == "discard"]
    hit = len(blocked) == 2
    print(f"{'✓' if hit else '✗'} 화면번호: 근거에 없는 번호는 여전히 폐기된다")
    ok += hit

    # 아는 화면은 원장 전체 기준 — 근거 한 건씩 재면 다른 근거의 화면이 «없는 화면»으로 폐기
    from pension_agent.nodes import plan as PLAN
    _blank = {"notices": [], "notice_scopes": [], "related": [], "marks": [],
              "sources": [], "meta": {}, "query": ""}
    _proc_text = "■ 연금개시는 [06-12-622] 세액 미공제 한도 등록 → [02-12-221] 연금지급 등록의 2단계"
    _scr_text = "■ [02-12-221] 개인형IRP 연금지급  (지급·과세이연·연금)"
    # 실제 _ev 처럼 본문을 allow 에도
    proc_ev = {**_blank, "tool": "procedure", "atomic": ["[06-12-622]", "[02-12-221]"],
               "text": _proc_text, "allow": [_proc_text]}
    scr_ev = {**_blank, "tool": "screen", "atomic": ["[02-12-221]"],
              "text": _scr_text, "allow": [_scr_text]}
    both = [proc_ev, scr_ev]
    passed = not PLAN._screen("먼저 06-12-622 에서 등록하고, 02-12-221 에서 연금지급을 등록해요.",
                              both, "연금개시 절차", set())[0]
    faults = PLAN._screen("먼저 06-12-999 에서 등록해요.", both, "연금개시 절차", set())[0]
    hit = passed and bool(faults)
    print(f"{'✓' if hit else '✗'} 화면번호: 원장의 다른 근거가 아는 번호는 통과하고, 어느 근거에도 "
          f"없는 번호는 폐기된다")
    ok += hit
    return ok


def check_graded_judge() -> int:
    """등급형 판정 — 답한다 · 전제를 밝히고 답한다 · 되묻는다 · 없다. 각 등급의 출력과 판정의 경계 확대 불가."""
    from pension_agent.nodes import answer as ANS, clarify as CL
    ok = 0
    print("\n[등급형 판정 — 네 결론이 각자 출력을 갖는다 (§5)]")

    def ev(tool="procedure", text="타행→당행 절차 / 당행→타행 절차"):
        # allow 는 `_ev` 기본값과 동일 — `_quotable` 과 compose 검증기가 같은 텍스트를 봐야 함
        return {"tool": tool, "query": "q", "text": text, "atomic": [], "notices": [],
                "notice_scopes": [], "marks": [], "related": [], "allow": [text],
                "sources": [{"id": "proc.020", "title": "계약이전", "doc": "d",
                             "score": None, "page": None}], "meta": {}}

    orig_gen = CL.generate
    try:
        # assume — 갈래를 정해 주는 재료가 있으면 전제를 밝히고 답함
        CL.generate = lambda prompt, **kw: '{"verdict": "assume", "premise": "타행에서 당행으로 가져오는 경우"}'
        out = CL.clarify({"question": "실물이전 어떻게 처리해?", "customer_id": "198734-1205842",
                          "evidence": [ev()]})
        hit = out.get("judge_verdict") == CL.ASSUME and "타행에서 당행으로" in (out.get("judge_note") or "") \
            and not out.get("clarify")
        print(f"{'✓' if hit else '✗'} assume — 되묻지 않고 전제를 작성 지시로 넘긴다")
        ok += hit

        # none — 질문의 핵심 대상이 재료에 없음
        CL.generate = lambda prompt, **kw: '{"verdict": "none", "missing": "타행 IRP 수수료"}'
        out = CL.clarify({"question": "타행 IRP 수수료는 우리보다 싼가?", "evidence": [ev()]})
        hit = out.get("judge_verdict") == CL.NONE and "타행 IRP 수수료" in (out.get("judge_note") or "")
        print(f"{'✓' if hit else '✗'} none — 없다는 사실을 첫 문장에 세우라고 넘긴다")
        ok += hit

        # 정해 줄 것(열린 고객·이전 대화)이 없으면 assume 전제는 지어낸 것 — 폐기
        CL.generate = lambda prompt, **kw: '{"verdict": "assume", "premise": "ISA 만기 전환 포함"}'
        out = CL.clarify({"question": "IRP 세액공제 한도가 얼마야?", "evidence": [ev()]})
        hit = out.get("judge_verdict") == CL.ASSUME and not out.get("judge_note")
        print(f"{'✓' if hit else '✗'} 고객도 이전 대화도 없으면 전제를 버린다(지어낸 전제)")
        ok += hit

        out = CL.clarify({"question": "IRP 세액공제 한도가 얼마야?", "evidence": [ev()],
                          "customer_id": "198734-1205842"})
        hit = bool(out.get("judge_note"))
        print(f"{'✓' if hit else '✗'} 고객이 열려 있으면 전제가 산다")
        ok += hit

        out = CL.clarify({"question": "그럼 얼마야?", "evidence": [ev()],
                          "history": [{"question": "앞 질문"}]})
        hit = bool(out.get("judge_note"))
        print(f"{'✓' if hit else '✗'} 이전 대화가 있으면 전제가 산다(맥락도 정해 주는 재료다)")
        ok += hit

        # 원장 밖 숫자가 든 전제는 폐기 — 작성자가 되받으면 답 전체가 폐기됨
        CL.generate = lambda prompt, **kw: '{"verdict": "assume", "premise": "총급여 7,700만원 구간"}'
        out = CL.clarify({"question": "얼마 돌려받아?", "customer_id": "198734-1205842",
                          "evidence": [ev(text="총급여 5,500만원 이하 16.5%")]})
        hit = out.get("judge_verdict") == CL.ASSUME and not out.get("judge_note")
        print(f"{'✓' if hit else '✗'} 원장 밖 수치가 든 전제는 버린다(경계는 코드가 쥔다)")
        ok += hit

        # 원장 안 수치면 통과
        CL.generate = lambda prompt, **kw: '{"verdict": "assume", "premise": "총급여 5,500만원 이하 구간"}'
        out = CL.clarify({"question": "얼마 돌려받아?", "customer_id": "198734-1205842",
                          "evidence": [ev(text="총급여 5,500만원 이하 16.5%")]})
        hit = bool(out.get("judge_note"))
        print(f"{'✓' if hit else '✗'} 원장 안 수치를 쓴 전제는 통과한다")
        ok += hit

        # 옛 규격(`{"ask": …}`)도 되묻기로 — 작은 모델의 규격 불일치 대비
        CL.generate = lambda prompt, **kw: '{"ask": "어느 방향인가요?", "options": ["타행 → 당행", "당행 → 타행"]}'
        out = CL.clarify({"question": "실물이전", "evidence": [ev()]})
        hit = out.get("judge_verdict") == CL.ASK and bool(out.get("clarify")) and bool(out.get("sources"))
        print(f"{'✓' if hit else '✗'} 등급 칸이 없는 옛 응답도 되묻기로 읽는다")
        ok += hit
    finally:
        CL.generate = orig_gen

    # 고객 재료뿐이면 판정 생략(관문 유지)
    hit = not CL.applicable({"question": "평가금액 얼마야",
                             "evidence": [ev(tool="customer", text="· 개인부담금 0원")]})
    print(f"{'✓' if hit else '✗'} 관문은 그대로 — 갈래를 만드는 재료가 없으면 판정을 안 돌린다")
    ok += hit

    # 게이트 표시 갈래가 판정 프롬프트에 — 없을 때 블록을 세우면 판정 LLM 이 갈래를 지어냄
    seen: list[str] = []
    CL.generate = lambda prompt, **kw: (seen.append(prompt), '{"verdict": "answer"}')[1]
    try:
        CL.clarify({"question": "실물이전", "evidence": [ev()],
                    "branches": [{"axis": "이전 방향", "options": ["타행 → 당행", "당행 → 타행"]}]})
        CL.clarify({"question": "실물이전", "evidence": [ev()]})
    finally:
        CL.generate = orig_gen
    hit = len(seen) == 2 and "이전 방향" in seen[0] and "<갈래" not in seen[1]
    print(f"{'✓' if hit else '✗'} 게이트가 표시한 갈래가 실리고, 없으면 블록이 안 붙는다")
    ok += hit

    # 게이트 응답은 객체·배열 모두 — 규격 불일치로 후보를 전멸시키지 않게
    keep, br = tools._adequacy_verdict(
        '{"keep": ["proc.020", "proc.031"],'
        ' "branches": [{"axis": "이전 방향", "options": ["타행 → 당행", "당행 → 타행"]}]}')
    hit = keep == {"proc.020", "proc.031"} and br == [
        {"axis": "이전 방향", "options": ["타행 → 당행", "당행 → 타행"]}]
    print(f"{'✓' if hit else '✗'} 게이트 응답: 채택과 갈래를 함께 읽는다")
    ok += hit

    hit = tools._adequacy_verdict('["proc.020"]') == ({"proc.020"}, [])
    print(f"{'✓' if hit else '✗'} 옛 배열 규격도 채택으로 읽는다(후보를 전멸시키지 않는다)")
    ok += hit

    # 선택지 1개는 갈래 아님
    hit = tools._adequacy_verdict('{"keep": [], "branches": [{"axis": "축", "options": ["하나"]}]}')[1] == []
    print(f"{'✓' if hit else '✗'} 선택지가 2개 미만이면 갈래로 세지 않는다")
    ok += hit

    # 갈래는 축 이름으로 중복 제거 — `tools.run` 이 질의를 바꿔 게이트를 두 번 돌림
    st: dict = {}
    axis = [{"axis": "이전 방향", "options": ["타행 → 당행", "당행 → 타행"]}]
    tools.record_branches(st, axis)
    tools.record_branches(st, axis)
    hit = st.get("branches") == axis
    print(f"{'✓' if hit else '✗'} 같은 갈래가 두 줄로 서지 않는다(원문 재검색 대비)")
    ok += hit

    # 배선 — assume 이면 다시 쓴 답, 비면 처음 답(답 소실 금지)
    calls: list[dict] = []

    def fake_compose(state):
        calls.append(dict(state))
        return {"answer": "다시 쓴 답" if state.get("judge_note") else "처음 답"}

    orig_compose, orig_clarify = ANS.compose, ANS.clarify
    ANS.compose = fake_compose
    ANS.clarify = lambda state: {"judge_verdict": "assume", "judge_note": "<전제>"}
    try:
        out = ANS.answer({"question": "q", "evidence": [ev()]})
    finally:
        ANS.compose, ANS.clarify = orig_compose, orig_clarify
    hit = out.get("answer") == "다시 쓴 답" and len(calls) == 2 and not calls[0].get("judge_note")
    print(f"{'✓' if hit else '✗'} assume — 첫 작성은 판정을 못 보고, 그 뒤 한 번 다시 쓴다")
    ok += hit

    calls.clear()
    ANS.compose = lambda state: (calls.append(1), {"answer": "" if state.get("judge_note") else "처음 답"})[1]
    ANS.clarify = lambda state: {"judge_verdict": "none", "judge_note": "<없다>"}
    try:
        out = ANS.answer({"question": "q", "evidence": [ev()]})
    finally:
        ANS.compose, ANS.clarify = orig_compose, orig_clarify
    hit = out.get("answer") == "처음 답"
    print(f"{'✓' if hit else '✗'} 다시 쓴 것이 비면 처음 답이 나간다(답을 잃지 않는다)")
    ok += hit

    # 되묻기 판정이면 써 둔 답 폐기
    ANS.compose = lambda state: {"answer": "써 둔 답"}
    ANS.clarify = lambda state: {"judge_verdict": "ask", "clarify": {"question": "?"},
                                 "answer": "되묻기"}
    try:
        out = ANS.answer({"question": "q", "evidence": [ev()]})
    finally:
        ANS.compose, ANS.clarify = orig_compose, orig_clarify
    hit = out.get("answer") == "되묻기" and bool(out.get("clarify"))
    print(f"{'✓' if hit else '✗'} ask — 써 둔 답을 버리고 되묻기로 턴이 끝난다")
    ok += hit

    # 게이트가 갈래를 «남긴 후보» 위에서 찾는지 — 뺄 후보가 있을 때만 읽히면 branches 가 늘 비어 아래 배선이 죽은 코드
    from pension_agent.prompts import ADEQUACY_PROMPT
    text = ADEQUACY_PROMPT
    hit = "남긴 후보 중에" in text and "빼려는 후보들이" not in text
    print(f"{'✓' if hit else '✗'} 게이트는 갈래를 «남긴 후보» 위에서 찾는다(뺄 때만이 아니다)")
    ok += hit

    # 게이트 프롬프트 — 객체 규격 하나(배열 지시 모순 금지) · 한 후보 안의 갈래(f50·f53) · 갈래 예시 먼저 · 트레이스용 첫 줄 유지
    first = text.split("{")[0].strip().splitlines()[0]
    hit = ("JSON 배열로 출력" not in text and "JSON 객체 하나" in text
           and "한 후보 안에도 있다" in text and "조건별 값:" in text
           and "단서는 갈래가 아니다" in text
           and text.index('"axis": "이전 방향"') < text.index("빈 배열로 둔다")
           and first == "직원의 질문과, 그 질문에 답하려고 검색된 근거 후보 목록이다.")
    print(f"{'✓' if hit else '✗'} 게이트 프롬프트 — 객체 규격 하나 · 한 후보 안의 갈래 · 갈래 예시가 먼저 · 첫 줄 유지")
    ok += hit

    # 다시 쓴 답이 근거 원문 폴백(fallback=raw_evidence)이면 처음의 검증된 답 — `answer` 가 차 있어도 폴백은 답 아님
    calls: list[str] = []
    first = {"answer": "전제 없이 쓴 검증된 답", "sources": [], "fallback": ""}
    dumped = {"answer": "■ 카드 제목\n| 표 | 덤프 |", "sources": [], "fallback": "raw_evidence"}

    def _compose(state, _seq=[0]):  # noqa: B006 — 호출 순서 카운터
        calls.append(state.get("judge_note") or "")
        _seq[0] += 1
        return first if _seq[0] == 1 else dumped

    orig_compose, orig_clarify, orig_applicable = ANS.compose, ANS.clarify, ANS.applicable
    ANS.compose, ANS.applicable = _compose, lambda s: True
    ANS.clarify = lambda s: {"judge_note": "<전제> 타행→당행으로 보고 답한다", "judge_verdict": "assume"}
    try:
        out = ANS.answer({"question": "실물이전 절차 알려줘", "evidence": [ev()]})
    finally:
        ANS.compose, ANS.clarify, ANS.applicable = orig_compose, orig_clarify, orig_applicable
    hit = out["answer"] == first["answer"] and len(calls) == 2 and bool(calls[1])
    print(f"{'✓' if hit else '✗'} 다시 쓴 답이 근거 원문 폴백이면 처음의 검증된 답을 낸다"
          f" (다시 쓰기는 실제로 돌았다: {len(calls)}회)")
    ok += hit

    # 반대쪽 — 다시 쓴 답이 정상이면 그것을 냄
    def _compose_ok(state, _seq=[0]):  # noqa: B006
        _seq[0] += 1
        return first if _seq[0] == 1 else {"answer": "전제를 밝힌 답", "sources": [], "fallback": ""}

    ANS.compose, ANS.applicable = _compose_ok, lambda s: True
    ANS.clarify = lambda s: {"judge_note": "<전제> …", "judge_verdict": "assume"}
    try:
        out = ANS.answer({"question": "q", "evidence": [ev()]})
    finally:
        ANS.compose, ANS.clarify, ANS.applicable = orig_compose, orig_clarify, orig_applicable
    hit = out["answer"] == "전제를 밝힌 답"
    print(f"{'✓' if hit else '✗'} 다시 쓴 답이 게이트를 통과하면 그것이 나간다")
    ok += hit

    return ok
