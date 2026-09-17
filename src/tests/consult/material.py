"""재료 — 브리핑·고객·화법·계좌·오늘·상담이력·시황 도구가 내는 근거와 그 표시.

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

import json
import pathlib

from pension_agent.consult_agent import tools
from pension_agent.consult_agent.nodes import plan
from pension_agent.verify import verify_texts

from tests.consult._common import print, _vt  # noqa: A001 — 집계용 print


def check_briefing_shared() -> int:
    """브리핑을 **화면과 대화형이 같은 것으로** 본다 (§3 · 지워진 gap 25).

    `propose()` 는 LLM 으로 산문을 쓰므로 부를 때마다 다른 문장이 나온다. 그런데 그 산출을
    화면(브리핑)과 대화형(`customer` 도구가 sentence·insight 를 재료에 그대로 싣는다)이
    함께 읽는다 — 각자 생성하면 "화면에 저렇게 써 있는데 왜 다르게 말하느냐"가 된다.

    부수 효과가 지연이다. 브리핑 한 편이 순차 LLM 11 회인데 대화형이 고객 질문마다 그걸
    새로 돌리고 있었다("이 고객 평가금액 얼마야" 한 마디가 순차 14 회).

    캐시가 «같은 값을 준다»만으로는 부족하다 — 돌려받은 것을 고쳤을 때 다음 호출자가
    고쳐진 브리핑을 받으면, 공유하려던 장치가 도리어 둘을 갈라놓는다. 그것도 함께 잰다.
    """
    from pension_agent.strategy_agent import agent as SA
    from pension_agent.strategy_agent import customer as CUST

    ok = 0
    calls: list[str] = []
    orig_gen, orig_avail = SA.llm.generate, SA.llm.available
    profile = CUST.PERSONAS[0]

    # 파일 저장소(briefing_cache/)를 끈다 — `scripts.prebuild_briefings` 를 돌린 체크아웃에는
    # 저장분이 있어 첫 호출이 LLM 0회로 그것을 읽고, «한 번만 만든다»의 1회차가 0 이 된다.
    # 여기서 재는 것은 프로세스 캐시이지 파일 저장소가 아니다(test_engine 의 같은 격리).
    from pension_agent import config as _cfg
    saved_cache_dir = _cfg.BRIEFING_CACHE_DIR
    _cfg.BRIEFING_CACHE_DIR = saved_cache_dir / "__off__"   # 없는 디렉터리 = 꺼짐

    SA.clear_briefing_cache()
    SA.llm.available = lambda: True
    # 부를 때마다 다른 문장을 내는 LLM — 캐시가 없으면 두 호출이 갈린다.
    SA.llm.generate = lambda prompt, **kw: (
        calls.append("llm"),
        json.dumps({"insight": f"해설 {len(calls)}", "sentence": f"문장 {len(calls)}"},
                   ensure_ascii=False))[1]
    try:
        first = SA.propose(profile)
        n_first = len(calls)
        second = SA.propose(profile)
        n_second = len(calls) - n_first
    finally:
        SA.llm.generate, SA.llm.available = orig_gen, orig_avail
        SA.clear_briefing_cache()

    hit = n_first > 0 and n_second == 0
    print(f"{'✓' if hit else '✗'} 같은 고객 브리핑은 한 번만 만든다 "
          f"(1회차 LLM {n_first}회 → 2회차 {n_second}회)")
    ok += hit

    hit = first["sentence"] == second["sentence"] and first["insight"] == second["insight"]
    print(f"{'✓' if hit else '✗'} 두 번째 호출이 같은 문장을 받는다(화면·대화형이 같은 브리핑)")
    ok += hit

    # 돌려받은 것을 고쳐도 캐시가 오염되지 않는다.
    SA.clear_briefing_cache()
    SA.llm.available, SA.llm.generate = (lambda: True), (
        lambda prompt, **kw: '{"insight": "해설", "sentence": "문장"}')
    try:
        a = SA.propose(profile)
        before = (a["sentence"], dict(a["facts"]["customer"]))
        a["sentence"] = "호출부가 고친 문장"      # 최상위 값
        a["facts"]["customer"] = {}              # 중첩된 값(얕은 복사로는 못 막는다)
        b = SA.propose(profile)
        hit = (b["sentence"], b["facts"]["customer"]) == before and bool(before[1])
    finally:
        SA.llm.generate, SA.llm.available = orig_gen, orig_avail
        SA.clear_briefing_cache()
    print(f"{'✓' if hit else '✗'} 돌려받은 브리핑을 고쳐도 다음 호출자는 원본을 받는다")
    ok += hit

    # 입력이 다르면 다른 브리핑이다 — id 가 같아도 내용이 다르면 캐시를 공유하지 않는다.
    # (`dataclasses.replace` 로 요건을 걷어낸 합성 고객이 실제로 같은 id 를 갖는다.)
    import dataclasses
    other = dataclasses.replace(profile, room=0, isa=None, bal=profile.bal + 1)
    hit = SA._cache_key(profile, True, 1) != SA._cache_key(other, True, 1)
    print(f"{'✓' if hit else '✗'} 캐시 키는 id 가 아니라 프로파일 내용이다")
    ok += hit

    # 무한히 쌓이지 않는다 — 실서비스는 고객 수만큼 부른다(시연 로스터 9명으로는 안 드러난다).
    SA.clear_briefing_cache()
    try:
        for i in range(SA._BRIEFING_MAX + 5):
            SA._BRIEFING_CACHE[f"key-{i}"] = {"x": i}
            while len(SA._BRIEFING_CACHE) > SA._BRIEFING_MAX:
                SA._BRIEFING_CACHE.popitem(last=False)
        hit = len(SA._BRIEFING_CACHE) == SA._BRIEFING_MAX \
            and "key-0" not in SA._BRIEFING_CACHE \
            and f"key-{SA._BRIEFING_MAX + 4}" in SA._BRIEFING_CACHE
    finally:
        SA.clear_briefing_cache()
    print(f"{'✓' if hit else '✗'} 캐시가 상한({SA._BRIEFING_MAX})에서 오래된 것부터 밀어낸다")
    ok += hit
    _cfg.BRIEFING_CACHE_DIR = saved_cache_dir
    return ok


def check_customer_material() -> int:
    """고객 재료는 **한 경로**이고, 그 고객에게 걸린 주의는 **코드가** 붙는다.

    회귀 대상 둘.
    ① 같은 고객 재료를 `briefing_qa` 노드와 `customer` 도구 두 경로가 답했다. 프롬프트·
       검증·표시 규약이 갈려서, 같은 질문이 분류에 따라 다른 답을 받았다(§3 · gap 11).
    ② 「하지 말 것」이 원장에 고객 요건이 실렸을 때만 — 즉 LLM 이 customer 도구를 부른
       턴에만 — 붙었다. 고객 상태는 코드가 이미 아는 값인데 LLM 의 도구 선택에 의존한
       것이고, 그래서 화법만 물은 턴에는 조용히 빠졌다(§8 · gap 10).

    실존 고객이 필요한 검사는 시연용 목업 9케이스의 이준호(KB-PIN 198734-1205842)를
    쓴다. "CX" 는 존재하지 않는 id 로 남겨 "고객 없음" 경로를 함께 검증한다.
    """
    from pension_agent.consult_agent.evidence import guard as GD
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0

    # ① 고객 재료 경로가 하나뿐인가.
    import importlib
    gone = importlib.util.find_spec("pension_agent.consult_agent.nodes.briefing_qa") is None
    print(f"{'✓' if gone else '✗'} 고객 재료를 답하던 두 번째 경로(briefing_qa)가 없다")
    ok += gone

    found = tools.run("customer", {"customer_id": "198734-1205842"}, "이 고객 평가금액 얼마야?")
    hit = bool(found) and "평가금액" in found["text"] and "성립 요건" in found["text"]
    print(f"{'✓' if hit else '✗'} customer 도구가 브리핑 재료(값·요건)를 싣는다")
    ok += hit

    # 화면에 뜬 AI 산문(제안 문장·근거 해설)도 재료에 실린다 — briefing_qa 노드가 갖고
    # 있던 노출 수준이다. 직원은 그 문장을 보면서 묻기 때문에, 재료에 없으면 "화면엔
    # 저렇게 써 있는데 왜 다르게 말하느냐"가 된다. LLM 없이는 산문이 비므로 스텁을 쓴다.
    from pension_agent.strategy_agent import agent as SA
    orig_propose = SA.propose
    SA.propose = lambda profile: {**orig_propose(profile),
                                  "sentence": "만기 예금을 이렇게 제안해 보세요.",
                                  "insight": "만기 자금이 대기 중이라 수익 기회를 놓칩니다."}
    try:
        found = tools.run("customer", {"customer_id": "198734-1205842"}, "브리핑 요약해줘")
        hit = bool(found) and "AI브리핑 문장" in found["text"] and "AI브리핑 근거해설" in found["text"]
    finally:
        SA.propose = orig_propose
    print(f"{'✓' if hit else '✗'} customer 도구가 화면에 뜬 AI브리핑 산문까지 재료로 싣는다")
    ok += hit

    # ── CLAUDE.md §3 「고객 정보 질의응답 — 되어야 하는 것」 재료 요건 ──────────────
    # 문서에 적어둔 것이 실제로 재료에 실리는지 본다. 아래가 하나라도 빠지면 그 질문은
    # 답이 나올 수 없고, LLM 은 없는 재료에 대해 말을 만든다.
    _mat = tools.run("customer", {"customer_id": "181245-3097614"}, "이 고객 현황")["text"]
    _NEED = [
        ("① 값 — 자산군별 금액", "고유계정대 2,000만원"),   # 비중만 있으면 금액을 못 답한다
        ("① 값 — 자산군별 비중(원장값)", "(7.7%)"),          # 4분류 반올림(8%)이 아니라 원장값
        ("① 값 — 만기 전건", "2027-02-01"),                 # 가장 가까운 한 건만이 아니다
        ("② 왜 이 고객인가", "· 왜 이 고객인가:"),
        ("② 판단근거", "· 판단근거:"),
        ("② 문제상황", "· 문제상황 1:"),
        ("② 성립 요건", "· 성립 요건:"),
    ]
    # 원장 → Profile → 재료 경로가 뚫려 있는지. 하나만 하면 값은 있는데 답은 못 한다.
    _big = tools.run("customer", {"customer_id": "188406-7352194"}, "현황")["text"]
    for _label, _needle in (("보유상품 개별 종목", "KB 퇴직연금 배당"),
                            ("판매중단 표시", "⚠판매중단"),
                            ("동연령대 비교", "동연령 평균 수익률"),
                            ("거래 활동", "1년 매매")):
        _h = _needle in _big
        print(f"{'✓' if _h else '✗'} 고객 재료: {_label}" + ("" if _h else f" — '{_needle}' 없음"))
        ok += _h

    # 과거 상담 기록은 세션 저장소에 심겨 있고(scripts/seed_sessions.py), 읽는 경로는
    # 하나다 — 화면 §14 와 대화형 history 도구가 같은 것을 본다. 원장에서 따로 읽는 두
    # 번째 경로를 만들면 같은 상담이 두 번 실린다.
    _PAST = "재투자하고 싶다"          # 송도윤 2025-10-06 상담 기록의 한 조각
    _hist = tools.run("history", {"customer_id": "188406-7352194"}, "지난번에 무슨 얘기 했어")
    hit = bool(_hist) and _PAST in _hist["text"] and "상담기록" in _hist["text"]
    print(f"{'✓' if hit else '✗'} history 도구: 과거 상담 기록을 싣는다(role=record)")
    ok += hit
    from pension_agent.strategy_agent import engine as _eng
    from pension_agent.strategy_agent.customer import get_profile as _gp
    _screen = _eng.prepare(_gp("188406-7352194"))["consult_history"]
    hit = sum(_PAST in line for line in _screen) == 1
    print(f"{'✓' if hit else '✗'} 화면 §14 도 같은 기록을 «한 번만» 본다(대화형과 답이 갈리지 않는다)")
    ok += hit

    # ISA 만기자금·납입이력 — 원장에 컬럼이 있어도 Profile 이 안 접으면 대화형은 못 본다.
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

    # 같은 항목이 재료 안에서 두 값이 되면 안 된다 — 3분류와 자산군별의 고유계정대가
    # 각각 8% · 7.7% 로 실리던 자리(4분류 반올림 대 원장값).
    import re as _re
    _three = _re.search(r"운용현황\(3분류\)[^\n]*고유계정대 ([\d.]+)%", _mat)
    _asset = _re.search(r"자산군별[^\n]*고유계정대[^(]*\(([\d.]+)%\)", _mat)
    hit = bool(_three and _asset) and _three.group(1) == _asset.group(1)
    print(f"{'✓' if hit else '✗'} 고객 재료: 같은 항목(고유계정대 비중)이 한 값으로만 실린다"
          + ("" if hit else f" — 3분류 {_three and _three.group(1)} vs 자산군별 {_asset and _asset.group(1)}"))
    ok += hit

    # 인용 허용 집합에 후보 더미(pools)를 싣지 않는다 — 답변이 쓰지도 않을 카드의 숫자가
    # 아무 주장에나 근거를 대주면 검증이 무력해진다("만기일 2026년 9월 11일" 이 통과하던 자리).
    from pension_agent.verify import verify_texts as _vt
    _ev = tools.run("customer", {"customer_id": "198734-1205842"}, "만기")
    hit = (_vt("만기일은 2026-09-10, 금액 4,050만원이에요.", _ev["allow"])[0]
           and not _vt("만기일은 2026년 9월 11일입니다.", _ev["allow"])[0])
    print(f"{'✓' if hit else '✗'} 고객 재료: 화면 값은 인용 통과, 후보 더미가 licensing 하던 오답은 거부")
    ok += hit

    hit = tools.run("customer", {"customer_id": None}, "평가금액") is None
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있으면 고객 재료를 만들지 않는다")
    ok += hit

    # ② 고객 상태 주의를 코드가 판단하는가. (이준호는 만기 요건이 성립한다)
    hit = "mat" in [c.split(":")[0] for c in GD.conditions_of("198734-1205842")]
    print(f"{'✓' if hit else '✗'} conditions_of: customer_id 만으로 고객 요건을 읽는다")
    ok += hit

    hit = GD.conditions_of(None) == [] and GD.conditions_of("없는고객") == []
    print(f"{'✓' if hit else '✗'} conditions_of: 고객이 없으면 요건을 지어내지 않는다")
    ok += hit

    # 화법만 물어 원장에 고객 재료가 **하나도 없는** 턴. 예전에는 여기서 가드가 빠졌다.
    pitch_only = [{"tool": "pitch", "query": "q", "text": "수익률 이야기를 이렇게 꺼내세요.",
                   "atomic": [], "notices": [], "notice_scopes": [],
                   "allow": ["수익률 이야기를 이렇게 꺼내세요."], "sources": [], "meta": {}}]
    orig_gen = P.generate
    P.generate = lambda prompt, **kw: "(스텁 답변)"
    try:
        # 김현수(dep·nod) — 이 요건에는 지식베이스에 대응 주의 카드가 실재한다.
        # 이준호(mat)로 재면 안 된다: 만기 요건의 주의 카드는 없어서 가드가 정당하게 빈다.
        out = P.compose({"question": "뭐라고 말하지?", "customer_id": "173544-2074623",
                         "evidence": pitch_only})
        hit = bool(out.get("guards"))
        closed = P.compose({"question": "뭐라고 말하지?", "evidence": pitch_only})
        hit = hit and not closed.get("guards")
    finally:
        P.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 원장에 고객 재료가 없어도 가드가 붙는다(고객 화면이 열려 있으면)")
    ok += hit

    # ③ 묶음으로 따라온 카드는 **답변이 쓴 것만** 근거로 나간다.
    #
    # 「이 고객 디폴트옵션 등록됐어?」에 "네, 설정되어 있어요" 한 줄로 답하고도 근거가 여섯
    # 건 섰다(2026-09-17 실측, 박정호). customer 도구가 원장 값과 함께 화면 ⑥⑦⑧ 카드를
    # 묶어 싣기 때문인데, 그 다섯 장은 답에 한 글자도 쓰이지 않았다.
    from pension_agent.consult_agent.tools.briefing import _customer
    ev = _customer({"customer_id": "198734-1205842"}, "디폴트옵션 등록 여부")
    hit = bool(ev) and bool(ev.get("source_keys"))
    print(f"{'✓' if hit else '✗'} customer 도구가 딸려 보낸 카드마다 «답변이 썼는지» 가릴 스팬을 선언한다")
    ok += hit

    if ev:
        # 원장 한 줄 답 — 딸려 온 카드는 전부 빠지고 원장 출처만 남는다.
        lean = P._sources([ev], [], [], "네, 디폴트옵션이 설정되어 있어요.")
        hit = len(lean) == 1 and lean[0]["id"].startswith("customer.")
        print(f"{'✓' if hit else '✗'} 원장 한 줄로 답한 턴에는 딸려 온 화법 카드가 근거로 서지 않는다"
              f" ({len(ev['sources'])}건 → {len(lean)}건)")
        ok += hit

        # 그 카드를 실제로 쓴 답 — 그 카드는 남는다(잡음을 줄이자고 근거를 잃지 않는다).
        cid, keys = next(iter(ev["source_keys"].items()))
        span = next(k for k in keys if len(k.strip()) >= P._KEY_MIN)
        used = P._sources([ev], [], [], f"이렇게 말해 보세요. {span}")
        hit = cid in {x["id"] for x in used}
        print(f"{'✓' if hit else '✗'} 답변이 그 카드의 문구를 쓰면 근거에 그대로 남는다 ({cid})")
        ok += hit

        # 검색으로 찾아온 재료는 이 판정의 대상이 아니다 — 전부 근거다(§3).
        searched = dict(ev, source_keys={})
        hit = len(P._sources([searched], [], [], "관계없는 답변")) == len(ev["sources"])
        print(f"{'✓' if hit else '✗'} 스팬을 선언하지 않은 출처(검색 결과)는 하나도 빠지지 않는다")
        ok += hit
    return ok


def check_playbook_material() -> int:
    """고객 상태에 걸린 화법 — 화면 ⑥⑦⑧ 과 **같은 후보군**에서 나오는가(§3 · §10).

    회귀 대상 넷.
    ① `customer` 도구가 화면 ⑥⑦⑧ 이 고른 것을 재료에 안 실었다. `allow` 에는 있어 인용은
       허용됐지만 재료 텍스트에 없어 LLM 이 본 적이 없었고, 그래서 "이 고객한테 뭐라고
       말하지"를 `pitch` 가 지식베이스 전체에서 고객과 무관하게 답했다 — 화면과 대화가
       같은 질문에 다른 카드를 말하는 상태다(§3).
    ② 선제 제안이 매 턴 붙으면 안 된다(§10). 지워진 LMS 갈래가 그렇게 죽었다.
    ③ 승낙 턴이 지식 카드를 **손으로 렌더하면** §5 형태·§6 점검·§7 표시가 그 경로만
       빠진다 — 근거만 싣고 답변은 compose 가 쓴다.
    ④ 화면이 막은 것을 대화형이 권하면 안 된다. 목업 9케이스는 전원 `pension_started=False`
       라 이 경로가 한 번도 발동한 적이 없다 — 합성 프로필로 고정한다.
    """
    import dataclasses

    from pension_agent.consult_agent.nodes import act as ACT
    from pension_agent.consult_agent import routing as R
    from pension_agent.strategy_agent import customer as SC
    from pension_agent.strategy_agent.situations import problem_situations

    ok = 0
    SONG = "188406-7352194"   # 송도윤 — 요건 6건이라 문제상황도 화법 후보도 넉넉하다

    # ① 화면 ⑥⑦⑧ 이 고른 것이 재료와 출처에 함께 실리는가.
    ev = tools.run("customer", {"customer_id": SONG}, "이 고객한테 뭐라고 말하지")
    text = ev["text"]
    hit = all(k in text for k in ("· 이렇게 말해보세요:", "· 예상 반론:", "· 상담 참고:"))
    print(f"{'✓' if hit else '✗'} customer 재료에 화면 ⑥⑦⑧ 이 고른 화법·반론·참고자료가 실린다")
    ok += hit

    card_ids = {s["id"] for s in ev["sources"] if s["id"].startswith(("pitch.", "m.", "proc."))}
    hit = bool(card_ids)
    print(f"{'✓' if hit else '✗'} 그 카드가 **출처**에도 실린다(§3) — {len(card_ids)}건")
    ok += hit

    # 검색으로 온 재료가 아니므로 관련도를 지어내지 않는다(§3).
    hit = all(s.get("score") is None for s in ev["sources"])
    print(f"{'✓' if hit else '✗'} 고객 재료의 출처에는 관련도를 붙이지 않는다")
    ok += hit

    # 같은 카드가 검색으로 오면 원천 게시글 URL 이 붙는데(sources_of) 이 재료로 오면 안
    # 붙던 자리다 — strategy_agent 가 넘겨주는 항목에 문서명만 있어서, 「출처에 URL 을
    # 싣는다」는 변경이 이 경로만 비껴갔다. 화면에는 ↗ 줄이 붙는 근거와 안 붙는 근거가
    # 섞여 나갔고, 직원은 왜 어떤 것만 원문으로 갈 수 있는지 알 수 없었다.
    from pension_agent.knowledge.kb import card_source_meta
    from pension_agent.consult_agent.state import KB as _KB
    hit = all("url" in s and s["url"] == card_source_meta(_KB, s["id"]).get("url")
              for s in ev["sources"] if s["id"] in card_ids)
    print(f"{'✓' if hit else '✗'} 고객 재료의 카드 출처가 검색 경로와 같은 URL 을 싣는다")
    ok += hit

    # 위 대조가 «둘 다 None» 으로 늘 참이 되지 않게, 되짚기가 실제로 도는지 따로 잰다 —
    # 송도윤의 ⑥⑦⑧ 은 본부 자료라 URL 이 없어서(핫팁 게시글이 아니다) 그 고객만으로는
    # 판정할 수 없다. 어느 고객에게 어떤 카드가 뽑히느냐에 이 회귀가 좌우되면 안 된다.
    linked = [c["id"] for c in _KB.cards if card_source_meta(_KB, c["id"]).get("url")]
    hit = len(linked) > 10
    print(f"{'✓' if hit else '✗'} 카드 id 로 원천 게시글 URL 을 되짚을 수 있다({len(linked)}건)")
    ok += hit

    # ② 후보는 strategy_agent 매칭에서만 나온다 — 대화형이 자기 매칭을 만들지 않는다.
    hits = tools.playbook_hits({"customer_id": SONG, "question": "증권사 얘기를 꺼내네요"})
    from pension_agent.strategy_agent.support import matching as M
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

    # ③ 제안 트리거 — 네 조건 중 하나라도 어긋나면 안 붙는다(§10).
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

    # 무엇에 걸렸는지 밝힌다 — 열어보지 않고도 왜 떴는지 알 수 있어야 한다. 대는 이름은
    # **그 카드를 세운 문제상황**이지 고객의 요건 목록이 아니다. 요건으로 대던 동안 제목과
    # 내용이 어긋났다(2026-09-17 실측, 박정호 — 요건 둘을 끊어 쓴 제목의 절반이 밑의 두
    # 카드와 아무 관계가 없었다). 요건 이름을 찾던 예전 검사는 그 어긋남을 통과시켰다.
    ranked = tools.playbook_ranked({"answer": "a", "customer_id": SONG,
                                    "evidence": pitch_ev}, lanes=("pitch",))
    matched = {ACT.situation_name(sit) for _s, _c, sit in ranked} - {""}
    label = (action or {}).get("label") or ""
    hit = bool(action) and bool(matched) and any(t in label for t in matched)
    print(f"{'✓' if hit else '✗'} 제안 문구가 «그 카드를 세운 문제상황»을 밝힌다 — {label}")
    ok += hit

    # 카드를 하나도 내지 않은 사유는 제목에 세우지 않는다 — 제목이 내용보다 넓으면 거짓말이다.
    stray = [n for n in SC.CONDS.values() if n in label and n not in matched]
    hit = bool(action) and not stray
    print(f"{'✓' if hit else '✗'} 카드를 내지 않은 요건 이름은 제안 문구에 서지 않는다"
          + (f" — {stray}" if stray else ""))
    ok += hit

    # 이미 이번 턴 원장에 실린 카드는 다시 제안하지 않는다.
    used = dict(pitch_ev[0], sources=[{"id": c["id"], "title": "", "doc": "", "score": None,
                                       "page": None} for c in
                                      [SITU for _s, SITU in tools.playbook_hits(
                                          {"customer_id": SONG, "question": "q"})]])
    again = ACT._propose({"answer": "a", "customer_id": SONG, "evidence": [used]})
    hit = again is None or not ({c["id"] for c in again["cards"]}
                                & {s["id"] for s in used["sources"]})
    print(f"{'✓' if hit else '✗'} 이번 턴이 이미 쓴 카드를 다시 보여드릴까요 하고 묻지 않는다")
    ok += hit

    # ③-b 갈래 일치 — 제안은 이번 턴이 다룬 갈래의 나머지 후보만이다. 절차를 물은 턴에
    #     화법을 제안하면 §3 「묻지 않은 값」의 제안 버전이 된다.
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

    # ③-c 종류별 렌더러·선언 — 화법 렌더러에 절차를 태우면 저작 메모(authoring)가 새고
    #     화면번호가 원문 강제(atomic)를 안 받는다(지워진 gap 17 이 고친 실패의 재발 경로).
    mixed = tools.playbook_hits({"customer_id": SONG, "question": "q"},
                                lanes=("procedure", "method"))
    pev = tools.playbook_evidence("점검", mixed)
    proc_cards = [c for _s, c in mixed if c["_kind"] == "procedure"]
    hit = (bool(pev) and "필자 해석" not in pev["text"] and "'role'" not in pev["text"]
           and all(sc in pev["atomic"] for c in proc_cards for sc in (c.get("screens") or [])))
    print(f"{'✓' if hit else '✗'} playbook 근거가 절차·방법론에 그 종류의 렌더러·선언을 쓴다"
          f" (화면번호 atomic {len(pev['atomic'])}건 · 저작 메모 미유출)")
    ok += hit

    # ④ 승낙 턴은 근거만 싣고 답변은 작성 단계가 쓴다.
    out = ACT.confirm_action({"question": "네", "customer_id": SONG,
                              "history": [{"pending_action": action}]})
    hit = bool(out.get("evidence")) and not out.get("answer")
    print(f"{'✓' if hit else '✗'} 승낙 턴이 근거만 싣고 답변 문장을 손으로 만들지 않는다")
    ok += hit

    # ④-b 승낙받은 자료가 «없는 자료»가 되어 나가면 안 된다.
    #
    #     실측(2026-09-02 김현수 세션): 화법 2건을 승낙받아 원장에 싣고도 답변은 "해당
    #     질문에 대응하는 대사가 지금 준비된 자료에는 없어요"로 시작했고, 직원에게
    #     디폴트옵션 등록 현황을 되물으며 끝났다. 작성 프롬프트에 실리는 것이 「직원 질문:
    #     네」와 이전 대화뿐이라, LLM 이 <자료>를 **직전 턴의 질문**에 대고 재고 안 맞으니
    #     시스템 규칙 9(핵심 대상이 자료에 없으면 그것이 결론)를 적용한 것이다. 그 판정은
    #     여기서 성립하지 않는다 — 자료를 고른 것은 질문이 아니라 고객 상태이고, 무엇을
    #     보여줄지는 제안한 턴이 이미 정했다(§10). 그래서 **코드가** 그 사실을 실어 준다.
    hit = out.get("accepted") == action["label"]
    print(f"{'✓' if hit else '✗'} 승낙 턴이 무엇을 승낙받았는지 남긴다(작성 단계가 볼 수 있게)")
    ok += hit

    # 선언이 없으면 LangGraph 가 노드 반환값에서 그 키를 **조용히** 버린다(state.py 주석).
    # 그러면 위 검사는 통과하는데 그래프로 돌린 턴만 옛 증상으로 돌아간다.
    from pension_agent.consult_agent.state import AgentState as _AS
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

    # 도착지는 `compose`(답변 작성) 다 — 되묻기 판정과 답변 작성이 그 노드에서 함께
    # 끝난다. 라벨이 상태 키 `answer` 와 다른 이유는 graph.py 의 add_node 주석 참고.
    hit = R.route_confirm(out) == "compose" and R.route_confirm(
        {"answer": "화면을 열었어요"}) == "__end__"
    print(f"{'✓' if hit else '✗'} 분기표가 그 턴을 답변 작성으로 보낸다(화면 연계는 그대로 끝)")
    ok += hit

    # 그 턴에는 되묻기 판정이 돌지 않는다 — 입력이 "네" 한 글자라 판정할 질문이 없다(§10).
    from pension_agent.consult_agent.nodes import clarify as _CL
    # 대조군은 지식베이스 검색 재료로 둔다 — 승낙 턴의 재료는 고객 상태에 걸린 카드(playbook)
    # 라서 그것만으로는 의도와 무관하게 판정이 돌지 않는다(지워진 gap 30).
    kb_evidence = [{"tool": "procedure", "query": "q", "text": "실물이전 절차", "atomic": [],
                    "notices": [], "notice_scopes": [], "allow": [], "sources": [], "meta": {}}]
    hit = not _CL.applicable({**out, "intent": "confirm_action"}) \
        and _CL.applicable({**out, "intent": "situation", "question": "실물이전 절차",
                            "evidence": kb_evidence})
    print(f"{'✓' if hit else '✗'} 승낙 턴은 되묻기 판정을 돌리지 않는다")
    ok += hit

    # 제안한 턴이 남긴 카드만 싣는다 — 이번 턴의 "네" 에서 다시 고르지 않는다(§10).
    hit = bool(out.get("evidence")) and {s["id"] for s in out["evidence"][0]["sources"]} == {
        c["id"] for c in action["cards"]}
    print(f"{'✓' if hit else '✗'} 승낙 턴이 제안한 턴의 카드를 그대로 싣는다")
    ok += hit

    # ⑤ 연금수령 개시 계좌 — 납입·세액공제 세그먼트가 후보에서 빠진다(§8 관리대장).
    #    9케이스 전원 pension_started=False 라 합성 프로필로만 재현된다.
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
    """재료 성격 표시 — 어느 자료에서 왔고, 고객에게 그대로 옮겨도 되는지 (§7 · gap 8·13).

    회귀 대상:
    ① `customer_facing` 이 참일 때 "안내 가능" 표시만 있고, **내부용 재료가 실렸을 때의
       주의는 없었다.** 직원용 에이전트라 내부용 자료도 답변에 쓰는데, 그러면 무엇을
       고객에게 옮기면 안 되는지 직원이 알 방법이 없었다.
    ② 신뢰 등급이 즉답 카드에만 붙었다. 일반 답변은 현장 관찰 한 종류만 전용 문구로
       붙였고, 본부 공식·대외 공개·교육자료 구분은 답변에 나타나지 않았다 — 현장 노하우가
       본부 지침으로 읽히면 그게 곧 잘못된 안내다.
    """
    from pension_agent.consult_agent.evidence import marks as M
    from pension_agent.consult_agent.nodes import plan as P
    from pension_agent.consult_agent.evidence import facts_qa, procedure_qa
    from pension_agent.consult_agent.state import KB

    ok = 0

    # 등급은 문서 레지스트리에서 나온다 — 카드 내용을 보고 추론하지 않는다.
    field = next(c for c in KB.cards if c["_kind"] == "fieldtip")
    hit = M.tier_of(KB, field) == M.TIER_NOTE["현장팁"]
    print(f"{'✓' if hit else '✗'} 신뢰 등급을 문서 레지스트리의 tier 에서 그대로 옮긴다")
    ok += hit

    hit = M.tier_of(KB, {"_source": {"doc": "없는문서"}}) is None and M.notes_for(KB, []) == []
    print(f"{'✓' if hit else '✗'} 문서를 못 찾으면 등급을 지어내지 않는다")
    ok += hit

    # 내부용 주의는 선언이 **거짓일 때만**. 선언이 없는 종류(pitch·method·segment)는
    # 판단 근거가 없다는 뜻이라 아무 쪽으로도 세지 않는다.
    internal = {"_source": {"doc": "없는문서"}, "customer_facing": False}
    facing = {"_source": {"doc": "없는문서"}, "customer_facing": True}
    undeclared = {"_source": {"doc": "없는문서"}}
    hit = (M.notes_for(KB, [internal]) == [M.INTERNAL_NOTE]
           and M.notes_for(KB, [facing]) == []
           and M.notes_for(KB, [undeclared]) == [])
    print(f"{'✓' if hit else '✗'} 내부용 주의는 customer_facing 선언이 거짓일 때만 붙는다")
    ok += hit

    # ③ **재료에도 같은 선언이 보여야 한다.** 주의(notes_for)는 거짓을 보는데 재료 조립은
    #    참일 때만 표시를 붙였다 — 그래서 답변 아래에는 "고객에게 안내하지 마세요"가 서고
    #    본문은 그 카드를 근거로 "고객에게 이렇게 안내하는 게 핵심"이라고 썼다(송도윤 S6).
    #    작성 프롬프트의 「'내부용'으로 표시된 재료는…」 규칙이 가리킬 표시가 없었다.
    hit = (M.facing_note(internal) == M.FACING_NOTE[False]
           and M.facing_note(facing) == M.FACING_NOTE[True]
           and M.facing_note(undeclared) is None)
    print(f"{'✓' if hit else '✗'} 재료 표시는 참·거짓을 둘 다 싣고 선언 없음은 비운다")
    ok += hit

    # 실제 재료 블록에 실리는가 — 두 종류 모두. 여기가 끊기면 위 단위 판정이 통과해도
    # LLM 은 여전히 내부용 카드를 구분하지 못한다.
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

    # 작성 프롬프트가 그 표시를 실제로 가리키는가. 문구가 갈리면 규칙이 다시 헛돈다.
    from pension_agent.consult_agent import prompts as PR
    hit = "내부용" in PR.COMPOSE_SYSTEM and "고객에게 할 말로 옮기지" in PR.COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 작성 규칙이 내부용 재료를 고객 안내로 옮기지 말라고 못 박는다")
    ok += hit

    # 같은 등급을 여러 장 썼다고 같은 문장을 여러 번 세우지 않는다.
    tips = [c for c in KB.cards if c["_kind"] == "fieldtip"][:3]
    hit = M.notes_for(KB, tips) == [M.TIER_NOTE["현장팁"]]
    print(f"{'✓' if hit else '✗'} 같은 표시를 겹쳐 세우지 않는다")
    ok += hit

    # 표시가 실제로 답변에 붙는가 — 등급 종류를 가리지 않고.
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

        # 답변이 이미 같은 말을 했으면 겹쳐 붙이지 않는다(§7 — 표시가 늘수록 묻힌다).
        P.generate = lambda prompt, **kw: f"이건 {M.TIER_NOTE['본부공식']} 근거예요."
        out = P.compose({"question": "q", "evidence": [ev([M.TIER_NOTE["본부공식"]])]})
        hit = out["answer"].count(M.TIER_NOTE["본부공식"]) == 1
        print(f"{'✓' if hit else '✗'} 답변이 이미 밝힌 표시를 겹쳐 세우지 않는다")
        ok += hit

        # 표시가 없으면 빈 머리말만 남기지 않는다.
        out = P.compose({"question": "q", "evidence": [ev([])]})
        hit = P.MATERIAL_MARKS not in out["answer"]
        print(f"{'✓' if hit else '✗'} 붙일 표시가 없으면 머리말도 붙이지 않는다")
        ok += hit
    finally:
        P.generate = orig_gen

    # 도구가 실제로 표시를 실어 보내는가(선언이 아니라 배선을 본다).
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
    """관계 기반 점검 — 값–조건 오짝과 알려진 오답을 데이터 선언으로 잡는가 (§6 · gap 2·6).

    회귀 대상: `verify_texts` 는 수치의 **집합 포함** 검사라 잘못 짝지은 것을 못 잡았다.
    "5,500만원 이하 16.5% / 초과 13.2%" 가 원장에 있으면 "초과면 16.5%" 도 통과한다 —
    두 숫자가 다 원장에 있으므로. 그 구멍 때문에 원문 문장을 통째로 답변에 싣게 강제했고
    (`atomic`), 답변이 인용문 나열이 됐다.

    **검증기가 옳은 문장을 거부하는 것은 틀린 문장을 통과시키는 것보다 나쁘다** — 직원은
    왜 막혔는지 알 수 없다. 그래서 잡는 것만큼 통과시키는 것도 함께 잰다.
    """
    from pension_agent.consult_agent.evidence import relations as R
    from pension_agent.consult_agent.nodes import plan as P
    from pension_agent.consult_agent.evidence import facts_qa
    from pension_agent.consult_agent.state import KB

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

    # 조건을 다른 말로 풀어 쓴 옳은 답 — 판정 불가이지 위반이 아니다.
    hit = not R.mispaired("총급여가 5,500만원을 넘으면 13.2% 가 적용돼요.", f2.get("tiers") or [])
    print(f"{'✓' if hit else '✗'} 풀어 쓴 옳은 답을 위반으로 바꾸지 않는다(판정 불가 ≠ 위반)")
    ok += hit

    hit = R.known_wrong("5,500만원 이상 13.2% 로 안내하시면 돼요.", f2.get("pitfalls") or []) != []
    print(f"{'✓' if hit else '✗'} 행원들이 적어둔 알려진 오답을 그대로 말하면 잡는다")
    ok += hit

    # 인용은 주장이 아니다 — 그 문구를 «틀렸다»고 짚는 답변까지 잡던 자리다. 카드의
    # verify_points 가 직원에게 그렇게 짚어주라고 적어둔 바로 그 문구라, 데이터가 시킨 일을
    # 했다고 벌하는 꼴이었다(폐기 뒤 나가는 카드 원문에는 같은 문구가 그대로 실려 있다).
    pf = f2.get("pitfalls") or []
    hit = (not R.known_wrong('"5,500만원 이상 13.2%"는 오기예요. "초과"가 맞습니다.', pf)
           and not R.known_wrong("「5,500만원 이상 13.2%」는 틀린 표기니 주의하세요.", pf))
    print(f"{'✓' if hit else '✗'} 오답 문구를 «틀렸다»고 짚는 정정은 막지 않는다")
    ok += hit

    # **정정 표지 목록은 카드가 실제로 쓰는 낱말을 덮어야 한다.** 「오답」이 빠져 있던 동안
    # F47(퇴직금 60일 내 IRP 입금)의 verify_points 가 «"이미 통장으로 받았으면 끝" = 오답»
    # 이라 적혀 있는데, 그 줄대로 짚어준 답변이 폐기되고 근거 원문이 덤프됐다 — 위와 똑같이
    # 데이터가 시킨 일을 했다고 벌하는 자리다(2026-09-02 실측).
    f47 = KB.facts.get("fact.k04.f47")
    pf47 = (f47 or {}).get("pitfalls") or []
    hit = bool(pf47) and not R.known_wrong(
        '"이미 통장으로 받았으면 끝"이라고 생각하기 쉽지만 오답이에요. 60일 이내면 됩니다.', pf47)
    print(f"{'✓' if hit else '✗'} 카드가 「오답」이라 부르는 문구를 그 말로 짚는 정정도 막지 않는다")
    ok += hit

    # 그렇다고 헐거워지지 않는다 — 따옴표 없이 그대로 주장하면 여전히 잡힌다.
    hit = R.known_wrong("이미 통장으로 받았으면 끝이니 어쩔 수 없다고 안내하세요.", pf47) != []
    print(f"{'✓' if hit else '✗'} 표지가 늘어도 그대로 주장한 오답은 잡는다")
    ok += hit

    # 정정으로 보는 조건은 둘 다다 — 하나만으로는 헐겁다.
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

    # 오답 문자열은 **구절**이어야 한다 — 값 하나짜리는 다른 팩트의 맞는 문장에도 들어간다.
    bare = [w for f in KB.facts.values() for x in f.get("pitfalls") or []
            for w in x.get("wrong") or [] if " " not in w and len(w) < 8]
    hit = not bare
    print(f"{'✓' if hit else '✗'} 값 하나짜리 오답 문자열은 대조에 쓰지 않는다"
          + ("" if hit else f" — {bare[:3]}"))
    ok += hit

    # 옳은 표현의 인용(→ O 로 표시된 것, 기준을 짚은 것)이 오답 목록에 섞이지 않았는가.
    every = [w for f in KB.facts.values() for x in f.get("pitfalls") or []
             for w in x.get("wrong") or []]
    hit = "평가금액" not in every and "퇴직금 포함 시 5년 전 가능" not in every
    print(f"{'✓' if hit else '✗'} 옳은 표현의 인용을 오답으로 등록하지 않는다")
    ok += hit

    # 관계를 선언한 팩트는 원문 강제가 해제되고(이행 순서 3), 선언이 없는 팩트는 같은
    # 원장에 섞여 있어도 그대로 강제된다 — 저작된 만큼만 물러난다. 두 종류를 한 원장에
    # 함께 올려서 본다(검색어에 의존하면 데이터가 바뀔 때 무엇을 재는지 흐려진다 —
    # 실제로 1세대 손저작 팩트가 지워졌을 때 이 검사가 조용히 빈 목록을 재고 있었다).
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

    # 선언이 없는 카드는 아직 원문 강제가 남는다 — 저작이 넓어지는 만큼만 물러난다.
    hit = not R.declared({"tiers": [], "pitfalls": [{"wrong": [], "why": "메모"}]}) \
        and R.declared({"tiers": [{"when": "a", "value": "b"}]})
    print(f"{'✓' if hit else '✗'} 선언이 없으면 관계 검사가 원문 강제를 대신하지 않는다")
    ok += hit

    # compose 가 실제로 관계 위반을 걸러내는가(배선을 본다).
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
    """표의 백분율 열 — 단위가 헤더에만 있는 값을 % 붙여 말해도 통과하는가 (§6 · §9).

    회귀 대상(2026-09-17 실측): 추천펀드·디폴트옵션 표는 단위를 열 머리말에 두고 셀에는
    `35`·`3.40`·`2.13` 만 적는다. 직원에게 답하는 문장은 「비중 35%」라고 쓰는데
    `verify._canon()` 이 «%-유무는 다른 주장»으로 보존해(15% ≠ 15) 그 답변이 폐기됐다.
    「디폴트옵션 포트폴리오는 어떻게 구성돼 있어?」가 두 번 다 이 사유로 걸려 근거 원문이
    덤프됐다 — `verify 통과=아니오 … 사유="수치 '35%'; 수치 '2.13%'; 수치 '4.23%'"`.

    **두 가지를 함께 잰다.** 인용 허용만 넓히면 오짝 검사가 조용히 꺼진다 — 「지켜드림의
    금리는 3.27%」(알파드림 행의 값)가 앞단을 통과하고 `table_mispaired` 도 토큰이 안 맞아
    지나간다. 거짓 양성을 고치면서 거짓 음성을 만들지 않는 것이 이 검사의 핵심이다.
    """
    from pension_agent.consult_agent.evidence import relations as R
    from pension_agent.consult_agent.nodes import plan as P
    from pension_agent.consult_agent.tools import market as M
    from pension_agent.consult_agent.state import KB

    ok = 0
    card = next((c for c in KB.cards if c["id"] == "lnp.퇴직연금_추천펀드_2026-08.01"), None)
    if not card:
        print("✗ 디폴트옵션 표 카드를 찾지 못했다 — 05 상품 자료가 적재되지 않았다")
        return ok

    # ① 데이터가 «이 열은 백분율»이라고 선언한다(scripts/kb_build/config.PERCENT_COLUMNS).
    table = (card.get("tables") or [{}])[0]
    units = set(table.get("units") or {})
    hit = {"비중", "금리"} <= units and "설정일" not in units
    print(f"{'✓' if hit else '✗'} 표가 백분율 열을 선언한다 — 비중·금리는 들어가고 설정일은 빠진다"
          f" ({' · '.join(sorted(units))})")
    ok += hit

    ev = M.market_evidence("lineup", "디폴트옵션 구성", [(1.0, card)])
    known = P._known_products()

    # ② 단위를 붙인 맞는 답변이 통과한다 — 이게 폴백을 만들던 자리다.
    faults, _ = P._screen("초저위험 지켜드림은 신한은행 정기예금 비중 35% 로 담고 "
                          "금리는 3.40% 예요.", [ev], "디폴트옵션 구성", known)
    print(f"{'✓' if not faults else '✗'} 표의 값에 % 를 붙여 말한 맞는 답변이 통과한다 {faults[:1]}")
    ok += not faults

    # 반대 방향도 막히지 않는다(표마다 셀 표기가 갈린다 — 투자성향별 표는 「30%」로 적는다).
    faults, _ = P._screen("초저위험 지켜드림의 금리는 3.40 이에요.", [ev], "디폴트옵션 구성", known)
    print(f"{'✓' if not faults else '✗'} % 없이 말한 답변도 그대로 통과한다 {faults[:1]}")
    ok += not faults

    # ③ 경계는 그대로다 — 반올림·지어낸 수치는 여전히 폐기된다.
    faults, _ = P._screen("지켜드림의 1년 수익률은 약 2.1% 수준이에요.",
                          [ev], "디폴트옵션 구성", known)
    print(f"{'✓' if faults else '✗'} 반올림한 수치는 여전히 폐기된다(원장에 2.13 만 있다)")
    ok += bool(faults)

    faults, _ = P._screen("지켜드림의 금리는 9.99% 예요.", [ev], "디폴트옵션 구성", known)
    print(f"{'✓' if faults else '✗'} 지어낸 수치는 여전히 폐기된다")
    ok += bool(faults)

    # ④ 오짝 검사가 % 표기로 꺼지지 않는다. 3.27 은 알파드림(수협은행) 행의 금리다.
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
    """주의·비고의 역할 선언 — 저작 메모(authoring)가 직원 답변에 새지 않는가.

    회귀 대상: 비고·⚠ 유의가 역할 구분 없는 한 덩이라, "화면번호안내PDF 미수록 → 관계
    확인 필요" 같은 지식베이스 검증 메모가 화면·채널 비고와 절차 표시(notices)로 직원
    답변에 그대로 나갔다(§12 지워진 gap 17). 역할은 데이터가 선언하고(build_kb + config
    예외표) 소비 코드는 선언만 본다 — guard 의 문자열 휴리스틱(_AUTHORING)은 지웠다.
    """
    from pension_agent.consult_agent.evidence import guard as GD
    from pension_agent.knowledge.kb import ROLE_FIELDS, role_texts
    from pension_agent.consult_agent.evidence import procedure_qa, segment_qa
    from pension_agent.consult_agent.state import KB

    ok = 0
    with_field = [c for c in KB.cards
                  if ROLE_FIELDS.get(c["_kind"]) and c.get(ROLE_FIELDS[c["_kind"]])]

    # ① 역할 선언이 전부 채워져 있다 — 선언 없는 항목은 어느 역할로도 안 세서 조용히 빠진다.
    undeclared = [c["id"] for c in with_field
                  for e in c[ROLE_FIELDS[c["_kind"]]]
                  if not isinstance(e, dict)
                  or e.get("role") not in ("caution", "info", "authoring")]
    hit = bool(with_field) and not undeclared
    print(f"{'✓' if hit else '✗'} 주의·비고 항목 전부에 역할이 선언돼 있다"
          f"({len(with_field)}카드)" + ("" if hit else f" — 누락 {undeclared[:3]}"))
    ok += hit

    # ② authoring 텍스트가 직원용 렌더에 나가지 않는다 — 종류별 렌더 전부.
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

    # ③ 절차 표시(notices)에도 새지 않는다 — proc.001 의 ⚠ 유의는 "필자 해석" 메모다.
    from pension_agent.consult_agent.evidence import procedure_qa as PQ
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

    # ④ caution 은 표시로 나간다 — 역할을 나눈 목적은 진짜 주의를 살리는 것이다.
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

    # ⑤ 가드는 method 의 caution 역할만 쓰고, 문자열 휴리스틱은 남아 있지 않다.
    gkb_cards = [c for c in KB.cards if c["_kind"] == "method"]
    m010 = next(c for c in gkb_cards if c["id"] == "m.010")
    hit = (role_texts(m010.get("cautions"), "authoring")
           and not GD._texts(m010)
           # 정의가 없어야 한다 — 주석의 언급("예전에는 …")까지 막지는 않는다.
           and "_AUTHORING =" not in pathlib.Path(GD.__file__).read_text(encoding="utf-8"))
    hit = bool(hit)
    print(f"{'✓' if hit else '✗'} 가드가 역할 선언만 본다(_AUTHORING 휴리스틱 삭제)")
    ok += hit
    return ok


def check_account_state() -> int:
    """계좌 상태 재료(§3) — «정상»인 항목을 물었을 때 답이 없던 자리.

    화면(①~⑨)은 «왜 이 고객이 관리 대상인가»를 보여주는 자리라 요건이 성립한 항목만
    렌더한다. 그게 맞다 — 한 장짜리 브리핑이다. 그런데 대화형은 같은 재료로 직원이 묻는
    아무 질문에나 답하므로, 그 필터가 그대로 넘어오면 **부정 확인만 되고 긍정 확인이
    안 된다**: "디폴트옵션 설정돼 있어?" 가 미설정 고객에게만 답해지고, 설정된 고객에게는
    "준비된 자료가 없어요" 가 나갔다 — 정확히 "네, 돼 있습니다" 라고 답해야 하는 자리에서.

    값이 없어서가 아니었다. 전부 Profile 에 있었고, 렌더 경로만 걸러냈다. 그래서 이 테스트는
    **로스터 전원**에 대해 재료가 있는지 본다 — 한 명이라도 빠지면 그 상태의 고객이 답을 못
    받는 것이고, 그게 원래 증상이었다(고치기 전 0~3/9).

    **부담금별 구성은 «렌더 경로»가 아니라 한 단계 앞에서 끊겨 있었다**(2026-09-07). 원장
    06_PENSION 에 `퇴직급여금액`·`개인부담금금액` 이 있는데 `Profile` 이 접지 않아 재료까지
    오지 못했고, 그래서 수수료율표(fact.k04.f50)가 갈리는 축을 대화형이 직원에게 되물었다
    (기준서 §12 지워진 gap 30). 같은 줄에서 함께 재는 이유는 증상이 하나이기 때문이다 —
    «원장에는 있는데 답을 못 한다».
    """
    ok = 0
    from pension_agent.strategy_agent import customer as CUST

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

    # 부담금 재원 구성은 **원장 두 컬럼을 옮긴 값**이라, 재료의 금액이 원장과 같아야 한다.
    # 그리고 그 값을 인용한 답변이 통과해야 되묻기가 실제로 줄어든다 — 재료에 실렸는데
    # 검증기가 자르면 gap 30 이 이름만 바뀐 채 남는다.
    split = next((p for p in CUST.PERSONAS if p.severance_amt and p.own_contrib_amt), None)
    only_one = next((p for p in CUST.PERSONAS if not p.severance_amt), None)
    hit = split is not None and only_one is not None
    print(f"{'✓' if hit else '✗'} 두 부담금이 섞인 고객과 한쪽뿐인 고객이 로스터에 다 있다")
    ok += hit
    if split is not None:
        ev = tools.TOOLS["customer"].run({"customer_id": split.id}, "수수료 얼마야?")
        from pension_agent.strategy_agent.engine.text import won  # noqa: PLC0415
        said = (f"이 고객 적립금은 사용자부담금(퇴직급여) {won(split.severance_amt)}, "
                f"가입자부담금(개인부담금) {won(split.own_contrib_amt)}이에요.")
        hit = _vt(said, (ev or {}).get("allow") or [])[0]
        print(f"{'✓' if hit else '✗'} 두 부담금 금액을 그대로 인용한 답변이 통과한다")
        ok += hit
        # 경계는 넓어지지 않았다 — 원장에 없는 금액은 여전히 막힌다.
        wrong = f"사용자부담금은 {won(split.severance_amt + 7_777_000)}이에요."
        hit = not _vt(wrong, (ev or {}).get("allow") or [])[0]
        print(f"{'✓' if hit else '✗'} 원장에 없는 부담금 금액은 막힌다")
        ok += hit

    # 값이 «정상»인 쪽도 말할 수 있어야 한다. 미설정만 실리던 것이 원래 증상이라, 설정된
    # 고객에서 그 값이 나오는지를 따로 본다.
    setted = [p for p in CUST.PERSONAS if p.dopt == "설정"]
    hit = bool(setted) and all("디폴트옵션 설정" in texts[p.id] for p in setted)
    print(f"{'✓' if hit else '✗'} 디폴트옵션이 «설정»된 고객도 그 사실을 재료로 갖는다 ({len(setted)}명)")
    ok += hit

    # 없는 것도 «없음»이라고 말할 수 있어야 한다 — 침묵과 부재는 다르다.
    clean = [p for p in CUST.PERSONAS if not any(h.get("discontinued") for h in p.holdings)]
    hit = bool(clean) and all("판매중단 보유상품 없음" in texts[p.id] for p in clean)
    print(f"{'✓' if hit else '✗'} 판매중단 상품이 없는 고객은 «없음»을 재료로 갖는다 ({len(clean)}명)")
    ok += hit

    # 화면은 건드리지 않았다 — 계좌 상태는 briefing(화면 요건)이 아니라 별도 키다.
    from pension_agent.strategy_agent import agent as SA
    facts = SA.propose(CUST.PERSONAS[0])["facts"]
    hit = ("account_state" in facts
           and not (set(facts["account_state"]) & set(facts["briefing"]))
           and not (set(facts["account_state"]) & set(facts["customer"])))
    print(f"{'✓' if hit else '✗'} 계좌 상태는 화면(briefing·상단)이 아니라 대화형 재료다")
    ok += hit

    # 가입일은 **날짜로** 싣는다. 경과연수만 주면 LLM 이 오늘에서 빼서 날짜를 만들어 말한다.
    p0 = CUST.PERSONAS[0]
    ev = tools.TOOLS["customer"].run({"customer_id": p0.id}, "언제 가입했어?")
    hit = bool(p0.joined) and p0.joined in (ev or {}).get("text", "")
    print(f"{'✓' if hit else '✗'} 가입일이 경과연수가 아니라 날짜로 실린다")
    ok += hit

    # 실린 값은 인용할 수 있고, 안 실린 날짜는 여전히 막힌다(경계는 넓어지지 않았다).
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
    """오늘 날짜 재료(§3) — 시점·기한이 걸린 질문에 답이 없던 자리.

    작성 규약이 «재료에 없는 값은 계산해서 만들어내지 않는다(날짜·차액·비율 전부)»라,
    오늘이 며칠인지가 재료에 없으면 "연말까지 며칠 남았다"를 **말할 수가 없다**. 세액공제는
    연말이 마감이라 그 문장이 상담의 알맹이인데도 그랬다. 고칠 방향은 규약을 푸는 게 아니라
    (풀면 LLM 의 학습 시점 감각이 그 자리를 채운다) 코드가 오늘을 재료로 싣는 것이다.

    여기서 재는 것 셋:
      ① 도구가 능력 목록에 있고 고객 화면과 무관하게 항상 쓸 수 있는가
      ② 재료가 오늘 날짜와 «두 가지 세는 법»을 함께 밝히는가 — 하나만 실으면 126 인지
         127 인지 분간되지 않아 하루짜리 오안내가 된다
      ③ 그 수치가 검증기를 통과하는가 — 원장에 없으면 답변에서 잘려 나간다
    """
    ok = 0
    from datetime import date

    import tests
    from pension_agent.strategy_agent import customer as CUST

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

    # 원장 기준일은 고객 화면이 열려 있을 때만. 닫혀 있으면 어느 고객의 원장인지가 없다.
    # 날짜값이 아니라 **줄**로 본다 — 테스트는 오늘을 AS_OF 로 고정한 채 돌아서 두 날짜가
    # 같은 문자열이고, 값으로 비교하면 "닫혀 있어도 실려 있다"로 잘못 읽힌다.
    opened = tools.TOOLS["date"].run({"customer_id": "198734-1205842"}, "오늘 며칠이야")
    label = "고객 계좌 원장 기준일"
    hit = (label in (opened or {}).get("text", "") and label not in text
           and CUST.AS_OF.isoformat() in (opened or {}).get("text", ""))
    print(f"{'✓' if hit else '✗'} 원장 스냅샷 기준일은 고객 화면이 열렸을 때만 함께 싣는다")
    ok += hit

    # ③ 답변이 그 수치를 써도 검증기가 자르지 않는가. 재료로 싣는 목적이 이것이다.
    allow = (ev or {}).get("allow") or []
    hit = verify_texts(f"올해가 {left}일 남았으니 연내 납입해야 세액공제를 받으세요.", allow)[0]
    print(f"{'✓' if hit else '✗'} 답변이 그 잔여일수를 써도 검증기가 자르지 않는다")
    ok += hit

    # 반대로 재료에 없는 날짜 수치는 여전히 잘린다 — 재료를 실었다고 경계가 넓어지면 안 된다.
    hit = not verify_texts(f"올해가 {left + 40}일 남았어요.", allow)[0]
    print(f"{'✓' if hit else '✗'} 재료 밖 잔여일수는 그대로 잘린다(경계는 넓어지지 않았다)")
    ok += hit
    return ok


def check_history_material() -> int:
    """상담 이력 재료(§3) — "지난번에 무슨 얘기 했지"가 답이 없던 자리.

    회귀 대상 셋이 한 턴에 얽혀 있었다.

      ① 기록은 매 턴 쌓이는데(graph.ask → session_store) 읽는 **도구가 없었다.** 능력
         표면은 도구 목록이라(§3) 없는 도구는 없는 능력이고, 그 질문은 "제가 도와드릴 수
         있는 것" 안내로 끝났다.
      ② 다음 턴(자동이체 화면번호) 답변에 앞 턴의 미답이 "이전 대화 내용은 기억하지
         못해요"로 따라 나왔다 — 사실도 아니었다. 재료가 없으면 LLM 은 없는 재료에 대해
         말을 만든다. 그래서 재료를 주고, 이전 대화의 쓰임을 프롬프트가 못박는다.
      ③ 그 답변의 근거 목록에는 질문과 무관한 수익률 관리 카드 4장이 '관련도 None' 으로
         서 있었다 — 고객 상태에 걸린 가드가 원장과 한 목록에 섞여서다(§8 · plan._sources).
    """
    import tempfile
    from pathlib import Path

    from pension_agent import session_store
    from pension_agent.consult_agent import prompts
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import meta
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = session_store.SESSION_DATA_DIR
        session_store.SESSION_DATA_DIR = Path(tmp)
        try:
            # 과거 상담 1건 + 에이전트와 나눈 대화 1세션. 시효 표시는 **과거 상담이
            # 실렸을 때** 붙는 것이라 이 픽스처에 record 가 있어야 그 검사가 성립한다.
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
        and found["sources"][0]["id"] == "session.CX"
    print(f"{'✓' if hit else '✗'} 지난 상담 기록이 재료로 올라온다")
    ok += hit

    # 지난 답변을 통째로 실으면 원장이 지난 상담의 문장으로 뒤덮인다 — 발췌만 싣는다.
    longest = max(len(x) for x in found["text"].splitlines()) if found else 0
    hit = bool(found) and longest <= tools.HISTORY_EXCERPT + 20 and "…" in found["text"]
    print(f"{'✓' if hit else '✗'} 에이전트 답변은 발췌만 싣는다(가장 긴 줄 {longest}자)")
    ok += hit

    # 기록은 "그때 무슨 얘기를 했나"의 근거이지 현재 기준 값의 근거가 아니다.
    hit = bool(found) and tools.HISTORY_MARK in found["notices"]
    print(f"{'✓' if hit else '✗'} 시효 표시를 재료가 달고 나온다(빠지면 코드가 채운다)")
    ok += hit

    # 고객 화면이 닫혀 있으면 어느 고객인지가 없다 — «확인하지 못함»이라 None 이고, 계획은
    # 다른 도구를 써 볼 여지가 남는다.
    hit = closed is None
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혔으면 지어내지 않는다")
    ok += hit

    # **기록 0건은 «확인한 값»이라 재료다**(2026-09-02). None 이던 동안 이것이 «질의가
    # 빗나감»과 구별되지 않아, 계획이 재계획으로 `customer` 를 끌어와 브리핑 한 편을
    # 원장에 싣고 질문과 무관한 ⑥⑦⑧ 화법 카드를 «근거»로 세웠다. 시효 표시는 붙지
    # 않는다 — 낡을 값 자체가 없다.
    hit = (bool(unseen) and tools.HISTORY_NONE in unseen["text"]
           and tools.HISTORY_MARK not in unseen["notices"])
    print(f"{'✓' if hit else '✗'} 기록 0건도 재료로 올라온다(없다고 답할 근거)")
    ok += hit

    hit = "history" not in tools.catalog({}) and "history" in tools.catalog({"customer_id": "CX"})
    print(f"{'✓' if hit else '✗'} 못 쓰는 도구는 계획에 보여주지 않는다")
    ok += hit

    hit = "history" in CL._NO_BRANCH and "history" in prompts.ANSWER_SHAPES
    print(f"{'✓' if hit else '✗'} 상담 기록에는 갈래가 없고, 답의 형태 요구는 등록돼 있다")
    ok += hit

    # ② 라우팅 기준과 작성 기준. 두 문장이 없으면 같은 증상이 그대로 돌아온다.
    hit = "지난 상담에서 무슨 얘기를 했는지 묻는 것도" in prompts.ROUTE_PROMPT
    print(f"{'✓' if hit else '✗'} 지난 상담을 묻는 질문이 agent_help 로 새지 않는다")
    ok += hit

    hit = "이전 대화는 이번 질문을 해석하는 데만 쓴다" in prompts.COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 앞 턴의 미답을 이번 답변에서 사과하지 않는다")
    ok += hit

    opened = meta.agent_help({"question": "뭘 도와줄 수 있어?", "customer_id": "CX"})["answer"]
    shut = meta.agent_help({"question": "뭘 도와줄 수 있어?"})["answer"]
    hit = "지난 상담 기록" in opened and "지난 상담 기록" not in shut \
        and "단말 화면번호" in opened
    print(f"{'✓' if hit else '✗'} 도울 수 있는 것 안내가 실제 능력과 같다(화면번호·채널·상담 기록)")
    ok += hit

    # ③ 답이 나온 재료와 표현을 제한한 재료를 갈라 싣는다.
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

    # 관련도는 검색으로 온 재료에만 있다. 화면은 이 값이 None 이면 그 칸을 아예 안 찍는다.
    hit = all(s.get("score") is None for s in caution) and ground[0]["score"] == 2.0
    print(f"{'✓' if hit else '✗'} 검색으로 오지 않은 재료에는 관련도가 없다")
    ok += hit
    return ok


def check_history_selection() -> int:
    """상담 이력의 선별 — 질의 반영·과거/오늘 분리·추천 칩(칩+검색 확장).

    회귀 대상 셋.

      ① `_history` 가 `query` 를 버리던 것. 계획 루프가 "어떤 질의로 부를지"를 정하는데
         (§2) 도구가 그 질의를 안 읽으면 그 절반이 껍데기다 — 무슨 질문이든 같은 최신순
         덤프가 나갔다. 이제 질의어가 걸리는 과거 상담을 앞세운다(순서만 — 걸러내면
         표현이 다른 기록을 없다고 답하게 된다).
      ② 과거 상담(record)과 에이전트 대화(user/agent)가 한 최신순 창을 쓰던 것. 시연 중
         대화 몇 턴이면(graph.ask 가 매 턴 2턴 append) "지난번"이 창 밖으로 밀렸다.
         예산을 갈라 과거 상담은 항상 실린다.
      ③ 추천 칩(suggest.history_chips) — 기록이 있는 고객에게만, 코드 조립로만 뜬다.
         LLM 이 칩을 쓰면 기록에 없는 내용이 질문에 실려 들어온다.
    """
    import tempfile
    from pathlib import Path

    from pension_agent import session_store
    from pension_agent.consult_agent.effects import suggest

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = session_store.SESSION_DATA_DIR
        session_store.SESSION_DATA_DIR = Path(tmp)
        try:
            # 과거 상담 2건(최신=ETF, 과거=수수료) + 대화 세션 3개 — 옛 로직이면
            # 대화 3세션이 최신순 창(3)을 다 차지해 record 가 밀린다.
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

            # 과거 상담이 없고 오늘 대화만 있는 고객 — 시효 표시가 붙으면 안 된다.
            session_store.append_turn("CZ", "chat-0", {
                "role": "user", "text": "평가금액 얼마야?", "ts": "2026-08-24T09:00:00Z"})
            session_store.append_turn("CZ", "chat-0", {
                "role": "agent", "text": "1억 2,000만원입니다", "ts": "2026-08-24T09:00:05Z"})
            today_only = tools.run("history", {"customer_id": "CZ"}, "오늘 무슨 얘기 했지")
        finally:
            session_store.SESSION_DATA_DIR = orig_dir

    # ② 대화가 아무리 쌓여도 과거 상담은 실린다 — 그리고 구획이 갈라져 있다.
    hit = bool(plain) and "수수료 부담 문의" in plain["text"] and "ETF 거래" in plain["text"] \
        and plain["text"].index("[과거 상담 기록]") < plain["text"].index("[에이전트와 나눈 최근 대화]")
    print(f"{'✓' if hit else '✗'} 오늘 대화가 쌓여도 과거 상담이 밀리지 않는다(구획 분리)")
    ok += hit

    # 대화 세션은 최근 1개만 — 원장이 오늘 발화로 뒤덮이지 않게.
    hit = bool(plain) and plain["text"].count("오늘 질문") == 1
    print(f"{'✓' if hit else '✗'} 에이전트 대화는 최근 {tools.HISTORY_DIALOG_SESSIONS}세션만 싣는다")
    ok += hit

    # ① 질의어가 걸린 상담(수수료·5/1)이 최신(ETF·7/1)보다 앞선다. 걸러내지는 않는다.
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

    # 시효 표시는 과거 상담이 실렸을 때만. 방금 나눈 대화에 "지난 상담 기록입니다"가
    # 붙으면 표시가 거짓말을 하고, 매번 붙는 표시는 정작 낡은 값이 실린 턴에서 안 읽힌다.
    hit = bool(today_only) and today_only["notices"] == [] \
        and "[과거 상담 기록]" not in today_only["text"]
    print(f"{'✓' if hit else '✗'} 오늘 대화만 있으면 시효 표시를 달지 않는다")
    ok += hit
    hit = bool(plain) and tools.HISTORY_MARK in plain["notices"]
    print(f"{'✓' if hit else '✗'} 과거 상담이 실리면 시효 표시를 단다")
    ok += hit
    return ok


def check_market_material() -> int:
    """시황·상품 기반지식(05 폴더)이 답변 재료로 닿는가.

    회귀 대상: 05_시황_상품_기반지식 5개 문서는 「상담 시 근거로 인용할 시장·상품 데이터」
    라고 폴더가 스스로 규정하고 문서마다 검색용 front-matter(trigger_keywords·key_points·
    as_of)까지 갖춰 저작돼 있었는데, **변환기에 경로가 없어** 에이전트에게는 통째로 없는
    재료였다(knowledge/CLAUDE.md 적재 감사 — 원문 폴더 중 유일하게 ❌ 였던 자리).
    "8월 추천펀드 뭐야"·"디폴트옵션 알파드림 구성"에 답할 재료가 저장소에 있는데도
    "찾지 못했습니다"로 끝났다 — screen 표A 88행이 빠져 있던 것과 같은 유형이다.

    이 재료가 다른 것과 갈리는 지점은 **시효**다(CLAUDE.md §9). 제도 확정값과 달리 시황
    수치는 주·월 단위로 낡으므로, 기준시점과 원문의 시효 경고가 답변에 함께 나가야 한다.
    """
    from pension_agent.consult_agent.evidence import marks as MARKS
    from pension_agent.consult_agent.evidence import relations as REL
    from pension_agent.consult_agent.evidence.kb_index import buckets
    from pension_agent.consult_agent.prompts import ANSWER_SHAPES
    from pension_agent.consult_agent.state import KB

    ok = 0
    # market(시황) · lineup(운용 상품) 두 종류다. 05 한 폴더에서 나오지만 **묻는 것이
    # 달라** 갈라 놨다 — screen(직원이 단말에서)·channel(고객이 앱에서)과 같은 이유다.
    cards = [c for c in KB.cards if c["_kind"] in ("market", "lineup")]

    hit = len(cards) >= 20
    print(f"{'✓' if hit else '✗'} 시황·상품 기반지식이 적재된다 ({len(cards)}장)")
    ok += hit

    # 두 갈래가 다 들어와야 하고, **갈래와 종류가 어긋나면 안 된다** — 상품 문서가 market
    # 으로 들어가면 「추천펀드」를 물었을 때 시황 도구가 그걸 들고 있게 된다.
    pairs = {(c["_kind"], c["category"]) for c in cards}
    hit = pairs == {("market", "시황"), ("lineup", "상품")}
    print(f"{'✓' if hit else '✗'} 시황→market · 상품→lineup 으로 갈라 적재된다 ({sorted(pairs)})")
    ok += hit

    # 도구·버킷도 함께 갈려야 라우팅이 쉬워진다. 종류만 나누고 도구를 하나로 두면 계획 LLM
    # 은 여전히 도구 하나로 둘을 다 받는다(이 분리의 목적이 그것이다).
    hit = ("market" in tools.TOOLS and "lineup" in tools.TOOLS
           and tools.TOOLS["market"].desc != tools.TOOLS["lineup"].desc)
    print(f"{'✓' if hit else '✗'} 도구가 둘로 갈리고 설명이 서로 다르다")
    ok += hit

    letters = {b["kind"]: code[0] for code, b in buckets(KB).items()
               if b["kind"] in ("market", "lineup")}
    hit = len(letters) == 2 and letters["market"] != letters["lineup"]
    print(f"{'✓' if hit else '✗'} 버킷 카탈로그에서도 갈린다 ({letters})")
    ok += hit

    # 기준시점 없는 시황·상품 수치는 인용 불가다(폴더 README 수록 규칙) — 필수로 잡는다.
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

    # 시효 표시는 **데이터가 정한다** — 폴더 README 의 ※ 안내(volatile)와 카드의 as_of 다.
    # 코드 상수로 붙이면 원문이 바뀔 때 두 곳이 갈린다(§12 지워진 gap 16·18 과 같은 자리).
    hit = bool(found) and any("빠르게 달라집니다" in n for n in (found.get("notices") or []))
    print(f"{'✓' if hit else '✗'} 시장·상품이 달라질 수 있다는 원문 경고가 함께 나간다")
    ok += hit

    sample = next((c for c in cards if c.get("volatile")), None)
    hit = bool(sample) and sample["volatile"] in tools.stale_mark(sample) \
        and sample["as_of"] in tools.stale_mark(sample)
    print(f"{'✓' if hit else '✗'} 경고 문구와 기준시점을 원문에서 읽어 온다")
    ok += hit

    # 필드 이름을 코드표기로 인용한 원문(`as_of`)이 밑줄 제거로 깨지지 않는가 —
    # 깨지면 직원이 존재하지 않는 필드를 찾게 된다.
    hit = bool(sample) and "asof" not in sample["volatile"]
    print(f"{'✓' if hit else '✗'} 원문의 필드 이름 표기가 깨지지 않는다")
    ok += hit

    # 행내한 자료는 고객에게 그대로 못 준다 — 원문 confidentiality 선언에서 온다.
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

    # 원문(content)은 고치지 않는다 — 표의 값이 그대로 실려 있어야 인용이 성립한다.
    tdf = next((c for c in cards if "TDF" in c["title"]), None)
    hit = bool(tdf) and "Glide-Path" in (tdf.get("content") or "")
    print(f"{'✓' if hit else '✗'} 절 본문이 원문 그대로 실린다")
    ok += hit

    # 절 카드는 자기 문서의 개요 카드를 부모로 갖는다 — 어느 회차 자료인지가 카드에 남는다.
    sections = [c for c in cards if c.get("parent")]
    ids = {c["id"] for c in cards}
    hit = bool(sections) and all(c["parent"] in ids for c in sections)
    print(f"{'✓' if hit else '✗'} 절 카드가 개요 카드를 부모로 가리킨다 ({len(sections)}장)")
    ok += hit

    # 저작·검수 기록(추출 노트)은 카드가 아니다 — 직원 답변 재료가 아니라 저작 메모다.
    hit = not any("추출 노트" in c["title"] for c in cards)
    print(f"{'✓' if hit else '✗'} 추출 노트·목차는 카드로 만들지 않는다")
    ok += hit

    # 한 글자 키워드는 검색 예시에서 빠진다 — 「금」은 거의 모든 절에 걸려 갈래를 못 가른다.
    one_char = [(c["id"], t) for c in cards for t in (c.get("trigger_examples") or [])
                if len(t.strip()) < 2]
    hit = not one_char
    print(f"{'✓' if hit else '✗'} 한 글자 검색 키워드를 달지 않는다"
          + ("" if hit else f" — {one_char[:3]}"))
    ok += hit

    # 새 종류를 적재하면 함께 손대야 하는 자리들 — 빠지면 "적재는 됐는데 검색되지 않는다".
    hit = all(k in tools.TOOLS and k in ANSWER_SHAPES for k in ("market", "lineup"))
    print(f"{'✓' if hit else '✗'} 도구·답변 형태 요구에 등록됨")
    ok += hit

    bucketed = {c["id"] for b in buckets(KB).values() for c in b["cards"]}
    hit = all(c["id"] in bucketed for c in cards)
    print(f"{'✓' if hit else '✗'} 버킷 카탈로그에 들어간다(LLM 후보 목록에 보인다)")
    ok += hit

    # ── 표를 관계로 선언했는가 (knowledge/CLAUDE.md §1) ──────────────
    #
    # 05 문서의 알맹이는 산문이 아니라 표다. 표를 텍스트 덩어리로만 실으면 두 가지가 같이
    # 막힌다 — 검색 입구가 없고(「1975년생이면 TDF 몇 년」의 답이 표에 있는데 못 찾았다),
    # 값–조건 오짝을 잡을 재료가 없다(「알파드림 금리 3.40」은 지켜드림의 값인데 통과했다).
    tabled = [c for c in cards if c.get("tables")]
    hit = len(tabled) >= 8
    print(f"{'✓' if hit else '✗'} 표가 행 단위 관계로 선언된다 ({len(tabled)}장)")
    ok += hit

    deck = next((c for c in cards if c["id"].endswith("추천펀드_2026-08.01")), None)
    rows = (deck or {}).get("tables", [{}])[0].get("rows") or []
    # 병합 셀(합계 행)은 위 행에서 이름을 이어받는다 — 안 이어받으면 「알파드림 포트폴리오
    # 수익률 4.23」이라는 **맞는 답변**이 남의 값으로 몰려 막힌다.
    hit = any(r["keys"][:2] == ["저위험", "알파드림"] and "100" in r["values"] for r in rows)
    print(f"{'✓' if hit else '✗'} 병합 셀 합계 행이 상품 이름을 이어받는다")
    ok += hit

    # 행을 못 가리는 이름(「포트폴리오」는 모든 상품 밑에 달려 있다)은 행 이름이 아니다.
    hit = not any("포트폴리오" in (r.get("keys") or []) for r in rows)
    print(f"{'✓' if hit else '✗'} 행을 못 가리는 이름은 행 이름으로 쓰지 않는다")
    ok += hit

    hit = REL.declared(deck or {})
    print(f"{'✓' if hit else '✗'} 표를 선언한 카드가 관계 검사 대상이 된다")
    ok += hit

    # 표에서 나온 검색 입구 — 열 머리말(1975년)과 행 이름(알파드림 III)이 둘 다 있어야 한다.
    tdf = next((c for c in cards if c["title"] == "TDF 포트폴리오"), None)
    hit = bool(tdf) and "1975년" in (tdf.get("trigger_examples") or [])
    print(f"{'✓' if hit else '✗'} 표의 열 머리말이 검색 입구가 된다 — 1975년")
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

    # ── 검색이 «답을 가진 카드»에 닿는가 ───────────────────────────
    #
    # 셋 다 실측으로 잡은 자리다. 재료는 적재돼 있는데 순위가 엉켜서 «상품을 물으면 잘 못
    # 찾는다»가 됐다 — 적재와 검색은 다른 문제라는 것을 이 검사가 지킨다.

    # ① category 를 topics 에 넣지 않는다. 「상품」·「시황」은 두 글자 흔한 말이라, 질문에
    #    "구성상품"·"편입상품"처럼 그 글자가 들어가면 **모든 카드가 똑같이** 가산점을 받아
    #    무더기 동점이 되고 순위가 사실상 id 사전순이 된다(config.TOPIC_VOCAB 머리말이
    #    금지한 그것). 갈래는 category 필드가 이미 들고 있다.
    polluted = [c["id"] for c in cards
                if {"상품", "시황"} & set(c["tags"].get("topics") or [])]
    hit = not polluted
    print(f"{'✓' if hit else '✗'} category 를 검색 태그에 섞지 않는다"
          + ("" if hit else f" — {polluted[:3]}"))
    ok += hit

    # ② 같은 문서의 절이 걸리면 개요 카드는 자리를 비켜준다. 개요는 문서 키워드를 통째로
    #    들고 있어 어떤 질문에나 걸리는데, **답이 든 표는 절에 있다**.
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

    # ③ 「+65,469억원」을 이름 칸으로 읽지 않는다. 읽으면 그 열이 이름 열이 되어 값 열이
    #    하나도 안 남고, **표가 통째로 버려진다** — 자금 동향 표가 그렇게 빠져서 「코스피
    #    얼마야」가 검색되지 않았다.
    flows = next((c for c in cards if c["title"].endswith("주간 자금 동향")), None)
    hit = bool(flows) and "코스피" in (flows.get("trigger_examples") or [])
    print(f"{'✓' if hit else '✗'} 수치에 한국어 단위가 붙어도 값으로 읽는다 — 코스피 행")
    ok += hit

    # 날짜는 반대다 — 일정표에서 「20일」은 값이 아니라 행 이름이다.
    sched = [t for c in cards for t in (c.get("tables") or [])
             if "일자" in (t.get("columns") or [])]
    hit = bool(sched) and any("20일" in (r.get("keys") or [])
                              for t in sched for r in t["rows"])
    print(f"{'✓' if hit else '✗'} 일정표의 날짜는 행 이름으로 남는다 — 20일")
    ok += hit

    # ── 오짝 판정: 막아야 할 것과 **막으면 안 되는 것** ─────────────
    #
    # 뒤쪽이 더 중요하다 — 검증기가 옳은 문장을 거부하는 것은 틀린 문장을 통과시키는 것보다
    # 나쁘다(relations.py 머리말). 그래서 맞는 답변 쪽을 더 많이 건다.
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
    """출처는 **원문 문서명**으로 말한다 — 적재 json 의 이름표가 새어나가면 안 된다.

    회귀 대상: 예전에는 원천 문서를 못 찾으면 적재 파일의 meta.title("영업 화법 — 06/03
    영업화법")로 물러서거나 출처 줄을 통째로 생략했다. 앞은 사내 파일명을 출처라고
    말하는 것이고, 뒤는 행원이 고객에게 옮길 수 없는 답을 주는 것이다.
    """
    from pension_agent.knowledge.kb import origin_of
    from pension_agent.consult_agent.evidence.kb_index import sources_of
    from pension_agent.consult_agent.evidence import facts_qa
    from pension_agent.consult_agent.state import KB

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

    # 출처 터미널 표기는 공용 함수 하나다(tools.source_lines). 운영 CLI 와 디버그 실행기가
    # 표기를 각자 복사해 갖고 있던 동안, URL 을 싣는 변경이 운영 CLI 에만 적용되고 디버그
    # 화면($CAD·$CADR)에는 빠졌다 — 한쪽만 고쳐지는 사고의 재발을 여기서 막는다.
    s_full = {"id": "x.1", "title": "제목", "doc": "문서", "score": 1.0, "url": "https://u"}
    s_bare = {"id": "x.2", "title": "제목", "doc": "문서"}   # 검색으로 오지 않은 재료
    full, bare = tools.source_lines(s_full), tools.source_lines(s_bare)
    compact = tools.source_lines(s_full, compact=True)
    hit = (full[-1] == "     ↗ https://u" and "관련도 1.0" in full[1]
           and "관련도" not in "".join(bare) and "↗" not in "".join(bare)
           and len(compact) == 2 and compact[0].startswith("   · 문서 — 제목 [x.1]"))
    print(f"{'✓' if hit else '✗'} source_lines — URL·관련도는 있을 때만, compact 는 한 줄")
    ok += hit

    # 네 진입점이 전부 그 함수에 닿는가. 운영 CLI 와 행내 API 는 «답변 + 출처 블록»을
    # 통째로 텍스트로 펴야 해서 `render.sources_block` 을 경유하고, 그 안에서 source_lines
    # 를 부른다 — 경유가 하나 늘었을 뿐 표기를 정하는 함수는 여전히 하나다. 그 경유까지
    # 따라가서 본다(중간에 표기를 복사해 갖는 순간 이 검사가 깨진다).
    # 운영 CLI 는 모듈 최상위에서 REPL 이 돌아 **임포트하면 안 되므로**(스크립트다)
    # 파일 텍스트로 확인한다. main.py 도 uvicorn 이 부르는 진입점이라 같게 다룬다.
    import inspect

    from pension_agent.consult_agent.effects import render
    from tests.debug import __main__ as dbg_main
    from tests.debug import reps as dbg_reps
    # 경로를 되짚지 않고 config 에서 받는다(루트 CLAUDE.md 규칙 4).
    from pension_agent import config as _config
    ops_src = (_config.PACKAGE_ROOT / "consult_agent/__main__.py").read_text(encoding="utf-8")
    api_src = (_config.SRC_ROOT / "main.py").read_text(encoding="utf-8")
    hit = ("source_lines" in inspect.getsource(render)
           and "render.sources_block" in ops_src
           and "render.sources_block" in api_src
           and "source_lines" in inspect.getsource(dbg_main._print_source)
           and "source_lines" in inspect.getsource(dbg_reps._print_source_line))
    print(f"{'✓' if hit else '✗'} 운영 CLI·행내 API·$CAD·$CADR 이 같은 출처 표기 함수에 닿는다")
    ok += hit

    # 카드가 밝힌 원천 문서(source.doc)가 레지스트리로 이어져 문서명으로 나온다.
    # 적재 파일(06/03 영업화법의 변환본)이 아니라 그 앞의 행내 PDF 이름이어야 한다.
    card = next((c for c in KB.cards if c["id"] == "pitch.k03.001"), None)
    origin = origin_of(KB, card) if card else ""
    hit = "연금왕" in origin and "06/" not in origin
    print(f"{'✓' if hit else '✗'} 카드가 밝힌 원천 문서가 문서명으로 해석됨 — {origin[:48]}")
    ok += hit

    # 답변에 붙는 근거 목록에 원문 출처가 함께 실린다(화면·CLI 가 이걸 읽어준다).
    hits = facts_qa.search("세액공제 한도")
    srcs = sources_of(KB, hits)
    hit = bool(srcs) and all(s.get("doc") for s in srcs)
    print(f"{'✓' if hit else '✗'} sources_of 가 근거마다 원문 출처(doc)를 함께 돌려줌")
    ok += hit

    return ok
