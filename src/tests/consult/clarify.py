"""되묻기 — 맥락 이어받기 · 근거 충분성 · 판정 골든셋 · 등급형 판정.

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

import json

from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import routing, tools
from pension_agent.llm import LLMError

from tests.consult._common import print, _REAL_FITS  # noqa: A001 — 집계용 print


def check_context_and_clarify() -> int:
    """후속 질문이 맥락을 이어받는가(§2-1 · gap 1), 그리고 모호하면 되묻는가(§5 · gap 5).

    둘을 한 자리에서 재는 이유는 **함께여야 성립하기 때문**이다. 되물은 다음 턴의
    답("타행에서요")을 이전 질문과 이어 해석하지 못하면 되묻기는 직원을 한 번 더
    귀찮게 하고 끝난다.

    회귀 대상:
    ① 히스토리가 라우팅·화법 슬롯 추출에만 전달되고 계획·작성 프롬프트에는 실리지 않았다.
       그래서 계획이 이번 질문 한 줄만 보고 재료를 골랐다.
    ② 되묻기 경로 자체가 없었다. 의도 분류가 항상 하나로 확정하고, 모호해도 기본값으로
       넘겨 한쪽을 골라 답했다 — 직원은 그게 다른 절차인 줄도 모른다.
    """
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import plan as P
    from pension_agent.consult_agent.state import KB, format_history

    ok = 0
    history = [{"question": "실물이전 어떻게 처리해?", "stage": "계약이전",
                "pending_clarify": {"question": "어느 방향인가요?",
                                    "options": ["타행 → 당행", "당행 → 타행"]}}]

    # ① 대화 맥락이 계획·작성 프롬프트에 실리는가.
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

    # ② 되묻기 — 갈래가 있으면 답변 대신 질문으로 끝난다.
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

        # 연속으로 되묻지 않는다 — 상한은 코드가 정한다.
        again = CL.clarify({"question": "실물이전", "evidence": evidence,
                            "history": [{"question": "q", "pending_clarify": {"question": "?"}}]})
        hit = not again.get("clarify")
        print(f"{'✓' if hit else '✗'} 직전 턴이 되묻기였으면 다시 되묻지 않는다")
        ok += hit

        # 근거를 못 찾은 것은 모호한 것이 아니다.
        hit = not CL.clarify({"question": "실물이전", "evidence": []}).get("clarify")
        print(f"{'✓' if hit else '✗'} 근거가 0건이면 되묻지 않는다(없다고 답할 일이다)")
        ok += hit
    finally:
        CL.generate = orig_gen

    # 선택지를 못 보여주면 되묻지 않는다 — "무엇을 원하세요?" 는 되묻기가 아니다.
    CL.generate = lambda prompt, **kw: '{"ask": "무엇을 원하세요?", "options": ["하나"]}'
    try:
        hit = not CL.clarify({"question": "실물이전", "evidence": evidence}).get("clarify")
    finally:
        CL.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 갈래를 2개 이상 못 보여주면 되묻지 않는다")
    ok += hit

    # 되묻지 않기로 하면 그대로 답변으로 흘러간다.
    CL.generate = lambda prompt, **kw: '{"ask": null}'
    try:
        # 등급은 남는다(계측용) — 막지 않는다는 것은 «되묻지도, 다시 쓰게 하지도 않는다»다.
        out = CL.clarify({"question": "한도 얼마야?", "evidence": evidence})
        hit = not out.get("clarify") and not out.get("judge_note") \
            and out.get("judge_verdict") == CL.ANSWER
    finally:
        CL.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 되묻지 않기로 하면 답변 경로를 막지 않는다")
    ok += hit

    # 되묻기 턴에는 화면 연계 제안이 붙지 않는다 — 배선으로 막는다(§5 마지막).
    hit = routing.route_answer({"clarify": {"question": "?"}}) == "__end__" \
        and routing.route_answer({}) == "offer"
    print(f"{'✓' if hit else '✗'} 되묻기 턴은 offer 를 거치지 않고 끝난다")
    ok += hit

    # ③ 되묻기의 답이 확인 응답으로 오분류돼도 막다른 안내로 끝나지 않는다(gap 19).
    #
    # 실제 사례: 되물은 다음 턴의 "2번째꺼"를 분류 LLM 이 confirm_action 으로 읽었고,
    # confirm_action 노드는 직전 턴의 화면 연계 제안(pending_action)만 찾으므로
    # "직전에 제안드린 작업이 없어요"로 끝났다. 확인할 제안이 있는지는 코드가 아는
    # 값이다 — 분기표가 LLM 분류에 의존하지 않고 계획 루프로 돌려보낸다.
    hit = routing.route_intent({"intent": "confirm_action", "history": history}) == "plan"
    print(f"{'✓' if hit else '✗'} 되묻기 직후의 확인 응답 오분류는 계획 루프로 돌아간다")
    ok += hit

    # 제안이 실제로 걸려 있으면 그대로 confirm_action 이다 — 정상 확인 경로는 안 바뀐다.
    hit = routing.route_intent(
        {"intent": "confirm_action",
         "history": [{"question": "q", "pending_action": {"label": "x"}}]}) == "confirm_action"
    print(f"{'✓' if hit else '✗'} 제안이 걸린 확인 응답은 그대로 confirm_action")
    ok += hit

    # 제안도 되묻기도 없었으면 confirm_action 노드가 사실대로 안내한다(빈 히스토리 포함).
    hit = (routing.route_intent({"intent": "confirm_action",
                                 "history": [{"question": "q"}]}) == "confirm_action"
           and routing.route_intent({"intent": "confirm_action"}) == "confirm_action")
    print(f"{'✓' if hit else '✗'} 제안도 되묻기도 없으면 '제안 없음' 안내를 유지한다")
    ok += hit

    # ④ 적합성 게이트도 이전 대화를 본다 (gap 21).
    #
    # 실제 사례: 되묻기 다음 턴 "1번꺼" 에 도구가 화면 카드를 제대로 찾아왔는데, 게이트가
    # "직원 질문: 1번꺼" 하나만 보고 판정해 전부 탈락시켰다 — 화면에는 "근거를 찾지
    # 못했습니다" 가 떴다. 히스토리를 계획·작성 프롬프트에 싣던 gap 1 이 이 프롬프트만
    # 빠뜨렸다. 후속 질문은 그 말만으로는 어떤 후보와도 맞지 않는다.
    seen: dict[str, str] = {}
    orig_gen, orig_fits = tools.generate, tools.fits_question
    tools.fits_question = _REAL_FITS          # 게이트 본체를 재야 하므로 스텁을 잠시 걷는다
    tools.generate = lambda p, **kw: seen.setdefault("p", p) and "[]"
    try:
        card = next(c for c in KB.cards if c["_kind"] == "screen")
        tools._adopt({"question": "1번꺼", "history": history}, "화면번호", [(2.0, card)], "화면")
    finally:
        tools.generate, tools.fits_question = orig_gen, orig_fits
    hit = "실물이전 어떻게 처리해?" in seen.get("p", "") and "타행 → 당행" in seen.get("p", "")
    print(f"{'✓' if hit else '✗'} 적합성 게이트 프롬프트에 이전 대화·되물은 선택지가 실린다")
    ok += hit

    # ⑤ 되묻기 턴도 근거를 밝힌다 (gap 22).
    #
    # 선택지는 근거 카드에서 나온 것인데 sources 를 비워 화면이 "근거: 없음" 이라고 말했다.
    # 직원 입장에서는 어디서 나온 갈래인지 모른 채 고르라는 말이 된다(§3).
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

    # 출처 역할 어휘는 한 곳에서 온다 — 답을 내보내는 노드가 둘이라 갈리면 화면이
    # 한쪽만 갈라 보여준다.
    hit = (P.GROUND, P.CAUTION) == (tools.GROUND, tools.CAUTION)
    print(f"{'✓' if hit else '✗'} compose·clarify 가 같은 출처 역할 어휘를 쓴다")
    ok += hit
    return ok


def check_adequacy_and_shape() -> int:
    """근거가 질문에 답이 되는지(§5 · gap 3), 답의 형태가 유형에 맞는지(§5 표 · gap 4).

    회귀 대상:
    ① 적합성 판정이 화법 도구에만 있었다. 값·절차·정의·방법론·현장 관찰은 검색 점수만으로
       채택돼서, 주제어만 겹친 카드를 거를 장치가 없었다. §6 의 점검은 전부 "틀린 것을
       막는" 검사라 여기를 대신하지 못한다 — 어긋난 카드로 쓴 답도 수치는 원장 안에 있다.
    ② 형태 요구가 작성 프롬프트의 산문 지시로만 있고 유형별 기준이 없었다. 근거가 맞아도
       값을 물었는데 화법이 나오면 답이 아니다.
    """
    from pension_agent.consult_agent.nodes import plan as P
    from pension_agent.consult_agent.evidence import procedure_qa
    from pension_agent.consult_agent.prompts import ANSWER_SHAPES, COMPOSE_SYSTEM

    ok = 0

    # ① 게이트가 재료 종류를 가리지 않는가 — 전부 버리면 어느 도구도 근거를 못 내놓는다.
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

    # 0건이면 게이트를 부르지 않는다 — 부를 이유가 없는 자리에서 LLM 을 쓰지 않는다.
    called: list[str] = []
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: (called.append(kind), h)[1]
    try:
        tools.run("fact", {"question": "오늘 서울 날씨 어때?"}, "오늘 서울 날씨 어때?")
    finally:
        tools.fits_question = orig
    hit = not called
    print(f"{'✓' if hit else '✗'} 후보가 0건이면 게이트를 돌리지 않는다")
    ok += hit

    # 후보 한 줄이 종류를 가리지 않고 만들어지는가(팩트는 title 이 없고 label 을 쓴다).
    head = tools._headline({"id": "f.1", "label": "세액공제 한도", "value": "연 900만원"})
    hit = "세액공제 한도" in head and "연 900만원" in head and "조건별 값" not in head
    print(f"{'✓' if hit else '✗'} 후보 요약이 종류마다 다른 필드 이름을 흡수한다")
    ok += hit

    # 카드가 조건별 값(tiers)을 선언했으면 후보 한 줄에 그 **조건**이 실린다 — 한 장 안에서
    # 답이 갈리는 카드(대면/비대면 면제)를 게이트가 갈래로 볼 수 있어야 한다(케이스 8).
    # 값은 싣지 않는다 — 게이트는 고르기만 하고 값은 원장이 갖는다.
    tiered = tools._headline({"id": "f.53", "label": "수수료 면제", "value": "면제된다",
                              "tiers": [{"when": "퇴직금 5천만원 이상 · 비대면 계좌", "value": "면제"},
                                        {"when": "퇴직금 5천만원 이상 · 대면 계좌", "value": "면제 아님"},
                                        {"when": "퇴직금 5천만원 이상 · 대면 계좌", "value": "중복"}]})
    hit = ("조건별 값: 퇴직금 5천만원 이상 · 비대면 계좌 / 퇴직금 5천만원 이상 · 대면 계좌" in tiered
           and tiered.count("대면 계좌") == 2 and "면제 아님" not in tiered)
    print(f"{'✓' if hit else '✗'} 조건별 값을 선언한 카드는 후보 줄에 조건이 실린다(값은 아니다)")
    ok += hit

    # 대사를 든 카드는 후보 줄에 **행원 대사**가 실린다 — 고객 발화가 아니다.
    #
    # 회귀 대상(2026-09-21): 화법 카드의 내용은 대사인데 후보 줄에는 `summary` 만 실렸고,
    # 그 정리 문단이 «원문 자료에 대한 저작 메모»인 카드가 있다(k03.020 「세 자료가 같은
    # 상황을 각각 다른 톤으로 다룬다…」·k03.069 「1부 지식항목 20 의 현장 실행판이다…」).
    # 게이트는 카드 안에 무슨 말이 들어 있는지 한 글자도 못 본 채 판정했고, 「금액도 얼마
    # 안 되는데 그냥 두면 안 되나요」에 계획이 정확히 골라온 방치 화법 3장을 전멸시켰다.
    # 고객 발화를 실으면 안 되는 이유도 같은 자리에서 나왔다 — k03.020 의 고객 발화는
    # 「아, 그래요? 신경 안서 잘 모르겠어요」로, 빌더가 검색 입구로 쓰기를 거부하는 부류다.
    spoken = tools._headline({"id": "p.1", "title": "방치 고객에게", "summary": "세 자료가 …",
                              "dialogue": [{"speaker": "고객", "text": "아, 그래요?"},
                                           {"speaker": "행원", "text": "운용지시가 안된 현금성자산이 있습니다"},
                                           {"speaker": "행원", "text": "두 번째 행원 대사"}]})
    hit = ("대사: 운용지시가 안된 현금성자산이 있습니다" in spoken
           and "아, 그래요?" not in spoken and "두 번째" not in spoken
           and "세 자료가" in spoken)
    print(f"{'✓' if hit else '✗'} 대사를 든 카드는 후보 줄에 행원 대사가 실린다(고객 발화 아님)")
    ok += hit

    # 대사가 없는 카드에는 그 칸이 아예 안 붙는다(`조건별 값:` 과 같은 규약).
    hit = "대사:" not in head
    print(f"{'✓' if hit else '✗'} 대사가 없는 카드에는 대사 칸이 붙지 않는다")
    ok += hit

    # 게이트는 **카드 하나씩** 판정한다 — 옆 후보가 빗나갔다고 맞는 카드까지 버리지 않는다.
    #
    # 회귀 대상: 처음에는 후보 묶음 전체를 한 번에 YES/NO 로 물었다. "디폴트옵션 변경
    # 화면번호 알려줘" 의 후보 2장 중 하나가 다른 주제였다는 이유로 둘 다 버려졌고,
    # 지식베이스에 멀쩡히 있는 절차를 "근거를 찾지 못했다"고 답했다.
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

    # 남길 것이 하나도 없을 때만 근거 없음이다.
    tools.fits_question = lambda question, h, kind="", history=None, query=None, sink=None: []
    try:
        hit = tools.run("procedure", {"question": q}, q) is None
    finally:
        tools.fits_question = orig
    print(f"{'✓' if hit else '✗'} 맞는 후보가 하나도 없을 때만 근거를 내놓지 않는다")
    ok += hit

    # LLM 이 없는 id 를 지어내도 실재 후보와 대조해 걸러낸다.
    orig_gen = tools.generate
    tools.generate = lambda prompt, **kw: '["없는카드id"]'
    try:
        hit = _REAL_FITS(q, candidates, "업무 처리 절차") == []
    finally:
        tools.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 지어낸 id 는 실재 후보와 대조해 걸러낸다")
    ok += hit

    # ② 답의 형태 요구가 **원장에 실린 재료의 것만** 실리는가.
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

    # 등록된 도구는 전부 형태 요구를 갖는다 — 새 도구를 붙이고 여기를 빼먹으면
    # 그 재료만 조용히 형태 기준 없이 답해진다.
    missing = [n for n in tools.TOOLS if n not in ANSWER_SHAPES]
    hit = not missing
    print(f"{'✓' if hit else '✗'} 모든 도구에 형태 요구가 선언돼 있다"
          + ("" if hit else f" — 빠진 도구 {missing}"))
    ok += hit

    # 출처는 화면의 근거 목록이 전담한다(§5 「출처는 본문 문장이 아니다」). 형태 요구가
    # 본문에 출처를 요구하던 동안 LLM 이 재료의 「· 기준시점 … · 출처 …」 메타 줄을 통째로
    # 복사했다(gemma 실측 — 카드 덤프체 답변). 기준시점은 시효성 요구라 남는다.
    hit = ("출처" not in ANSWER_SHAPES["fact"] and "기준시점" in ANSWER_SHAPES["fact"]
           and "재료 블록의 형식은 옮기지 않는다" in COMPOSE_SYSTEM)
    print(f"{'✓' if hit else '✗'} 출처는 형태 요구가 아니라 근거 목록이 전담한다")
    ok += hit

    # 실제 프롬프트에 그 요구가 실리는가.
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


#: 되묻기 판정 골든셋 — «되물어야 하는 질문»과 «되물으면 안 되는 질문»을 실제 지식베이스
#: 재료로 고정한다. 판정 자체는 LLM 이 하므로 여기서 재는 것은 **판정이 내려졌을 때 턴이
#: 어떻게 끝나는가**다. 그 배선이 바뀌지 않았음을 보증해야, 되묻기 판정을 다른 자리로
#: 옮기는 변경(예: 작성과 동시 실행)이 답을 바꾸지 않았다고 말할 수 있다.
#:
#: (질문, 도구, 판정, 되묻기로 끝나야 하나, 근거에 있어야 할 갈래 표시)
def check_clarify_settled() -> int:
    """되묻기 판정이 열린 고객 화면의 값을 본다 — 갈래가 아니라 «정해진 것»으로 (§5 · gap 30).

    2026-09-04 gemma 실측: 만기 임박 고객(원리금보장 32.4%)을 열고 「뭐라고 말하면 좋아?」를
    물으니, 고객 상태에 걸린 화법 2장(「만기 임박+디폴트옵션 미등록 고객에게」·「원리금보장
    100% 운용 고객에게」)을 갈래로 읽어 **직원에게 고객 상태를 되물었다.** 상태 코드는 코드가
    원장에서 계산한 값이다(§3 「축을 가르는 것은 코드다」).
    """
    from pension_agent.consult_agent.nodes import clarify as CL

    ok = 0
    cid = "198734-1205842"          # 이준호 — 성립 요건 mat(만기예금 보유) 하나, 디폴트옵션 설정
    playbook = [{"tool": "playbook", "query": "q",
                 "text": "만기 임박+디폴트옵션 미등록 고객에게 → … / 원리금보장 100% 운용 고객에게 → …",
                 "atomic": [], "notices": [], "notice_scopes": [], "allow": [], "sources": [], "meta": {}}]
    fact = [{"tool": "fact", "query": "q", "text": "세액공제 한도 900만원 / 연금저축 단독 600만원",
             "atomic": [], "notices": [], "notice_scopes": [], "allow": [], "sources": [], "meta": {}}]
    customer = [{"tool": "customer", "query": "q", "text": "■ 고객 — 퇴직급여 5.2억 · 개인부담금 0원",
                 "atomic": [], "notices": [], "notice_scopes": [], "allow": [], "sources": [], "meta": {}}]

    # ① 고객 상태에 걸린 카드(playbook)만 있는 턴은 판정을 돌리지 않는다 — 그 카드를 고른
    #    기준(어느 상태인가)은 코드가 이미 정했다.
    hit = not CL.applicable({"question": "뭐라고 말하면 좋아?", "customer_id": cid, "evidence": playbook})
    print(f"{'✓' if hit else '✗'} playbook 근거만 있는 턴은 되묻기 판정을 돌리지 않는다")
    ok += hit

    # ② 고객이 열려 있으면 판정 프롬프트에 코드가 아는 상태가 «이미 정해진 것»으로 실린다 —
    #    <근거> 밖에. 고객 도구가 안 불린 턴에도(재료는 fact 만) 실린다.
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

        # 소득구간은 **양쪽을 다 재야 한다.** 원장에 총급여가 없던 동안(2026-09-18 부여 전)은
        # 전원 미확인이라 「안 실린다」만 재면 됐는데, 지금은 전원 확인이라 그 검사가 통째로
        # 사라질 뻔했다. 실데이터에는 이 컬럼이 없을 수 있고, 그때 「미확인」이 정해진 것으로
        # 실리면 판정이 그 축까지 정해진 줄 알고 되묻지 않는다(2026-09-05 리허설 K3).
        import dataclasses  # noqa: PLC0415
        from pension_agent.strategy_agent import customer as _SC  # noqa: PLC0415
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

        # ②-2 부담금 종류도 «정해진 것»이다(2026-09-07). 수수료율표(fact.k04.f50)가 갈리는
        #     축 셋 중 둘(부담금 종류·거래채널)이 원장 값이라, 이 블록에 없으면 T8 이 다시
        #     직원에게 되묻는다. 고객 도구가 안 불린 턴에도 실려야 한다 — 재료는 fact 뿐이다.
        hit = "사용자부담금" in settled and "가입자부담금" in settled
        print(f"{'✓' if hit else '✗'} 부담금 종류(사용자/가입자)가 «정해진 것»에 실린다")
        ok += hit

        # ③ 원장에 이미 실린 고객 재료 본문도 같은 블록에 온다.
        seen.clear()
        CL.clarify({"question": "수수료 얼마야?", "customer_id": cid, "evidence": customer + fact})
        prompt = seen[-1] if seen else ""
        settled = prompt.split("<이미 정해진 것>")[-1].split("</이미 정해진 것>")[0] if "<이미 정해진 것>" in prompt else ""
        hit = "개인부담금 0원" in settled
        print(f"{'✓' if hit else '✗'} 원장의 고객 재료 본문이 «정해진 것»에 실린다")
        ok += hit

        # ④ 고객이 없으면 블록도 없다 — 지식 질의응답의 판정은 그대로다.
        seen.clear()
        CL.clarify({"question": "실물이전 어떻게 해?", "evidence": fact})
        hit = bool(seen) and "<이미 정해진 것>" not in seen[-1]
        print(f"{'✓' if hit else '✗'} 고객이 열려 있지 않으면 «정해진 것» 블록이 없다")
        ok += hit
    finally:
        CL.generate = orig

    # ⑤ 블록은 LLM 없이 만들어진다 — 프로파일이 없는 id 면 비고, 예외를 내지 않는다.
    hit = CL.settled_block({"customer_id": "000000-0000000", "evidence": []}) == ""
    print(f"{'✓' if hit else '✗'} 없는 고객 id 는 빈 블록이고 예외가 아니다")
    ok += hit
    return ok


_CLARIFY_GOLDEN = (
    # ① 진짜 갈래 — 근거에 신청 경로가 셋이라 어느 쪽인지 정해야 답이 갈린다.
    ("계약이전 어떻게 신청해?", "procedure",
     {"ask": "어느 경로로 신청하시나요?", "options": ["후선 의뢰", "스타뱅킹", "인터넷뱅킹"]},
     True, "3경로"),
    # ② 가짜 갈래 — 카드는 여러 장이지만 답은 화면번호 하나로 확정된다. 카드 수는 갈래가
    #    아니다("근거가 여러 장 = 모호하다"로 읽으면 답할 수 있는 질문에 되묻게 된다).
    ("실물이전 가능여부 조회 화면번호?", "screen", {"ask": None}, False, "06-AD-020"),
)


def check_clarify_golden() -> int:
    """되묻기 판정 골든셋 — 판정이 내려진 뒤 **턴이 어떻게 끝나는가**를 실제 재료로 고정한다.

    §5 가 정한 것은 둘이다. 되물으면 그 턴은 답변도 화면 연계 제안도 없이 끝나고(선택지와
    출처만 나간다), 되묻지 않으면 답변 경로를 막지 않는다. 이 검사는 그 두 갈래를 **그래프
    전체로** 통과시켜 잰다 — 노드 하나만 직접 부르면 배선이 바뀌었을 때 조용히 지나간다.

    판정 자체(LLM)는 여기서 재지 않는다. 재료는 진짜 지식베이스에서 오고, 그 재료에 갈래가
    실제로 실려 있는지(=LLM 이 볼 수 있는지)까지가 코드가 보증할 수 있는 범위다.
    """
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    for question, tool, verdict, should_ask, marker in _CLARIFY_GOLDEN:
        found = tools.run(tool, {"question": question}, question)
        if not found:
            print(f"✗ 골든셋 재료 확보: {tool}({question}) → 근거 없음")
            continue

        # 재료에 갈래(또는 확정 답)가 실제로 실려 있나 — 없으면 판정은 근거 없는 추측이 된다.
        hit = marker in found["text"]
        print(f"{'✓' if hit else '✗'} 골든셋 재료에 «{marker}» 가 실려 있다 — {question}")
        ok += hit

        seen: list[str] = []
        orig_cl, orig_plan_node, orig_gen = CL.generate, G.plan_step, P.generate
        CL.generate = lambda prompt, **kw: (seen.append(prompt), json.dumps(verdict))[1]
        # 계획은 재료를 이미 넣어 둔 채로 끝낸다 — 이 검사가 재는 것은 판정 뒤의 배선이지
        # 도구 선택이 아니다(모듈 전역 stub_plan_pitch 는 화법 재료로 덮어쓴다).
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
            # 되묻기 턴 — 선택지가 답으로 나가고, 출처가 실리고(§5 마지막·gap 22),
            # 화면 연계 제안은 붙지 않는다(§5 · §10 은 다른 사건이다).
            hit = all(o in out["answer"] for o in verdict["options"]) \
                and bool(out.get("sources")) and not out.get("pending_action")
            print(f"{'✓' if hit else '✗'} 되묻기 턴: 선택지+출처가 나가고 연계 제안은 없다")
        else:
            # 되묻지 않은 턴 — 답변이 나가고 되묻기 흔적이 남지 않는다.
            hit = bool(out.get("answer")) and not out.get("clarify")
            print(f"{'✓' if hit else '✗'} 되묻지 않은 턴: 답변이 그대로 나간다")
        ok += hit

        # 판정 프롬프트가 그 재료를 실제로 봤나 — 못 보면 판정은 질문 한 줄로 하는 추측이다.
        hit = bool(seen) and marker in seen[0]
        print(f"{'✓' if hit else '✗'} 판정 프롬프트에 근거가 실린다 — {question}")
        ok += hit

    # ③ 맥락으로 갈래가 이미 정해진 후속 질문 — 판정 프롬프트가 이전 대화를 본다.
    #    "타행에서 가져오려는 고객"이 앞 턴에 나왔으면 방향은 정해진 것이고, 여기서 또
    #    되물으면 직원은 방금 말한 것을 다시 말해야 한다(§5 "맥락으로 추측이 서면 되묻지
    #    않는다"). 그 판단의 재료가 프롬프트에 실리는지가 코드의 몫이다.
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

    # ④ 갈래가 있을 수 없는 재료뿐이면 판정 자체를 돌리지 않는다 — 오판의 기회를 없앤다.
    #    (check_turn_cost 가 같은 것을 '아낀 호출' 쪽에서 재고, 여기서는 '판정 정확도' 쪽에서 잰다.)
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
    """되묻기 판정과 답변 작성을 동시에 돌려도 **답이 달라지지 않는가**(nodes/answer.py).

    아낀 것은 순차 왕복 하나이고, 아끼려고 판정을 건너뛰거나 규약을 바꾸지 않았다는 것이
    이 검사의 전부다. 세 가지를 고정한다:

      ① 되묻기로 결정되면 **써 둔 답은 나가지 않는다** — 투기 실행이 §5 를 뚫으면 안 된다.
      ② 판정이 없는 턴(근거 0건·갈래 없는 재료)은 스레드를 띄우지 않고 그대로 작성한다.
      ③ 진행 표시가 스레드를 건너간다 — ContextVar 는 자동으로 따라가지 않아서, 복사를
         빠뜨리면 "작성하고 있어요"가 조용히 사라진다(progress.py).

    판정이 LLM 장애로 죽었을 때의 답도 직렬일 때와 같아야 한다 — 그때는 원인만 남기고
    작성 결과가 나갔다(route_clarify 가 clarify 키만 봤다). §11 이 요구하는 것은 «어느
    단계에서 깨졌든 직원이 받는 답이 같을 것»이고, 작성이 성공했다면 그 답은 게이트를
    통과한 답이다.
    """
    from pension_agent.consult_agent import progress as PROG
    from pension_agent.consult_agent.nodes import answer as A
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    evidence = [{"tool": "procedure", "query": "q", "text": "전입 절차 / 전출 절차",
                 "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
                 "allow": ["전입 절차 / 전출 절차"], "sources": [{"id": "proc.x"}], "meta": {}}]
    state = {"question": "계약이전 어떻게 신청해?", "evidence": evidence}

    # ① 되묻기로 결정되면 써 둔 답은 버려진다.
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

    # 판정이 죽어도 작성이 성공했으면 그 답이 나간다 — 직렬일 때와 같은 규약(§11).
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

    # ② 판정이 없는 턴은 판정 LLM 을 부르지 않는다(스레드도 띄우지 않는다).
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

    # ③ 진행 표시가 스레드를 건너간다 — 작성은 다른 스레드에서 돈다.
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
    """되묻기 다음 턴이 «원래 질문»의 금액으로 계산하는가 · 화면번호를 일부만 써도 되는가.

    둘 다 리허설에서 실제로 터진 것이다(2026-09-02 이수민·박정호).

    ① `tax_credit` 이 이번 턴 질문에서만 금액을 뽑아, 되물은 갈래를 고르는 답의 수치를
       납입액으로 읽었다. 「300만원 더 넣으면?」 → 총급여 구간 되묻기 → 「5,500만원
       이하야」 에서 5,500만원을 납입액으로 읽고 잔여한도로 잘라 **1,485,000원**을 답했다.
       물어본 300만원의 답(495,000원)이 아니고, 되묻기 선택지에 방금 495,000원이라 적어
       놓고 그랬다. 기준서 §5 — 「원래 질문과 고른 갈래를 합쳐 답한다」.

    ② `span` 게이트가 화면번호를 흩어진 토큰으로 재서, 답변이 **일부만 인용하면** 폐기했다.
       번호끼리 앞 마디를 공유하기 때문이다(`04-12-…`·`06-12-…`). 원장 화면 일곱 개 중
       여섯 개를 정확히 인용한 절차 답변이 그래서 덤프됐다(박정호 P3).
    """
    from pension_agent.consult_agent import tools
    from pension_agent.consult_agent.nodes.plan import _span_verdict
    from pension_agent.strategy_agent.customer import PERSONAS

    ok = 0
    cid = next((p.id for p in PERSONAS if p.room > 0), PERSONAS[0].id)

    # ① 되묻기 다음 턴 — 원래 질문의 금액을 쓴다
    clarified = [{"question": "300만원 더 넣으면 얼마 돌려받아?",
                  "pending_clarify": {"question": "총급여 구간을 확인해 주세요",
                                      "options": ["5,500만원 이하", "5,500만원 초과"]}}]
    ev = tools._tax_credit({"customer_id": cid, "question": "5,500만원 이하야",
                            "history": clarified}, "")
    hit = ev is not None and "추가 납입액 300만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 되묻기 답: 갈래를 고른 말이 아니라 원래 질문의 금액으로 계산한다")
    ok += hit

    # 되묻기가 아니면 이번 질문에서 그대로 읽는다 — 넓히기만 하고 기존 동작을 바꾸지 않는다
    ev = tools._tax_credit({"customer_id": cid, "question": "500만원 더 넣으면?",
                            "history": []}, "")
    hit = ev is not None and "추가 납입액 500만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 되묻기 답: 평범한 턴은 이번 질문의 금액을 그대로 쓴다")
    ok += hit

    # ② 화면번호 — 일부만 인용해도 통과, 근거에 없는 번호는 폐기
    atomic = ["[06-12-501]", "[01-12-213]", "[04-12-641]", "[04-12-648]",
              "[04-12-644]", "[06-12-626]", "[04-12-646]"]
    found = {"atomic": atomic, "notices": [], "notice_scopes": [], "text": "", "allow": []}
    passing = [a for a in (
        "[06-12-501] 등록 후 [01-12-213] 로 입금하고 [04-12-646] 로 발굴합니다.",
        "06-12-501 등록 후 01-12-213 으로 입금합니다.",          # 대괄호 없이도 같은 화면이다
        "과세이연정보를 먼저 등록하고 60일 안에 입금합니다.",       # 아예 안 쓴 것은 위반이 아니다
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

    # ③ 아는 화면은 **원장 전체**로 본다 — 근거 한 건씩 재면 다른 근거의 화면이 «없는 화면»이
    #    된다. 실측(2026-09-07, 오세훈 SH5 · 박정호 PJ5): 절차 카드([06-12-622] → [02-12-221])와
    #    화면 카드([02-12-221])가 함께 실린 턴에서 답변이 절차 본문의 [06-12-622] 를 인용하자
    #    화면 카드 근거 차례에서 폐기됐고, 절차 원문 2건이 저작 메모까지 그대로 덤프됐다.
    from pension_agent.consult_agent.nodes import plan as PLAN
    _blank = {"notices": [], "notice_scopes": [], "related": [], "marks": [],
              "sources": [], "meta": {}, "query": ""}
    _proc_text = "■ 연금개시는 [06-12-622] 세액 미공제 한도 등록 → [02-12-221] 연금지급 등록의 2단계"
    _scr_text = "■ [02-12-221] 개인형IRP 연금지급  (지급·과세이연·연금)"
    # 실제 도구(_ev)처럼 본문을 수치 검사 허용 텍스트(allow)에도 싣는다.
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
    """등급형 판정 — 답한다 · 전제를 밝히고 답한다 · 되묻는다 · 없다 (§5 · gap 30).

    판정의 출력이 「되물을까/말까」 둘이던 동안 §5 의 나머지 두 결론은 **출력을 갖지
    못했다**: 판정자가 갈래를 알아내고도 그 사실이 작성자에게 건너가지 않았고(전제),
    핵심 대상이 없다는 판단은 작성 지시로만 걸려 있었다(대본 T12). 여기서 재는 것은
    네 등급이 각자 다른 일을 하는지, 그리고 **판정이 경계를 넓히지 못하는지**다.
    """
    from pension_agent.consult_agent.nodes import answer as ANS, clarify as CL
    ok = 0
    print("\n[등급형 판정 — 네 결론이 각자 출력을 갖는다 (§5)]")

    def ev(tool="procedure", text="타행→당행 절차 / 당행→타행 절차"):
        # allow 는 `_ev` 의 기본값과 같게 둔다 — `_quotable` 이 보는 원장 텍스트가
        # compose 의 검증기가 보는 것과 같아야 «통과할 전제»와 «폐기될 답»이 어긋나지 않는다.
        return {"tool": tool, "query": "q", "text": text, "atomic": [], "notices": [],
                "notice_scopes": [], "marks": [], "related": [], "allow": [text],
                "sources": [{"id": "proc.020", "title": "계약이전", "doc": "d",
                             "score": None, "page": None}], "meta": {}}

    orig_gen = CL.generate
    try:
        # ① assume — 갈래를 «정해 주는» 재료가 있으면 되묻지 말고 전제를 밝히고 답한다.
        CL.generate = lambda prompt, **kw: '{"verdict": "assume", "premise": "타행에서 당행으로 가져오는 경우"}'
        out = CL.clarify({"question": "실물이전 어떻게 처리해?", "customer_id": "198734-1205842",
                          "evidence": [ev()]})
        hit = out.get("judge_verdict") == CL.ASSUME and "타행에서 당행으로" in (out.get("judge_note") or "") \
            and not out.get("clarify")
        print(f"{'✓' if hit else '✗'} assume — 되묻지 않고 전제를 작성 지시로 넘긴다")
        ok += hit

        # ② none — 질문의 핵심 대상이 재료에 없다(§5 · 대본 T12 타행 수수료).
        CL.generate = lambda prompt, **kw: '{"verdict": "none", "missing": "타행 IRP 수수료"}'
        out = CL.clarify({"question": "타행 IRP 수수료는 우리보다 싼가?", "evidence": [ev()]})
        hit = out.get("judge_verdict") == CL.NONE and "타행 IRP 수수료" in (out.get("judge_note") or "")
        print(f"{'✓' if hit else '✗'} none — 없다는 사실을 첫 문장에 세우라고 넘긴다")
        ok += hit

        # ②-b **정해 줄 것이 없으면 전제를 만들 수 없다.** 열린 고객도 이전 대화도 없는
        #      턴에서 assume 이 나오면 그것은 무엇을 읽고 정한 것이 아니라 지어낸 전제다
        #      (2026-09-07 리허설 케이스 1 — 첫 턴·고객 없음인데 전제를 세워 답을 ISA 쪽으로
        #      밀었고, 카드가 못박은 오답에 걸려 폐기됐다).
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

        # ③ 판정이 **수치를 새로 만들 수 없다** — 원장 밖 숫자가 든 전제는 버린다.
        #    작성 프롬프트에 들어가면 작성자가 되받고, 그 수치는 원장 밖이라 답이 통째로
        #    폐기된다(§6). 넓히는 대신 넓힐 필요가 없는 문장만 통과시킨다.
        CL.generate = lambda prompt, **kw: '{"verdict": "assume", "premise": "총급여 7,700만원 구간"}'
        out = CL.clarify({"question": "얼마 돌려받아?", "customer_id": "198734-1205842",
                          "evidence": [ev(text="총급여 5,500만원 이하 16.5%")]})
        hit = out.get("judge_verdict") == CL.ASSUME and not out.get("judge_note")
        print(f"{'✓' if hit else '✗'} 원장 밖 수치가 든 전제는 버린다(경계는 코드가 쥔다)")
        ok += hit

        # 원장 안 수치면 통과한다 — 잃는 쪽으로만 기울지 않는다.
        CL.generate = lambda prompt, **kw: '{"verdict": "assume", "premise": "총급여 5,500만원 이하 구간"}'
        out = CL.clarify({"question": "얼마 돌려받아?", "customer_id": "198734-1205842",
                          "evidence": [ev(text="총급여 5,500만원 이하 16.5%")]})
        hit = bool(out.get("judge_note"))
        print(f"{'✓' if hit else '✗'} 원장 안 수치를 쓴 전제는 통과한다")
        ok += hit

        # ④ 옛 규격(`{"ask": …}`)도 되묻기로 읽는다 — 등급을 늘린 변경이 있던 기능을
        #    없애는 쪽으로 작동하면 안 된다(작은 모델이 규격을 못 맞추는 일이 있다).
        CL.generate = lambda prompt, **kw: '{"ask": "어느 방향인가요?", "options": ["타행 → 당행", "당행 → 타행"]}'
        out = CL.clarify({"question": "실물이전", "evidence": [ev()]})
        hit = out.get("judge_verdict") == CL.ASK and bool(out.get("clarify")) and bool(out.get("sources"))
        print(f"{'✓' if hit else '✗'} 등급 칸이 없는 옛 응답도 되묻기로 읽는다")
        ok += hit
    finally:
        CL.generate = orig_gen

    # 관문은 그대로다 — 고객 재료뿐이면 판정을 아예 돌리지 않는다(오판의 기회를 없앤다).
    hit = not CL.applicable({"question": "평가금액 얼마야",
                             "evidence": [ev(tool="customer", text="· 개인부담금 0원")]})
    print(f"{'✓' if hit else '✗'} 관문은 그대로 — 갈래를 만드는 재료가 없으면 판정을 안 돌린다")
    ok += hit

    # ⑥ 게이트가 표시한 갈래가 판정 프롬프트에 실린다. 없으면 블록 자체가 안 붙는다 —
    #    없는데 「갈래: 없음」을 세우면 판정 LLM 이 없는 갈래를 만든다(§7 과 같은 이유).
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

    # ⑦ 게이트 응답 읽기 — 객체·배열 둘 다 읽는다. 규격을 못 맞췄다고 후보를 전멸시키면
    #    갈래를 적게 한 변경이 «맞는 답을 지우는» 쪽으로 작동한다(§6).
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

    # 선택지가 하나뿐이면 갈래가 아니다 — 갈래를 보여주지 못하는 표시는 아무것도 정해주지 않는다.
    hit = tools._adequacy_verdict('{"keep": [], "branches": [{"axis": "축", "options": ["하나"]}]}')[1] == []
    print(f"{'✓' if hit else '✗'} 선택지가 2개 미만이면 갈래로 세지 않는다")
    ok += hit

    # ⑧ 갈래는 축 이름으로 중복이 걷힌다 — `tools.run` 이 질의를 바꿔 게이트를 두 번 돌린다.
    st: dict = {}
    axis = [{"axis": "이전 방향", "options": ["타행 → 당행", "당행 → 타행"]}]
    tools.record_branches(st, axis)
    tools.record_branches(st, axis)
    hit = st.get("branches") == axis
    print(f"{'✓' if hit else '✗'} 같은 갈래가 두 줄로 서지 않는다(원문 재검색 대비)")
    ok += hit

    # ⑨ 배선 — assume 이면 **다시 쓴 답**이 나가고, 다시 쓴 것이 비면 처음 것이 나간다.
    #    판정을 도우려던 장치가 답을 없애면 안 된다(§6 의 «옳은 답의 거부»가 판정 쪽에서
    #    재현되는 자리다).
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

    # ⑩ 되묻기는 그대로 — 판정이 되묻자고 하면 써 둔 답은 나가지 않는다(§5 의 지위는 불변).
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

    # ⑪ 게이트가 갈래를 **남긴 후보 위에서** 찾는지. 이 지시가 「빼려는 후보들이 서로
    #    갈래면」이던 동안 갈래 절은 «뺄 후보가 있는 턴»에만 읽혔고, 실측 11턴 내내
    #    `branches` 가 한 번도 안 찍혔다(gap 34). 갈래가 걸리는 질문일수록 갈래마다 답이
    #    되는 카드가 전부 맞는 카드라 하나도 안 빠지기 때문이다 — 장치가 붙어 있는데
    #    입력이 영원히 비는 형태라, 되묻기가 잘 되는 동안 아무도 눈치채지 못한다.
    #    문구를 재는 테스트인 이유는 **여기서 갈래가 생기지 않으면 아래 배선이 전부 죽은
    #    코드**이기 때문이다(⑥⑦⑧ 이 전부 통과해도 실전에서 안 돈다).
    from pension_agent.consult_agent.prompts import ADEQUACY_PROMPT
    text = ADEQUACY_PROMPT
    hit = "남긴 후보 중에" in text and "빼려는 후보들이" not in text
    print(f"{'✓' if hit else '✗'} 게이트는 갈래를 «남긴 후보» 위에서 찾는다(뺄 때만이 아니다)")
    ok += hit

    # ⑫ 그래도 케이스 8 에서 안 찍혔다(2026-09-15). 원인 셋 — 둘째 줄이 「JSON 배열로 출력」
    #    이라 끝의 객체 규격과 모순(배열로 오면 파서가 갈래를 못 읽는다) · 갈래를 «후보 사이»
    #    에서만 찾게 해 한 장 안의 구간별 표(f50)·조건별 값(f53)이 갈래로 안 보임 · 출력 예시가
    #    빈 배열부터. 셋을 재고, 트레이스가 호출을 가르는 첫 줄은 그대로인지도 잰다.
    first = text.split("{")[0].strip().splitlines()[0]
    hit = ("JSON 배열로 출력" not in text and "JSON 객체 하나" in text
           and "한 후보 안에도 있다" in text and "조건별 값:" in text
           and "단서는 갈래가 아니다" in text
           and text.index('"axis": "이전 방향"') < text.index("빈 배열로 둔다")
           and first == "직원의 질문과, 그 질문에 답하려고 검색된 근거 후보 목록이다.")
    print(f"{'✓' if hit else '✗'} 게이트 프롬프트 — 객체 규격 하나 · 한 후보 안의 갈래 · 갈래 예시가 먼저 · 첫 줄 유지")
    ok += hit

    # ⑬ **다시 쓴 답이 게이트에 걸리면 처음 답을 낸다.** `answer.py` 머리말이 그렇게 적어
    #    두고도 코드는 구분하지 못했다 — 근거 원문 폴백도 `answer` 가 채워져 나오니
    #    «답이 있다»로 읽혀 검증을 통과한 답을 버렸다. 실측(2026-09-17): 첫 생성문 1141자가
    #    `verify 통과=예` 였는데 판정=전제로 다시 쓴 786자가 표 오짝에 걸렸고, 직원 화면에는
    #    4,442자 표 덤프가 떴다. 판정을 도우려던 장치가 답을 없앤 자리다.
    calls: list[str] = []
    first = {"answer": "전제 없이 쓴 검증된 답", "sources": [], "fallback": ""}
    dumped = {"answer": "■ 카드 제목\n| 표 | 덤프 |", "sources": [], "fallback": "raw_evidence"}

    def _compose(state, _seq=[0]):  # noqa: B006 — 호출 순서를 세는 자리다
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

    # 반대쪽 — 다시 쓴 것이 멀쩡하면 그것을 낸다(장치가 죽어 있으면 안 된다).
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
