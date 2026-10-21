"""카드 색인 — 팩트 색인 · 계층 인덱스(L0/L1) · 종류별 도달 · 검색 입구 · 도구 설명 축.

`test_agent.py::main` 이 호출(스텁 설치·정리 포함). 직접 실행 금지.
"""

from __future__ import annotations

from pension_agent import tools
from pension_agent.tools.kb_search import select

from tests.agent._common import print, _REAL_LLM_PICK  # noqa: A001 — 집계용 print


def check_fact_in_index() -> int:
    """팩트가 카드 색인에 실려 LLM 카드 선택 후보가 되는지(배선만, 모델 판단 아님)."""
    ok = 0
    from pension_agent.tools.kb_search import kb_index
    from pension_agent.tools.kb_search import facts_qa

    kb = tools.KB
    # 같은 객체 필수 — 사본이면 한쪽만 수정됨
    f2 = kb.facts["fact.k04.f2"]
    hit = any(c is f2 for c in kb.cards) and sum(1 for c in kb.cards if c["_kind"] == "fact") == len(kb.facts)
    print(f"{'✓' if hit else '✗'} 팩트가 카드 색인에 **같은 객체로** 실린다 ({len(kb.facts)}장)")
    ok += hit

    # 카탈로그 누락 시 LLM 후보에서 통째로 사라짐
    cat = kb_index.index_catalog(kb, ("fact",))
    hit = cat.startswith("■ fact") and "납입·세액공제" in cat
    print(f"{'✓' if hit else '✗'} 팩트 버킷이 카탈로그에 뜬다")
    ok += hit

    # 슬라이스에 예상질문 동반 — LLM id 선택 재료
    sl = kb_index.index_slice(kb, ["X01"], kinds=("fact",))
    hit = "fact.k04.f2" in sl and "예상질문" in sl
    print(f"{'✓' if hit else '✗'} 팩트 슬라이스에 카드와 예상질문이 실린다")
    ok += hit

    # LLM 선택 시 팩트 반환 — 전역 off 된 llm_pick 원본 복구, 캔드 응답
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

    # 미선택 시 n-gram 그대로
    hit = [h[1]["no"] for h in facts_qa.search("세액공제 한도")][:1] == ["F2"]
    print(f"{'✓' if hit else '✗'} LLM 이 못 고르면 n-gram 폴백이 예전대로 동작한다")
    ok += hit
    return ok


def check_hier_index() -> int:
    """계층 인덱스 L0→L1 — 예산 상한 준수·버킷 카드 누락 없음(행내 모델 컨텍스트 제약)."""
    from pension_agent.tools.kb_search import kb_index
    from pension_agent.knowledge import kb as K

    kb = K.load_kb()
    ok = 0

    # ① 버킷 카드 누락 없음 — tags.topics 축이면 일부 카드가 영구 미검색(group 축 사용 이유)
    bk = kb_index.buckets(kb)
    covered = sum(len(b["cards"]) for b in bk.values())
    hit = covered == len(kb.cards)
    print(f"{'✓' if hit else '✗'} 버킷 커버리지 {covered}/{len(kb.cards)}장 (버킷 {len(bk)}개)")
    ok += hit

    # ② 카탈로그 결정론(같은 KB → 같은 문자열)
    hit = kb_index.index_catalog(kb) == kb_index.index_catalog(kb)
    print(f"{'✓' if hit else '✗'} 카탈로그 결정론")
    ok += hit

    # ③ L0 크기 상한 — 카드 증가로 초과하면 회귀, 종류 증가(약 160자/종)면 한도 조정
    cat = kb_index.index_catalog(kb)
    hit = len(cat) <= 2200
    print(f"{'✓' if hit else '✗'} L0 카탈로그 {len(cat)}자 ≤ 2200 (종류 {len(kb_index._KIND_ORDER)})")
    ok += hit

    # ④ 예산은 헤더·생략안내 포함 실제 상한
    codes = list(bk)
    over = [b for b in (200, 500, 1000, 2500, 4000)
            if len(kb_index.index_slice(kb, codes, budget_chars=b)) > b]
    hit = not over
    print(f"{'✓' if hit else '✗'} 예산 상한 준수 (초과: {over or '없음'})")
    ok += hit

    # ⑤ 잘라낸 장수 명시
    tight = kb_index.index_slice(kb, codes, budget_chars=500)
    hit = "생략" in tight
    print(f"{'✓' if hit else '✗'} 절단 시 생략 사실 명시")
    ok += hit

    # ⑥ 버킷 하나는 기본 예산 내 — 2단 충분 보장
    worst = max(len(kb_index.index_slice(kb, [c])) for c in bk)
    hit = worst <= kb_index.INDEX_BUDGET_CHARS
    print(f"{'✓' if hit else '✗'} 최악 버킷 {worst}자 ≤ 기본예산 {kb_index.INDEX_BUDGET_CHARS}")
    ok += hit

    # ⑦ 목록 밖 버킷 코드 조회 불가
    hit = kb_index.index_slice(kb, ["ZZ99", "없는코드"]) == ""
    print(f"{'✓' if hit else '✗'} 없는 버킷 코드 → 빈 슬라이스")
    ok += hit

    # ⑧ llm_pick 버킷 → id 2단, 지어낸 id 제거
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

    # ⑨ 1차 버킷 미선택 시 2차 호출 생략
    calls.clear()
    select.generate = lambda prompt, **kw: (calls.append(prompt), "[]")[1]
    try:
        hits = _REAL_LLM_PICK(("pitch",), "아무 질문")
    finally:
        select.generate = orig
    hit = len(calls) == 1 and hits == []
    print(f"{'✓' if hit else '✗'} 버킷 0건 → 2차 호출 생략({len(calls)}회)")
    ok += hit

    # ⑩ 종류 확장 시 그 종류 카드도 후보
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
    """전 카드 인덱스가 예산 내인 종류는 L0 호출 생략(kb.whole_index 판정, check_hier_index 의 반대짝)."""
    from pension_agent.tools.kb_search import kb_index

    ok = 0

    # ① 작은 종류는 텍스트, 큰 종류는 None(2단 유지)
    small = kb_index.whole_index(tools.KB, ("channel",))
    hit = small is not None and kb_index.whole_index(tools.KB, ("pitch",)) is None
    print(f"{'✓' if hit else '✗'} whole_index: channel 은 1단, pitch 는 2단 유지")
    ok += hit

    # ② 1단에서도 카드 누락 없음, 예상질문 최소 1개 유지(examples=0 압축 금지)
    n_channel = sum(1 for c in tools.KB.cards if c["_kind"] == "channel")
    hit = small is not None and \
        sum(1 for line in small.splitlines() if line.startswith("[")) == n_channel
    print(f"{'✓' if hit else '✗'} 1단 인덱스에 channel 전 카드({n_channel}장)가 실린다")
    ok += hit

    # ③ llm_pick(("channel",)) 은 카드 선택 1회만(버킷 프롬프트 없음)
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
    """모든 카드 종류가 도구로 도달(LLM 선택·n-gram 폴백 두 경로). market 은 check_market_material."""
    ok = 0
    hit = ({"fact", "procedure", "segment", "method", "fieldtip", "market", "lineup"}
           <= set(tools.TOOLS))
    print(f"{'✓' if hit else '✗'} 일곱 종류 모두 도구로 등록됨")
    ok += hit

    for kind in ("method", "fieldtip", "market", "lineup"):
        card = next(c for c in tools.KB.cards if c["_kind"] == kind)

        # ① LLM 선택 경로(주 경로) — 시임은 select.pick()
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

        # ② n-gram 폴백
        ex = next((e for e in (card.get("trigger_examples") or [])), card["title"])
        found = tools.run(kind, {"question": ex}, ex)
        by_ngram = found is not None and found["text"].startswith("■")
        print(f"{'✓' if by_ngram else '✗'} {kind} — n-gram 폴백으로도 근거 반환")
        ok += by_ngram

    # 현장팁은 본부 지침 아님 표시 필수
    tip = next(c for c in tools.KB.cards if c["_kind"] == "fieldtip")
    hit = "본부 공식 지침이 아닙니다" in tools._render_fieldtip(tip)
    print(f"{'✓' if hit else '✗'} fieldtip 근거에 '본부 지침 아님' 표시")
    ok += hit
    return ok


def check_trigger_entrances() -> int:
    """검색 입구(trigger_examples)가 목록 한 줄(kb._card_line, 예상질문 2개)에 정보를 더하는지."""
    import re
    from pension_agent.knowledge.similarity import ngram_sim
    ok = 0
    cards = tools.KB.cards

    # ① 제목을 예상질문 첫 칸에 중복 금지(pitch 는 원문 발화라 제외)
    dup = [c["id"] for c in cards
           if (c.get("trigger_examples") or [""])[0] == (c.get("title") or c.get("label"))]
    hit = not dup
    print(f"{'✓' if hit else '✗'} 예상질문 첫 칸이 제목의 중복이 아니다"
          + ("" if hit else f" — {dup[:3]} 외 {len(dup)}장"))
    ok += hit

    # ② 자릿수 쉼표에서 잘린 입구 없음(「1,800만원」 → 「1」)
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

    # ③ 종류마다 제목과 다른 입구 존재(screen·channel 제외)
    for kind in ("fact", "segment", "method", "procedure", "fieldtip", "market", "lineup"):
        weak = [c["id"] for c in cards if c["_kind"] == kind
                and not [e for e in (c.get("trigger_examples") or [])
                         if ngram_sim(c.get("title") or "", e) <= 0.6]]
        hit = not weak
        print(f"{'✓' if hit else '✗'} {kind} — 제목 밖의 검색 단서가 전 카드에 있다"
              + ("" if hit else f" — {weak[:3]} 외 {len(weak)}장"))
        ok += hit

    # ④ 보강표는 실재 카드만, 그 카드 주제어 포함
    from scripts.kb_build import config as kb_config
    from scripts.kb_build.build_kb import useful_trigger
    by_id = {c["id"]: c for c in cards}
    bad = [(cid, e[:20]) for cid, extras in kb_config.TRIGGER_EXTRA.items() for e in extras
           if cid not in by_id or not useful_trigger(e, by_id[cid].get("title") or "")]
    hit = not bad
    print(f"{'✓' if hit else '✗'} TRIGGER_EXTRA 는 실재 카드의 주제어만 담는다"
          + ("" if hit else f" — {bad[:3]}"))
    ok += hit

    # ⑤ 보강표 화법에도 적용 — 화법 빌더는 common.triggers_of 미사용(별도 규칙)
    missed = [cid for cid, extras in kb_config.TRIGGER_EXTRA.items()
              if cid in by_id and extras
              and not set(extras) <= set(by_id[cid].get("trigger_examples") or [])]
    hit = not missed
    print(f"{'✓' if hit else '✗'} TRIGGER_EXTRA 가 카드에 실제로 실린다(화법 포함)"
          + ("" if hit else f" — {missed[:3]}"))
    ok += hit

    # ⑥ 방치 화법 n-gram 도달(0.000 이면 폴백 불가)
    from pension_agent.knowledge import kb as KBMOD
    idle = [KBMOD.score_parts(by_id[cid], utterance="그냥 두면 안 되나요")[1]
            for cid in ("pitch.k03.020", "pitch.k03.045") if cid in by_id]
    hit = len(idle) == 2 and all(s > 0 for s in idle)
    print(f"{'✓' if hit else '✗'} 「그냥 두면」 방치 화법이 n-gram 점수를 받는다 "
          f"({' · '.join(f'{s:.2f}' for s in idle)})")
    ok += hit

    # 판매중단 펀드 교체 화법(k03.027) — 문턱 넘어 채택까지 확인
    q82 = "고객이 '손실 난 걸 지금 팔라는 거냐'고 하면 뭐라고 하지?"
    top = tools.retrieve(tools.KB, top_k=1, kinds=["pitch"], utterance=q82)
    hit = bool(top) and top[0][1]["id"] == "pitch.k03.027"
    print(f"{'✓' if hit else '✗'} 「손실 난 걸 지금 팔라는 거냐」가 폴백에서 판매중단 교체 화법을 찾는다"
          + ("" if hit else f" — {[c['id'] for _s, c in top]}"))
    ok += hit

    # ⑦ 표 조각을 검색 입구로 금지(세그먼트 3·33·50 조건문의 마크다운 표)
    piped = [(c["id"], e[:40]) for c in cards
             for e in (c.get("trigger_examples") or []) if "|" in e or "\n" in e]
    hit = not piped
    print(f"{'✓' if hit else '✗'} 검색 입구에 표 조각이 들어가지 않는다"
          + ("" if hit else f" — {piped[:3]}"))
    ok += hit

    # ⑧ 본문 표 줄바꿈 유지 — 뭉개지면 atomic 항목이라 답변에 통째로 나감
    flat = [c["id"] for c in cards
            if "|---" in str(c.get("condition_text") or "")
            and "\n|" not in str(c.get("condition_text") or "")]
    hit = not flat
    print(f"{'✓' if hit else '✗'} 조건문 안의 표가 한 줄로 뭉개지지 않는다"
          + ("" if hit else f" — {flat[:3]}"))
    ok += hit

    # ⑨ 폴백 점수에 제목 포함(첫 칸에서 뺀 보완)
    card = next(c for c in cards if c["_kind"] == "method")
    _, with_title = KBMOD.score_parts(card, utterance=card["title"])
    hit = with_title >= 4.0
    print(f"{'✓' if hit else '✗'} 제목 그대로의 질문이 n-gram 점수 상한을 받는다 ({with_title:.2f})")
    ok += hit
    return ok


def check_tool_axes() -> int:
    """`pitch`·`playbook` 도구 설명이 검색 축(질문 vs 고객 계좌 상태)으로 갈리는지 — 계획 LLM 의 유일한 판단 재료."""
    from pension_agent.state import KB
    ok = 0
    print("\n[도구 설명 — pitch 와 playbook 이 «무엇으로 찾나»로 갈린다 (gap 35)]")

    pitch, playbook = tools.TOOLS["pitch"].desc, tools.TOOLS["playbook"].desc

    hit = "고객의 말" in pitch and "고객 화면이 열려 있어도" in pitch
    print(f"{'✓' if hit else '✗'} pitch — 질문에 담긴 «고객이 한 말»로 찾는다고 밝힌다")
    ok += hit

    hit = "계좌 상태" in playbook and "좁히기만 한다" in playbook
    print(f"{'✓' if hit else '✗'} playbook — 후보를 고르는 것은 상태이고 질문이 아니라고 밝힌다")
    ok += hit

    # 상호 참조 필수 — 잘못 든 계획의 복귀 경로
    hit = "pitch" in playbook
    print(f"{'✓' if hit else '✗'} playbook 이 고객의 말은 pitch 라고 되돌려 보낸다")
    ok += hit

    # 재료 실재 확인 — 슬롯(거절유형) 있으면 n-gram 폴백만으로 해지 화법 1등
    hits = tools.retrieve(KB, top_k=3, kinds=["pitch"],
                          utterance="고객이 '손실만 나는데 그냥 해지하겠다'는데 어떻게 대응하지?",
                          objection_type="해지·망설임")
    hit = bool(hits) and hits[0][1]["tags"].get("objection_type") == "해지·망설임"
    print(f"{'✓' if hit else '✗'} 그 질문의 화법이 pitch 쪽 검색에 실재한다"
          f" ({hits[0][1]['id'] if hits else '0건'})")
    ok += hit

    return ok


def check_procedure_axes() -> int:
    """«~ 등록 방법»이 절차 카드 한 장으로 끝나지 않게 screen·channel 로 이어지는지(도구 설명·계획 규칙·형태 요구)."""
    from pension_agent.prompts import ANSWER_SHAPES
    from pension_agent.prompts.plan import PLAN_PROMPT
    from pension_agent.state import KB
    ok = 0
    print("\n[도구 설명 — 등록 방법은 procedure + screen·channel (gap 38)]")

    desc = tools.TOOLS["procedure"].desc
    hit = "screen" in desc and "channel" in desc and "방법" in desc
    print(f"{'✓' if hit else '✗'} procedure 설명이 화면번호·메뉴 경로는 screen·channel 에 있다고 밝힌다")
    ok += hit

    hit = "반대 방향도 같다" in PLAN_PROMPT and "procedure 에 `\"last\": true` 를 붙이지 않는다" in PLAN_PROMPT
    print(f"{'✓' if hit else '✗'} 계획 규칙 — 업무 방법 질문은 procedure 하나로 끝내지 않는다")
    ok += hit

    # 화면 무조건 요구 시 LLM 이 재료의 아무 번호로 채움
    shape = ANSWER_SHAPES["procedure"]
    hit = "재료에 있는" in shape and "그 업무의 화면으로 소개하지 않는다" in shape
    print(f"{'✓' if hit else '✗'} procedure 형태 요구가 재료에 없는 화면을 요구하지 않는다")
    ok += hit

    # 재료 실재 확인
    q = "디폴트옵션 등록 방법 알려줘"
    ch = [c["id"] for _s, c in tools.retrieve(KB, top_k=3, kinds=["channel"], utterance=q)]
    sc = [c["id"] for _s, c in tools.retrieve(KB, top_k=3, kinds=["screen"], utterance=q)]
    hit = "channel.030" in ch and "screen.06-12-610" in sc
    print(f"{'✓' if hit else '✗'} 등록 경로가 channel·screen 검색에 실재한다 (channel {ch[:1]} · screen {sc})")
    ok += hit

    return ok
