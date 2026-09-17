"""답변 문장 — 인용 허용 범위 · 상품 조언 · 반복 금지 · 직전 답변 수정 · 형태·표기.

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

import pathlib

from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import routing, tools
from pension_agent.consult_agent.nodes import plan
from pension_agent.verify import first_measure, verify_texts

from tests.consult._common import print, _vt, _REAL_FITS  # noqa: A001 — 집계용 print


def check_prompt_is_quotable() -> int:
    """프롬프트에 들어간 것은 인용도 허용된다 (§6).

    코드가 이번 턴 프롬프트에 실어 보내는데 원장에는 없는 텍스트가 있었다. 시킨 대로
    인용하면 «자료 밖 수치»로 답이 통째로 버려지고 근거 원문이 덤프됐다 — `relations.py`
    머리말이 「데이터가 시킨 일을 했다고 벌하는 것」이라 부른 것의 네 번째다.

    실측(2026-09-02 박정호 P2)에서 답을 죽인 것은 **카드 기준시점의 범위 표기**다.
    `ANSWER_SHAPES["fact"]` 가 기준시점을 쓰라고 요구하는데, `as_of` 가 «2026.03~04» 일 때
    답변의 «2026년 3~4월» 이 날짜로 안 끊겨 3·4 가 맨숫자로 남았다. 재작성해도 형태 요구가
    그대로라 또 썼다.

    **짝으로 잰다.** 넓힌 쪽만 재면 헐거워진 것을 못 잡는다.
    """
    from pension_agent.consult_agent import tools
    from pension_agent.consult_agent.evidence import guard, kb_index
    from pension_agent.consult_agent.nodes import plan as PLAN
    from pension_agent.consult_agent.evidence import facts_qa
    from pension_agent.consult_agent.state import KB
    from pension_agent.verify import verify_texts

    ok = 0

    # ── ① 기준시점 범위 표기 — 원장·답변 양쪽 정규화 (verify.py)
    ledger = ["· 기준시점 2026.03~04 · 출처 …"]
    passes = [t for t in ("이 내용은 2026년 3~4월 기준이에요.",
                          "이 내용은 2026년 3월~4월 기준이에요.",
                          "이 내용은 2026.03~04 기준이에요.",
                          "2026년 3월 기준 자료입니다.")
              if verify_texts(t, ledger, echoable=[""])[0]]
    hit = len(passes) == 4
    print(f"{'✓' if hit else '✗'} 기준시점: 원장의 기간 표기를 한국어로 풀어 쓴 답변이 통과한다")
    ok += hit

    # 넓히기만 하고 좁히는 쪽은 그대로여야 한다 — 기간을 늘리거나 옮기면 여전히 걸린다.
    blocked = [t for t in ("이 내용은 2026년 3~9월 기준이에요.",
                           "이 내용은 2026년 1~4월 기준이에요.",
                           "이 내용은 2025년 3~4월 기준이에요.",
                           "2026년 7월 기준 자료입니다.")
               if not verify_texts(t, ledger, echoable=[""])[0]]
    hit = len(blocked) == 4
    print(f"{'✓' if hit else '✗'} 기준시점: 기간을 늘리거나 옮긴 답변은 여전히 걸린다")
    ok += hit

    # 화면번호·대표번호가 기간으로 오독되면 그 답변이 통째로 거부된다(_DATE_DOT 과 같은 경계).
    hit = verify_texts("[04-12-640] 화면에서 1588-1234 로 문의하세요.",
                       ["[04-12-640] 1588-1234"], echoable=[""])[0]
    print(f"{'✓' if hit else '✗'} 기준시점: 화면번호·대표번호를 기간으로 읽지 않는다")
    ok += hit

    # ── ①-b 표시(notices)는 인용해도 된다 — 채널 카드의 시효 표시 「— 2025.03.31 기준 표기입니다」.
    #    본문(_render_channel)에는 기준시점이 없고 표시에만 있어서, 시킨 대로 옮겨 쓴 채널
    #    답변이 리허설 3턴 전부 «자료 밖 날짜»로 폐기됐다(2026-09-05 demo T3b · cases 2·3).
    chan = next((c for c in KB.cards if c["_kind"] == "channel" and c.get("as_of")
                 and c.get("volatile")), None)
    if chan is None:
        print("✗ 표시 인용: 기준시점·시효 경고를 가진 채널 카드가 없다")
    else:
        ev = tools._ev("channel", "q", tools._render_channel(chan),
                       kb_index.sources_of(KB, [(2.0, chan)]),
                       notices=[tools.stale_mark(chan)], cards=[chan])
        mark = tools.stale_mark(chan)
        hit = verify_texts(f"메뉴는 위와 같아요.\n{mark}", tools.ledger_texts([ev]),
                           echoable=[""])[0]
        print(f"{'✓' if hit else '✗'} 표시 인용: 시효 표시의 기준시점을 옮겨 쓴 채널 답변이 통과한다"
              f" ({chan.get('as_of')})")
        ok += hit
        # 좁히는 쪽은 그대로다 — 표시에 없는 다른 날짜는 여전히 걸린다.
        hit = not verify_texts("메뉴는 위와 같아요. 2024.01.15 기준 표기입니다.",
                               tools.ledger_texts([ev]), echoable=[""])[0]
        print(f"{'✓' if hit else '✗'} 표시 인용: 표시에 없는 날짜는 여전히 걸린다")
    ok += hit

    # ── ② 가드·승낙 문구 — 프롬프트에 실어 보낸 것 (plan._screen)
    card = KB.facts.get("fact.k04.f47")
    if card is None:
        print("✗ 프롬프트 인용: 기준 카드(fact.k04.f47)가 없어 검사를 건너뛴다")
        return ok
    ev = tools._ev("fact", "q", facts_qa.render([(1.0, card)]),
                   kb_index.sources_of(KB, [(1.0, card)]), cards=[card])
    known = PLAN._known_products()
    question = "이 절차 얼마나 걸려?"
    injected = ["- 사용계획 있는 자금은 먼저 걸러낼 것 → 6번",
                "«원리금보장상품 편중» 고객에게 쓰는 화법 2건"]

    quoted = [a for a in ("사용계획 있는 자금은 먼저 걸러내세요(6번). 60일 이내면 됩니다.",
                          "말씀하신 화법 2건을 보여드릴게요. 60일 이내면 재입금이 됩니다.")
              if not PLAN._screen(a, [ev], question, known, prompt_texts=injected)[0]]
    hit = len(quoted) == 2
    print(f"{'✓' if hit else '✗'} 프롬프트 인용: 가드·승낙 문구를 인용한 답변이 폐기되지 않는다")
    ok += hit

    # 넓힌 것은 «프롬프트에 실제로 들어간 수치» 하나뿐이다 — 지어낸 값은 그대로 걸린다.
    still = [a for a in ("이 상품은 연 7.2% 수익을 보장해요.",
                         "이 고객은 IRP에 2,000만원이 있어요.",
                         "사용계획 있는 자금은 먼저 걸러내세요(9번).",
                         "말씀하신 화법 5건을 보여드릴게요.")
             if PLAN._screen(a, [ev], question, known, prompt_texts=injected)[0]]
    hit = len(still) == 4
    print(f"{'✓' if hit else '✗'} 프롬프트 인용: 프롬프트에 없던 수치는 여전히 걸린다")
    ok += hit

    # 상품명은 넓히지 않는다 — 이름만 대서 적합성 게이트를 뚫는 길을 열지 않는다.
    src = pathlib.Path("pension_agent/consult_agent/nodes/plan.py").read_text(encoding="utf-8")
    hit = "echoable=[question, *(t for t in prompt_texts if t)]" in src
    print(f"{'✓' if hit else '✗'} 프롬프트 인용: 넓히는 통로가 echoable(수치 전용) 하나다")
    ok += hit
    return ok


def check_product_advice() -> int:
    """「이 고객 무슨 상품 추천해주지?」가 답이 되는가 — 그리고 그 답이 권유가 아닌가.

    회귀 대상은 한 질문에서 함께 터진 결함 넷이다. 실제 트레이스에서 lineup 이 세 바퀴
    돌며 전부 '재료 없음'을 내고, 겨우 쓴 문장은 '미등록 상품명'으로 폐기돼, 화면에는
    고객 브리핑 재료가 통째로 떨어졌다.

    ① 적합성 게이트가 **계획이 무엇을 찾는 중인지**를 못 봤다. 직원 질문만 보고 판정하니
       「투자성향별 포트폴리오」 같은 일반 자료가 "이 고객에 대한 답이 아니다"로 전멸했다.
    ② 상품 등록부가 데모 카탈로그 12종뿐이라, 행내 원문 표에 버젓이 있는 상품
       (「KB 온국민 TDF 시리즈」)을 말한 답변이 '미등록'으로 통째로 버려졌다.
    ③ 상품명 정규식이 문장을 삼켜 **실재 상품과 지어낸 상품을 한 이름으로** 붙였다.
    ④ 적합성 게이트가 이미 계산해둔 «허용 범위»를 부를 도구가 대화형에 없었다.
    """
    from pension_agent.consult_agent.evidence import kb_index
    from pension_agent.consult_agent.prompts import ANSWER_SHAPES, COMPOSE_SYSTEM
    from pension_agent.consult_agent.state import KB
    ok = 0

    # ── ① 게이트가 계획 질의를 받는다 ──────────────────────────────
    seen: dict[str, str] = {}
    orig_gen, orig_fits = tools.generate, tools.fits_question
    tools.fits_question = _REAL_FITS          # 게이트 본체를 재야 하므로 스텁을 걷는다
    tools.generate = lambda p, **kw: seen.setdefault("p", p) and "[]"
    try:
        card = next(c for c in KB.cards if c["_kind"] == "lineup")
        tools._adopt({"question": "이 고객 무슨 상품 추천해주지?"},
                     "투자성향별 추천 포트폴리오", [(2.0, card)], "운용 상품")
    finally:
        tools.generate, tools.fits_question = orig_gen, orig_fits
    prompt = seen.get("p", "")
    hit = "이 고객 무슨 상품 추천해주지?" in prompt and "투자성향별 추천 포트폴리오" in prompt
    print(f"{'✓' if hit else '✗'} 적합성 게이트 프롬프트에 직원 질문과 계획 질의가 함께 실린다")
    ok += hit

    # 「고객 이름이 안 적힌 자료는 뺀다」로 읽히지 않도록 판단 기준에 명시돼 있는가.
    hit = "일반 자료는 남긴다" in prompt
    print(f"{'✓' if hit else '✗'} 고객 특정 질문에서도 일반 자료를 남기라는 기준이 실린다")
    ok += hit

    # ── ② 등록부가 지식베이스 상품명을 안다 ────────────────────────
    names = kb_index.product_names(KB)
    hit = "KB 온국민 TDF 시리즈" in names and "KB RISE 미국ETF 모아드림 (주식-재간접)" in names
    print(f"{'✓' if hit else '✗'} 지식베이스가 선언한 상품명이 등록부에 있다 ({len(names)}종)")
    ok += hit

    # 등록부는 **표의 상품명 칸**만 본다 — 합계 행의 라벨은 상품이 아니다.
    hit = "포트폴리오" not in names
    print(f"{'✓' if hit else '✗'} 합계 행 라벨(「포트폴리오」)은 상품 등록부에 안 들어간다")
    ok += hit

    known = plan._known_products()
    hit = "KB 온국민TDF2040 C-P" in known and "KB 온국민 TDF 시리즈" in known
    print(f"{'✓' if hit else '✗'} 등록부가 상품 카탈로그와 지식베이스를 합친다 ({len(known)}종)")
    ok += hit

    # ── ③ 상품명 경계 — 실재 상품은 통과하고 지어낸 이름만 걸린다 ──
    #
    # 트레이스에 찍힌 실제 문장이다. 예전 정규식은 마크다운 강조를 넘어
    # 'KB 온국민 TDF 시리즈**나 **KBSTAR 미국나스닥100' 을 **한 이름**으로 읽어,
    # 원문 표에 있는 앞쪽까지 미등록으로 판정했다.
    ledger = ["KB 온국민 TDF 시리즈 · KB 온국민TDF2040 C-P"]
    real = "동연령 인기 상품인 **KB 온국민 TDF 시리즈**를 보실 수 있어요."
    mixed = ("**KB 온국민 TDF 시리즈**나 **KBSTAR 미국나스닥100**을 보실 수 있어요.")
    tail = "다만 KB 온국민TDF2040 C-P의 적격 TDF 위험자산 한도는 확인이 필요해요."
    made_up = "KB 무지개 성장 펀드를 보실 수 있어요."

    hit = verify_texts(real, ledger, known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 원문 표에 있는 상품명을 말한 답변이 통과한다")
    ok += hit

    hit = verify_texts(tail, ledger, known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 상품명 뒤에 조사가 붙어도 통과한다")
    ok += hit

    # 예전 정규식은 이 문장에서 두 이름을 **한 토큰**으로 읽어, 원문 표에 있는 앞쪽까지
    # 미등록으로 몰았다. 지금은 마크다운 강조에서 끊겨 앞쪽만 후보가 되고 통과한다.
    from pension_agent.verify import _PROD
    hit = _PROD.findall(mixed) == ["KB 온국민 TDF 시리즈"]
    print(f"{'✓' if hit else '✗'} 실재 상품과 지어낸 상품이 붙어 있어도 따로 잡힌다")
    ok += hit

    # 이 문장은 여전히 거부된다 — 다만 걸리는 이유가 «지어낸 이름이 달고 온 수치»여야지,
    # 원문 표에 있는 앞쪽 상품이 「미등록」으로 몰려서는 안 된다.
    _good, bad = verify_texts(mixed, ledger, known_products=known)
    hit = not any(b.startswith("상품명") for b in bad)
    print(f"{'✓' if hit else '✗'} 앞쪽 실재 상품이 뒤쪽 때문에 미등록으로 몰리지 않는다")
    ok += hit

    hit = not verify_texts(made_up, ledger, known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 등록부에 없는 상품명은 여전히 거부된다")
    ok += hit

    # 등록부에 있어도 **이번 턴 재료에 없으면** 인용할 수 없다 — 등록부를 12종에서
    # 80여 종으로 넓히면서 함께 조인 자리다.
    hit = not verify_texts(real, ["다른 재료"], known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 등록 상품이어도 이번 턴 원장에 없으면 못 쓴다")
    ok += hit

    # 괄호 표기가 판정을 뒤집으면 안 된다 — 등록명이 "KB 정기예금(1년)"인데 `_PROD` 가
    # 괄호에서 이름을 끊으므로 LLM 은 "KB 정기예금 1년"으로 풀어 쓸 수밖에 없다. 공백만
    # 지우던 동안 두 표기가 다른 키가 되어, suitable 재료의 8종을 정확히 옮긴 답변이
    # '미등록'으로 통째로 버려지고 근거 원문이 덤프됐다(시연 대본 T10).
    paren_ledger = ["KB 정기예금(1년) — 매우낮은위험 · 최근 1년 3.1%"]
    paren_answer = "KB 정기예금 1년(매우낮은위험, 최근 1년 3.1%)도 범위 안에 들어요."
    hit = verify_texts(paren_answer, paren_ledger, known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 등록명의 괄호를 풀어 쓴 표기('KB 정기예금 1년')가 통과한다")
    ok += hit

    # ── ④ 적합성 범위 도구 ─────────────────────────────────────────
    cid = "176903-5528417"
    q = "이 고객 무슨 상품 추천해주지?"
    found = tools.run("suitable", {"question": q, "customer_id": cid}, q)
    text = (found or {}).get("text", "")
    hit = bool(found) and "적합성 허용 상한: 다소높은위험" in text
    print(f"{'✓' if hit else '✗'} suitable 이 이 고객에게 허용되는 위험등급 상한을 말한다")
    ok += hit

    hit = "KB 성장형 MP" in text and "KB 온국민TDF2040 C-P" in text
    print(f"{'✓' if hit else '✗'} 게이트를 통과한 상품이 목록으로 나온다")
    ok += hit

    # "왜 이건 없어?" 에 답할 수 있어야 목록을 믿을 수 있다.
    hit = "KB 글로벌리츠 ETF" in text and "허용 상한" in text.split("제외된 상품")[-1]
    print(f"{'✓' if hit else '✗'} 제외된 상품과 그 사유가 함께 나온다")
    ok += hit

    # 답이 상품명을 말할 텐데, 그 이름이 이번 턴 원장에 있어야 통과한다(위 ③ 의 조임).
    hit = verify_texts("KB 성장형 MP 를 보실 수 있어요.", tools.ledger_texts([found]),
                       known_products=known)[0]
    print(f"{'✓' if hit else '✗'} suitable 재료로 쓴 답변이 검증을 통과한다")
    ok += hit

    hit = "suitable" in tools.TOOLS and "suitable" in ANSWER_SHAPES
    print(f"{'✓' if hit else '✗'} suitable 이 도구 목록과 답변 형태 요구 양쪽에 있다")
    ok += hit

    # 고객 화면이 닫혀 있으면 성립하지 않는다(§3) — 카탈로그에도 안 뜬다.
    hit = ("suitable" not in tools.usable({})
           and "suitable" in tools.usable({"customer_id": cid}))
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있으면 suitable 을 제안하지 않는다")
    ok += hit

    # ── 스탠스 — 권유가 아니라 정보 제공 ───────────────────────────
    #
    # 표시는 **코드가** 붙인다(guard.py 규약). 프롬프트로 톤만 잡으면 LLM 이 무시해도
    # 아무도 모른다 — 검증기는 수치·상품명만 보지 톤은 안 본다.
    note = kb_index.advisory_note(KB)
    hit = bool(note) and "정보 제공" in note and "자본시장" in note
    print(f"{'✓' if hit else '✗'} 인용 고지를 지식베이스 선언에서 읽어 온다")
    ok += hit

    hit = any("정보 제공" in n for n in (found or {}).get("notices") or [])
    print(f"{'✓' if hit else '✗'} 적합성 판정 재료에 정보제공 고지가 붙는다")
    ok += hit

    # 선언이 없는 재료에는 붙지 않는다 — 무조건 붙는 표시는 §7 이 막는 것이다.
    hit = tools.advisory_mark({}) is None
    print(f"{'✓' if hit else '✗'} 선언이 없으면 고지를 붙이지 않는다")
    ok += hit

    # 2026-09-02 개정(§8 관리대장): 직원 대상 도구라 특정 상품을 짚어 말하는 것은 허용하고,
    # 남는 경계는 표현이다 — «이런 상품이 있습니다» 톤까지, 권유(«추천드립니다»)는 금지.
    hit = ("특정해 말하" in COMPOSE_SYSTEM
           and "권유 표현은 쓰지 않는다" in COMPOSE_SYSTEM
           and "직원이 정한다" in COMPOSE_SYSTEM)
    print(f"{'✓' if hit else '✗'} 생성 지시가 상품 특정을 허용하되 권유 표현을 금지한다")
    ok += hit

    hit = "권유 표현" in ANSWER_SHAPES["lineup"]
    print(f"{'✓' if hit else '✗'} lineup 의 답변 형태도 권유 표현 금지를 요구한다")
    ok += hit

    hit = "투자권유가 아니라는 표시" in ANSWER_SHAPES["suitable"]
    print(f"{'✓' if hit else '✗'} suitable 의 답변 형태가 '권유 아님'을 요구한다")
    ok += hit
    return ok


def check_no_repeat() -> int:
    """좁히는 후속 질문에 앞 답을 통째로 다시 세우지 않는가.

    회귀 대상: 「그 중에 ISA 만기자금이랑 같이 가져갈 만한 건?」에 「자료가 없어요」로 잘
    시작해 놓고, 직전 턴에서 방금 말한 적합성 목록 8종 + 제외 4종을 그대로 반복했다
    (실 LLM 시연 대본 T11, 1,021자). **지시로 못 막는다** — 프롬프트에 실리는 이전 대화는
    직원 질문만이고 답변 원문이 없어서(state.Turn) LLM 은 자기가 무엇을 나열했는지 볼 수
    없다. 답변 원문을 싣는 것도 답이 아니다: 그 수치를 되받으면 이번 턴 원장 밖이라
    `verify` 가 답을 통째로 버린다(§6). 그래서 **도구 이름만** 턴에 남기고, 겹침 판정은
    코드가 한다.
    """
    ok = 0
    ev = tools._ev("suitable", "q", "■ 재료", [{"id": "s.1", "title": "적합성"}])
    seen: dict[str, str] = {}
    orig = plan.generate
    plan.generate = lambda p, **kw: seen.setdefault("p", p) or "답변"
    try:
        plan.compose({"question": "그 중에 ISA 만기자금이랑 같이 가져갈 만한 건?",
                      "evidence": [ev],
                      "history": [{"question": "그럼 이 고객한테 뭘 권할 수 있어?",
                                   "tools": ["suitable"]}]})
        hit = "직전 답변과 겹치는 자료" in seen["p"]
        print(f"{'✓' if hit else '✗'} 직전 턴과 재료가 겹치면 반복 금지 블록이 실린다")
        ok += hit

        seen.clear()
        plan.compose({"question": "q", "evidence": [ev],
                      "history": [{"question": "IRP 세액공제 한도?", "tools": ["fact"]}]})
        hit = "직전 답변과 겹치는 재료" not in seen["p"]
        print(f"{'✓' if hit else '✗'} 재료가 다르면 붙지 않는다")
        ok += hit

        seen.clear()
        plan.compose({"question": "q", "evidence": [ev], "history": []})
        hit = "직전 답변과 겹치는 재료" not in seen["p"]
        print(f"{'✓' if hit else '✗'} 첫 턴에는 붙지 않는다")
        ok += hit
    finally:
        plan.generate = orig

    # 형태 요구와 모순이 되면 안 된다 — 「목록을 실어라」와 「다시 세우지 마라」가 함께
    # 걸리면 LLM 은 어느 쪽이든 지키려다 재료를 지어낸다(§5, T13 과 같은 부류).
    from pension_agent.consult_agent.prompts import REPEAT_BLOCK
    hit = "이미 채운 항목은 다시 채우지 않아도 된다" in REPEAT_BLOCK
    print(f"{'✓' if hit else '✗'} 반복 금지 블록이 형태 요구를 명시적으로 풀어준다")
    ok += hit

    # 턴 기록에 도구 이름이 남아야 판정이 성립한다 — **답변 원문은 남기지 않는다**(§6).
    orig_agent = G._AGENT
    try:
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": "KB 중립형 MP 는 최근 1년 5.1% 예요.", "sources": [],
            "evidence": [ev, tools._ev("customer", "q", "■ 고객", [])],
        })})
        out = G.ask("이 고객한테 뭘 권할 수 있어?")
    finally:
        G._AGENT = orig_agent
    turn = out["history"][-1]
    hit = turn.get("tools") == ["customer", "suitable"]
    print(f"{'✓' if hit else '✗'} 턴 기록이 무슨 재료로 답했는지 남긴다({turn.get('tools')})")
    ok += hit

    # 답변 원문은 턴 기록에는 남지만(`Turn.answer` — last_answer 도구의 재료, 2026-09-10)
    # **프롬프트의 대화 맥락에는 실리지 않는다.** 실리면 LLM 이 그 수치를 되받고 §6 이 답을
    # 통째로 버린다 — 이 검사가 지키는 것은 그 경계다.
    from pension_agent.consult_agent.state import format_history
    hit = "5.1" not in format_history(out["history"]) and "5.1" in (turn.get("answer") or "")
    print(f"{'✓' if hit else '✗'} 답변 수치는 턴 기록에만 남고 프롬프트 맥락에는 실리지 않는다")
    ok += hit
    return ok


def check_last_answer() -> int:
    """직전 답변을 다시 쓰는 턴 — 「고객에게 할 말 좀 더 짧게 줄여줘」(2026-09-10 실측).

    실측: 「증권사는 ETF 종류가 많던데요」 화법을 답한 다음 턴의 그 요청이 **브리핑 수정**
    (correction)으로 분류돼, 화법과 무관한 AI브리핑 문장(「만기 예정 예금이 있어…」)을 고쳐
    «이렇게 반영할게요»로 끝났다. 원인이 둘이었다 — ① 직전 답변 원문이 어디에도 재료로
    없었다(대화 맥락은 질문만 싣는다) ② 분류가 어긋나면 기본값(계획 루프)으로 떨어진다는
    규약이 correction 노드에는 없었다.

    고정하는 것: 답변 원문이 턴 기록에 남고 `last_answer` 도구가 그것을 원장에 싣는다 ·
    프롬프트 맥락에는 여전히 안 실린다 · 다시 쓴 답변이 직전 답변의 수치를 옮겨도 §6 을
    통과한다 · 되묻기 판정이 돌지 않는다 · correction 노드가 «화면 문장이 아니다»면 답을
    내지 않고 계획 루프로 넘어간다(그래프 배선까지).
    """
    from pension_agent.consult_agent import prompts, routing as R
    from pension_agent.consult_agent.nodes import clarify as CL, correction as CORR
    from pension_agent.consult_agent.state import HISTORY_LIMIT, format_history

    ok = 0
    print("\n[직전 답변 다시 쓰기 — last_answer 도구 · correction 되돌림]")

    prev = ("증권사의 많은 상품 수보다 '선별된 선택'이 더 중요하다는 논리로 대응하는 게 핵심이에요.\n\n"
            "\"현재 당행에서 판매 중인 ETF는 총 193종(국내 90종, 해외 103종)이고, 라인업을 "
            "최대 400개까지 확대할 예정입니다.\"\n\n"
            f"{plan.MATERIAL_MARKS}\n· 본부 공식 자료\n· 직원 교육자료\n\n"
            "— «만기예금 보유» 고객에게 쓰는 화법 2건, 보여드릴까요? (네 / 아니오)")
    history = [
        {"question": "고객이 증권사가 더 좋지 않냐고 하시네", "tools": ["pitch"], "answer": None,
         "pending_clarify": {"question": "어떤 점을 이유로?", "options": ["수수료 혜택", "ETF 상품 종류"]}},
        {"question": "ETF 상품 종류", "tools": ["pitch"], "answer": prev,
         "sources": [{"id": "pitch.k03.005", "title": "ETF 종류 반론", "doc": "마스터북",
                      "score": 2.0, "page": None, "role": tools.GROUND},
                     {"id": "m.004", "title": "가드", "doc": "d", "score": None, "page": None,
                      "role": tools.CAUTION}],
         "marks": ["본부 공식 자료", "직원 교육자료"]},
    ]
    state = {"question": "고객에게 해야 할 말 좀 더 짧게 줄여줘", "history": history}

    # ① 도구가 카탈로그에 서는 조건은 코드가 아는 값이다 — 다시 쓸 답변이 있을 때만.
    hit = ("last_answer" not in tools.usable({})
           and "last_answer" not in tools.usable({"history": [history[0]]})   # 되묻기 턴뿐
           and "last_answer" in tools.usable(state))
    print(f"{'✓' if hit else '✗'} last_answer 는 다시 쓸 답변이 있을 때만 카탈로그에 선다")
    ok += hit

    # ② 원장에 실리는 것은 직전 답변 본문이고 화면 장치(제안 문구·표시 블록)는 뗀다.
    found = tools.run("last_answer", state, "직전 답변")
    hit = (bool(found) and "193종" in found["text"] and "ETF 상품 종류" in found["text"]
           and "(네 / 아니오)" not in found["text"] and plan.MATERIAL_MARKS not in found["text"])
    print(f"{'✓' if hit else '✗'} 직전 답변 본문이 원장에 실리고 화면 장치는 떼어진다")
    ok += hit

    # ③ 출처는 직전 답변의 «근거»만 잇고 «주의»(고객 상태 가드)는 잇지 않는다. 표시는 잇는다.
    hit = (bool(found) and [s["id"] for s in found["sources"]] == ["pitch.k03.005"]
           and all("role" not in s for s in found["sources"])
           and found["marks"] == ["본부 공식 자료", "직원 교육자료"])
    print(f"{'✓' if hit else '✗'} 출처는 원래 답변의 근거를 잇고 가드는 잇지 않는다 · 표시도 잇는다")
    ok += hit

    hit = tools.run("last_answer", {"question": "q", "history": []}, "직전 답변") is None \
        and tools.run("last_answer", {"question": "q", "history": [history[0]]}, "x") is None
    print(f"{'✓' if hit else '✗'} 다시 쓸 답변이 없으면 지어내지 않는다(None)")
    ok += hit

    # 출처가 하나도 없던 답변(메타 안내 등)은 «직전 답변» 하나를 출처로 세운다 — 지어내지 않는다.
    bare = tools.run("last_answer", {"question": "q", "history": [{"question": "뭘 도와줘?",
                                                                  "answer": "제가 도울 수 있는 것…"}]}, "x")
    hit = bool(bare) and [s["id"] for s in bare["sources"]] == [tools.SELF_SOURCE["id"]]
    print(f"{'✓' if hit else '✗'} 출처 없는 답변은 «직전 답변» 자체를 출처로 세운다")
    ok += hit

    # ④ 다시 쓴 답변이 직전 답변의 수치를 옮겨도 §6 을 통과한다 — 원장이 곧 직전 답변이다.
    #    그리고 작성 프롬프트에 «다시 쓰는 턴» 블록이 실리고, 반복 금지 블록은 실리지 않는다
    #    (직원이 다시 정리해 달라고 한 턴이다).
    seen: dict[str, str] = {}
    orig = plan.generate

    def _rewriter(p, **kw):
        seen.setdefault("p", p)
        return '"당행 ETF는 193종이고 400개까지 늘릴 예정입니다."'

    plan.generate = _rewriter
    try:
        out = plan.compose({**state, "evidence": [found]})
    finally:
        plan.generate = orig
    hit = out["answer"].startswith('"당행 ETF는 193종이고 400개까지') \
        and "이전 답변을 다시 쓰는 턴" in seen["p"] and "직전 답변과 겹치는 자료" not in seen["p"] \
        and "본부 공식 자료" in out["answer"] and [s["id"] for s in out["sources"]] == ["pitch.k03.005"]
    print(f"{'✓' if hit else '✗'} 다시 쓴 답변이 직전 답변의 수치로 §6 을 통과하고 표시·출처가 따라온다")
    ok += hit

    seen.clear()
    plan.generate = lambda p, **kw: seen.setdefault("p", p) or "답"
    try:
        plan.compose({"question": "q", "evidence": [tools._ev("fact", "q", "■ 재료", [])]})
    finally:
        plan.generate = orig
    hit = "이전 답변을 다시 쓰는 턴" not in seen["p"] and "last_answer" in prompts.ANSWER_SHAPES
    print(f"{'✓' if hit else '✗'} 다시 쓰는 턴이 아니면 그 블록이 붙지 않는다 · 형태 요구는 등록돼 있다")
    ok += hit

    # ⑤ 되묻기 판정이 돌지 않는다 — 갈래가 있었다면 그 답을 쓴 턴에서 이미 끝났다.
    hit = "last_answer" in CL._NO_BRANCH and not CL.applicable({**state, "evidence": [found]}) \
        and found["text"] not in CL.settled_block({**state, "evidence": [found]})
    print(f"{'✓' if hit else '✗'} 다시 쓰는 턴에는 되묻기 판정이 없고 «이미 정해진 것»에도 안 실린다")
    ok += hit

    # ⑥ 프롬프트의 대화 맥락에는 답변 원문이 여전히 안 실린다(§6) — 원장으로만 들어간다.
    hit = "193종" not in format_history(history) and HISTORY_LIMIT >= 12
    print(f"{'✓' if hit else '✗'} 대화 맥락에는 답변 원문이 안 실리고, 맥락 창은 12턴 이상이다")
    ok += hit

    # ⑦ 진입점이 답변 원문을 턴에 남긴다 — 되묻기·LLM 장애 턴은 비운다.
    orig_agent = G._AGENT
    try:
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": "답변 본문", "sources": [{"id": "x", "role": tools.GROUND}],
            "evidence": [tools._ev("fact", "q", "■ 재료", [{"id": "x", "title": "t"}])]})})
        answered = G.ask("q")["history"][-1]
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": "어느 쪽인가요?", "sources": [], "clarify": {"question": "어느 쪽인가요?"}})})
        asked = G.ask("q")["history"][-1]
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": plan.LLM_FAILED.format(reason="x"), "sources": [], "llm_error": "x"})})
        dead = G.ask("q")["history"][-1]
        # 근거 0건·도구 고장 안내는 상태 키가 없다 — 문장으로 가려야 한다(케이스 12c: 「찾지
        # 못했다」가 «이전 답변»이 되어 있는 자료를 없다고 답했다).
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": plan.NO_EVIDENCE + plan.TRIED.format(calls="last_answer:[1]"), "sources": [],
            "steps": [{"tool": "last_answer", "query": "[1]", "outcome": "miss"}]})})
        empty = G.ask("q")["history"][-1]
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": plan.TOOL_FAILED.format(what="화법", reasons="KeyError: x"), "sources": [],
            "steps": [{"tool": "pitch", "query": "q", "outcome": "failed", "reason": "KeyError: x"}]})})
        broken = G.ask("q")["history"][-1]
    finally:
        G._AGENT = orig_agent
    hit = (answered.get("answer") == "답변 본문" and answered.get("sources") == [{"id": "x", "role": tools.GROUND}]
           and asked.get("answer") is None and dead.get("answer") is None
           and empty.get("answer") is None and broken.get("answer") is None
           and not plan.is_failure_notice("답변 본문") and not plan.is_failure_notice(None))
    print(f"{'✓' if hit else '✗'} 진입점이 답변 원문을 턴에 남기고 되묻기·장애·근거 0건·도구 고장 턴은 비운다")
    ok += hit

    # ⑧ 라우팅·계획 프롬프트가 이 요청을 correction 이 아니라 situation·last_answer 로 이끈다.
    hit = ("짧게 줄여줘" in prompts.ROUTE_PROMPT and "correction 이 아니라 situation" in prompts.ROUTE_PROMPT
           and "last_answer" in prompts.PLAN_PROMPT)
    print(f"{'✓' if hit else '✗'} 라우팅·계획 프롬프트가 «방금 한 답변 고쳐줘»를 situation·last_answer 로 이끈다")
    ok += hit

    # ⑨ correction 노드 — 분류가 «화면 문장이 아니다»면 답을 내지 않고 계획 루프로 넘긴다.
    #    고객 화면이 닫혀 있는데 다시 쓸 답변은 있는 턴도 같다. 기록(감사로그)도 남기지 않는다.
    hit = R.route_correction({}) == "plan" and R.route_correction({"answer": "반영"}) == "__end__"
    print(f"{'✓' if hit else '✗'} 분기표 — 답이 없으면 계획 루프, 있으면 끝")
    ok += hit

    out = CORR.correction({**state, "customer_id": None})
    hit = not out.get("answer") and out.get("intent") == routing.DEFAULT_INTENT
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있어도 다시 쓸 답변이 있으면 계획 루프로 넘긴다")
    ok += hit

    hit = "고객 화면을 먼저" in CORR.correction({"question": "고쳐줘", "history": []})["answer"]
    print(f"{'✓' if hit else '✗'} 다시 쓸 답변도 없으면 예전대로 고객 화면을 먼저 열라고 답한다")
    ok += hit

    from pension_agent.strategy_agent import agent as SA, customer as SC
    orig_profile, orig_propose, orig_gen, logged = SC.get_profile, SA.propose, CORR.generate, []
    SC.get_profile = lambda cid: object()
    SA.propose = lambda p: {"facts": {"items": []}, "sentence": "만기 예정 예금이 있어 빠른 운용 결정이 필요합니다.",
                            "insight": "i"}
    CORR.generate = lambda prompt, **kw: (logged.append(prompt) or
                                          '{"target": "not_briefing", "item_id": null, "revised_text": "", "reject_reason": ""}')
    orig_log = CORR._log_correction
    CORR._log_correction = lambda *a, **kw: logged.append("LOGGED")
    try:
        out = CORR.correction({**state, "customer_id": "CX"})
    finally:
        SC.get_profile, SA.propose, CORR.generate, CORR._log_correction = orig_profile, orig_propose, orig_gen, orig_log
    hit = (not out.get("answer") and out.get("intent") == routing.DEFAULT_INTENT
           and "LOGGED" not in logged and "ETF 상품 종류" in logged[0]
           and "not_briefing" in prompts.CORRECTION_SYSTEM)
    print(f"{'✓' if hit else '✗'} 분류가 not_briefing 이면 반영도 기록도 없이 넘긴다 · 프롬프트가 이전 대화를 본다")
    ok += hit

    # ⑩ 그래프 배선 — understand 가 correction 으로 오분류해도 턴이 다시 쓴 답변으로 끝난다.
    def stub_plan_last(st):
        found = tools.run("last_answer", st, "직전 답변")
        return {"plan_done": True, "evidence": [found] if found else [],
                "steps": [{"tool": "last_answer", "query": "직전 답변", "outcome": "found"}]}

    orig_u, orig_p, orig_g = G.understand, G.plan_step, plan.generate
    G.understand = lambda st: {"intent": "correction", "utterance": st["question"]}
    G.plan_step = stub_plan_last
    plan.generate = lambda p, **kw: '"당행 ETF는 193종이고 400개까지 늘릴 예정입니다."'
    try:
        out = G.build_agent().invoke({"question": state["question"], "history": history, "customer_id": None})
    finally:
        G.understand, G.plan_step, plan.generate = orig_u, orig_p, orig_g
    hit = out["answer"].startswith('"당행 ETF는 193종') and [e["tool"] for e in out["evidence"]] == ["last_answer"] \
        and "반영할게요" not in out["answer"]
    print(f"{'✓' if hit else '✗'} 그래프 — correction 오분류가 다시 쓴 답변으로 끝난다(브리핑을 건드리지 않는다)")
    ok += hit

    # ⑪ 몇 턴 전 답변 되짚기 — LLM 이 번호를 고르고 코드가 **한 턴만** 확정한다(2026-09-10 결정).
    #    번호가 없거나 범위 밖이거나 되묻기 턴이면 직전 답변이고, 문장 가운데 숫자(금액)는 번호가
    #    아니다. 「자세히」는 그 턴의 근거 카드를 id 로 되싣는다 — 재검색이 아니다.
    from pension_agent.consult_agent.state import KB, numbered_history
    fact = next(c for c in KB.cards if c["_kind"] == "fact" and c.get("value"))
    proc = next(c for c in KB.cards if c["_kind"] == "procedure" and c.get("screens"))
    long_q = "우리 수수료가 얼마고, 증권사는 무료라는데 뭐라고 답하지? 그리고 ETF 는 어떻게 말하지?"
    many = [{"question": q, "tools": ["fact"], "answer": f"답변{i} — 세액공제 한도 900만원",
             "sources": [{"id": fact["id"], "title": "t", "doc": "d", "score": 1.5, "page": None, "role": tools.GROUND},
                         {"id": proc["id"], "title": "p", "doc": "d", "score": 1.0, "page": None, "role": tools.GROUND},
                         {"id": "session.CX", "title": "상담 이력", "doc": "d", "score": None, "page": None, "role": tools.GROUND}],
             "marks": ["본부 공식 자료"]}
            for i, q in enumerate(["수수료 얼마야?", "지난번엔 무슨 얘기 했지?", long_q, "왜 관리 대상이야?",
                                   "뭘 권할 수 있어?", "세미나 있어?", long_q, "타행보다 싼가?"], 1)]
    many[1]["answer"] = None            # 되묻기 턴 — 답변이 없다
    many[1]["pending_clarify"] = {"question": "어느 상담?", "options": ["7월", "8월"]}
    ref = lambda q: tools.referenced_turn(many, q)[0]  # noqa: E731
    hit = ref("[3] 근거") == 3 and ref("3") == 3 and ref("300만원 답변 요약") == 8 \
        and ref("[99]") == 8 and ref("") == 8 and ref("[2]") == 8 \
        and numbered_history(many)[2][1]["question"] == long_q
    print(f"{'✓' if hit else '✗'} 턴 번호 해석 — [3]·3 은 그 턴, 금액·범위 밖·빈 값·되묻기 턴은 직전 답변")
    ok += hit

    detail = tools.run("last_answer", {"question": "아까 수수료 얘기 자세히 설명해줘", "history": many}, "[3] 근거")
    brief = tools.run("last_answer", {"question": "아까 수수료 얘기 요약해줘", "history": many}, "[3]")
    hit = (bool(detail) and detail["text"].count("■ 이전 답변 원문") == 1 and "[3]" in detail["text"]
           and tools.CARDS_HEADER in detail["text"] and proc["screens"][0] in detail["atomic"]
           and any(c.get("id") == fact["id"] for c in detail["related"])
           and [s["id"] for s in detail["sources"]] == [fact["id"], proc["id"], "session.CX"]
           and detail["meta"] == {"turn": 3, "question": long_q, "with_cards": True})
    print(f"{'✓' if hit else '✗'} «근거» 붙이면 그 턴의 카드를 id 로 되싣는다 — 화면번호 스팬·관계 선언이 따라오고 한 턴만 실린다")
    ok += hit

    hit = bool(brief) and tools.CARDS_HEADER not in brief["text"] and not brief["atomic"] \
        and brief["meta"]["with_cards"] is False and "답변3" in brief["text"]
    print(f"{'✓' if hit else '✗'} «근거» 없으면 답변 원문만 싣는다(요약·줄이기)")
    ok += hit

    # 카드가 아닌 출처(상담 이력·고객 원장)는 되실을 것이 없어 건너뛴다 — 지어내지 않는다.
    only_log = [{"question": "지난번?", "answer": "기록: 7월 상담", "marks": [],
                 "sources": [{"id": "session.CX", "title": "상담 이력", "doc": "d", "score": None, "page": None}]}]
    got = tools.run("last_answer", {"question": "자세히", "history": only_log}, "[1] 근거")
    hit = bool(got) and tools.CARDS_HEADER not in got["text"] and got["meta"]["with_cards"] is False
    print(f"{'✓' if hit else '✗'} 카드가 아닌 출처는 되싣지 않는다")
    ok += hit

    # ⑫ 대화 맥락 창 — 기록은 전부 남고, 프롬프트 한 줄만 오래된 턴의 질문을 접는다.
    #    번호는 `numbered_history` 와 같아야 한다 — 갈리면 LLM 이 맥락에서 본 번호로 다른 턴을 꺼낸다.
    from pension_agent.consult_agent.state import HISTORY_OLD_CHARS, HISTORY_VERBATIM
    block = format_history(many)
    hit = (f"[3] 직원: {long_q[:HISTORY_OLD_CHARS]}…" in block          # 오래된 턴 — 접힌다
           and f"[7] 직원: {long_q}" in block                            # 최근 4턴 — 원문
           and "[8] 직원: 타행보다 싼가?" in block and "(에이전트가 되물음: 어느 상담?" in block
           and len(numbered_history(many)) == len(many) and HISTORY_VERBATIM == 4
           and all(f"[{i}] 직원:" in block for i, _t in numbered_history(many)))
    print(f"{'✓' if hit else '✗'} 오래된 턴의 질문만 {HISTORY_OLD_CHARS}자로 접히고 번호·되묻기 표시는 남는다")
    ok += hit

    # ⑬ 호출별 입력 크기가 관측 점수로 남는다 — gemma 부담이 어느 호출에서 오는지 볼 자리.
    from pension_agent import llm as LLM, observability as OBS
    seen_scores: list[tuple] = []
    orig_score = OBS.score
    OBS.score = lambda n, v, comment=None: seen_scores.append((n, v, comment))
    try:
        LLM._observe("consult.compose", 0.0, "p" * 100, "s" * 50, 10, 0.2, "",
                     meta={}, output="o", error=None)
    finally:
        OBS.score = orig_score
    hit = ("prompt_chars", 150, "consult.compose") in seen_scores
    print(f"{'✓' if hit else '✗'} LLM 호출마다 prompt_chars 점수가 남는다(시스템 프롬프트 포함)")
    ok += hit
    return ok


def check_table_row_names() -> int:
    """표의 행 이름을 **답변이 부르는 표기**로도 알아보는가.

    회귀 대상: 행 이름이 「사용자부담금(퇴직금)」한 덩이라, 답변이 「퇴직금(사용자부담금)」·
    「사용자부담금」으로 부르면 그 행을 말한 줄 몰랐다. 그러면 그 행이 «답변이 말하지 않은
    행»이 되어 그 값이 남의 값으로 신고되고, **표의 네 구간을 전부 정확히 옮긴 답변이
    폐기됐다**(실 LLM 시연 대본 T8b). 카드의 pitfalls 는 반대로 「구간을 확인하지 않은 단일
    수치 답변은 오답」이라고 적혀 있어, 재료와 검증기가 서로 모순이었다.

    이름을 못 알아본 것은 판정 불가이지 위반이 아니다(§6). 별칭은 «말한 행»을 늘리는
    쪽이라 판정을 좁히기만 한다 — 오짝 검출은 그대로여야 한다(아래 ③④).
    """
    from pension_agent.consult_agent.evidence import relations
    from pension_agent.consult_agent.state import KB as _KB
    ok = 0
    card = next((c for c in _KB.cards if c["id"] == "fact.k04.f50"), None)
    if card is None:
        print("✗ fact.k04.f50 카드를 찾지 못했다")
        return 0

    both = ("퇴직금(사용자부담금) 대면은 5천만원 미만 연 0.45%, 5천만원 이상 연 0.38%이고, "
            "비대면은 5천만원 미만 연 0.20%, 5천만원 이상은 면제예요. 가입자부담금 대면은 "
            "1억원 미만 연 0.28%, 1억원 이상 연 0.25%이고, 비대면은 1억원 미만 연 0.23%, "
            "1억원 이상 연 0.21%예요.")
    hit = not relations.check(both, [card])
    print(f"{'✓' if hit else '✗'} 두 부담금을 함께 정확히 말한 답변이 통과한다(T8b 회귀)")
    ok += hit

    one = "가입자부담금 대면은 1억원 미만 연 0.28%, 1억원 이상 연 0.25%예요."
    hit = not relations.check(one, [card])
    print(f"{'✓' if hit else '✗'} 한쪽만 말한 답변도 그대로 통과한다")
    ok += hit

    wrong = "가입자부담금 대면 1억원 미만은 연 0.45%예요."
    hit = bool(relations.check(wrong, [card]))
    print(f"{'✓' if hit else '✗'} 가입자부담금에 사용자부담금 값을 붙이면 잡힌다")
    ok += hit

    # 예전에는 이 방향이 **판정 불가로 통과**했다 — 「사용자부담금(퇴직금)」을 아예 못
    # 알아봐서 said 가 비었기 때문이다. 별칭이 그 구멍을 함께 막는다.
    flipped = "퇴직금(사용자부담금) 대면 5천만원 미만은 연 0.28%예요."
    hit = bool(relations.check(flipped, [card]))
    print(f"{'✓' if hit else '✗'} 사용자부담금에 가입자부담금 값을 붙여도 잡힌다")
    ok += hit
    return ok


def check_suitable_shape() -> int:
    """적합성 재료와 답변 형태가 서로 모순되지 않는가 — 제외가 0건인 고객.

    회귀 대상: 형태가 「제외된 상품과 사유」를 무조건 요구하는데 재료는 제외 0건일 때
    침묵했다. LLM 은 형태를 지키려고 **통과 목록에서 하나를 골라 뺐다** — 12종을 11종이라
    말하고 "상담 실익이 없다"는 재료에 없는 사유를 붙였다(실 LLM 시연 대본 T13, 정민석).
    재료에 없는 것을 요구한 쪽이 원인이다.

    「게이트」는 개발 용어라 재료에서 걷어낸다 — 재료에 있으면 답변이 그대로 쓴다.
    """
    from pension_agent.consult_agent.prompts import ANSWER_SHAPES, SHAPE_BLOCK
    ok = 0
    open_top = tools._suitable({"customer_id": "181245-3097614"}, "q")   # 상한 = 최고 등급
    capped = tools._suitable({"customer_id": "188406-7352194"}, "q")     # 제외가 있는 고객
    if not open_top or not capped:
        print("✗ 적합성 재료를 만들지 못했다")
        return 0

    hit = "안내할 수 없는 상품 없음" in open_top["text"]
    print(f"{'✓' if hit else '✗'} 제외 0건이면 재료가 «없음»을 말한다(침묵하지 않는다)")
    ok += hit

    hit = "안내할 수 없는 상품 4종" in capped["text"]
    print(f"{'✓' if hit else '✗'} 제외가 있으면 종수와 사유를 그대로 싣는다")
    ok += hit

    hit = all("게이트" not in x["text"] for x in (open_top, capped))
    print(f"{'✓' if hit else '✗'} 재료 본문에 개발 용어 «게이트»가 없다")
    ok += hit

    hit = all("게이트" not in (s.get("doc") or "")
              for x in (open_top, capped) for s in x["sources"])
    print(f"{'✓' if hit else '✗'} 근거 출처 이름에도 «게이트»가 없다")
    ok += hit

    hit = "안내할 수 없는 상품이 있으면" in ANSWER_SHAPES["suitable"]
    print(f"{'✓' if hit else '✗'} 형태가 제외를 조건부로 요구한다")
    ok += hit

    hit = "자료가 적은 종수를 그대로 쓴다" in ANSWER_SHAPES["suitable"]
    print(f"{'✓' if hit else '✗'} 형태가 통과 종수를 그대로 쓰라고 요구한다")
    ok += hit

    hit = "자료에 없는 항목은 쓰지 않는다" in SHAPE_BLOCK
    print(f"{'✓' if hit else '✗'} 형태 머리말이 «없으면 안 쓴다»를 전역으로 건다")
    ok += hit

    # 재료가 말하는 수와 보여주는 목록은 같아야 한다 — 12명 전원.
    #
    # 회귀 대상(2026-09-07 실측, 오세훈·박정호): 제외 목록을 5건에서 자르던 상한 때문에
    # 안정추구형(제외 6건) 재료가 «안내할 수 없는 상품 6종»이라 쓰고 5건만 실었다. LLM 이
    # 목록을 세어 «5종»이라 쓰자 verify 가 원장에 없는 수로 답을 버리고 이 블록을 덤프했다.
    import re as _re
    from pension_agent.strategy_agent.customer import PERSONAS
    _head = _re.compile(r"^── 안내할 수 (있는|없는) 상품 (\d+)종")
    mismatch: list[str] = []
    for p in PERSONAS:
        ev = tools._suitable({"customer_id": p.id}, "q")
        if not ev:
            continue
        section, said, listed = None, {}, {}
        for line in ev["text"].splitlines():
            m = _head.match(line)
            if m:
                section = m.group(1)
                said[section] = int(m.group(2))
                listed.setdefault(section, 0)
            elif line.startswith("── "):
                section = None
            elif section and line.startswith("· ") and not line.startswith("· [포트폴리오]"):
                listed[section] += 1
        for section, n in said.items():
            if listed.get(section) != n:
                mismatch.append(f"{p.nm}:{section} {n}종 ≠ 목록 {listed.get(section)}줄")
    hit = not mismatch
    print(f"{'✓' if hit else '✗'} 머리말의 종수와 목록 줄 수가 12명 전원에서 같다"
          + (f" ({', '.join(mismatch)})" if mismatch else ""))
    ok += hit
    return ok


def check_question_echo() -> int:
    """직원이 질문에 넣은 수치를 **되받아 말한** 답변이 살아남는가.

    회귀 대상: 원장은 턴 단위인데 대화는 이어진다. "총급여 6천만원이면 얼마 돌려받아?"
    에 답하려면 답변이 그 전제를 옮겨 적는데(6,000), 카드가 아는 경계값은 5,500 뿐이라
    **맞는 답변이 원장 밖 수치로 통째로 폐기되고** 근거 원문이 덤프됐다 — 실 LLM 시연
    대본 T2 의 실제 결과다. §6 이 "검증기가 옳은 문장을 거부하는 것은 틀린 문장을
    통과시키는 것보다 나쁘다"고 적어 둔 자리다.

    넓히는 폭은 «되받기» 하나다. 질문의 수치로 **계산한** 값과 **상품명**은 그대로 막힌다.
    """
    ok = 0
    VALUE = "총급여 5,500만원 이하 16.5%, 초과 13.2% (지방소득세 포함)"
    ev = tools._ev("fact", "q", f"■ 세액공제율\n{VALUE}",
                   [{"id": "f.1", "title": "세액공제율"}], atomic=[VALUE])
    q = "총급여 6천만원이면 얼마 돌려받아?"
    echoed = "총급여 6,000만원이면 초과 구간이에요."

    # ① 구멍 재현 — 질문을 재료로 안 보면 되받은 문장이 폐기된다.
    hit = not verify_texts(echoed, [ev["text"]])[0]
    print(f"{'✓' if hit else '✗'} 질문을 빼면 되받은 답변이 폐기된다(구멍 재현)")
    ok += hit

    # ② 질문을 함께 보면 통과한다 — 직원이 방금 말한 값을 옮겨 적은 것이다.
    hit = verify_texts(echoed, [ev["text"]], echoable=[q])[0]
    print(f"{'✓' if hit else '✗'} 질문의 수치를 되받은 답변은 통과한다")
    ok += hit

    # ③ 되받기까지다. 질문 수치로 **계산한** 값은 질문에도 원장에도 없다.
    derived = "총급여 6,000만원이면 792만원을 돌려받아요."
    good, bad = verify_texts(derived, [ev["text"]], echoable=[q])
    hit = not good and any("792" in b for b in bad)
    print(f"{'✓' if hit else '✗'} 질문 수치로 계산한 값은 여전히 거부된다")
    ok += hit

    # ④ 상품명 게이트는 넓어지지 않는다 — 이름만 대서 적합성 밖 상품을 올릴 수 없다.
    known = {"KB 글로벌리츠 ETF"}
    hit = not verify_texts("KB 글로벌리츠 ETF 를 보실 수 있어요.", ["다른 재료"],
                           known_products=known, echoable=["KB 글로벌리츠 ETF 어때?"])[0]
    print(f"{'✓' if hit else '✗'} 질문이 부른 상품명은 인용 허가가 되지 않는다")
    ok += hit

    # ⑤ 배선 — compose 가 실제로 이번 턴 질문을 넘긴다(넘기지 않으면 ① 로 되돌아간다).
    orig = plan.generate
    try:
        plan.generate = lambda p, **kw: echoed
        out = plan.compose({"question": q, "evidence": [ev]})
        hit = out["answer"] == echoed
    finally:
        plan.generate = orig
    print(f"{'✓' if hit else '✗'} compose 가 질문을 검증 재료로 넘긴다")
    ok += hit

    # ⑥ 「없는 것은 첫 문장에서 없다고」 — 가진 재료로 다른 질문에 답하지 않게 하는 지시.
    from pension_agent.consult_agent.prompts import COMPOSE_SYSTEM
    hit = "핵심 대상이 자료에 없으면 그것이 결론" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 «없음»을 결론 자리에 세운다")
    ok += hit

    # ⑦ 고객 속성은 원장 값만 — 화법·방법론 카드의 상황 설명(«비대면 개설 + 권유직원
    # 미존재…»)을 이 고객의 속성으로 굳히지 않게 하는 지시. 실 LLM 시연 대본 T4 에서
    # m.045 카드를 근거로 "비대면 신규 계좌"라고 단정했는데, 원장에는 채널 컬럼 자체가
    # 없다 — 숫자가 아니라 검증 게이트에도 걸리지 않는 자리라 지시로 막는다.
    hit = "원장 값에 있는 것만" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 고객 속성 단정을 원장 값으로 한정한다 (T4 회귀)")
    ok += hit

    # ⑧ 인용이 생략한 조건은 코칭 문장이 채운다 — 실 LLM 시연 대본 T9 에서 답변이 표로는
    # 「대면 5천만원 이상 0.38%」라 말하고, 채널 조건을 생략한 대사 원문을 근거로
    # 「5천만원 이상이면 면제」라고 이어 말해 스스로 모순됐다. 원문은 못 고치므로(절대
    # 규칙 1) 조건 보완은 생성 지시가 맡는다.
    hit = "생략한 조건은 코칭 문장이 채운다" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 인용 대사의 생략 조건을 채우게 한다 (T9 회귀)")
    ok += hit

    # ⑨ 상품 나열은 한 줄에 하나 — 실 LLM 시연 대본 T13 에서 12종을 쉼표로 이은 한
    # 문단이 나왔다. 출력 형식의 «불릿 없이»가 목록까지 줄글로 밀어붙인 것이라, 3종
    # 이상 나열에는 예외를 선언한다.
    hit = "3종 이상 나열할 때는 줄글로 잇지 않는다" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 상품 나열을 줄 단위로 세우게 한다 (T13 회귀)")
    ok += hit

    # ⑩ 스냅샷에서 이력·추세를 추정하지 않는다 — 실 LLM 시연 대본 T4 에서 카드의
    # 「당해 납입액 0원」만 보고 "전년 납입 이력이 있었던 고객이라 납입이 끊긴 신호"라고
    # 지어냈다. 원장의 연도별 납입액은 전부 0원인데 0원 연도는 카드 렌더에서 빠지므로
    # (customer._paid_by_year 의 `and v` 필터) LLM 은 과거 값을 본 적이 없다. 성립 요건
    # 6종에 없는 «납입 중단»을 일곱 번째 사유로 세운 것도 같은 문장이다 — 숫자가 없어
    # 검증 게이트에 안 걸리는 자리라 지시로 막는다.
    hit = "과거 이력이나 추세를 추정하지 않는다" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 스냅샷 값의 이력·추세 추정을 금지한다 (T4 회귀)")
    ok += hit
    hit = "사유를 새로 만들어 붙이지 않는다" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 관리 사유를 성립 요건 목록으로 한정한다 (T4 회귀)")
    ok += hit
    return ok


def check_labeled_pairs() -> int:
    """레이블–값 짝(§6) — 「이 항목의 값이라며 남의 수치를 붙였는가」.

    고객 재료의 허용 집합에는 화면 값 말고도 ⑥⑦⑧ 에 실린 화법·반론·참고자료의 수치가 함께
    들어 있다 — 직원이 그것도 묻기 때문에 뺄 수 없다. 그래서 수치 집합 포함 검사만으로는
    **"세액공제 잔여한도는 300만원이에요"(실제 0만원)가 통과했다** — 300 은 화법 문구
    「적립금 300만원 이상…」에 실제로 있는 숫자다. 경계 밖으로 나간 게 아니라 **엉뚱한
    이름표에 갖다 붙인 것**이라, 막을 자리는 verify 가 아니라 relations 다.

    이 테스트가 재는 것 셋. 뒤엣것이 더 중요하다 — 옳은 문장을 거부하는 것은 틀린 문장을
    통과시키는 것보다 나쁘다(relations.py 머리말).
      ① 남의 값을 갖다 붙이면 잡는다
      ② 재료를 그대로 옮긴 답변은 한 줄도 막지 않는다
      ③ 이름이 재료의 다른 자리에도 나오는 항목은 아예 판정하지 않는다(판정 불가)
    """
    ok = 0
    from pension_agent.consult_agent.evidence import relations as REL
    from pension_agent.strategy_agent import customer as CUST

    evs = {p.id: tools.TOOLS["customer"].run({"customer_id": p.id}, "확인") for p in CUST.PERSONAS}

    # ① 그 항목의 값이 아닌 수치를 붙이면 잡는다.
    cid = CUST.PERSONAS[0].id
    cards = tools.ledger_related([evs[cid]])
    rows = REL.checkable(cards[0]["labeled"], cards[0]["context"])
    numeric = [r for r in rows if REL.numbers(r["value"])]
    caught = cases = 0
    for i, row in enumerate(numeric):
        other = numeric[(i + 1) % len(numeric)]
        if other["value"] == row["value"]:
            continue
        # **판정 불가는 놓친 것이 아니다**(relations.py 머리말 · §6). 판정은 이름 뒤의 **첫
        # 수치** 하나를 그 항목의 값과 견주는데(`labeled_mispaired`), 남의 값이 마침 그
        # 수치부터 시작하면 두 문장이 구별되지 않는다 — 잔여한도 0만원인 고객에게 「0원
        # …」으로 시작하는 남의 값을 붙이는 짝이 그렇다. 어떤 구현으로도 못 잡는 짝을
        # 검출률에 넣으면, 재료에 줄이 하나 늘 때마다 회전 짝이 다시 섞여 이 비율이
        # 흔들린다(2026-09-07 부담금별 구성이 실려 11/13 → 11/14 로 떨어졌다).
        said = first_measure(other["value"][:REL.LABEL_NEAR])
        if said is not None and said[1] & REL.numbers(row["value"]):
            continue
        cases += 1
        caught += bool(REL.check(f"{row['label']}은 {other['value']}이에요.", cards))
    hit = cases and caught / cases >= 0.8
    print(f"{'✓' if hit else '✗'} 남의 값을 갖다 붙이면 잡는다 ({caught}/{cases})")
    ok += hit

    # 원래 증상 그대로. 재료 밖 수치가 아니라 **재료 안에 있는 남의 수치**여야 의미가 있다.
    ev = evs[cid]
    room = [r for r in cards[0]["labeled"] if r["label"] == "세액공제 잔여한도"]
    wrong = f"세액공제 잔여한도는 300만원이에요."
    hit = bool(room) and bool(REL.check(wrong, cards)) and _vt(wrong, ev["allow"])[0]
    print(f"{'✓' if hit else '✗'} 수치 검사는 통과하지만 관계 검사가 잡는다 (원래 증상)")
    ok += hit

    # ② 재료를 그대로 옮긴 답변은 막지 않는다 — 9명 전원의 모든 줄.
    false_rej, total = 0, 0
    for e in evs.values():
        c2 = tools.ledger_related([e])
        for line in e["text"].split("\n")[1:]:
            total += 1
            false_rej += bool(REL.check(line.strip("· ").strip(), c2))
    hit = false_rej == 0
    print(f"{'✓' if hit else '✗'} 재료를 그대로 옮긴 답변은 막지 않는다 ({total}줄 · 거짓 거부 {false_rej})")
    ok += hit

    # 여러 항목을 한 답변에 묶어도 마찬가지다.
    joined = 0
    for e in evs.values():
        whole = " ".join(l.strip("· ").strip() for l in e["text"].split("\n")[1:])
        joined += bool(REL.check(whole, tools.ledger_related([e])))
    hit = joined == 0
    print(f"{'✓' if hit else '✗'} 여러 항목을 묶어 말해도 막지 않는다 ({joined}/9)")
    ok += hit

    # ③ 이름이 겹치는 항목은 판정 대상에서 빠진다 — 「수익률」은 다른 값 안에도 있다.
    labels = {r["label"] for r in rows}
    hit = "수익률" not in labels and "운용수익률" in labels
    print(f"{'✓' if hit else '✗'} 이름이 겹치는 항목은 판정하지 않는다(수익률 제외·운용수익률 유지)")
    ok += hit

    # context 를 안 넘기면 문제상황 제목 같은 다른 자리를 못 걸러낸다 — 넘기는 쪽이 안전하다.
    hit = len(REL.checkable(cards[0]["labeled"], cards[0]["context"])) <= \
          len(REL.checkable(cards[0]["labeled"]))
    print(f"{'✓' if hit else '✗'} 재료 전문을 넘기면 판정 대상이 좁아진다(넓어지지 않는다)")
    ok += hit
    return ok


def check_followups() -> int:
    """답변 끝 추천질문 — **재료가 있는 것만 띄운다**가 이 기능의 알맹이다.

    추천질문을 눌렀는데 "근거를 찾지 못했습니다"가 나오면 안 띄우느니만 못하다. 그래서
    suggest 는 후보마다 그 질문에 답할 재료가 실제로 있는지 LLM 없이 먼저 찾아본다.
    없으면 안 뜬다 — 아래 첫 두 검사가 그 계약을 고정한다.

    나머지는 «매 턴 붙지 않는다»(게이트 넷)와 «고객 화면이 닫히면 고객 질문은 없다»,
    그리고 문구가 매번 같지 않다는 것(회전·슬롯)이다.
    """
    from pension_agent.knowledge import kb as KBMOD
    from pension_agent.consult_agent.effects import suggest
    from pension_agent.consult_agent.evidence import facts_qa
    from pension_agent.strategy_agent.customer import PERSONAS

    def ev(tool: str, title: str | None = None) -> dict:
        return {"tool": tool, "query": "q", "text": "t", "atomic": [], "notices": [],
                "notice_scopes": [], "allow": [], "related": [], "marks": [],
                "sources": ([{"id": "x", "title": title}] if title else []), "meta": {}}

    ok = 0

    # ① 재료가 하나도 없으면 아무것도 띄우지 않는다. 이 검사가 이 기능의 존재 이유다 —
    #    빠지면 "누르면 근거 없음"인 질문이 답변마다 세 줄씩 붙는다.
    orig_retrieve, orig_facts = KBMOD.retrieve, facts_qa.search
    try:
        KBMOD.retrieve = lambda *a, **k: []
        facts_qa.search = lambda q: []
        dead = suggest.followup_questions(
            {"evidence": [ev("fact", "세액공제 한도"), ev("procedure", "계약이전")], "history": []})
    finally:
        KBMOD.retrieve, facts_qa.search = orig_retrieve, orig_facts
    hit = dead == []
    print(f"{'✓' if hit else '✗'} 재료가 없으면 추천질문을 띄우지 않는다({dead})")
    ok += hit

    # ② 한 종류만 재료가 있으면 그 칩만 뜬다 — 있는 것과 없는 것을 실제로 가른다.
    try:
        KBMOD.retrieve = lambda kb, **k: [(1.0, {"id": "c"})] if k.get("kinds") == ["screen"] else []
        facts_qa.search = lambda q: []
        only = suggest.followup_questions({"evidence": [ev("procedure", "계약이전")], "history": []})
    finally:
        KBMOD.retrieve, facts_qa.search = orig_retrieve, orig_facts
    hit = only == ["이 업무는 단말 어느 화면에서 처리해?"]
    print(f"{'✓' if hit else '✗'} 재료가 있는 후보만 남는다(screen 만 열어둠 → {len(only)}건)")
    ok += hit

    # ③ 게이트 넷 — 되묻기·확인대기·LLM실패·근거0건 턴에는 붙지 않는다.
    base = {"evidence": [ev("fact", "세액공제 한도")], "history": []}
    gates = {"되묻기": {**base, "clarify": {"question": "어느 쪽이요?"}},
             "확인대기": {**base, "pending_action": {"label": "화면 열기"}},
             "LLM실패": {**base, "llm_error": "LLMError: down"},
             "근거0건": {**base, "evidence": []}}
    blocked = [name for name, st in gates.items() if suggest.followup_questions(st)]
    hit = not blocked
    print(f"{'✓' if hit else '✗'} 되묻기·확인대기·LLM실패·근거0건 턴에는 붙지 않는다"
          + (f" (샌 것: {blocked})" if blocked else ""))
    ok += hit

    # ④ 고객 화면이 닫혀 있으면 "이 고객 ~" 질문은 성립하지 않는다(§3).
    closed = suggest.followup_questions({"evidence": [ev("customer")], "history": []})
    opened = suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": PERSONAS[0].id})
    hit = closed == [] and opened and all("이 고객" in q for q in opened)
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫히면 고객 질문은 안 뜬다(닫힘 {len(closed)} · 열림 {len(opened)})")
    ok += hit

    # ④-b 안내 콘텐츠 칩은 **이 고객 상태에 걸린 콘텐츠가 실제로 열려 있을 때만** 뜬다.
    #      화면 ⑨ 는 섹션을 비우지 않으려고 관련 없는 콘텐츠도 한 건 세우는데(화면 요건이다),
    #      그 폴백을 «있다»로 세면 칩이 어느 고객에게나 떠서 배경이 된다.
    from pension_agent.strategy_agent import situations as _sit
    from pension_agent.strategy_agent import support as _sup
    matched = [p for p in PERSONAS if _sup.relevant_outreach(_sit.problem_situations(p))]
    unmatched = [p for p in PERSONAS if not _sup.relevant_outreach(_sit.problem_situations(p))]
    _CHIP = "이 고객한테 안내할 만한 세미나나 이벤트 있어?"
    hit = bool(matched) and _CHIP in suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": matched[0].id})
    print(f"{'✓' if hit else '✗'} 걸린 콘텐츠가 있는 고객에게는 안내 콘텐츠 질문이 뜬다"
          + (f" ({matched[0].nm})" if matched else " — 대상 고객 없음"))
    ok += hit

    hit = bool(unmatched) and _CHIP not in suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": unmatched[0].id})
    print(f"{'✓' if hit else '✗'} 걸린 콘텐츠가 없는 고객에게는 안 뜬다"
          + (f" ({unmatched[0].nm})" if unmatched else " — 대조군 없음: 전원이 매칭되면 «조건이 맞을 때만»이 검증되지 않는다"))
    ok += hit

    # 화면을 여는 칩도 같은 판정이다 — 문구 자체가 «지금 이 고객에게 맞는 세미나가 열려
    # 있다»는 알림이라, 조건이 아닌 고객에게 뜨면 알림이 아니라 배경이 된다.
    hit = (bool(matched) and suggest.outreach_chips(matched[0].id)
           and (not unmatched or not suggest.outreach_chips(unmatched[0].id))
           and not suggest.outreach_chips(None))
    print(f"{'✓' if hit else '✗'} 입구 칩도 조건이 맞는 고객에게만 뜬다")
    ok += hit

    # **칩 판정은 LLM 을 부르지 않는다.** 칩 하나 띄우자고 브리핑 한 편(LLM 11회)을
    # 돌리면 답변 지연이 그대로 늘고, LLM 이 죽으면 칩이 통째로 사라진다.
    from pension_agent import llm as _llm
    _orig_gen = _llm.generate
    try:
        _llm.generate = lambda *a, **k: (_ for _ in ()).throw(AssertionError("칩이 LLM 을 불렀다"))
        chips = suggest.outreach_chips(matched[0].id) if matched else []
        follow = suggest.followup_questions(
            {"evidence": [ev("customer")], "history": [],
             "customer_id": matched[0].id if matched else None})
        no_llm = True
    except AssertionError:
        chips, follow, no_llm = [], [], False
    finally:
        _llm.generate = _orig_gen
    hit = no_llm and bool(chips) and bool(follow)
    print(f"{'✓' if hit else '✗'} 안내 콘텐츠 칩 판정에 LLM 을 부르지 않는다")
    ok += hit

    # ⑤ 이번 턴에 이미 쓴 재료로 다시 보내지 않는다 — 방금 답한 것을 또 묻게 된다.
    both = suggest.followup_questions(
        {"evidence": [ev("procedure", "계약이전"), ev("screen", "계약이전")], "history": []})
    hit = not any(q in ("이 업무는 단말 어느 화면에서 처리해?",
                        "이 화면에서 처리하는 절차가 어떻게 돼?") for q in both)
    print(f"{'✓' if hit else '✗'} 이미 쓴 재료로 이끄는 질문은 빠진다({both})")
    ok += hit

    # pitch → pitch(반론 후속)만 예외다. 같은 재료의 **다른 카드**가 답하기 때문이다.
    again = suggest.followup_questions({"evidence": [ev("pitch", "수수료 부담 반론")], "history": []})
    hit = "고객이 그래도 망설이면 뭐라고 답하지?" in again
    print(f"{'✓' if hit else '✗'} 화법의 반론 후속만은 같은 재료로 다시 보낸다({again})")
    ok += hit

    # ⑤-b 같은 도구로 이끄는 칩은 MAX_PER_LEAD 까지만. 하나로 조이면 직원에게는 다른
    #     질문인 것이 «도구가 같다»는 이유로 빠지고, 넘기면 다음 걸음이 한 갈래로 보인다.
    def lead_of(question: str) -> str | None:
        """이 문구가 어느 도구로 이끄는 후보였나 — 상한을 세려면 되짚어야 한다."""
        for rows in suggest._NEXT.values():
            for variants, lead, _probe in rows:
                for text in variants:
                    tail = text.split("}")[-1] if "{topic}" in text else text
                    if question == text or question.endswith(tail):
                        return lead
        return None

    # 재료 확인과 총 상한을 잠시 걷어내고 **도구별 상한만** 잰다 — 안 그러면 총 상한(3)에
    # 먼저 걸려 도구별 상한이 실제로 도는지 알 수 없다.
    orig_has, orig_max = suggest._has_material, suggest.MAX_FOLLOWUPS
    try:
        suggest._has_material = lambda *a, **k: True
        suggest.MAX_FOLLOWUPS = 99
        wide = suggest.followup_questions(
            {"evidence": [ev("fact", "세액공제 한도"), ev("segment", "미운용 현금성자산"),
                          ev("method", "수익률 관리"), ev("fieldtip", "현장 관찰")],
             "history": []})
    finally:
        suggest._has_material, suggest.MAX_FOLLOWUPS = orig_has, orig_max
    to_pitch = [q for q in wide if lead_of(q) == "pitch"]
    hit = len(to_pitch) == suggest.MAX_PER_LEAD
    print(f"{'✓' if hit else '✗'} 같은 도구로 이끄는 칩은 {suggest.MAX_PER_LEAD}개까지만"
          f"(후보 4개 → pitch 행 {len(to_pitch)}개)")
    ok += hit

    # ⑤-c 재료 확인어 — n-gram 유사도는 질의가 길수록 희석돼(kb._sim) 자연스러운 문장이
    #     문턱 아래로 떨어진다. 자기완결형 칩("요즘 시장 상황은 어때?")이 자기 확인어를
    #     갖지 않으면, 시황 카드가 멀쩡히 있는데도 «없다»로 판정돼 안 뜬다.
    selfcontained = [(found, lead, probe) for found, rows in suggest._NEXT.items()
                     for _v, lead, probe in rows if probe]
    market_chip = suggest.followup_questions(
        {"evidence": [ev("lineup", "8월 추천펀드")], "history": []})
    hit = bool(selfcontained) and "요즘 시장 상황은 어때?" in market_chip
    print(f"{'✓' if hit else '✗'} 자기완결형 칩은 확인어로 재료를 찾는다(문장으로는 0건인 자리)")
    ok += hit

    # ⑥ 슬롯 — 근거 카드 제목이 문구에 박힌다. 없으면 슬롯 없는 변형으로 물러선다.
    slotted = suggest.followup_questions({"evidence": [ev("fact", "세액공제 한도")], "history": []})
    hit = bool(slotted) and "「세액공제 한도」" in slotted[0]
    print(f"{'✓' if hit else '✗'} 근거 카드 제목이 추천질문에 실린다")
    ok += hit
    # 슬롯을 못 채우면 그 변형은 건너뛴다 — 빈칸으로 두면 「「」 이 내용」 이 나간다.
    long_title = "가" * (suggest.TOPIC_MAX + 1)
    hit = suggest._topic({"sources": [{"title": long_title}]}) == "" \
        and suggest._phrase(("「{topic}」 앞", "뒤"), "", 0) == "뒤" \
        and suggest._phrase(("「{topic}」 앞", "뒤"), "제목", 0) == "「제목」 앞"
    print(f"{'✓' if hit else '✗'} 제목이 길거나 없으면 슬롯 없는 변형으로 물러선다")
    ok += hit

    # ⑦ 회전 — 대화가 이어지면 같은 재료에서도 문구가 바뀐다(매번 같으면 안 읽힌다).
    turns = {suggest._phrase(("A{topic}", "B", "C"), "T", n) for n in range(3)}
    hit = len(turns) == 3
    print(f"{'✓' if hit else '✗'} 대화 턴에 따라 문구 변형이 회전한다({sorted(turns)})")
    ok += hit

    # ⑧ 직원이 이미 물어본 질문을 다시 제안하지 않는다 — **이번 질문 포함**이 핵심이다.
    #    history 는 이 턴에 들어온 이력이라 이번 질문이 없다(ask 가 invoke 뒤에 붙인다).
    #    그래서 이 필터가 이전 턴만 보면 방금 물은 것이 그대로 추천으로 되돌아온다
    #    — 「이 고객 왜 관리 대상이야?」 를 묻고 답을 읽었는데 맨 아래 같은 질문이 다시
    #    서 있던 자리다. 「이미 쓴 재료」 제외는 이걸 못 잡는다: 그 답은 customer 재료로
    #    나왔는데 그 질문은 segment 로 이끄는 후보라 재료 축이 겹치지 않는다.
    asked = suggest.followup_questions(
        {"evidence": [ev("fact", "세액공제 한도")], "history": [], "customer_id": None})
    repeat = suggest.followup_questions(
        {"evidence": [ev("fact", "세액공제 한도")], "history": [{"question": asked[0]}]})
    hit = asked[0] not in repeat
    print(f"{'✓' if hit else '✗'} 직원이 이미 물은 질문은 다시 제안하지 않는다")
    ok += hit

    same = suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": PERSONAS[0].id,
         "question": "이 고객 왜 관리 대상이야?"})
    hit = "이 고객 왜 관리 대상이야?" not in same
    print(f"{'✓' if hit else '✗'} 방금 물은 질문이 추천으로 되돌아오지 않는다({same})")
    ok += hit

    # 표기 차이(공백·물음표) 하나로 같은 질문이 다시 서면 안 된다.
    loose = suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": PERSONAS[0].id,
         "question": "이 고객 왜 관리대상이야"})
    hit = "이 고객 왜 관리 대상이야?" not in loose
    print(f"{'✓' if hit else '✗'} 공백·물음표만 다른 같은 질문도 다시 제안하지 않는다")
    ok += hit

    # ⑨ ask() 배선 — 답변 끝에 머리말과 함께 붙고, 반환에 followups 가 따로 실린다.
    #    상담이력에는 **붙이기 전 원 답변**이 남는다(history 도구가 재료로 되읽는 텍스트다).
    orig_agent = G._AGENT
    try:
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": "답변 본문", "sources": [],
            "evidence": [ev("fact", "세액공제 한도")],
            "customer_id": st.get("customer_id"), "history": st.get("history") or []})})()
        wired = G.ask("세액공제 한도 얼마야?")
    finally:
        G._AGENT = orig_agent
    hit = wired["followups"] and G.FOLLOWUP_HEADER in wired["answer"] \
        and wired["answer"].startswith("답변 본문") \
        and all(q in wired["answer"] for q in wired["followups"])
    print(f"{'✓' if hit else '✗'} ask() 가 답변 끝에 추천질문을 붙이고 followups 로도 준다")
    ok += hit

    orig_agent = G._AGENT
    try:
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": "어느 쪽인가요?", "sources": [], "clarify": {"question": "어느 쪽?"},
            "evidence": [ev("fact", "세액공제 한도")]})})()
        asking = G.ask("실물이전 어떻게 해?")
    finally:
        G._AGENT = orig_agent
    hit = asking["followups"] == [] and G.FOLLOWUP_HEADER not in asking["answer"]
    print(f"{'✓' if hit else '✗'} 되묻기 턴의 답변에는 추천질문 블록이 붙지 않는다")
    ok += hit

    # ask(x_client_user=) — 이 턴 안의 **모든** LLM 호출이 그 직원 이름으로 나가는가.
    # 한 턴이 노드·도구 수십 갈래로 흩어지므로 인자로 꿰지 않고 ContextVar 로 흘린다
    # (llm.client_user). 배선이 끊기면 전사 호출이 한 쿼터 버킷에 몰려 429 를 자초하는데,
    # 그건 행내에 들고 가서야 드러난다 — 그래서 여기서 잡는다.
    seen: dict = {}
    orig_agent = G._AGENT
    try:
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: (
            seen.update(who=G.llm.current_client_user()) or
            {"answer": "답변 본문", "sources": [], "evidence": []}))})()
        G.ask("세액공제 한도 얼마야?", x_client_user="emp-0417")
        inside = seen.get("who")
        G.ask("세액공제 한도 얼마야?")
        default_used = seen.get("who")
    finally:
        G._AGENT = orig_agent
    hit = inside == "emp-0417"
    print(f"{'✓' if hit else '✗'} ask(x_client_user=) 가 턴 전체의 LLM 호출 주체를 세운다")
    ok += hit
    hit = default_used == G.llm.DEFAULT_CLIENT_USER
    print(f"{'✓' if hit else '✗'} 주지 않으면 기본 주체로 떨어진다(빈 값으로 나가지 않는다)")
    ok += hit
    return ok
