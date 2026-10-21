"""재료 검사 — 브리핑·고객·화법·계좌·오늘·상담이력·시황 도구의 근거와 표시.

실행: `tests/test_agent.py::main` 경유(스텁 설치·정리가 그쪽에 있음).
"""

from __future__ import annotations

import json
import pathlib

from pension_agent import tools
from pension_agent.nodes import plan
from pension_agent.evidence.verify import verify_texts

from tests.agent._common import print, _vt  # noqa: A001 — 집계용 print


def check_briefing_shared() -> int:
    """전체 브리핑 공유 — 한 번 만들어 대화가 같은 것을 봄 · 캐시 오염 방지 · 부분 질문 LLM 0회."""
    from pension_agent.tools.customer_solution import customer as CUST
    from pension_agent.tools.customer_solution import solution as SOL

    ok = 0
    calls: list[str] = []
    orig_gen, orig_avail = SOL.llm.generate, SOL.llm.available
    profile = CUST.PERSONAS[0]

    SOL.clear_cache()
    SOL.llm.available = lambda: True
    # 호출마다 다른 문장을 내는 LLM — 캐시가 없으면 두 호출이 갈림
    SOL.llm.generate = lambda prompt, **kw: (
        calls.append("llm"),
        json.dumps({"recent": [], "focus": f"확인할 점 {len(calls)}", "why_now": "", "confirm": []},
                   ensure_ascii=False))[1]
    try:
        first = SOL.document(profile)
        n_first = len(calls)
        second = SOL.document(profile)
        n_second = len(calls) - n_first
        # 부분 질문 재료(customer)는 LLM 미호출
        before = len(calls)
        tools.run("customer", {"customer_id": profile.id}, "평가금액 얼마야?")
        n_partial = len(calls) - before
    finally:
        SOL.llm.generate, SOL.llm.available = orig_gen, orig_avail

    hit = n_first == 1 and n_second == 0
    print(f"{'✓' if hit else '✗'} 같은 고객 브리핑은 한 번만 만든다 "
          f"(1회차 LLM {n_first}회 → 2회차 {n_second}회)")
    ok += hit

    hit = first["markdown"] == second["markdown"]
    print(f"{'✓' if hit else '✗'} 두 번째 호출이 같은 문서를 받는다")
    ok += hit

    hit = n_partial == 0
    print(f"{'✓' if hit else '✗'} 부분 질문의 재료(customer)는 LLM 을 부르지 않는다 ({n_partial}회)")
    ok += hit

    # 돌려받은 값을 고쳐도 캐시 오염 금지
    a = SOL.document(profile)
    kept = a["markdown"]
    a["markdown"] = "호출부가 고친 문서"
    a["narrative"]["focus"] = "고친 서술"
    f1 = SOL.facts(profile)
    f1["customer"] = {}
    hit = SOL.document(profile)["markdown"] == kept and bool(SOL.facts(profile)["customer"])
    SOL.clear_cache()
    print(f"{'✓' if hit else '✗'} 돌려받은 브리핑을 고쳐도 다음 호출자는 원본을 받는다")
    ok += hit

    # 캐시 키는 프로파일 내용 — id 가 같아도 내용이 다르면 별개
    import dataclasses
    other = dataclasses.replace(profile, room=0, isa=None, bal=profile.bal + 1)
    hit = SOL._key(profile) != SOL._key(other)
    print(f"{'✓' if hit else '✗'} 캐시 키는 id 가 아니라 프로파일 내용이다")
    ok += hit

    # 캐시 상한 — 실서비스는 고객 수만큼 호출
    try:
        for i in range(SOL._CACHE_MAX + 5):
            SOL._remember(SOL._DOCS, f"key-{i}", {"x": i})
        hit = len(SOL._DOCS) == SOL._CACHE_MAX and "key-0" not in SOL._DOCS \
            and f"key-{SOL._CACHE_MAX + 4}" in SOL._DOCS
    finally:
        SOL.clear_cache()
    print(f"{'✓' if hit else '✗'} 캐시가 상한({SOL._CACHE_MAX})에서 오래된 것부터 밀어낸다")
    ok += hit
    return ok


def check_customer_material() -> int:
    """고객 재료 단일 경로(customer 도구) · 고객 상태 주의는 코드가 부착."""
    from pension_agent.tools.customer_solution import guard as GD
    from pension_agent.nodes import plan as P

    ok = 0

    # ① 고객 재료 경로가 하나뿐인지
    import importlib
    gone = importlib.util.find_spec("pension_agent.nodes.briefing_qa") is None
    print(f"{'✓' if gone else '✗'} 고객 재료를 답하던 두 번째 경로(briefing_qa)가 없다")
    ok += gone

    found = tools.run("customer", {"customer_id": "198734-1205842"}, "이 고객 평가금액 얼마야?")
    hit = bool(found) and "평가금액" in found["text"] and "성립 요건" in found["text"]
    print(f"{'✓' if hit else '✗'} customer 도구가 브리핑 재료(값·요건)를 싣는다")
    ok += hit

    # 전체 브리핑의 AI 분석도 재료에 포함 — 직원은 그 문서를 보며 질문
    from pension_agent.tools.customer_solution import solution as SOL
    orig_peek = SOL.peek
    SOL.peek = lambda profile: {"recent": ["만기 자금이 대기 중입니다."],
                                "focus": "만기 자금의 운용 방향을 확인합니다.",
                                "why_now": "", "confirm": []}
    try:
        found = tools.run("customer", {"customer_id": "198734-1205842"}, "브리핑 요약해줘")
        hit = bool(found) and "브리핑 최근 금융상황" in found["text"] \
            and "브리핑 이번 상담에서 확인할 점" in found["text"]
    finally:
        SOL.peek = orig_peek
    print(f"{'✓' if hit else '✗'} customer 도구가 전체 브리핑의 AI 분석까지 재료로 싣는다")
    ok += hit

    # 고객 정보 질의응답 재료 요건 — 빠지면 LLM 이 없는 재료로 말을 만듦
    _mat = tools.run("customer", {"customer_id": "181245-3097614"}, "이 고객 현황")["text"]
    _NEED = [
        ("① 값 — 자산군별 금액", "고유계정대 2,000만원"),   # 비중만으로는 금액 답변 불가
        ("① 값 — 자산군별 비중(원장값)", "(7.7%)"),          # 4분류 반올림(8%)이 아니라 원장값
        ("① 값 — 만기 전건", "2027-02-01"),                 # 가장 가까운 한 건만이 아님
        ("② 왜 이 고객인가", "· 왜 이 고객인가:"),
        ("② 판단근거", "· 판단근거:"),
        ("② 문제상황", "· 문제상황 1:"),
        ("② 성립 요건", "· 성립 요건:"),
    ]
    # 원장 → Profile → 재료 경로 관통 여부
    _big = tools.run("customer", {"customer_id": "188406-7352194"}, "현황")["text"]
    for _label, _needle in (("보유상품 개별 종목", "KB 퇴직연금 배당"),
                            ("판매중단 표시", "⚠판매중단"),
                            ("동연령대 비교", "동연령 평균 수익률"),
                            ("거래 활동", "1년 매매")):
        _h = _needle in _big
        print(f"{'✓' if _h else '✗'} 고객 재료: {_label}" + ("" if _h else f" — '{_needle}' 없음"))
        ok += _h

    # 과거 상담 기록은 세션 저장소(scripts/seed_sessions.py) 단일 경로 — 두 경로면 같은 상담 중복
    _PAST = "재투자하고 싶다"          # 송도윤 상담 기록 한 조각
    _hist = tools.run("history", {"customer_id": "188406-7352194"}, "지난번에 무슨 얘기 했어")
    hit = bool(_hist) and _PAST in _hist["text"] and "상담기록" in _hist["text"]
    print(f"{'✓' if hit else '✗'} history 도구: 과거 상담 기록을 싣는다(role=record)")
    ok += hit
    from pension_agent.tools.customer_solution import engine as _eng
    from pension_agent.tools.customer_solution.customer import get_profile as _gp
    _screen = _eng.prepare(_gp("188406-7352194"))["consult_history"]
    hit = sum(_PAST in line for line in _screen) == 1
    print(f"{'✓' if hit else '✗'} 화면 §14 도 같은 기록을 «한 번만» 본다(대화형과 답이 갈리지 않는다)")
    ok += hit

    # ISA 만기자금·납입이력 — Profile 이 안 접으면 대화형 미노출
    _isa_mat = tools.run("customer", {"customer_id": "188406-7352194"}, "ISA")["text"]
    hit = "ISA만기자금" in _isa_mat and "1억 2,000만원" in _isa_mat
    print(f"{'✓' if hit else '✗'} 고객 재료: ISA 만기자금이 원장에서 대화형까지 온다")
    ok += hit
    _pay_mat = tools.run("customer", {"customer_id": "176903-5528417"}, "납입")["text"]
    hit = "납입이력" in _pay_mat and "2025년" in _pay_mat
    print(f"{'✓' if hit else '✗'} 고객 재료: 연도별 납입 이력이 실린다(당해분만이 아니다)")
    ok += hit

    for _label, _needle in _NEED:
        _h = _needle in _mat
        print(f"{'✓' if _h else '✗'} 고객 재료: {_label}" + ("" if _h else f" — '{_needle}' 없음"))
        ok += _h

    # 같은 항목이 재료 안에서 두 값 금지(4분류 반올림 vs 원장값)
    import re as _re
    _three = _re.search(r"운용현황\(3분류\)[^\n]*고유계정대 ([\d.]+)%", _mat)
    _asset = _re.search(r"자산군별[^\n]*고유계정대[^(]*\(([\d.]+)%\)", _mat)
    hit = bool(_three and _asset) and _three.group(1) == _asset.group(1)
    print(f"{'✓' if hit else '✗'} 고객 재료: 같은 항목(고유계정대 비중)이 한 값으로만 실린다"
          + ("" if hit else f" — 3분류 {_three and _three.group(1)} vs 자산군별 {_asset and _asset.group(1)}"))
    ok += hit

    # 인용 허용 집합에 후보 더미(pools) 미포함 — 쓰지 않을 카드 숫자가 근거가 되면 검증 무력화
    from pension_agent.evidence.verify import verify_texts as _vt
    _ev = tools.run("customer", {"customer_id": "198734-1205842"}, "만기")
    hit = (_vt("만기일은 2026-09-10, 금액 4,050만원이에요.", _ev["allow"])[0]
           and not _vt("만기일은 2026년 9월 11일입니다.", _ev["allow"])[0])
    print(f"{'✓' if hit else '✗'} 고객 재료: 화면 값은 인용 통과, 후보 더미가 licensing 하던 오답은 거부")
    ok += hit

    hit = tools.run("customer", {"customer_id": None}, "평가금액") is None
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있으면 고객 재료를 만들지 않는다")
    ok += hit

    # ② 고객 상태 주의를 코드가 판단(이준호는 만기 요건 성립)
    hit = "mat" in [c.split(":")[0] for c in GD.conditions_of("198734-1205842")]
    print(f"{'✓' if hit else '✗'} conditions_of: customer_id 만으로 고객 요건을 읽는다")
    ok += hit

    hit = GD.conditions_of(None) == [] and GD.conditions_of("없는고객") == []
    print(f"{'✓' if hit else '✗'} conditions_of: 고객이 없으면 요건을 지어내지 않는다")
    ok += hit

    # 화법만 물어 원장에 고객 재료가 없는 턴에도 가드 부착
    pitch_only = [{"tool": "pitch", "query": "q", "text": "수익률 이야기를 이렇게 꺼내세요.",
                   "atomic": [], "notices": [], "notice_scopes": [],
                   "allow": ["수익률 이야기를 이렇게 꺼내세요."], "sources": [], "meta": {}}]
    orig_gen = P.generate
    P.generate = lambda prompt, **kw: "(스텁 답변)"
    try:
        # 김현수(dep·nod) — 대응 주의 카드 실재. 이준호(mat)는 주의 카드가 없어 가드가 정당하게 빔
        out = P.compose({"question": "뭐라고 말하지?", "customer_id": "173544-2074623",
                         "evidence": pitch_only})
        hit = bool(out.get("guards"))
        closed = P.compose({"question": "뭐라고 말하지?", "evidence": pitch_only})
        hit = hit and not closed.get("guards")
    finally:
        P.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 원장에 고객 재료가 없어도 가드가 붙는다(고객 화면이 열려 있으면)")
    ok += hit

    # ③ 묶음으로 따라온 카드는 답변이 쓴 것만 근거로
    from pension_agent.tools.customer_solution.tool import _customer
    ev = _customer({"customer_id": "198734-1205842"}, "디폴트옵션 등록 여부")
    hit = bool(ev) and bool(ev.get("source_keys"))
    print(f"{'✓' if hit else '✗'} customer 도구가 딸려 보낸 카드마다 «답변이 썼는지» 가릴 스팬을 선언한다")
    ok += hit

    if ev:
        # 원장 한 줄 답 — 딸려 온 카드 제외, 원장 출처만
        lean = P._sources([ev], [], [], "네, 디폴트옵션이 설정되어 있어요.")
        hit = len(lean) == 1 and lean[0]["id"] == "customer"
        print(f"{'✓' if hit else '✗'} 원장 한 줄로 답한 턴에는 딸려 온 화법 카드가 근거로 서지 않는다"
              f" ({len(ev['sources'])}건 → {len(lean)}건)")
        ok += hit

        # 그 카드를 실제로 쓴 답 — 근거 유지
        cid, keys = next(iter(ev["source_keys"].items()))
        span = next(k for k in keys if len(k.strip()) >= P._KEY_MIN)
        used = P._sources([ev], [], [], f"이렇게 말해 보세요. {span}")
        hit = cid in {x["id"] for x in used}
        print(f"{'✓' if hit else '✗'} 답변이 그 카드의 문구를 쓰면 근거에 그대로 남는다 ({cid})")
        ok += hit

        # 검색 재료는 판정 대상 아님 — 전부 근거
        searched = dict(ev, source_keys={})
        hit = len(P._sources([searched], [], [], "관계없는 답변")) == len(ev["sources"])
        print(f"{'✓' if hit else '✗'} 스팬을 선언하지 않은 출처(검색 결과)는 하나도 빠지지 않는다")
        ok += hit
    return ok


def check_no_card_ids_in_material() -> int:
    """작성 프롬프트용 재료 본문에 카드 id 미포함 — id 는 출처에만. 접두는 지식베이스에서 읽음."""
    import re

    from pension_agent.tools.kb_search import kb_index
    from pension_agent.state import KB as _KB
    from pension_agent.tools.kb_search.combine import evidence_from_cards

    ok = 0
    SONG = "188406-7352194"
    prefixes = sorted({c["id"].split(".")[0] for c in _KB.cards}, key=len, reverse=True)
    card_id = re.compile(r"(?<![0-9A-Za-z_가-힣.])(" + "|".join(map(re.escape, prefixes))
                         + r")\.[0-9A-Za-z_][0-9A-Za-z_.-]*")

    def leaked(text: str) -> list[str]:
        return sorted({m.group(0) for m in card_id.finditer(text or "")})

    # ① 화법 렌더러 — 검색 결과 3장(도구·playbook·last_answer 공용)
    pitches = [(4.0 - i, c) for i, c in enumerate(_KB.pitches[:3])]
    text = kb_index.build_context(_KB, pitches)
    bad = leaked(text)
    hit = bool(text) and not bad and all(f"■ {c['title']}" in text for _s, c in pitches)
    print(f"{'✓' if hit else '✗'} 화법 재료 머리줄은 「■ 제목」이고 카드 id·관련도가 없다"
          + (f" — {bad[:3]}" if bad else ""))
    ok += hit

    # ② 종류 혼합 묶음(playbook·last_answer 조립) — 어느 렌더러도 id 미포함
    mixed = [(1.0, c) for kind in ("pitch", "procedure", "method", "fact", "screen", "channel",
                                   "segment", "fieldtip", "market", "lineup")
             for c in [next((x for x in _KB.cards if x["_kind"] == kind), None)] if c]
    ev = evidence_from_cards("playbook", "q", mixed, customer_id=SONG)
    bad = leaked(ev["text"] if ev else "")
    hit = ev is not None and not bad
    print(f"{'✓' if hit else '✗'} 종류별 렌더러 전부가 재료 본문에 카드 id 를 싣지 않는다"
          + (f" — {bad[:3]}" if bad else ""))
    ok += hit

    # ③ 고객 재료 — ⑥⑦⑧ 줄 꼬리에 문제상황만, id 는 출처로
    ev = tools.run("customer", {"customer_id": SONG}, "이 고객 상태에 걸린 참고자료 뭐뭐 있어?")
    bad = leaked(ev["text"] if ev else "")
    hit = ev is not None and not bad
    print(f"{'✓' if hit else '✗'} 고객 재료의 ⑥⑦⑧ 줄에 카드 id 가 없다"
          + (f" — {bad[:3]}" if bad else ""))
    ok += hit

    hit = ev is not None and any(card_id.fullmatch(s["id"]) for s in ev["sources"])
    print(f"{'✓' if hit else '✗'} 그 카드 id 는 출처에 그대로 남는다(역추적용)")
    ok += hit
    return ok


def check_playbook_material() -> int:
    """고객 상태 화법 — 화면 ⑥⑦⑧ 과 같은 후보군 · 선제 제안 조건 · 승낙 턴 · 연금개시 차단 상속."""
    import dataclasses

    from pension_agent.nodes import act as ACT
    from pension_agent import routing as R
    from pension_agent.tools.customer_solution import customer as SC
    from pension_agent.tools.customer_solution.situations import problem_situations

    ok = 0
    SONG = "188406-7352194"   # 송도윤 — 요건 6건, 후보 넉넉

    # ① 화면 ⑥⑦⑧ 선택이 재료·출처에 함께 실림
    ev = tools.run("customer", {"customer_id": SONG}, "이 고객한테 뭐라고 말하지")
    text = ev["text"]
    hit = all(k in text for k in ("· 이렇게 말해보세요:", "· 예상 반론:", "· 상담 참고:"))
    print(f"{'✓' if hit else '✗'} customer 재료에 화면 ⑥⑦⑧ 이 고른 화법·반론·참고자료가 실린다")
    ok += hit

    card_ids = {s["id"] for s in ev["sources"] if s["id"].startswith(("pitch.", "m.", "proc."))}
    hit = bool(card_ids)
    print(f"{'✓' if hit else '✗'} 그 카드가 **출처**에도 실린다(§3) — {len(card_ids)}건")
    ok += hit

    # 검색 재료가 아니므로 관련도 미기재
    hit = all(s.get("score") is None for s in ev["sources"])
    print(f"{'✓' if hit else '✗'} 고객 재료의 출처에는 관련도를 붙이지 않는다")
    ok += hit

    # 고객 재료 경로의 카드 출처에도 검색 경로와 같은 원천 URL
    from pension_agent.knowledge.kb import card_source_meta
    from pension_agent.state import KB as _KB
    hit = all("url" in s and s["url"] == card_source_meta(_KB, s["id"]).get("url")
              for s in ev["sources"] if s["id"] in card_ids)
    print(f"{'✓' if hit else '✗'} 고객 재료의 카드 출처가 검색 경로와 같은 URL 을 싣는다")
    ok += hit

    # 위 대조가 «둘 다 None» 으로 늘 참이 되지 않게 되짚기 자체를 측정 — 송도윤 카드는 URL 없음
    linked = [c["id"] for c in _KB.cards if card_source_meta(_KB, c["id"]).get("url")]
    hit = len(linked) > 10
    print(f"{'✓' if hit else '✗'} 카드 id 로 원천 게시글 URL 을 되짚을 수 있다({len(linked)}건)")
    ok += hit

    # ② 후보는 strategy_agent 매칭에서만 — 대화형 자체 매칭 금지
    hits = tools.playbook_hits({"customer_id": SONG, "question": "증권사 얘기를 꺼내네요"})
    from pension_agent.tools.customer_solution.support import matching as M
    sits = problem_situations(SC.get_profile(SONG), SC.conditions(SC.get_profile(SONG)))
    pool = ({c["id"] for t in ("proposal", "objection", "guide")
             for _s, c, _seg in M.scored_situation_cards(sits, t, 50)}
            | {c["id"] for _s, c, _seg in M.scored_situation_procedures(sits, 50)}
            | {c["id"] for _s, c, _seg in M.scored_situation_methods(sits, 50)})
    hit = bool(hits) and all(c["id"] in pool for _s, c in hits)
    print(f"{'✓' if hit else '✗'} situation 후보가 화면 ⑥⑦⑧ 과 같은 매칭 결과 안에 있다")
    ok += hit

    hit = tools.playbook_hits({"customer_id": None, "question": "뭐라고 말하지"}) == []
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있으면 상태 화법을 만들지 않는다")
    ok += hit

    hit = "playbook" not in tools.usable({}) and "playbook" in tools.usable({"customer_id": SONG})
    print(f"{'✓' if hit else '✗'} 고객이 닫혀 있으면 도구 목록에서도 빠진다")
    ok += hit

    # ③ 제안 트리거 — 네 조건 중 하나라도 어긋나면 미부착
    pitch_ev = [{"tool": "pitch", "query": "q", "text": "이렇게 말해보세요.", "atomic": [],
                 "notices": [], "notice_scopes": [], "allow": ["이렇게 말해보세요."],
                 "sources": [], "meta": {}}]
    fact_ev = [{**pitch_ev[0], "tool": "fact"}]
    hit = ACT._propose({"answer": "a", "customer_id": SONG, "evidence": fact_ev}) is None
    print(f"{'✓' if hit else '✗'} 화법을 다루지 않은 턴(값 질의)에는 상태 화법 제안이 안 붙는다")
    ok += hit

    hit = ACT._propose({"answer": "a", "evidence": pitch_ev}) is None
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있으면 제안이 안 붙는다")
    ok += hit

    action = ACT._propose({"answer": "a", "customer_id": SONG, "evidence": pitch_ev})
    hit = bool(action) and action["kind"] == "pitch" and bool(action.get("cards"))
    print(f"{'✓' if hit else '✗'} 화법 턴 + 고객 열림 + 남은 카드 → 제안이 붙는다")
    ok += hit

    # 제안 문구는 그 카드를 세운 문제상황을 밝힘(고객 요건 목록 아님)
    ranked = tools.playbook_ranked({"answer": "a", "customer_id": SONG,
                                    "evidence": pitch_ev}, lanes=("pitch",))
    matched = {ACT.situation_name(sit) for _s, _c, sit in ranked} - {""}
    label = (action or {}).get("label") or ""
    hit = bool(action) and bool(matched) and any(t in label for t in matched)
    print(f"{'✓' if hit else '✗'} 제안 문구가 «그 카드를 세운 문제상황»을 밝힌다 — {label}")
    ok += hit

    # 카드를 내지 않은 사유는 제목에 미포함 — 제목이 내용보다 넓으면 거짓
    stray = [n for n in SC.CONDS.values() if n in label and n not in matched]
    hit = bool(action) and not stray
    print(f"{'✓' if hit else '✗'} 카드를 내지 않은 요건 이름은 제안 문구에 서지 않는다"
          + (f" — {stray}" if stray else ""))
    ok += hit

    # 이번 턴 원장에 실린 카드는 재제안 금지
    used = dict(pitch_ev[0], sources=[{"id": c["id"], "title": "", "doc": "", "score": None,
                                       "page": None} for c in
                                      [SITU for _s, SITU in tools.playbook_hits(
                                          {"customer_id": SONG, "question": "q"})]])
    again = ACT._propose({"answer": "a", "customer_id": SONG, "evidence": [used]})
    hit = again is None or not ({c["id"] for c in again["cards"]}
                                & {s["id"] for s in used["sources"]})
    print(f"{'✓' if hit else '✗'} 이번 턴이 이미 쓴 카드를 다시 보여드릴까요 하고 묻지 않는다")
    ok += hit

    # ③-b 갈래 일치 — 이번 턴이 다룬 갈래의 나머지 후보만 제안
    hit = all(c["_kind"] == "pitch" for c in
              (lambda a: [next(x for x in tools.KB.cards if x["id"] == cc["id"])
                          for cc in a["cards"]])(action))
    print(f"{'✓' if hit else '✗'} 화법 턴의 제안은 화법 카드만 담는다")
    ok += hit

    proc_ev = [{**pitch_ev[0], "tool": "procedure"}]
    p_action = ACT._propose({"answer": "a", "customer_id": SONG, "evidence": proc_ev})
    by_id = {c["id"]: c for c in tools.KB.cards}
    hit = bool(p_action) and all(by_id[c["id"]]["_kind"] == "procedure"
                                 for c in p_action["cards"])
    print(f"{'✓' if hit else '✗'} 절차 턴의 제안은 절차 카드만 담는다 — {p_action and p_action['label']}")
    ok += hit

    m_action = ACT._propose({"answer": "a", "customer_id": SONG,
                             "evidence": [{**pitch_ev[0], "tool": "method"}]})
    hit = bool(m_action) and all(by_id[c["id"]]["_kind"] == "method" for c in m_action["cards"])
    print(f"{'✓' if hit else '✗'} 방법론 턴의 제안은 방법론 카드만 담는다")
    ok += hit

    # ③-c 종류별 렌더러·선언 — 화법 렌더러로 절차를 렌더하면 저작 메모 유출·화면번호 atomic 누락
    mixed = tools.playbook_hits({"customer_id": SONG, "question": "q"},
                                lanes=("procedure", "method"))
    pev = tools.playbook_evidence("점검", mixed)
    proc_cards = [c for _s, c in mixed if c["_kind"] == "procedure"]
    hit = (bool(pev) and "필자 해석" not in pev["text"] and "'role'" not in pev["text"]
           and all(sc in pev["atomic"] for c in proc_cards for sc in (c.get("screens") or [])))
    print(f"{'✓' if hit else '✗'} playbook 근거가 절차·방법론에 그 종류의 렌더러·선언을 쓴다"
          f" (화면번호 atomic {len(pev['atomic'])}건 · 저작 메모 미유출)")
    ok += hit

    # ④ 승낙 턴은 근거만, 답변은 작성 단계
    out = ACT.confirm_action({"question": "네", "customer_id": SONG,
                              "history": [{"pending_action": action}]})
    hit = bool(out.get("evidence")) and not out.get("answer")
    print(f"{'✓' if hit else '✗'} 승낙 턴이 근거만 싣고 답변 문장을 손으로 만들지 않는다")
    ok += hit

    # ④-b 승낙 자료가 «없는 자료»로 나가면 안 됨 — 질문 «네»로 자료를 재지 않게 승낙 사실을 코드가 전달
    hit = out.get("accepted") == action["label"]
    print(f"{'✓' if hit else '✗'} 승낙 턴이 무엇을 승낙받았는지 남긴다(작성 단계가 볼 수 있게)")
    ok += hit

    # 상태 선언이 없으면 LangGraph 가 노드 반환 키를 조용히 버림
    from pension_agent.state import AgentState as _AS
    hit = "accepted" in _AS.__annotations__
    print(f"{'✓' if hit else '✗'} 그 값이 상태에 선언돼 있다(선언 없으면 그래프가 버린다)")
    ok += hit

    seen: dict[str, str] = {}
    orig_gen = plan.generate
    plan.generate = lambda p, **kw: seen.setdefault("p", p) or "답변"
    try:
        plan.compose({"question": "네", "customer_id": SONG, **out})
        hit = ("승낙에 대한 답이다" in seen["p"] and action["label"] in seen["p"]
               and '"준비된 자료가 없다"고 말하지 않는다' in seen["p"])
        print(f"{'✓' if hit else '✗'} 승낙 턴의 작성 프롬프트가 «이 자료를 보여주라»고 말한다")
        ok += hit

        seen.clear()
        plan.compose({"question": "실물이전 절차 알려줘", "customer_id": SONG,
                      "evidence": out["evidence"]})
        hit = "승낙에 대한 답이다" not in seen["p"]
        print(f"{'✓' if hit else '✗'} 승낙 턴이 아니면 그 블록이 붙지 않는다")
        ok += hit
    finally:
        plan.generate = orig_gen

    # 도착지는 compose(되묻기 판정·답변 작성)
    hit = R.route_confirm(out) == "compose" and R.route_confirm(
        {"answer": "화면을 열었어요"}) == "__end__"
    print(f"{'✓' if hit else '✗'} 분기표가 그 턴을 답변 작성으로 보낸다(화면 연계는 그대로 끝)")
    ok += hit

    # 승낙 턴은 되묻기 판정 미실행 — 입력이 «네» 한 글자
    from pension_agent.nodes import clarify as _CL
    # 대조군은 지식베이스 검색 재료 — playbook 재료만으로는 판정이 원래 안 돎
    kb_evidence = [{"tool": "procedure", "query": "q", "text": "실물이전 절차", "atomic": [],
                    "notices": [], "notice_scopes": [], "allow": [], "sources": [], "meta": {}}]
    hit = not _CL.applicable({**out, "intent": "confirm_action"}) \
        and _CL.applicable({**out, "intent": "situation", "question": "실물이전 절차",
                            "evidence": kb_evidence})
    print(f"{'✓' if hit else '✗'} 승낙 턴은 되묻기 판정을 돌리지 않는다")
    ok += hit

    # 제안한 턴이 남긴 카드만 적재 — «네»에서 재선택 금지
    hit = bool(out.get("evidence")) and {s["id"] for s in out["evidence"][0]["sources"]} == {
        c["id"] for c in action["cards"]}
    print(f"{'✓' if hit else '✗'} 승낙 턴이 제안한 턴의 카드를 그대로 싣는다")
    ok += hit

    # ⑤ 연금수령 개시 계좌 — 납입·세액공제 세그먼트 제외. 목업 전원 pension_started=False 라 합성 프로필
    base = SC.get_profile("176903-5528417")       # 한지우 — isa·tax·add
    started = dataclasses.replace(base, pension_started=True)
    before = {s["id"] for s in problem_situations(base, SC.conditions(base))}
    after = {s["id"] for s in problem_situations(started, SC.conditions(started))}
    hit = {"seg.13", "seg.15", "seg.16"} <= before and not ({"seg.13", "seg.15", "seg.16"} & after)
    print(f"{'✓' if hit else '✗'} 연금개시 계좌에서 납입·세액공제 세그먼트가 빠진다(exclusions)")
    ok += hit

    orig = SC.get_profile
    SC.get_profile = lambda cid: started if cid == "PENSION-STARTED" else orig(cid)
    try:
        blocked = tools.playbook_hits({"customer_id": "PENSION-STARTED", "question": "뭐라고 말하지"})
        allowed_sits = {s["id"] for s in problem_situations(started, SC.conditions(started))}
        sits2 = problem_situations(started, SC.conditions(started))
        pool2 = ({c["id"] for t in ("proposal", "objection", "guide")
                  for _s, c, _seg in M.scored_situation_cards(sits2, t, 50)}
                 | {c["id"] for _s, c, _seg in M.scored_situation_procedures(sits2, 50)}
                 | {c["id"] for _s, c, _seg in M.scored_situation_methods(sits2, 50)})
        hit = all(c["id"] in pool2 for _s, c in blocked) and "seg.13" not in allowed_sits
    finally:
        SC.get_profile = orig
    print(f"{'✓' if hit else '✗'} 그 차단이 대화형 후보에도 그대로 상속된다(따로 막지 않는다)")
    ok += hit
    return ok


def check_material_marks() -> int:
    """재료 성격 표시 — 신뢰 등급 · 내부용/고객 안내 가능 표시."""
    from pension_agent.evidence import marks as M
    from pension_agent.nodes import plan as P
    from pension_agent.tools.kb_search import facts_qa, procedure_qa
    from pension_agent.state import KB

    ok = 0

    # 등급은 문서 레지스트리에서 — 카드 내용으로 추론 금지
    field = next(c for c in KB.cards if c["_kind"] == "fieldtip")
    hit = M.tier_of(KB, field) == M.TIER_NOTE["현장팁"]
    print(f"{'✓' if hit else '✗'} 신뢰 등급을 문서 레지스트리의 tier 에서 그대로 옮긴다")
    ok += hit

    hit = M.tier_of(KB, {"_source": {"doc": "없는문서"}}) is None and M.notes_for(KB, []) == []
    print(f"{'✓' if hit else '✗'} 문서를 못 찾으면 등급을 지어내지 않는다")
    ok += hit

    # 내부용 주의는 선언이 거짓일 때만 — 선언 없음(pitch·method·segment)은 미계산
    internal = {"_source": {"doc": "없는문서"}, "customer_facing": False}
    facing = {"_source": {"doc": "없는문서"}, "customer_facing": True}
    undeclared = {"_source": {"doc": "없는문서"}}
    hit = (M.notes_for(KB, [internal]) == [M.INTERNAL_NOTE]
           and M.notes_for(KB, [facing]) == []
           and M.notes_for(KB, [undeclared]) == [])
    print(f"{'✓' if hit else '✗'} 내부용 주의는 customer_facing 선언이 거짓일 때만 붙는다")
    ok += hit

    # ③ 재료에도 같은 선언 표시 — 참·거짓 둘 다(작성 프롬프트 «내부용» 규칙이 가리킬 대상)
    hit = (M.facing_note(internal) == M.FACING_NOTE[False]
           and M.facing_note(facing) == M.FACING_NOTE[True]
           and M.facing_note(undeclared) is None)
    print(f"{'✓' if hit else '✗'} 재료 표시는 참·거짓을 둘 다 싣고 선언 없음은 비운다")
    ok += hit

    # 실제 재료 블록에 실리는지 — 두 종류 모두
    internal_fact = next(f for f in KB.facts.values() if f.get("customer_facing") is False)
    hit = M.FACING_NOTE[False] in "\n".join(facts_qa._render(internal_fact))
    print(f"{'✓' if hit else '✗'} 내부용 팩트의 재료 블록에 내부용 표시가 실린다")
    ok += hit

    def proc(card):
        return "\n".join(procedure_qa._render({**card, "title": "t"}))

    hit = (M.FACING_NOTE[False] in proc(internal)
           and M.FACING_NOTE[True] in proc(facing)
           and not any(n in proc(undeclared) for n in M.FACING_NOTE.values()))
    print(f"{'✓' if hit else '✗'} 절차 재료 블록도 같은 표시를 쓴다")
    ok += hit

    # 작성 프롬프트가 그 표시를 가리키는지 — 문구가 갈리면 규칙이 헛돎
    from pension_agent import prompts as PR
    hit = "내부용" in PR.COMPOSE_SYSTEM and "고객에게 할 말로 옮기지" in PR.COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 작성 규칙이 내부용 재료를 고객 안내로 옮기지 말라고 못 박는다")
    ok += hit

    # 같은 등급 여러 장이어도 표시 한 번
    tips = [c for c in KB.cards if c["_kind"] == "fieldtip"][:3]
    hit = M.notes_for(KB, tips) == [M.TIER_NOTE["현장팁"]]
    print(f"{'✓' if hit else '✗'} 같은 표시를 겹쳐 세우지 않는다")
    ok += hit

    # 표시가 실제 답변에 부착되는지 — 등급 종류 무관
    def ev(marks):
        return {"tool": "fact", "query": "q", "text": "근거 블록", "atomic": [], "notices": [],
                "notice_scopes": [], "allow": ["근거 블록"], "marks": marks,
                "sources": [], "meta": {}}

    orig_gen = P.generate
    P.generate = lambda prompt, **kw: "이렇게 안내하시면 돼요."
    try:
        out = P.compose({"question": "q",
                         "evidence": [ev([M.TIER_NOTE["본부공식"], M.INTERNAL_NOTE])]})
        hit = M.TIER_NOTE["본부공식"] in out["answer"] and M.INTERNAL_NOTE in out["answer"]
        print(f"{'✓' if hit else '✗'} 본부 공식·내부용 표시가 일반 답변에도 붙는다")
        ok += hit

        # 답변이 이미 같은 말을 했으면 중복 부착 금지
        P.generate = lambda prompt, **kw: f"이건 {M.TIER_NOTE['본부공식']} 근거예요."
        out = P.compose({"question": "q", "evidence": [ev([M.TIER_NOTE["본부공식"]])]})
        hit = out["answer"].count(M.TIER_NOTE["본부공식"]) == 1
        print(f"{'✓' if hit else '✗'} 답변이 이미 밝힌 표시를 겹쳐 세우지 않는다")
        ok += hit

        # 표시가 없으면 빈 머리말 미부착
        out = P.compose({"question": "q", "evidence": [ev([])]})
        hit = P.MATERIAL_MARKS not in out["answer"]
        print(f"{'✓' if hit else '✗'} 붙일 표시가 없으면 머리말도 붙이지 않는다")
        ok += hit

        # 끝이 깎인 표시도 겹침으로 판정 — 기준시점 꼬리는 as_of 라 앞의 경고 문장으로 대조(plan._already_said)
        warn = ("※ 시황·상품 정보는 시장 변동과 상품 개편에 따라 빠르게 달라집니다. "
                "인용 전 as_of 기준시점을 확인하고, 시효가 지난 수치는 갱신하세요.")
        # 꼬리에 숫자 금지 — 허용 집합 밖 수치면 검증이 폐기해 폴백을 재게 됨
        mark = f"{warn} — 기준시점 표기입니다."
        P.generate = lambda prompt, **kw: f"{warn} — 기준시점 표기"   # 끝이 깎임
        out = P.compose({"question": "q", "evidence": [ev([mark])]})
        hit = P.MATERIAL_MARKS not in out["answer"]
        print(f"{'✓' if hit else '✗'} 표시 끝을 흘려 옮겨도 겹침으로 잡는다(기준시점 꼬리 제외)")
        ok += hit

        # 반대 — 본문에 없는 표시는 그대로 부착
        P.generate = lambda prompt, **kw: "전혀 다른 답변이에요."
        out = P.compose({"question": "q", "evidence": [ev([mark])]})
        hit = mark in out["answer"]
        print(f"{'✓' if hit else '✗'} 본문에 없는 표시는 빠뜨리지 않는다")
        ok += hit
    finally:
        P.generate = orig_gen

    # 도구가 표시를 실어 보내는지(배선)
    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        q = "사전 고지를 안 하면 민원으로 돌아온다는데 현장에서는 어떻게 하나요?"
        found = tools.run("fieldtip", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(found) and M.TIER_NOTE["현장팁"] in (found.get("marks") or [])
    print(f"{'✓' if hit else '✗'} 도구가 근거와 함께 재료 성격 표시를 돌려준다")
    ok += hit

    return ok


def check_relations() -> int:
    """관계 기반 점검 — 값–조건 오짝·알려진 오답 검출, 옳은 문장 무거부를 함께 측정."""
    from pension_agent.evidence import relations as R
    from pension_agent.nodes import plan as P
    from pension_agent.tools.kb_search import facts_qa
    from pension_agent.state import KB

    ok = 0
    f2 = KB.facts.get("fact.k04.f2") or {}

    hit = len(f2.get("tiers") or []) >= 2
    print(f"{'✓' if hit else '✗'} 세액공제 팩트가 조건–값 쌍을 선언한다 ({len(f2.get('tiers') or [])}쌍)")
    ok += hit

    hit = bool(R.mispaired("총급여 5,500만원 초과면 16.5% 를 공제받아요.", f2.get("tiers") or []))
    print(f"{'✓' if hit else '✗'} 조건과 값을 뒤집은 답변을 잡는다(수치 집합 검사는 못 잡던 것)")
    ok += hit

    hit = not R.mispaired("총급여 5,500만원 이하면 16.5%, 초과면 13.2% 예요.",
                          f2.get("tiers") or [])
    print(f"{'✓' if hit else '✗'} 옳게 짝지은 답변은 막지 않는다")
    ok += hit

    # 조건을 풀어 쓴 옳은 답 — 판정 불가이지 위반 아님
    hit = not R.mispaired("총급여가 5,500만원을 넘으면 13.2% 가 적용돼요.", f2.get("tiers") or [])
    print(f"{'✓' if hit else '✗'} 풀어 쓴 옳은 답을 위반으로 바꾸지 않는다(판정 불가 ≠ 위반)")
    ok += hit

    hit = R.known_wrong("5,500만원 이상 13.2% 로 안내하시면 돼요.", f2.get("pitfalls") or []) != []
    print(f"{'✓' if hit else '✗'} 행원들이 적어둔 알려진 오답을 그대로 말하면 잡는다")
    ok += hit

    # 오답 문구를 «틀렸다»고 짚는 정정은 허용 — 카드 verify_points 가 시킨 문구
    pf = f2.get("pitfalls") or []
    hit = (not R.known_wrong('"5,500만원 이상 13.2%"는 오기예요. "초과"가 맞습니다.', pf)
           and not R.known_wrong("「5,500만원 이상 13.2%」는 틀린 표기니 주의하세요.", pf))
    print(f"{'✓' if hit else '✗'} 오답 문구를 «틀렸다»고 짚는 정정은 막지 않는다")
    ok += hit

    # 정정 표지 목록은 카드가 쓰는 낱말(「오답」 등)을 포함해야 함
    f47 = KB.facts.get("fact.k04.f47")
    pf47 = (f47 or {}).get("pitfalls") or []
    hit = bool(pf47) and not R.known_wrong(
        '"이미 통장으로 받았으면 끝"이라고 생각하기 쉽지만 오답이에요. 60일 이내면 됩니다.', pf47)
    print(f"{'✓' if hit else '✗'} 카드가 「오답」이라 부르는 문구를 그 말로 짚는 정정도 막지 않는다")
    ok += hit

    # 따옴표 없이 그대로 주장하면 여전히 검출
    hit = R.known_wrong("이미 통장으로 받았으면 끝이니 어쩔 수 없다고 안내하세요.", pf47) != []
    print(f"{'✓' if hit else '✗'} 표지가 늘어도 그대로 주장한 오답은 잡는다")
    ok += hit

    # 정정 판정은 두 조건 모두 필요 — 하나만으로는 헐거움
    hit = R.known_wrong("오기 주의하시고, 5,500만원 이상 13.2% 로 안내하세요.", pf) != []
    print(f"{'✓' if hit else '✗'} 정정 표지만 곁에 있고 문구는 주장했으면 잡는다")
    ok += hit

    hit = R.known_wrong('고객님께 "5,500만원 이상 13.2%" 라고 안내드릴게요.', pf) != []
    print(f"{'✓' if hit else '✗'} 따옴표만 있고 정정 표지가 없으면 잡는다(고객 대사도 따옴표에 담긴다)")
    ok += hit

    hit = R.known_wrong('"5,500만원 이상 13.2%"는 오기예요. '
                        "그래도 5,500만원 이상 13.2% 로 하세요.", pf) != []
    print(f"{'✓' if hit else '✗'} 한쪽에서 정정하고 다른 쪽에서 그대로 말하면 잡는다")
    ok += hit

    # 문구 바로 뒤의 부정은 반대 주장 — 따옴표 없어도 정정
    pf4 = (KB.facts.get("fact.k04.f4") or {}).get("pitfalls") or []
    hit = (bool(pf4)
           and not R.known_wrong("전환금 전액이 공제 대상이 되는 것이 아니에요. 10%만 더해져요.", pf4)
           and not R.known_wrong("전환금 전액이 공제 대상이 아니라 10%예요.", pf4)
           and R.known_wrong("전환금 전액이 공제 대상이에요.", pf4) != []
           and R.known_wrong("전환금 전액이 공제 대상이에요. 아니, 확인해 볼게요.", pf4) != [])
    print(f"{'✓' if hit else '✗'} 오답 문구 바로 뒤의 부정은 정정이고, 문장 부호 너머의 부정은 아니다")
    ok += hit

    # 도구 재료 본문에 자기 카드의 오답 문구 금지 — 재료에 있으면 답변이 그대로 씀
    from pension_agent.tools.customer_solution import customer as CUST
    isa_cust = next((p for p in CUST.PERSONAS if p.isa), None)
    ev_tax = tools.run("tax_credit", {"customer_id": isa_cust.id, "question": "ISA 8천만원 다 옮기면?"},
                       "ISA") if isa_cust else None
    wrongs = [w for c in (ev_tax or {}).get("related") or [] for x in c.get("pitfalls") or []
              for w in x.get("wrong") or []]
    hit = bool(ev_tax) and bool(wrongs) and not any(w in ev_tax["text"] for w in wrongs)
    print(f"{'✓' if hit else '✗'} 세액공제 재료 본문에 자기 카드의 오답 문구가 없다")
    ok += hit
    hit = bool(ev_tax) and "10%는" in ev_tax["text"] and "상한에서 잘려" in ev_tax["text"]
    print(f"{'✓' if hit else '✗'} 상한에 잘린 전환액은 10% 원값도 재료에 있다(수치 검사 대비)")
    ok += hit

    # 오답 문자열은 구절이어야 함 — 값 하나짜리는 다른 팩트의 맞는 문장에도 등장
    bare = [w for f in KB.facts.values() for x in f.get("pitfalls") or []
            for w in x.get("wrong") or [] if " " not in w and len(w) < 8]
    hit = not bare
    print(f"{'✓' if hit else '✗'} 값 하나짜리 오답 문자열은 대조에 쓰지 않는다"
          + ("" if hit else f" — {bare[:3]}"))
    ok += hit

    # 옳은 표현의 인용이 오답 목록에 섞이지 않았는지
    every = [w for f in KB.facts.values() for x in f.get("pitfalls") or []
             for w in x.get("wrong") or []]
    hit = "평가금액" not in every and "퇴직금 포함 시 5년 전 가능" not in every
    print(f"{'✓' if hit else '✗'} 옳은 표현의 인용을 오답으로 등록하지 않는다")
    ok += hit

    # 관계 선언 팩트는 원문 강제 해제, 미선언 팩트는 같은 원장에서도 강제 유지 — 두 종류를 한 원장에 고정 적재
    by_id = {f["id"]: f for f in KB.facts.values()}
    with_rel = next(f for f in by_id.values() if R.declared(f) and f.get("value"))
    without_rel = next(f for f in by_id.values() if not R.declared(f) and f.get("value"))
    orig_fits, orig_search = tools.fits_question, facts_qa.search
    tools.fits_question = lambda question, h, kind="", history=None, query=None, sink=None: h
    facts_qa.search = lambda question: [(2.0, with_rel), (2.0, without_rel)]
    try:
        found = tools.run("fact", {"question": "q"}, "세액공제 공제율")
    finally:
        tools.fits_question, facts_qa.search = orig_fits, orig_search
    atomic = (found or {}).get("atomic") or []

    hit = with_rel["value"] not in atomic
    print(f"{'✓' if hit else '✗'} 관계를 선언한 팩트는 원문을 통째로 강제하지 않는다")
    ok += hit

    hit = without_rel["value"] in atomic
    print(f"{'✓' if hit else '✗'} 선언이 없는 팩트는 같은 원장에서도 원문 강제가 남는다")
    ok += hit

    # 선언 없는 카드는 원문 강제 유지
    hit = not R.declared({"tiers": [], "pitfalls": [{"wrong": [], "why": "메모"}]}) \
        and R.declared({"tiers": [{"when": "a", "value": "b"}]})
    print(f"{'✓' if hit else '✗'} 선언이 없으면 관계 검사가 원문 강제를 대신하지 않는다")
    ok += hit

    # compose 가 관계 위반을 걸러내는지(배선)
    ev = {"tool": "fact", "query": "q", "text": (f2.get("value") or "")[:200],
          "atomic": [], "notices": [], "notice_scopes": [],
          "allow": [f2.get("value") or ""], "marks": [], "related": [f2],
          "sources": [], "meta": {}}
    orig_gen = P.generate
    P.generate = lambda prompt, **kw: "총급여 5,500만원 초과면 16.5% 예요."
    try:
        out = P.compose({"question": "세액공제율이 얼마야?", "evidence": [ev]})
        hit = "총급여 5,500만원 초과면 16.5%" not in out["answer"]
    finally:
        P.generate = orig_gen
    print(f"{'✓' if hit else '✗'} compose 가 관계를 어긴 생성문을 내보내지 않는다")
    ok += hit
    return ok + check_percent_unit()


def check_percent_unit() -> int:
    """표 백분율 열 — 헤더에만 단위가 있는 값에 % 를 붙여도 통과, 오짝 검사는 유지."""
    from pension_agent.evidence import relations as R
    from pension_agent.nodes import plan as P
    from pension_agent.tools.kb_search import market as M
    from pension_agent.state import KB

    ok = 0
    card = next((c for c in KB.cards if c["id"] == "lnp.퇴직연금_추천펀드_2026-08.01"), None)
    if not card:
        print("✗ 디폴트옵션 표 카드를 찾지 못했다 — 05 상품 자료가 적재되지 않았다")
        return ok

    # ① 데이터가 백분율 열을 선언(scripts/kb_build/config.PERCENT_COLUMNS)
    table = (card.get("tables") or [{}])[0]
    units = set(table.get("units") or {})
    hit = {"비중", "금리"} <= units and "설정일" not in units
    print(f"{'✓' if hit else '✗'} 표가 백분율 열을 선언한다 — 비중·금리는 들어가고 설정일은 빠진다"
          f" ({' · '.join(sorted(units))})")
    ok += hit

    ev = M.market_evidence("lineup", "디폴트옵션 구성", [(1.0, card)])
    known = P._known_products()

    # ② 단위를 붙인 맞는 답변 통과
    faults, _ = P._screen("초저위험 지켜드림은 신한은행 정기예금 비중 35% 로 담고 "
                          "금리는 3.40% 예요.", [ev], "디폴트옵션 구성", known)
    print(f"{'✓' if not faults else '✗'} 표의 값에 % 를 붙여 말한 맞는 답변이 통과한다 {faults[:1]}")
    ok += not faults

    # 반대 방향도 통과 — 표마다 셀 표기가 다름
    faults, _ = P._screen("초저위험 지켜드림의 금리는 3.40 이에요.", [ev], "디폴트옵션 구성", known)
    print(f"{'✓' if not faults else '✗'} % 없이 말한 답변도 그대로 통과한다 {faults[:1]}")
    ok += not faults

    # ③ 반올림·지어낸 수치는 여전히 폐기
    faults, _ = P._screen("지켜드림의 1년 수익률은 약 2.1% 수준이에요.",
                          [ev], "디폴트옵션 구성", known)
    print(f"{'✓' if faults else '✗'} 반올림한 수치는 여전히 폐기된다(원장에 2.13 만 있다)")
    ok += bool(faults)

    faults, _ = P._screen("지켜드림의 금리는 9.99% 예요.", [ev], "디폴트옵션 구성", known)
    print(f"{'✓' if faults else '✗'} 지어낸 수치는 여전히 폐기된다")
    ok += bool(faults)

    # ④ % 표기로 오짝 검사가 꺼지지 않음. 3.27 은 알파드림(수협은행) 행의 금리
    tables = card.get("tables") or []
    bare = R.table_mispaired("초저위험 지켜드림의 금리는 3.27 이에요.", tables)
    suffixed = R.table_mispaired("초저위험 지켜드림의 금리는 3.27% 예요.", tables)
    hit = bool(bare) and bool(suffixed)
    print(f"{'✓' if hit else '✗'} 남의 행 값을 갖다 붙인 답변은 % 를 붙여도 잡힌다")
    ok += hit

    hit = not R.table_mispaired("초저위험 지켜드림의 금리는 3.40% · 3.25% · 3.32% 예요.", tables)
    print(f"{'✓' if hit else '✗'} 제 행 값을 % 붙여 말한 답변은 막지 않는다")
    ok += hit
    return ok


def check_caution_roles() -> int:
    """주의·비고 역할 선언 — 저작 메모(authoring)의 직원 답변 유출 차단."""
    from pension_agent.tools.customer_solution import guard as GD
    from pension_agent.knowledge.kb import ROLE_FIELDS, role_texts
    from pension_agent.tools.kb_search import procedure_qa, segment_qa
    from pension_agent.state import KB

    ok = 0
    with_field = [c for c in KB.cards
                  if ROLE_FIELDS.get(c["_kind"]) and c.get(ROLE_FIELDS[c["_kind"]])]

    # ① 역할 선언 전부 기재 — 미선언 항목은 어느 역할로도 안 잡혀 조용히 누락
    undeclared = [c["id"] for c in with_field
                  for e in c[ROLE_FIELDS[c["_kind"]]]
                  if not isinstance(e, dict)
                  or e.get("role") not in ("caution", "info", "authoring")]
    hit = bool(with_field) and not undeclared
    print(f"{'✓' if hit else '✗'} 주의·비고 항목 전부에 역할이 선언돼 있다"
          f"({len(with_field)}카드)" + ("" if hit else f" — 누락 {undeclared[:3]}"))
    ok += hit

    # ② authoring 텍스트가 직원용 렌더에 미포함 — 종류별 렌더 전부
    render = {"screen": tools._render_screen, "channel": tools._render_channel,
              "method": tools._render_method,
              "procedure": lambda c: "\n".join(procedure_qa._render(c)),
              "segment": lambda c: "\n".join(segment_qa._render(c, None, None))}
    leaked = []
    for c in with_field:
        memos = role_texts(c.get(ROLE_FIELDS[c["_kind"]]), "authoring")
        if memos and any(m in render[c["_kind"]](c) for m in memos):
            leaked.append(c["id"])
    hit = not leaked
    print(f"{'✓' if hit else '✗'} 저작 메모가 답변 재료 렌더에 실리지 않는다"
          + ("" if hit else f" — 유출 {leaked[:3]}"))
    ok += hit

    # ③ 절차 표시(notices)에도 미포함 — proc.001 의 ⚠ 유의는 «필자 해석» 메모
    from pension_agent.tools.kb_search import procedure_qa as PQ
    by_id = {c["id"]: c for c in KB.cards}
    orig_search, orig_fits = PQ.search, tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        PQ.search = lambda q: [(2.0, by_id["proc.001"])]
        found = tools.run("procedure", {"question": "q"}, "적립금 조회 절차")
    finally:
        PQ.search, tools.fits_question = orig_search, orig_fits
    hit = bool(found) and not any("필자" in n for n in found["notices"])
    print(f"{'✓' if hit else '✗'} 절차의 저작 메모가 표시(notices)로 강제되지 않는다")
    ok += hit

    # ④ caution 은 표시로 강제
    orig_pick, orig_fits = tools.pick, tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        tools.pick = lambda kinds, q, **kw: [(2.0, by_id["screen.06-10-182"])]
        found = tools.run("screen", {"question": "q"}, "연금납입정보 조회 화면")
    finally:
        tools.pick, tools.fits_question = orig_pick, orig_fits
    hit = bool(found) and any("징구 필수" in n for n in found["notices"])
    print(f"{'✓' if hit else '✗'} caution 역할의 주의는 표시로 강제된다")
    ok += hit

    # ⑤ 가드는 method 의 caution 역할만 사용, 문자열 휴리스틱 없음
    gkb_cards = [c for c in KB.cards if c["_kind"] == "method"]
    m010 = next(c for c in gkb_cards if c["id"] == "m.010")
    hit = (role_texts(m010.get("cautions"), "authoring")
           and not GD._texts(m010)
           # 정의 부재만 검사 — 주석 언급은 허용
           and "_AUTHORING =" not in pathlib.Path(GD.__file__).read_text(encoding="utf-8"))
    hit = bool(hit)
    print(f"{'✓' if hit else '✗'} 가드가 역할 선언만 본다(_AUTHORING 휴리스틱 삭제)")
    ok += hit

    # ⑥ 직원에게 나가는 문구(caution·info)에 코드 이름·룰베이스 번호 금지
    import re as _re
    _code = _re.compile(r"[a-z_]{3,}\.[a-z_]{3,}"          # customer.conditions
                        r"|\bTG-\d+\b"                     # 룰베이스 번호
                        r"|(?<![0-9A-Za-z_])(dep|mat|nod|dor|idl|out|hlt|nch|sec)"
                        r"(?![0-9A-Za-z_가-힣])")            # 요건 코드
    jargon = [(c["id"], e["text"][:40]) for c in with_field
              for e in c[ROLE_FIELDS[c["_kind"]]]
              if isinstance(e, dict) and e.get("role") in ("caution", "info")
              and _code.search(e.get("text") or "")]
    hit = not jargon
    print(f"{'✓' if hit else '✗'} 직원에게 나가는 주의·비고에 코드 이름이 없다"
          + ("" if hit else f" — {jargon[:3]}"))
    ok += hit

    # ⑦ 형제 표와 행 이름을 공유하는 표는 오짝 판정 생략(판정 불가)
    from pension_agent.evidence import relations as _REL
    _port = next(c for c in KB.cards if c["id"] == "lnp.퇴직연금펀드_포트폴리오_2026-08.02")
    _ok_answer = ("위험중립형은 국내채권 60% · 해외채권 15% · 해외혼합 25% 로 짜여 있어요. "
                  "하나 파워e단기채 60%, 우리미국 단기채공모주(H) 15%, 삼성EMP 리얼리턴(UH) 25% 예요.")
    hit = not _REL.table_mispaired(_ok_answer, _port["tables"])
    print(f"{'✓' if hit else '✗'} 형제 표와 행 이름이 겹치면 오짝 판정을 하지 않는다")
    ok += hit

    # 표가 한 장이면 판정 유지
    _one = next((c for c in KB.cards if len(c.get("tables") or []) == 1
                 and len((c["tables"][0].get("rows") or [])) >= 2), None)
    hit = _one is not None and not (_REL._row_names(_one["tables"][0])
                                    & _REL._ambiguous_names(_one["tables"]))
    print(f"{'✓' if hit else '✗'} 표가 한 장인 카드는 건너뛰지 않는다")
    ok += hit
    return ok


def check_account_state() -> int:
    """계좌 상태 재료 — «정상» 항목도 답하도록 로스터 전원에 상태 값 적재(부담금별 구성 포함)."""
    ok = 0
    from pension_agent.tools.customer_solution import customer as CUST

    STATES = ("디폴트옵션", "연금개시", "연금개시요건", "세액공제 잔여한도",
              "판매중단 보유상품", "ISA 만기자금", "IRP 가입일", "부담금별 구성")
    texts = {p.id: ((tools.TOOLS["customer"].run({"customer_id": p.id}, "확인") or {}).get("text", ""))
             for p in CUST.PERSONAS}
    for key in STATES:
        missing = [pid for pid, t in texts.items() if key not in t]
        hit = not missing
        print(f"{'✓' if hit else '✗'} 계좌 상태 «{key}» 가 {len(texts)}명 전원 재료에 있다"
              + ("" if hit else f" — 빠진 고객 {len(missing)}명"))
        ok += hit

    # 부담금 구성은 원장 두 컬럼 값 — 인용 답변이 통과해야 되묻기가 줄어듦
    split = next((p for p in CUST.PERSONAS if p.severance_amt and p.own_contrib_amt), None)
    only_one = next((p for p in CUST.PERSONAS if not p.severance_amt), None)
    hit = split is not None and only_one is not None
    print(f"{'✓' if hit else '✗'} 두 부담금이 섞인 고객과 한쪽뿐인 고객이 로스터에 다 있다")
    ok += hit
    if split is not None:
        ev = tools.TOOLS["customer"].run({"customer_id": split.id}, "수수료 얼마야?")
        from pension_agent.tools.customer_solution.engine.text import won  # noqa: PLC0415
        said = (f"이 고객 적립금은 사용자부담금(퇴직급여) {won(split.severance_amt)}, "
                f"가입자부담금(개인부담금) {won(split.own_contrib_amt)}이에요.")
        hit = _vt(said, (ev or {}).get("allow") or [])[0]
        print(f"{'✓' if hit else '✗'} 두 부담금 금액을 그대로 인용한 답변이 통과한다")
        ok += hit
        # 원장에 없는 금액은 차단
        wrong = f"사용자부담금은 {won(split.severance_amt + 7_777_000)}이에요."
        hit = not _vt(wrong, (ev or {}).get("allow") or [])[0]
        print(f"{'✓' if hit else '✗'} 원장에 없는 부담금 금액은 막힌다")
        ok += hit

    # «정상» 값도 재료에 — 설정된 고객 확인
    setted = [p for p in CUST.PERSONAS if p.dopt == "설정"]
    hit = bool(setted) and all("디폴트옵션 설정" in texts[p.id] for p in setted)
    print(f"{'✓' if hit else '✗'} 디폴트옵션이 «설정»된 고객도 그 사실을 재료로 갖는다 ({len(setted)}명)")
    ok += hit

    # 없는 것도 «없음»으로 — 침묵과 부재는 다름
    clean = [p for p in CUST.PERSONAS if not any(h.get("discontinued") for h in p.holdings)]
    hit = bool(clean) and all("판매중단 보유상품 없음" in texts[p.id] for p in clean)
    print(f"{'✓' if hit else '✗'} 판매중단 상품이 없는 고객은 «없음»을 재료로 갖는다 ({len(clean)}명)")
    ok += hit

    # 계좌 상태는 화면(briefing)이 아닌 별도 키
    from pension_agent.tools.customer_solution import solution as SOL
    facts = SOL.facts(CUST.PERSONAS[0])
    hit = ("account_state" in facts
           and not (set(facts["account_state"]) & set(facts["briefing"]))
           and not (set(facts["account_state"]) & set(facts["customer"])))
    print(f"{'✓' if hit else '✗'} 계좌 상태는 화면(briefing·상단)이 아니라 대화형 재료다")
    ok += hit

    # 가입일은 날짜로 — 경과연수만 주면 LLM 이 날짜를 계산해 만듦
    p0 = CUST.PERSONAS[0]
    ev = tools.TOOLS["customer"].run({"customer_id": p0.id}, "언제 가입했어?")
    hit = bool(p0.joined) and p0.joined in (ev or {}).get("text", "")
    print(f"{'✓' if hit else '✗'} 가입일이 경과연수가 아니라 날짜로 실린다")
    ok += hit

    # 경과는 「3년」·「2년 10개월」 꼴 — invest_period_years 는 소수 반올림값(2년 11개월 → 3.0년)
    import re as _re
    from pension_agent.tools.customer_solution.engine import render as RENDER
    bad = [p.id for p in CUST.PERSONAS
           if _re.search(r"가입 후 \d+\.\d", str(RENDER._account_state(p).get("IRP_가입일") or ""))]
    hit = not bad
    print(f"{'✓' if hit else '✗'} 가입 후 경과에 소수점 연수를 쓰지 않는다"
          + ("" if hit else f" — {bad[:3]}"))
    ok += hit

    hit = (CUST.tenure_text("2023-10-02") is not None
           and "." not in (CUST.tenure_text("2023-10-02") or "")
           and CUST.tenure_text(None) is None)
    print(f"{'✓' if hit else '✗'} tenure_text 는 「n년」·「n년 m개월」만 만든다 "
          f"({CUST.tenure_text('2023-10-02')})")
    ok += hit

    # 실린 값은 인용 가능, 안 실린 날짜는 차단
    from datetime import date, timedelta
    allow = (ev or {}).get("allow") or []
    real = date.fromisoformat(p0.joined)
    wrong = real + timedelta(days=3)
    hit = (_vt(f"{real.year}년 {real.month}월 {real.day}일에 가입하셨어요.", allow)[0]
           and not _vt(f"{wrong.year}년 {wrong.month}월 {wrong.day}일에 가입하셨어요.", allow)[0])
    print(f"{'✓' if hit else '✗'} 가입일은 인용되고, 하루라도 어긋난 날짜는 잘린다")
    ok += hit
    return ok


def check_today_material() -> int:
    """오늘 날짜 재료 — 도구 상시 사용 · 날짜와 두 가지 잔여일수(하나만이면 하루 오안내) · 검증 통과."""
    ok = 0
    from datetime import date

    import tests
    from pension_agent.tools.customer_solution import customer as CUST

    hit = "date" in tools.usable({}) and "date" in tools.usable({"customer_id": "CX"})
    print(f"{'✓' if hit else '✗'} 오늘 도구는 고객 화면이 닫혀 있어도 쓸 수 있다")
    ok += hit

    pinned = date.fromisoformat(tests.PINNED_TODAY)
    left = CUST.days_to_year_end(pinned)
    ev = tools.TOOLS["date"].run({}, "연말까지 얼마 남았어?")
    text = (ev or {}).get("text", "")

    hit = f"{pinned.year}년 {pinned.month}월 {pinned.day}일" in text
    print(f"{'✓' if hit else '✗'} 재료가 오늘 날짜를 그대로 밝힌다")
    ok += hit

    hit = str(left) in text and str(left + 1) in text and "오늘을 세지 않은" in text
    print(f"{'✓' if hit else '✗'} 연말 잔여일수를 두 가지 세는 법으로 함께 싣는다"
          + ("" if hit else f" — {text!r}"))
    ok += hit

    # 원장 기준일은 고객 화면이 열려 있을 때만. 테스트는 오늘=AS_OF 고정이라 값이 아닌 줄로 판정
    opened = tools.TOOLS["date"].run({"customer_id": "198734-1205842"}, "오늘 며칠이야")
    label = "고객 계좌 원장 기준일"
    hit = (label in (opened or {}).get("text", "") and label not in text
           and CUST.AS_OF.isoformat() in (opened or {}).get("text", ""))
    print(f"{'✓' if hit else '✗'} 원장 스냅샷 기준일은 고객 화면이 열렸을 때만 함께 싣는다")
    ok += hit

    # ③ 답변이 그 수치를 써도 검증 통과
    allow = (ev or {}).get("allow") or []
    hit = verify_texts(f"올해가 {left}일 남았으니 연내 납입해야 세액공제를 받으세요.", allow)[0]
    print(f"{'✓' if hit else '✗'} 답변이 그 잔여일수를 써도 검증기가 자르지 않는다")
    ok += hit

    # 재료 밖 날짜 수치는 여전히 차단
    hit = not verify_texts(f"올해가 {left + 40}일 남았어요.", allow)[0]
    print(f"{'✓' if hit else '✗'} 재료 밖 잔여일수는 그대로 잘린다(경계는 넓어지지 않았다)")
    ok += hit
    return ok


def check_history_material() -> int:
    """상담 이력 재료 — history 도구 · 시효 표시 · 기록 0건 · 능력 안내 · 가드와 근거 분리."""
    import tempfile
    from pathlib import Path

    from pension_agent.common import session_store
    from pension_agent import prompts
    from pension_agent.nodes import clarify as CL
    from pension_agent.nodes import meta
    from pension_agent.nodes import plan as P
    from pension_agent.state import HISTORY_LIMIT

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = session_store.SESSION_DATA_DIR
        session_store.SESSION_DATA_DIR = Path(tmp)
        try:
            # 과거 상담 1건 + 대화 1세션 — 시효 표시 검사에 record 필요
            session_store.append_turn("CX", "past-2026-07-01", {
                "role": "record", "text": "타사 수수료 비교 문의", "ts": "2026-07-01T09:00:00Z"})
            session_store.append_turn("CX", "s1", {
                "role": "user", "text": "수수료 부담된다고 하시네요", "ts": "2026-08-01T09:00:00Z"})
            session_store.append_turn("CX", "s1", {
                "role": "agent", "text": "수수료는 " + "가" * 400, "ts": "2026-08-01T09:00:05Z"})
            state = {"question": "지난번에 고객 상담에서 무슨 얘기 했지?", "customer_id": "CX"}
            found = tools.run("history", state, "지난 상담 내용")
            closed = tools.run("history", {"question": "지난번에 무슨 얘기 했지?"}, "지난 상담")
            unseen = tools.run("history", {"question": "q", "customer_id": "C_없음"}, "지난 상담")
        finally:
            session_store.SESSION_DATA_DIR = orig_dir

    hit = bool(found) and "수수료 부담된다고 하시네요" in found["text"] \
        and found["sources"][0]["id"] == "session"
    print(f"{'✓' if hit else '✗'} 지난 상담 기록이 재료로 올라온다")
    ok += hit

    # 지난 답변은 발췌만 — 원장이 지난 문장으로 뒤덮이지 않게
    longest = max(len(x) for x in found["text"].splitlines()) if found else 0
    hit = bool(found) and longest <= tools.HISTORY_EXCERPT + 20 and "…" in found["text"]
    print(f"{'✓' if hit else '✗'} 에이전트 답변은 발췌만 싣는다(가장 긴 줄 {longest}자)")
    ok += hit

    # 기록 시효 표시는 marks — notices 면 <필수 인용>으로 본문 아무 자리에 섬
    hit = (bool(found) and tools.HISTORY_MARK in found["marks"]
           and tools.HISTORY_MARK not in found["notices"])
    print(f"{'✓' if hit else '✗'} 시효 표시를 재료가 성격 표시로 달고 나온다(본문 필수 인용이 아니다)")
    ok += hit

    # 고객 화면이 닫혀 있으면 None(확인 못 함) — 계획이 다른 도구 시도 가능
    hit = closed is None
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혔으면 지어내지 않는다")
    ok += hit

    # 기록 0건은 확인한 값이라 재료 — None 이면 «질의 빗나감»과 구별 불가. 시효 표시 없음
    hit = (bool(unseen) and tools.HISTORY_NONE in unseen["text"]
           and tools.HISTORY_MARK not in unseen["marks"])
    print(f"{'✓' if hit else '✗'} 기록 0건도 재료로 올라온다(없다고 답할 근거)")
    ok += hit

    hit = "history" not in tools.catalog({}) and "history" in tools.catalog({"customer_id": "CX"})
    print(f"{'✓' if hit else '✗'} 못 쓰는 도구는 계획에 보여주지 않는다")
    ok += hit

    hit = "history" in CL._NO_BRANCH and "history" in prompts.ANSWER_SHAPES
    print(f"{'✓' if hit else '✗'} 상담 기록에는 갈래가 없고, 답의 형태 요구는 등록돼 있다")
    ok += hit

    # ② 라우팅 기준·작성 기준 문구
    hit = "지난 상담에서 무슨 얘기를 했는지 묻는 것도" in prompts.ROUTE_PROMPT
    print(f"{'✓' if hit else '✗'} 지난 상담을 묻는 질문이 agent_help 로 새지 않는다")
    ok += hit

    hit = "이전 대화는 이번 질문을 해석하는 데만 쓴다" in prompts.COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 앞 턴의 미답을 이번 답변에서 사과하지 않는다")
    ok += hit

    # 능력 안내 재료는 코드(쓸 수 있는 도구만), 문장은 LLM 이 질문에 맞춰 작성
    opened = meta.help_material({"question": "뭘 도와줄 수 있어?", "customer_id": "CX"})
    shut = meta.help_material({"question": "뭘 도와줄 수 있어?"})
    hit = ("지난 상담" in opened and "지난 상담 기록" in opened
           and "지난 상담에서 무슨 얘기를 했는지도 볼 수 있어요" not in shut
           and "고객 화면이 열려 있지 않아서" in shut
           and "단말 화면번호" in opened and "문자(LMS)를 직접 보내지 않아요" in opened
           and f"최근 {HISTORY_LIMIT}턴" in opened)
    print(f"{'✓' if hit else '✗'} 능력 안내 재료가 실제 능력과 같다(화면번호·상담 기록·기억 범위·하지 않는 것)")
    ok += hit

    # 도구 설명의 개발 용어는 재료에서 직원 표현으로 치환 — 재료의 말은 답변에 그대로 나옴
    import re as _re
    dev = [w for w in ("(86건)", "(131건)", "게이트", "원장", "도구", "세션", "후보군", "⑥⑦⑧", "**", "LLM")
           if w in opened]
    dev += [n for n in tools.TOOLS if _re.search(rf"(?<![0-9A-Za-z_]){n}(?![0-9A-Za-z_])", opened)]
    hit = not dev
    print(f"{'✓' if hit else '✗'} 능력 안내 재료에 카드 장수·개발 용어가 없다" + (f" — {dev}" if dev else ""))
    ok += hit

    orig_gen = meta.generate
    try:
        meta.generate = lambda p, **kw: "네, 이번 상담의 대화는 최근 12턴까지 함께 보고 답해요."
        out = meta.agent_help({"question": "너 이전 대화 기억해?", "customer_id": "CX"})
        hit = out["answer"].startswith("네, 이번 상담") and out["sources"] == []
        print(f"{'✓' if hit else '✗'} 능력 안내는 LLM 이 질문에 맞춰 쓴 문장으로 나간다")
        ok += hit

        # 재료 밖 수치를 지어내면 재료 그대로 출력
        meta.generate = lambda p, **kw: "최근 40턴까지 기억해요."
        out = meta.agent_help({"question": "너 이전 대화 기억해?"})
        hit = out["answer"] == shut
        print(f"{'✓' if hit else '✗'} 재료 밖 수치를 말하면 재료를 그대로 낸다")
        ok += hit

        from pension_agent.common.llm import LLMError as _LLMError
        def _dead(p, **kw):
            raise _LLMError("LLM 미설정 — 테스트")
        meta.generate = _dead
        out = meta.agent_help({"question": "뭘 도와줄 수 있어?"})
        hit = bool(out.get("llm_error")) and out["answer"].startswith(plan.LLM_FAILED.split("{", 1)[0])
        print(f"{'✓' if hit else '✗'} LLM 이 죽으면 규칙으로 대신 답하지 않고 장애 안내로 끝난다(§11)")
        ok += hit
    finally:
        meta.generate = orig_gen

    # ③ 답의 근거 재료와 표현 제한 재료(가드) 분리
    ev = [{"tool": "screen", "query": "자동이체", "text": "퇴직연금 자동이체 [06-12-619]",
           "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
           "allow": ["퇴직연금 자동이체 [06-12-619]"], "meta": {},
           "sources": [{"id": "screen.06-12-619", "title": "퇴직연금 자동이체",
                        "doc": "화면번호 안내", "score": 2.0, "page": None}]}]
    guards = [{"cond": "low", "text": "지적이 아니라 개선방안 제시로 접근", "card": "m.004", "doc": "d"}]
    alts = [{"card": "pitch.k03.028", "title": "민감 응대", "doc": "d"}]
    srcs = P._sources(ev, guards, alts)
    ground = [s for s in srcs if s["role"] == P.GROUND]
    caution = [s for s in srcs if s["role"] == P.CAUTION]
    hit = [s["id"] for s in ground] == ["screen.06-12-619"] \
        and {s["id"] for s in caution} == {"m.004", "pitch.k03.028"}
    print(f"{'✓' if hit else '✗'} 고객 상태 가드가 답의 '근거'로 서지 않는다(근거 {len(ground)} · 주의 {len(caution)})")
    ok += hit

    # 관련도는 검색 재료에만 — None 이면 화면이 칸 생략
    hit = all(s.get("score") is None for s in caution) and ground[0]["score"] == 2.0
    print(f"{'✓' if hit else '✗'} 검색으로 오지 않은 재료에는 관련도가 없다")
    ok += hit
    return ok


def check_history_selection() -> int:
    """상담 이력 선별 — 질의어 반영(순서만) · 과거 상담/오늘 대화 예산 분리 · 추천 칩(코드 조립)."""
    import tempfile
    from pathlib import Path

    from pension_agent.common import session_store
    from pension_agent.actions import suggest

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = session_store.SESSION_DATA_DIR
        session_store.SESSION_DATA_DIR = Path(tmp)
        try:
            # 과거 상담 2건(최신=ETF, 과거=수수료) + 대화 3세션 — 단일 최신순 창이면 record 가 밀림
            session_store.append_turn("CY", "past-2026-05-01", {
                "role": "record", "text": "수수료 부담 문의로 상품 전환 보류",
                "ts": "2026-05-01T09:00:00Z"})
            session_store.append_turn("CY", "past-2026-07-01", {
                "role": "record", "text": "ETF 거래 편의성 문의", "ts": "2026-07-01T09:00:00Z"})
            for i in range(3):
                session_store.append_turn("CY", f"chat-{i}", {
                    "role": "user", "text": f"오늘 질문 {i}", "ts": f"2026-08-2{i}T09:00:00Z"})
                session_store.append_turn("CY", f"chat-{i}", {
                    "role": "agent", "text": f"오늘 답변 {i}", "ts": f"2026-08-2{i}T09:00:05Z"})

            plain = tools.run("history", {"customer_id": "CY"}, "지난 상담 내용")
            fee = tools.run("history", {"customer_id": "CY"}, "수수료 얘기 했었나")
            chips = suggest.history_chips("CY")
            no_chips = suggest.history_chips("C_없음")

            # 과거 상담 없이 오늘 대화만 있는 고객 — 시효 표시 금지
            session_store.append_turn("CZ", "chat-0", {
                "role": "user", "text": "평가금액 얼마야?", "ts": "2026-08-24T09:00:00Z"})
            session_store.append_turn("CZ", "chat-0", {
                "role": "agent", "text": "1억 2,000만원입니다", "ts": "2026-08-24T09:00:05Z"})
            today_only = tools.run("history", {"customer_id": "CZ"}, "오늘 무슨 얘기 했지")
        finally:
            session_store.SESSION_DATA_DIR = orig_dir

    # ② 대화가 쌓여도 과거 상담 적재 · 구획 분리
    hit = bool(plain) and "수수료 부담 문의" in plain["text"] and "ETF 거래" in plain["text"] \
        and plain["text"].index("[과거 상담 기록]") < plain["text"].index("[에이전트와 나눈 최근 대화]")
    print(f"{'✓' if hit else '✗'} 오늘 대화가 쌓여도 과거 상담이 밀리지 않는다(구획 분리)")
    ok += hit

    # 대화 세션은 최근 1개만
    hit = bool(plain) and plain["text"].count("오늘 질문") == 1
    print(f"{'✓' if hit else '✗'} 에이전트 대화는 최근 {tools.HISTORY_DIALOG_SESSIONS}세션만 싣는다")
    ok += hit

    # ① 질의어가 걸린 상담(수수료·5/1)이 최신(ETF·7/1)보다 앞섬 — 걸러내지는 않음
    hit = bool(fee) and fee["text"].index("수수료 부담") < fee["text"].index("ETF 거래")
    print(f"{'✓' if hit else '✗'} 질의어가 걸린 과거 상담을 앞세운다(query 반영)")
    ok += hit
    hit = bool(fee) and "ETF 거래" in fee["text"]
    print(f"{'✓' if hit else '✗'} 질의어와 다른 기록도 걸러내지 않는다(순서만 바꾼다)")
    ok += hit

    # ③ 칩 — record 있는 고객에게만, 날짜·경과일은 계산값.
    hit = len(chips) == 2 and "7/1" in chips[0] and no_chips == []
    print(f"{'✓' if hit else '✗'} 추천 칩은 기록 있는 고객에게만, 최신 상담 날짜로 뜬다")
    ok += hit

    # 시효 표시는 과거 상담이 실렸을 때만 — 오늘 대화에 붙으면 거짓 표시
    hit = bool(today_only) and today_only["marks"] == [] \
        and "[과거 상담 기록]" not in today_only["text"]
    print(f"{'✓' if hit else '✗'} 오늘 대화만 있으면 시효 표시를 달지 않는다")
    ok += hit
    hit = bool(plain) and tools.HISTORY_MARK in plain["marks"]
    print(f"{'✓' if hit else '✗'} 과거 상담이 실리면 시효 표시를 단다")
    ok += hit

    # 표시는 본문이 아닌 답변 끝 「참고한 자료」 블록에만 — 본문 경계는 형태 요구(날짜 문단)
    from pension_agent import prompts as PR

    mark = tools.HISTORY_MARK
    prompts_seen: list[str] = []
    orig_gen = plan.generate

    def fake_generate(prompt, **kw):
        prompts_seen.append(prompt)
        return "지난 상담에서는 수수료 부담으로 상품 전환을 보류하셨어요."

    try:
        plan.generate = fake_generate
        out = plan.compose({"question": "지난 상담 참고해서 오늘 뭐라고 말하지", "evidence": [plain]})
    finally:
        plan.generate = orig_gen
    body, _sep, tail = out["answer"].partition("\n\n" + plan.MATERIAL_MARKS + "\n")
    hit = bool(prompts_seen) and mark not in prompts_seen[0] \
        and mark not in body and f"· {mark}" in tail
    print(f"{'✓' if hit else '✗'} 시효 표시는 필수 인용으로 넘기지 않고 답변 끝 참고 블록에만 선다")
    ok += hit
    hit = "날짜를 앞세운" in PR.ANSWER_SHAPES["history"] and "오늘 기준" in PR.ANSWER_SHAPES["history"]
    print(f"{'✓' if hit else '✗'} history 형태 요구가 지난 상담 문단의 경계(날짜 문단)를 시킨다")
    ok += hit
    return ok


def check_market_material() -> int:
    """시황·상품 기반지식(market·lineup) — 적재·검색·시효 표시·표 관계 선언."""
    from pension_agent.evidence import marks as MARKS
    from pension_agent.evidence import relations as REL
    from pension_agent.tools.kb_search.kb_index import buckets
    from pension_agent.prompts import ANSWER_SHAPES
    from pension_agent.state import KB

    ok = 0
    # market(시황)·lineup(운용 상품) — 같은 폴더지만 묻는 것이 달라 분리
    cards = [c for c in KB.cards if c["_kind"] in ("market", "lineup")]

    hit = len(cards) >= 20
    print(f"{'✓' if hit else '✗'} 시황·상품 기반지식이 적재된다 ({len(cards)}장)")
    ok += hit

    # 갈래와 종류 일치 — 상품 문서가 market 으로 가면 시황 도구가 추천펀드를 듦
    pairs = {(c["_kind"], c["category"]) for c in cards}
    hit = pairs == {("market", "시황"), ("lineup", "상품")}
    print(f"{'✓' if hit else '✗'} 시황→market · 상품→lineup 으로 갈라 적재된다 ({sorted(pairs)})")
    ok += hit

    # 도구·버킷도 분리 — 도구가 하나면 계획 LLM 이 둘을 한 도구로 받음
    hit = ("market" in tools.TOOLS and "lineup" in tools.TOOLS
           and tools.TOOLS["market"].desc != tools.TOOLS["lineup"].desc)
    print(f"{'✓' if hit else '✗'} 도구가 둘로 갈리고 설명이 서로 다르다")
    ok += hit

    letters = {b["kind"]: code[0] for code, b in buckets(KB).items()
               if b["kind"] in ("market", "lineup")}
    hit = len(letters) == 2 and letters["market"] != letters["lineup"]
    print(f"{'✓' if hit else '✗'} 버킷 카탈로그에서도 갈린다 ({letters})")
    ok += hit

    # 기준시점 없는 시황·상품 수치는 인용 불가(폴더 README 수록 규칙)
    missing = [c["id"] for c in cards if not c.get("as_of")]
    hit = not missing
    print(f"{'✓' if hit else '✗'} 모든 카드가 기준시점을 갖는다"
          + ("" if hit else f" — {missing[:3]}"))
    ok += hit

    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        q = "디폴트옵션 알파드림 구성상품이 뭐야"
        found = tools.run("lineup", {"question": q}, q)
    finally:
        tools.fits_question = orig

    hit = bool(found) and "수협은행 노후보장 정기예금" in found["text"]
    print(f"{'✓' if hit else '✗'} market 도구가 디폴트옵션 편입상품을 근거로 돌려준다")
    ok += hit

    # 시효 표시는 데이터(volatile·as_of)가 결정 — 코드 상수면 원문과 갈림
    hit = bool(found) and any("빠르게 달라집니다" in n for n in (found.get("notices") or []))
    print(f"{'✓' if hit else '✗'} 시장·상품이 달라질 수 있다는 원문 경고가 함께 나간다")
    ok += hit

    sample = next((c for c in cards if c.get("volatile")), None)
    hit = bool(sample) and sample["volatile"] in tools.stale_mark(sample) \
        and sample["as_of"] in tools.stale_mark(sample)
    print(f"{'✓' if hit else '✗'} 경고 문구와 기준시점을 원문에서 읽어 온다")
    ok += hit

    # 경고 문구에 필드명(as_of)·저작 표기(⏳) 금지 — README ※ 줄은 저장소 선언이라 수정 가능
    hit = bool(sample) and not any(w in sample["volatile"] for w in ("as_of", "asof", "⏳"))
    print(f"{'✓' if hit else '✗'} 시효 경고 문구에 개발 용어·저작 표기가 없다")
    ok += hit

    # 같은 경고 카드 여러 장이면 ※ 줄 한 번, 기준시점만 나란히
    from pension_agent.tools.kb_search.market import market_evidence, stale_marks
    two = [c for c in cards if c.get("volatile") and c.get("as_of")]
    two = [next(c for c in two if c["_kind"] == "market"), next(c for c in two if c["_kind"] == "lineup")]
    ev2 = market_evidence("market", "q", [(1.0, c) for c in two])
    stale = [n for n in (ev2["notices"] if ev2 else []) if n.startswith("※")]
    hit = (len(stale) == 1 and all(c["as_of"] in stale[0] for c in two)
           and ev2["text"].count("※") == 1)
    print(f"{'✓' if hit else '✗'} 시황 카드 2장의 시효 표시가 한 줄로 합쳐진다"
          + ("" if hit else f" — {stale}"))
    ok += hit

    hit = bool(sample) and stale_marks([sample]) == [tools.stale_mark(sample)]
    print(f"{'✓' if hit else '✗'} 카드 한 장이면 합친 표시가 낱장 표시와 같다")
    ok += hit

    # TDF 표 빈티지 행은 열 머리말로 접힘(config.TABLE_HEADER_ROWS) — 데이터 행이면 맞는 답이 오짝으로 폐기
    from pension_agent.evidence import relations as _REL
    tdf = next(c for c in cards if c["id"] == "lnp.퇴직연금펀드_포트폴리오_2026-08.01")
    table = tdf["tables"][0]
    hit = ("1975년 (TDF 2035)" in table["columns"]
           and not any("TDF" in k.replace(" ", "") for r in table["rows"] for k in r["keys"]))
    print(f"{'✓' if hit else '✗'} TDF 표의 빈티지 행이 열 머리말로 접히고 데이터 행에서 빠진다")
    ok += hit

    right = "1975년생이면 TDF 2035 예요. 위험자산 비중은 KB 온국민 59.5%, 신한 58.6% 예요(2026.08 기준)."
    wrong = "1975년생이면 TDF 2035 예요. KB 온국민 위험자산 비중은 65.8% 예요."
    hit = not _REL.check(right, [tdf]) and bool(_REL.check(wrong, [tdf]))
    print(f"{'✓' if hit else '✗'} 빈티지를 말한 맞는 답은 통과하고, 남의 행 비중을 붙인 답은 잡힌다")
    ok += hit

    # 행내한 자료는 고객에게 그대로 안내 불가 — 원문 confidentiality 선언
    internal = [c for c in cards if c.get("customer_facing") is False]
    facing = [c for c in cards if c.get("customer_facing") is True]
    hit = bool(internal) and bool(facing)
    print(f"{'✓' if hit else '✗'} 고객용·행내한이 원문 표기대로 갈린다 "
          f"(행내한 {len(internal)} · 고객용 {len(facing)})")
    ok += hit

    marks = MARKS.notes_for(KB, internal[:1]) if internal else []
    hit = any("고객에게 그대로 안내하지는 마세요" in m for m in marks)
    print(f"{'✓' if hit else '✗'} 행내한 자료를 쓰면 고객 안내 주의가 붙는다")
    ok += hit

    # 원문(content) 무수정 — 표 값이 그대로 있어야 인용 성립
    tdf = next((c for c in cards if "TDF" in c["title"]), None)
    hit = bool(tdf) and "Glide-Path" in (tdf.get("content") or "")
    print(f"{'✓' if hit else '✗'} 절 본문이 원문 그대로 실린다")
    ok += hit

    # 절 카드의 부모는 자기 문서 개요 카드
    sections = [c for c in cards if c.get("parent")]
    ids = {c["id"] for c in cards}
    hit = bool(sections) and all(c["parent"] in ids for c in sections)
    print(f"{'✓' if hit else '✗'} 절 카드가 개요 카드를 부모로 가리킨다 ({len(sections)}장)")
    ok += hit

    # 저작·검수 기록(추출 노트)은 카드 아님
    hit = not any("추출 노트" in c["title"] for c in cards)
    print(f"{'✓' if hit else '✗'} 추출 노트·목차는 카드로 만들지 않는다")
    ok += hit

    # 한 글자 키워드 제외 — 「금」은 거의 모든 절에 걸림
    one_char = [(c["id"], t) for c in cards for t in (c.get("trigger_examples") or [])
                if len(t.strip()) < 2]
    hit = not one_char
    print(f"{'✓' if hit else '✗'} 한 글자 검색 키워드를 달지 않는다"
          + ("" if hit else f" — {one_char[:3]}"))
    ok += hit

    # 새 종류 적재 시 함께 등록할 자리 — 빠지면 적재돼도 검색 안 됨
    hit = all(k in tools.TOOLS and k in ANSWER_SHAPES for k in ("market", "lineup"))
    print(f"{'✓' if hit else '✗'} 도구·답변 형태 요구에 등록됨")
    ok += hit

    bucketed = {c["id"] for b in buckets(KB).values() for c in b["cards"]}
    hit = all(c["id"] in bucketed for c in cards)
    print(f"{'✓' if hit else '✗'} 버킷 카탈로그에 들어간다(LLM 후보 목록에 보인다)")
    ok += hit

    # 표의 관계 선언 — 없으면 검색 입구도, 값–조건 오짝 판정 재료도 없음
    tabled = [c for c in cards if c.get("tables")]
    hit = len(tabled) >= 8
    print(f"{'✓' if hit else '✗'} 표가 행 단위 관계로 선언된다 ({len(tabled)}장)")
    ok += hit

    deck = next((c for c in cards if c["id"].endswith("추천펀드_2026-08.01")), None)
    rows = (deck or {}).get("tables", [{}])[0].get("rows") or []
    # 병합 셀(합계 행)은 위 행 이름 승계 — 안 하면 맞는 답변이 남의 값으로 차단
    hit = any(r["keys"][:2] == ["저위험", "알파드림"] and "100" in r["values"] for r in rows)
    print(f"{'✓' if hit else '✗'} 병합 셀 합계 행이 상품 이름을 이어받는다")
    ok += hit

    # 행을 못 가리는 이름(「포트폴리오」)은 행 이름 아님
    hit = not any("포트폴리오" in (r.get("keys") or []) for r in rows)
    print(f"{'✓' if hit else '✗'} 행을 못 가리는 이름은 행 이름으로 쓰지 않는다")
    ok += hit

    hit = REL.declared(deck or {})
    print(f"{'✓' if hit else '✗'} 표를 선언한 카드가 관계 검사 대상이 된다")
    ok += hit

    # 표의 검색 입구 — 열 머리말(1975년)·행 이름(알파드림 III) 둘 다
    tdf = next((c for c in cards if c["title"] == "TDF 포트폴리오"), None)
    # 빈티지 행을 머리말에 접어 입구는 「1975년 (TDF 2035)」
    hit = bool(tdf) and any("1975년" in t and "TDF 2035" in t for t in tdf.get("trigger_examples") or [])
    print(f"{'✓' if hit else '✗'} 표의 열 머리말이 검색 입구가 된다 — 1975년 (TDF 2035)")
    ok += hit

    hit = bool(deck) and "알파드림 III" in (deck.get("trigger_examples") or [])
    print(f"{'✓' if hit else '✗'} 표의 행 이름이 검색 입구가 된다 — 알파드림 III")
    ok += hit

    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        q = "1975년생이면 TDF 몇 년짜리 골라야 해?"
        found = tools.run("lineup", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(found) and "출생연도" in found["text"]
    print(f"{'✓' if hit else '✗'} 표 안에만 있던 질문이 근거에 닿는다 — 출생연도별 TDF")
    ok += hit

    # 검색이 답을 가진 카드에 닿는지 — 적재와 검색은 별개 문제

    # ① category 를 topics 에 넣지 않음 — 흔한 두 글자라 전 카드 동점, 순위가 id 사전순
    polluted = [c["id"] for c in cards
                if {"상품", "시황"} & set(c["tags"].get("topics") or [])]
    hit = not polluted
    print(f"{'✓' if hit else '✗'} category 를 검색 태그에 섞지 않는다"
          + ("" if hit else f" — {polluted[:3]}"))
    ok += hit

    # ② 같은 문서의 절이 걸리면 개요 카드 제외 — 답이 든 표는 절에 있음
    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    try:
        found = tools.run("lineup", {"question": "지켜드림 금리 얼마야"}, "지켜드림 금리 얼마야")
    finally:
        tools.fits_question = orig
    ids = [s["id"] for s in (found or {}).get("sources") or []]
    by_id = {c["id"]: c for c in cards}
    hit = bool(ids) and all(by_id[i].get("parent") for i in ids)   # 전부 절 카드인가
    print(f"{'✓' if hit else '✗'} 절이 걸리면 개요가 후보 자리를 먹지 않는다 — {ids}")
    ok += hit

    # ③ 「+65,469억원」은 값 — 이름 칸으로 읽으면 값 열이 없어 표 전체 폐기
    flows = next((c for c in cards if c["title"].endswith("주간 자금 동향")), None)
    hit = bool(flows) and "코스피" in (flows.get("trigger_examples") or [])
    print(f"{'✓' if hit else '✗'} 수치에 한국어 단위가 붙어도 값으로 읽는다 — 코스피 행")
    ok += hit

    # 날짜는 반대 — 일정표의 「20일」은 행 이름
    sched = [t for c in cards for t in (c.get("tables") or [])
             if "일자" in (t.get("columns") or [])]
    hit = bool(sched) and any("20일" in (r.get("keys") or [])
                              for t in sched for r in t["rows"])
    print(f"{'✓' if hit else '✗'} 일정표의 날짜는 행 이름으로 남는다 — 20일")
    ok += hit

    # 오짝 판정 — 차단할 것과 통과시킬 것(옳은 문장 거부가 더 나빠 통과 쪽을 더 많이)
    tables = (deck or {}).get("tables") or []
    for expect, label, answer in (
        (True, "다른 행의 금리를 갖다 붙임",
         "알파드림의 정기예금 금리는 3.40 이에요."),
        (True, "형제 상품의 수익률을 갖다 붙임",
         "모두드림 III 의 1년 수익률은 20.63 이에요."),
        (False, "원문 그대로 옮긴 답",
         "알파드림은 수협은행 노후보장 정기예금 디폴트옵션용(3년) 70, "
         "키움키워드림적격TDF2030 20, 삼성글로벌EMP적격TDF2035 10 으로 구성돼요."),
        (False, "산문 칸(상품특징)의 수치를 인용",
         "알파드림은 시중은행 정기예금 70, TDF 30 투자하는 포트폴리오예요."),
        (False, "합계 행의 값을 인용",
         "알파드림 포트폴리오의 1년 수익률은 4.23 이에요."),
        (False, "여러 행을 함께 말함",
         "지켜드림은 3.40·3.25·3.32, 알파드림은 3.27 이에요."),
        (False, "어느 행인지 안 밝힘 — 판정 불가는 위반이 아니다",
         "정기예금 금리는 3.40 수준이에요."),
    ):
        broken = REL.table_mispaired(answer, tables)
        hit = bool(broken) == expect
        print(f"{'✓' if hit else '✗'} {'차단' if expect else '통과'}: {label}")
        ok += hit
    return ok


def check_origin() -> int:
    """출처는 원문 문서명 — 적재 json 이름표 유출·출처 줄 누락 금지."""
    from pension_agent.knowledge.kb import origin_of
    from pension_agent.tools.kb_search.kb_index import sources_of
    from pension_agent.tools.kb_search import facts_qa
    from pension_agent.state import KB

    ok = 0
    materials = list(KB.cards) + list(KB.facts.values())

    leaked = [c["id"] for c in materials if c.get("_doc") and origin_of(KB, c) == c["_doc"]]
    hit = not leaked
    print(f"{'✓' if hit else '✗'} 적재 파일 제목이 출처로 나가지 않음"
          + ("" if hit else f" — {leaked[:3]}"))
    ok += hit

    empty = [c["id"] for c in materials if not (origin_of(KB, c) or "").strip()]
    hit = not empty
    print(f"{'✓' if hit else '✗'} 출처 줄이 비는 카드 없음(못 찾으면 '확인 필요'라고 말한다)"
          + ("" if hit else f" — {empty[:3]}"))
    ok += hit

    # 출처 표기는 공용 함수 tools.source_lines 하나 — 운영 CLI·디버그($CAD·$CADR) 표기 분기 방지
    s_full = {"id": "x.1", "title": "제목", "doc": "문서", "score": 1.0, "url": "https://u"}
    s_bare = {"id": "x.2", "title": "제목", "doc": "문서"}   # 검색으로 오지 않은 재료
    full, bare = tools.source_lines(s_full), tools.source_lines(s_bare)
    compact = tools.source_lines(s_full, compact=True)
    hit = (full[-1] == "     ↗ https://u" and "관련도 1.0" in full[1]
           and "관련도" not in "".join(bare) and "↗" not in "".join(bare)
           and len(compact) == 2 and compact[0].startswith("   · 문서 — 제목 [x.1]"))
    print(f"{'✓' if hit else '✗'} source_lines — URL·관련도는 있을 때만, compact 는 한 줄")
    ok += hit

    # 텍스트 진입점(운영 CLI·$CAD·$CADR)이 같은 출처 표기 함수 사용
    # 운영 CLI 는 임포트 시 REPL 실행 — 파일 텍스트로 확인. main.py 는 sources 이벤트라 해당 없음
    import inspect

    from pension_agent.actions import render
    from tests.debug import __main__ as dbg_main
    from tests.debug import reps as dbg_reps
    # 경로는 config 에서 — Path(__file__) 되짚기 금지
    from pension_agent.common import config as _config
    ops_src = (_config.PACKAGE_ROOT / "__main__.py").read_text(encoding="utf-8")
    hit = ("source_lines" in inspect.getsource(render)
           and "render.sources_block" in ops_src
           and "source_lines" in inspect.getsource(dbg_main._print_source)
           and "source_lines" in inspect.getsource(dbg_reps._print_source_line))
    print(f"{'✓' if hit else '✗'} 운영 CLI·$CAD·$CADR 이 같은 출처 표기 함수에 닿는다")
    ok += hit

    # 카드의 원천 문서(source.doc) → 레지스트리 → 행내 PDF 이름(적재 변환본 아님)
    card = next((c for c in KB.cards if c["id"] == "pitch.k03.001"), None)
    origin = origin_of(KB, card) if card else ""
    hit = "연금왕" in origin and "06/" not in origin
    print(f"{'✓' if hit else '✗'} 카드가 밝힌 원천 문서가 문서명으로 해석됨 — {origin[:48]}")
    ok += hit

    # 근거 목록에 원문 출처(doc) 동반
    hits = facts_qa.search("세액공제 한도")
    srcs = sources_of(KB, hits)
    hit = bool(srcs) and all(s.get("doc") for s in srcs)
    print(f"{'✓' if hit else '✗'} sources_of 가 근거마다 원문 출처(doc)를 함께 돌려줌")
    ok += hit

    return ok
