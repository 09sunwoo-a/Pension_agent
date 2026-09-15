"""카드 색인 — 팩트 색인 · 계층 인덱스(L0/L1) · 종류별 도달 · 검색 입구 · 도구 설명 축.

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

from pension_agent.consult_agent import select, tools

from tests.consult._common import print, _REAL_LLM_PICK  # noqa: A001 — 집계용 print


def check_fact_in_index() -> int:
    """팩트가 카드 색인에 있는가 — LLM 카드 선택의 후보가 되는가(§3).

    팩트는 오래도록 «id 로 참조되는 값»이기만 해서(kinds.json `consumed: reference`) 카드
    색인 밖에 살았고, 그래서 **9종 재료 중 유일하게 LLM 카드 선택을 못 받았다**. 다른
    종류는 LLM 이 버킷→카드로 고르고 못 고를 때만 n-gram 으로 물러서는데(select.pick),
    팩트는 n-gram 하나뿐이라 직원 말과 카드 말이 다르면 통째로 0건이 났다 —
    "연말정산 얼마나 돌려받아?" 가 세액공제 카드를, "중도에 깨면 세금 얼마나 떼?" 가
    중도해지 카드를 못 찾았다. 하필 팩트는 한도·세율처럼 숫자를 묻는 재료다.

    여기서 재는 것은 **배선**이다(모델의 판단이 아니라). LLM 이 골랐을 때 그 팩트가 실제로
    돌아오는지, 그리고 못 골랐을 때 n-gram 이 예전 그대로인지.
    """
    ok = 0
    from pension_agent.consult_agent import kb_index
    from pension_agent.consult_agent.tools import facts_qa

    kb = tools.KB
    # 같은 객체로 두 자리에 산다 — 사본이면 한쪽만 고쳐지는 자리가 생긴다(화법과 같은 규약).
    f2 = kb.facts["fact.k04.f2"]
    hit = any(c is f2 for c in kb.cards) and sum(1 for c in kb.cards if c["_kind"] == "fact") == len(kb.facts)
    print(f"{'✓' if hit else '✗'} 팩트가 카드 색인에 **같은 객체로** 실린다 ({len(kb.facts)}장)")
    ok += hit

    # 버킷 카탈로그에 종류가 뜬다 — 여기 빠지면 LLM 후보에서 통째로 사라진다.
    cat = kb_index.index_catalog(kb, ("fact",))
    hit = cat.startswith("■ fact") and "납입·세액공제" in cat
    print(f"{'✓' if hit else '✗'} 팩트 버킷이 카탈로그에 뜬다")
    ok += hit

    # 슬라이스에 카드가 예상질문과 함께 실린다 — LLM 이 id 를 고를 재료다.
    sl = kb_index.index_slice(kb, ["X01"], kinds=("fact",))
    hit = "fact.k04.f2" in sl and "예상질문" in sl
    print(f"{'✓' if hit else '✗'} 팩트 슬라이스에 카드와 예상질문이 실린다")
    ok += hit

    # LLM 이 골랐을 때 그 팩트가 실제로 돌아오는가(배선 검증 — 캔드 응답).
    # 이 스위트는 전역에서 llm_pick 을 꺼 두므로(머리말) 여기서만 원본을 되살린다 —
    # check_hier_index 와 같은 방식이다. 모델 응답은 캔드로 고정한다.
    canned = iter(['["X01"]', '["fact.k04.f2"]'])
    real_gen, real_pick = select.generate, select.llm_pick
    select.generate = lambda prompt, **kw: next(canned)
    select.llm_pick = _REAL_LLM_PICK
    try:
        got = [h[1]["id"] for h in facts_qa.search("연말정산 얼마나 돌려받아?")]
    finally:
        select.generate, select.llm_pick = real_gen, real_pick
    hit = got[:1] == ["fact.k04.f2"]
    print(f"{'✓' if hit else '✗'} LLM 이 고른 팩트가 검색 결과로 돌아온다 {got[:1]}")
    ok += hit

    # 못 골랐을 때는 예전 n-gram 그대로다 — 넓히기만 하고 좁히지 않는다.
    hit = [h[1]["no"] for h in facts_qa.search("세액공제 한도")][:1] == ["F2"]
    print(f"{'✓' if hit else '✗'} LLM 이 못 고르면 n-gram 폴백이 예전대로 동작한다")
    ok += hit
    return ok


def check_hier_index() -> int:
    """계층 인덱스 — 버킷 카탈로그(L0) → 카드 슬라이스(L1).

    행내에서 쓸 수 있는 모델(gemma4-31b·dna3.0-35b)의 컨텍스트에 카드 목록을
    실을 수 있는지가 이 기능의 존재 이유다. 그래서 "예산이 실제 상한인가"와
    "버킷이 카드를 빠뜨리지 않는가"를 회귀로 잡는다.
    """
    from pension_agent.consult_agent import kb_index
    from pension_agent.knowledge import kb as K

    kb = K.load_kb()
    ok = 0

    # ① 버킷이 카드를 하나도 빠뜨리지 않는다.
    #    축을 tags.topics 로 잡으면 429장 중 69장이 어떤 버킷에도 안 들어가서
    #    영구히 검색되지 않는 카드가 생긴다 — group 축을 고른 이유가 이것이다.
    bk = kb_index.buckets(kb)
    covered = sum(len(b["cards"]) for b in bk.values())
    hit = covered == len(kb.cards)
    print(f"{'✓' if hit else '✗'} 버킷 커버리지 {covered}/{len(kb.cards)}장 (버킷 {len(bk)}개)")
    ok += hit

    # ② 카탈로그는 결정론적이다(같은 KB → 같은 문자열). 코드가 흔들리면 프롬프트가 흔들린다.
    hit = kb_index.index_catalog(kb) == kb_index.index_catalog(kb)
    print(f"{'✓' if hit else '✗'} 카탈로그 결정론")
    ok += hit

    # ③ L0 카탈로그가 작게 유지된다. **카드가 늘어도** 여기가 커지면 안 된다 — 그게 계층의
    #    목적이고, 카드 수는 버킷 줄의 "(N장)" 한 자리만 움직인다.
    #
    #    한도를 2000 → 2200 으로 올린 것은 카드가 아니라 **종류가 하나 늘어서**다(9종 → 10종.
    #    팩트가 색인 밖에 있다가 들어왔다). 종류 하나는 머리말 한 줄 + 버킷 줄 몇 개라
    #    약 160자를 쓴다. 카드가 늘어 넘치면 그건 이 테스트가 잡아야 하는 회귀가 맞고,
    #    종류가 늘어 넘치면 여기를 함께 고치는 것이 맞다 — 둘을 구분해 두려고 적는다.
    cat = kb_index.index_catalog(kb)
    hit = len(cat) <= 2200
    print(f"{'✓' if hit else '✗'} L0 카탈로그 {len(cat)}자 ≤ 2200 (종류 {len(kb_index._KIND_ORDER)})")
    ok += hit

    # ④ 예산은 실제 상한이다 — 헤더·생략안내까지 포함해서 절대 넘지 않는다.
    codes = list(bk)
    over = [b for b in (200, 500, 1000, 2500, 4000)
            if len(kb_index.index_slice(kb, codes, budget_chars=b)) > b]
    hit = not over
    print(f"{'✓' if hit else '✗'} 예산 상한 준수 (초과: {over or '없음'})")
    ok += hit

    # ⑤ 잘라냈으면 몇 장을 못 보여줬는지 밝힌다(조용히 자르지 않는다).
    tight = kb_index.index_slice(kb, codes, budget_chars=500)
    hit = "생략" in tight
    print(f"{'✓' if hit else '✗'} 절단 시 생략 사실 명시")
    ok += hit

    # ⑥ 버킷 하나는 기본 예산 안에 통째로 들어간다 = 2단으로 충분하다는 보장.
    worst = max(len(kb_index.index_slice(kb, [c])) for c in bk)
    hit = worst <= kb_index.INDEX_BUDGET_CHARS
    print(f"{'✓' if hit else '✗'} 최악 버킷 {worst}자 ≤ 기본예산 {kb_index.INDEX_BUDGET_CHARS}")
    ok += hit

    # ⑦ 목록에 없는 버킷 코드는 조회되지 않는다(안전장치 ②).
    hit = kb_index.index_slice(kb, ["ZZ99", "없는코드"]) == ""
    print(f"{'✓' if hit else '✗'} 없는 버킷 코드 → 빈 슬라이스")
    ok += hit

    # ⑧ llm_pick 이 버킷 → id 2단으로 돌고, 지어낸 id 는 걸러진다(안전장치 ③).
    real = tools.KB.pitches[0]["id"]
    calls: list[str] = []

    def stub_generate(prompt, **kw):
        calls.append(prompt)
        if "묶음 식별자" in prompt:      # 1차: 버킷 선택
            return '["P01", "ZZ99"]'
        return f'["{real}", "존재하지_않는_id"]'  # 2차: 카드 선택 + 지어낸 id

    orig = select.generate
    select.generate = stub_generate
    try:
        hits = _REAL_LLM_PICK(("pitch",), "아무 질문")
    finally:
        select.generate = orig
    hit = len(calls) == 2 and [c["id"] for _, c in hits] == [real]
    print(f"{'✓' if hit else '✗'} llm_pick 2단 호출({len(calls)}회) · 지어낸 id 차단")
    ok += hit

    # ⑨ 1차에서 버킷을 못 고르면 2차 호출을 하지 않는다(낭비 방지).
    calls.clear()
    select.generate = lambda prompt, **kw: (calls.append(prompt), "[]")[1]
    try:
        hits = _REAL_LLM_PICK(("pitch",), "아무 질문")
    finally:
        select.generate = orig
    hit = len(calls) == 1 and hits == []
    print(f"{'✓' if hit else '✗'} 버킷 0건 → 2차 호출 생략({len(calls)}회)")
    ok += hit

    # ⑩ 종류를 넓히면 그 종류 카드가 후보로 들어온다 — 화법 전용이 아니다.
    calls.clear()
    first_method = next(c["id"] for c in tools.KB.cards if c["_kind"] == "method")
    select.generate = lambda prompt, **kw: (
        calls.append(prompt),
        '["M01"]' if "묶음 식별자" in prompt else f'["{first_method}"]',
    )[1]
    try:
        hits = _REAL_LLM_PICK(("method",), "어떤 고객부터 관리해야 하나")
    finally:
        select.generate = orig
    hit = [c["id"] for _, c in hits] == [first_method]
    print(f"{'✓' if hit else '✗'} llm_pick 이 화법 아닌 종류(method)도 고른다")
    ok += hit

    return ok


def check_l0_skip() -> int:
    """전 카드 인덱스가 예산에 들어가는 종류는 버킷 선택(L0) 호출을 생략한다.

    2단의 존재 이유는 "카드 전부는 컨텍스트에 못 싣는다"인데, channel(56장)처럼 그 전제가
    안 서는 종류에서 L0 은 후보를 좁히지 않고 순차 LLM 왕복 하나만 쓴다 — 오히려 버킷
    오선택으로 맞는 카드가 후보에서 빠지는 자리다. 판정은 kb.whole_index 가 데이터로
    한다: 카드가 늘어 예산을 넘으면 저절로 2단으로 돌아간다(check_hier_index ⑧이 pitch
    로 2단 경로를 그대로 고정하고 있다 — 이 검사는 그 반대짝이다).
    """
    from pension_agent.consult_agent import kb_index

    ok = 0

    # ① 판정 자체 — 작은 종류는 텍스트, 큰 종류는 None(= 2단 유지).
    small = kb_index.whole_index(tools.KB, ("channel",))
    hit = small is not None and kb_index.whole_index(tools.KB, ("pitch",)) is None
    print(f"{'✓' if hit else '✗'} whole_index: channel 은 1단, pitch 는 2단 유지")
    ok += hit

    # ② 1단으로 돌 때 카드가 하나도 빠지지 않는다 — 왕복을 아끼는 것이지 후보를 줄이는 게
    #    아니다. 제목만 남기는 압축(examples=0)까지 내려가서 얻은 1단도 아니다(예상질문이
    #    최소 1개는 남는 예산일 때만 생략한다 — 선택 품질을 팔아 왕복을 사지 않는다).
    n_channel = sum(1 for c in tools.KB.cards if c["_kind"] == "channel")
    hit = small is not None and \
        sum(1 for line in small.splitlines() if line.startswith("[")) == n_channel
    print(f"{'✓' if hit else '✗'} 1단 인덱스에 channel 전 카드({n_channel}장)가 실린다")
    ok += hit

    # ③ 배선 — llm_pick(("channel",)) 은 LLM 을 카드 선택 한 번만 부른다(버킷 프롬프트 없음).
    real = next(c["id"] for c in tools.KB.cards if c["_kind"] == "channel")
    calls: list[str] = []
    orig = select.generate
    select.generate = lambda prompt, **kw: (calls.append(prompt), f'["{real}"]')[1]
    try:
        hits = _REAL_LLM_PICK(("channel",), "연금 수령 신청 스타뱅킹에서 돼?")
    finally:
        select.generate = orig
    hit = len(calls) == 1 and "묶음 식별자" not in calls[0] \
        and [c["id"] for _, c in hits] == [real]
    print(f"{'✓' if hit else '✗'} llm_pick: 작은 종류는 호출 1번({len(calls)}회) · 버킷 프롬프트 없음")
    ok += hit
    return ok


def check_all_kinds_reachable() -> int:
    """카드 종류가 전부 도구로 닿는지 — method 131장·fieldtip 10장이 답변 근거로
    쓰이는 경로가 없던 것이 이 변경의 동기 중 하나였다(guard 가 caution 8건만 썼다).
    market 23장은 적재 경로 자체가 없어 통째로 닿지 않던 자리다(check_market_material).

    두 경로를 다 본다. 2026-09-04 까지 이 종류들은 trigger_examples 가 제목과 거의 같아서
    n-gram 폴백이 화법보다 약했다 — 지금은 본문 절이 입구다(check_trigger_entrances).
    """
    ok = 0
    hit = ({"fact", "procedure", "segment", "method", "fieldtip", "market", "lineup"}
           <= set(tools.TOOLS))
    print(f"{'✓' if hit else '✗'} 일곱 종류 모두 도구로 등록됨")
    ok += hit

    for kind in ("method", "fieldtip", "market", "lineup"):
        card = next(c for c in tools.KB.cards if c["_kind"] == kind)

        # ① LLM 선택 경로 — 주 경로다. 이 도구들은 select.pick() 을 거치므로 시임이 거기다.
        orig = select.llm_pick
        select.llm_pick = lambda kinds, query, _c=card: [(2.0, _c)]
        try:
            found = tools.run(kind, {"question": "q"}, "아무 질문")
        finally:
            select.llm_pick = orig
        by_llm = (found is not None and found["text"].startswith("■")
                  and found["sources"][0]["id"] == card["id"])
        print(f"{'✓' if by_llm else '✗'} {kind} — LLM 선택으로 근거 반환")
        ok += by_llm

        # ② n-gram 폴백 — 예상질문에 가까운 질의라면 LLM 없이도 닿는다.
        ex = next((e for e in (card.get("trigger_examples") or [])), card["title"])
        found = tools.run(kind, {"question": ex}, ex)
        by_ngram = found is not None and found["text"].startswith("■")
        print(f"{'✓' if by_ngram else '✗'} {kind} — n-gram 폴백으로도 근거 반환")
        ok += by_ngram

    # 현장팁은 본부 지침이 아니라는 표시를 본문에 남긴다(신뢰 표시가 답변에 붙어야 한다).
    tip = next(c for c in tools.KB.cards if c["_kind"] == "fieldtip")
    hit = "본부 공식 지침이 아닙니다" in tools._render_fieldtip(tip)
    print(f"{'✓' if hit else '✗'} fieldtip 근거에 '본부 지침 아님' 표시")
    ok += hit
    return ok


def check_trigger_entrances() -> int:
    """카드의 검색 입구(trigger_examples)가 목록 한 줄에 정보를 더하는가 (CLAUDE.md §3).

    LLM 카드 목록 한 줄은 제목 뒤에 예상질문 2개까지만 싣는다(kb._card_line). 변환기가 첫
    칸에 제목을 그대로 넣던 동안(2026-09-04 이전, 633장 중 388장) 정보 칸은 하나뿐이었고,
    fieldtip 은 제목 하나뿐이었고, 절 자르기가 「1,800만원」의 쉼표에서 끊겨 팩트 11장의
    입구가 「연간 납입한도는 1」로 잘렸다. 세 결함의 재발을 막는다.
    """
    import re
    from pension_agent.knowledge.similarity import ngram_sim
    ok = 0
    cards = tools.KB.cards

    # ① 제목을 예상질문 첫 칸에 중복해 싣지 않는다(pitch 는 원문 발화라 애초에 제목이 아니다).
    dup = [c["id"] for c in cards
           if (c.get("trigger_examples") or [""])[0] == (c.get("title") or c.get("label"))]
    hit = not dup
    print(f"{'✓' if hit else '✗'} 예상질문 첫 칸이 제목의 중복이 아니다"
          + ("" if hit else f" — {dup[:3]} 외 {len(dup)}장"))
    ok += hit

    # ② 숫자 자릿수 쉼표에서 잘린 입구가 없다 — 본문 절이 숫자로 끝나면서 원문에서는 그 뒤에
    #    ",digit" 이 이어지는 경우.
    def body_of(c):
        return " ".join(str(c.get(k) or "") for k in ("value", "situation", "action",
                                                       "condition_text", "summary"))
    cut = [(c["id"], t) for c in cards for t in (c.get("trigger_examples") or [])
           if re.search(r"\d$", t) and (t + ",") in body_of(c)
           and re.search(re.escape(t) + r",\d", body_of(c))]
    hit = not cut
    print(f"{'✓' if hit else '✗'} 숫자 사이 쉼표에서 잘린 예상질문이 없다"
          + ("" if hit else f" — {cut[:2]}"))
    ok += hit

    # ③ 종류마다 제목과 뚜렷이 다른 입구가 있다(screen·channel 은 패턴이 제목을 품으므로 제외).
    for kind in ("fact", "segment", "method", "procedure", "fieldtip", "market", "lineup"):
        weak = [c["id"] for c in cards if c["_kind"] == kind
                and not [e for e in (c.get("trigger_examples") or [])
                         if ngram_sim(c.get("title") or "", e) <= 0.6]]
        hit = not weak
        print(f"{'✓' if hit else '✗'} {kind} — 제목 밖의 검색 단서가 전 카드에 있다"
              + ("" if hit else f" — {weak[:3]} 외 {len(weak)}장"))
        ok += hit

    # ④ 보강표는 실재하는 카드만 가리키고, 그 카드의 주제어를 담는다(지어낸 입구 금지).
    from scripts.kb_build import config as kb_config
    from scripts.kb_build.build_kb import useful_trigger
    by_id = {c["id"]: c for c in cards}
    bad = [(cid, e[:20]) for cid, extras in kb_config.TRIGGER_EXTRA.items() for e in extras
           if cid not in by_id or not useful_trigger(e, by_id[cid].get("title") or "")]
    hit = not bad
    print(f"{'✓' if hit else '✗'} TRIGGER_EXTRA 는 실재 카드의 주제어만 담는다"
          + ("" if hit else f" — {bad[:3]}"))
    ok += hit

    # ⑤ 폴백 점수는 제목도 잰다 — 제목을 첫 칸에서 뺀 대가를 여기서 갚는다.
    card = next(c for c in cards if c["_kind"] == "method")
    from pension_agent.knowledge import kb as KBMOD
    _, with_title = KBMOD.score_parts(card, utterance=card["title"])
    hit = with_title >= 4.0
    print(f"{'✓' if hit else '✗'} 제목 그대로의 질문이 n-gram 점수 상한을 받는다 ({with_title:.2f})")
    ok += hit
    return ok


def check_tool_axes() -> int:
    """`pitch` 와 `playbook` 의 설명이 «무엇으로 찾나»로 갈리는가 (gap 35).

    도구 설명은 계획 LLM 이 읽는 **유일한** 판단 재료다(`tools.catalog`). 둘 다 「반론」을
    말하고 축을 말하지 않던 동안, 고객 화면이 열린 턴의 반론 질문이 통째로 `playbook` 으로
    갔다 — 그쪽은 후보를 **고객 계좌 상태**로 고르고 질문은 좁히기만 해서, 「손실만 나는데
    해지하겠다」로 조회하면 관련도 0.09 의 절차 카드 한 장이 남는다. 같은 질문이 `pitch` 로
    가면 pitch.k03.012(해지 대신 연금개시 후 부분인출)가 1등이다.

    **재료가 있는데 도구가 안 불린 것**이라, 검색을 고쳐서는 안 닫힌다. 같은 처방을 이
    저장소가 이미 두 번 썼다(`suitable` vs `lineup` · `history` vs `transcript`).
    """
    from pension_agent.consult_agent.state import KB
    ok = 0
    print("\n[도구 설명 — pitch 와 playbook 이 «무엇으로 찾나»로 갈린다 (gap 35)]")

    pitch, playbook = tools.TOOLS["pitch"].desc, tools.TOOLS["playbook"].desc

    hit = "고객의 말" in pitch and "고객 화면이 열려 있어도" in pitch
    print(f"{'✓' if hit else '✗'} pitch — 질문에 담긴 «고객이 한 말»로 찾는다고 밝힌다")
    ok += hit

    hit = "계좌 상태" in playbook and "좁히기만 한다" in playbook
    print(f"{'✓' if hit else '✗'} playbook — 후보를 고르는 것은 상태이고 질문이 아니라고 밝힌다")
    ok += hit

    # 서로를 가리켜야 계획이 잘못 든 자리에서 되돌아 나올 수 있다. 한쪽 설명만 고치면
    # 다른 쪽은 여전히 「화법·예상반론」을 내걸고 서 있다.
    hit = "pitch" in playbook
    print(f"{'✓' if hit else '✗'} playbook 이 고객의 말은 pitch 라고 되돌려 보낸다")
    ok += hit

    # 재료가 실제로 그쪽에 있다는 것 — 설명만 갈라 두고 검색이 못 찾으면 아무것도 아니다.
    # 슬롯(거절유형)이 붙으면 n-gram 폴백만으로도 해지 화법이 1등으로 올라온다.
    hits = tools.retrieve(KB, top_k=3, kinds=["pitch"],
                          utterance="고객이 '손실만 나는데 그냥 해지하겠다'는데 어떻게 대응하지?",
                          objection_type="해지·망설임")
    hit = bool(hits) and hits[0][1]["tags"].get("objection_type") == "해지·망설임"
    print(f"{'✓' if hit else '✗'} 그 질문의 화법이 pitch 쪽 검색에 실재한다"
          f" ({hits[0][1]['id'] if hits else '0건'})")
    ok += hit

    return ok
