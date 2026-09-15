"""세액공제 계산기 — 환급 예상액 · ISA 만기자금 전환.

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

from pension_agent.consult_agent import tools

from tests.consult._common import print, _vt  # noqa: A001 — 집계용 print


def check_tax_credit_calc() -> int:
    """환급 예상액 계산기(07/01 ② 3번) — 「얼마 더 넣으면 얼마 받나」에 답이 없던 자리.

    재료에는 **현재 납입액 기준 한 값**만 있었고("예상 세액공제액 118만원"), 재료 밖 계산은
    금지라(§5) 직원이 실제로 묻는 것에 답할 방법이 없었다. 그 장이 근거로 든 것이 이것이다 —
    직원 두 명이 각자 엑셀 계산기를 만들어 배포했을 만큼 니즈가 강하다.

    여기서 재는 것 다섯:
      ① 입력 금액은 **직원이 친 말**에서 뽑는다(계획 LLM 의 재작성본이 아니라)
      ② 총급여 구간이 미확인이면 두 경우를 다 낸다
      ③ 한도를 이미 채웠으면 «추가 공제 없음»으로 갈리고, 그 갈래에는 결정세액 단서를
         붙이지 않는다 — 최대 환급액을 단정할 때 걸리는 단서라 여기서는 무관하다(§7)
      ④ 계산 결과는 인용할 수 있고, 계산 밖 금액은 잘린다
      ⑤ **«어디에 넣는 금액인가»가 재료에 있다** — 질문("300만원 더 넣으면")에는 계좌가
         없다. 갈래가 있어서가 아니라 직원이 말하지 않을 뿐이고, 열려 있는 고객의 계좌가
         개인형IRP 다. 재료가 비워 두면 답변도 비우고, 직원은 어느 계좌에 넣는 300만원인지
         적히지 않은 금액을 고객에게 옮긴다. 한도 쪽은 반대로 계좌를 가리지 않는다 —
         연금저축과 함께 쓰는 한도라(fact.k04.f2) 그 사실도 함께 적혀야 한다
    """
    ok = 0
    from pension_agent.consult_agent.tools import relations as REL
    from pension_agent.strategy_agent import customer as CUST

    room = next(p for p in CUST.PERSONAS if p.room > 0)          # 잔여한도가 있는 고객
    full = next(p for p in CUST.PERSONAS if p.room == 0)         # 한도를 채운 고객

    # ① 금액은 직원 질문에서 온다. 계획이 넘기는 query 에 다른 수가 있어도 그쪽을 안 쓴다.
    ev = tools.TOOLS["tax_credit"].run(
        {"customer_id": room.id, "question": "300만원 더 넣으면 얼마 받아?"}, "세액공제 900만원")
    hit = ev is not None and "추가 납입액 300만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 입력 금액은 직원 질문에서 뽑는다(계획의 재작성본이 아니다)")
    ok += hit

    # 단위 없는 맨숫자는 금액으로 보지 않는다 — 300원인지 300만원인지 가릴 근거가 없다.
    bare = tools.TOOLS["tax_credit"].run(
        {"customer_id": room.id, "question": "300 더 넣으면 얼마 받아?"}, "q")
    hit = bare is not None and "질문에 금액이 없어 잔여한도로 계산했다" in bare["text"]
    print(f"{'✓' if hit else '✗'} 단위 없는 맨숫자는 금액으로 읽지 않는다(잔여한도로 떨어진다)")
    ok += hit

    # ② 구간 미확인이면 두 경우를 다 낸다.
    hit = all(f"{r * 100:.1f}%" in ev["text"] for r in CUST.TAX_CREDIT_RATE.values())
    print(f"{'✓' if hit else '✗'} 총급여 구간 미확인이면 두 공제율을 다 싣는다")
    ok += hit

    # ③ 한도를 채운 고객은 다른 갈래로 가고, 그 갈래에는 결정세액 단서가 없다.
    done = tools.TOOLS["tax_credit"].run(
        {"customer_id": full.id, "question": "500만원 더 넣으면 얼마 받아?"}, "q")
    hit = done is not None and "추가 공제 대상이 없다" in done["text"] and not done["notices"]
    print(f"{'✓' if hit else '✗'} 한도를 채웠으면 «추가 공제 없음» + 결정세액 단서를 붙이지 않는다"
          + ("" if hit else f" — notices={(done or {}).get('notices')}"))
    ok += hit

    # 반대로 금액을 내놓는 갈래에는 반드시 붙는다. 문장은 코드가 아니라 카드에서 온다.
    card = tools.KB.facts[tools.TAX_FACT_ID]
    hit = bool(ev["notices"]) and ev["notices"][0] in card["value"]
    print(f"{'✓' if hit else '✗'} 금액을 내놓는 갈래에는 카드가 못박은 단서가 따라붙는다")
    ok += hit

    # ④ 계산 결과는 인용 가능, 계산 밖 금액은 잘린다.
    gain = CUST.tax_credit(min(room.pension_paid_ytd + room.room * 10_000,
                               CUST.TAX_CREDIT_CAP_WON), CUST.TAX_CREDIT_RATE["5500이하"]) \
        - CUST.tax_credit(room.pension_paid_ytd, CUST.TAX_CREDIT_RATE["5500이하"])
    hit = (_vt(f"16.5% 구간이면 {gain:,}원 더 돌려받으세요.", ev["allow"])[0]
           and not _vt(f"16.5% 구간이면 {gain + 70_000:,}원 더 돌려받으세요.", ev["allow"])[0])
    print(f"{'✓' if hit else '✗'} 계산 결과는 인용되고 계산 밖 금액은 잘린다 ({gain:,}원)")
    ok += hit

    # 공제율 카드의 조건–값 짝도 그대로 걸린다(오짝은 수치 검사로는 안 잡힌다).
    cards = tools.ledger_related([ev])
    hit = (not REL.check(ev["text"], cards)
           and bool(REL.check("총급여 5,500만원 초과면 16.5% 적용돼요.", cards)))
    print(f"{'✓' if hit else '✗'} 재료는 자기대조를 통과하고, 공제율 오짝은 잡힌다")
    ok += hit

    # ⑤ 어디에 넣는 금액인지가 재료에 있다. 없으면 답변도 말하지 않는다.
    hit = "개인형IRP 계좌 추가 납입" in ev["text"]
    print(f"{'✓' if hit else '✗'} 어느 계좌에 넣는 금액인지를 재료가 적는다")
    ok += hit

    # 정해져 있는 값이라 «가정하면» 으로 적지 않는다 — 추측처럼 적으면 직원은 확인해야
    # 할 것이 있는 줄 안다. 되묻기 대상도 아니다(갈래가 없다).
    hit = not any(w in ev["text"] for w in ("가정", "~라면", "로 보고 계산"))
    print(f"{'✓' if hit else '✗'} 계좌를 가정 어법으로 적지 않는다(정해져 있는 값이다)")
    ok += hit

    # 한도 쪽은 반대로 계좌를 가리지 않는다 — 「IRP 에 900만원까지」로 읽히면 안 된다.
    hit = "연금저축 납입분까지 합산한 값" in ev["text"]
    print(f"{'✓' if hit else '✗'} 한도·잔여한도가 연금저축과 합산된 값이라는 것을 함께 적는다")
    ok += hit

    # 브리핑과 계산기가 같은 산식을 쓴다 — 두 곳이 각자 곱하면 화면과 답변이 갈린다.
    from pension_agent.strategy_agent import agent as SA
    shown = SA.propose(room)["facts"]["briefing"]["예상_세액공제액"]
    now = CUST.tax_credit(room.pension_paid_ytd, room.tax_credit_rate)
    hit = f"{now // 10_000:,}만원" in shown or f"{now:,}" in shown
    print(f"{'✓' if hit else '✗'} 화면의 예상 세액공제액과 같은 산식을 쓴다 ({shown})")
    ok += hit
    return ok + check_tax_credit_isa()


def check_tax_credit_isa() -> int:
    """ISA 만기자금 전환 — 900만원 한도 «안»이 아니라 그 한도에 **더해지는** 축.

    이 갈래가 없던 동안 「ISA 8,000만원 중 일부만 옮기면 세액공제 어떻게 돼?」에 잔여한도
    500만원으로 답했다 — 같은 대화에서 방금 인용한 카드(fact.k04.f4 「최대 1,200만원」)와
    어긋나는 금액이었고, 카드가 오답으로 못박은 「전환금 전액이 공제 대상」의 반대편 오답
    (「전환해도 잔여한도까지만」)이었다.

    재는 것 다섯:
      ① ISA 보유 고객이면 질문에 'ISA' 라는 말이 없어도 전환 축이 실린다(되묻기 뒤의
         한 마디 답 "초과야" 가 그 자리다 — 말에서 찾으면 정작 필요한 턴에 빠진다)
      ② 늘어나는 것은 전환액의 10%(300만원 상한)이지 전환액 전체가 아니다
      ③ 잔여한도가 0이어도 전환으로는 더 받을 수 있다 — 두 축이 다르다는 것의 실증
      ④ 60일이 지난 자금에는 싣지 않는다
      ⑤ ISA 가 없는 고객의 답은 예전과 같다(축을 늘리지 않는다)
    """
    ok = 0
    from pension_agent.strategy_agent import customer as CUST

    isa = next(p for p in CUST.PERSONAS if p.isa and p.room > 0)
    ev = tools.TOOLS["tax_credit"].run({"customer_id": isa.id, "question": "초과야"}, "세액공제")
    text = (ev or {}).get("text", "")

    hit = ev is not None and "ISA 만기자금을 전환하는 경우" in text
    print(f"{'✓' if hit else '✗'} 질문에 'ISA' 가 없어도 보유 고객이면 전환 축이 실린다")
    ok += hit

    # ② 전환액 전체가 아니라 10%·300만원 상한. 상한에 닿는 전환액도 함께 있어야
    #    「일부만 옮기면?」이 금액과 이어진다.
    cap = CUST.ISA_ROLLOVER_CREDIT_CAP_WON
    at_cap = int(cap / CUST.ISA_ROLLOVER_CREDIT_RATE)
    add = CUST.isa_rollover_credit(isa.isa["amount"])
    hit = (add == cap and CUST.isa_rollover_credit(10_000_000) == 1_000_000
           and f"{at_cap // 10_000:,}만원에서 상한에 닿는다" in text
           and "전환금 전액이 공제 대상이 되는 것이 아니다" in text)
    print(f"{'✓' if hit else '✗'} 늘어나는 몫은 전환액의 10%(상한 {cap // 10_000:,}만원)다")
    ok += hit

    # 환급액은 «늘어난 공제 대상»에만 공제율을 곱한 값이다. 전환액을 tax_credit() 에 그대로
    # 넣으면 8,000만원이 한도를 채운 것으로 계산된다 — 그 오답이 여기서 갈린다.
    gain = CUST.tax_credit(add, CUST.TAX_CREDIT_RATE["5500초과"])
    hit = f"{gain:,}원" in text and _vt(f"이 전환으로 {gain:,}원 더 돌려받아요.", ev["allow"])[0]
    print(f"{'✓' if hit else '✗'} 전환 환급액은 늘어난 공제 대상 × 공제율이다 ({gain:,}원)")
    ok += hit

    # 카드가 못박은 1,200만원 한도와 코드의 두 상수가 같은 값을 말한다.
    hit = f"{(CUST.TAX_CREDIT_CAP_WON + cap) // 10_000:,}만원" in text
    print(f"{'✓' if hit else '✗'} 공제 대상 한도가 900만원 → 1,200만원으로 늘어난다고 싣는다")
    ok += hit

    # ③ 잔여한도가 0인데 ISA 가 있는 고객 — 예전에는 "더 넣어도 안 늘어난다"로 끝났다.
    full_isa = next((p for p in CUST.PERSONAS if p.isa and p.room == 0), None)
    if full_isa is not None:
        zero = tools.TOOLS["tax_credit"].run({"customer_id": full_isa.id, "question": "얼마 더 받아?"}, "q")
        hit = (zero is not None and "추가 공제 대상이 없다" in zero["text"]
               and "ISA 만기자금을 전환하는 경우" in zero["text"] and bool(zero["notices"]))
        print(f"{'✓' if hit else '✗'} 잔여한도 0이어도 전환 축은 따로 답한다 + 결정세액 단서가 붙는다")
        ok += hit

    # ④ 60일이 지나면 안내할 수 없는 것이라 싣지 않는다.
    keep = isa.isa
    try:
        isa.isa = dict(keep, dd=-(CUST.ISA_ROLLOVER_DEADLINE_DAYS + 1))
        late = tools.TOOLS["tax_credit"].run({"customer_id": isa.id, "question": "얼마 더 받아?"}, "q")
        hit = late is not None and "ISA 만기자금을 전환하는 경우" not in late["text"]
        print(f"{'✓' if hit else '✗'} 전환 기한(60일)이 지난 자금에는 싣지 않는다")
        ok += hit
        isa.isa = dict(keep, dd=-10)
        mid = tools.TOOLS["tax_credit"].run({"customer_id": isa.id, "question": "얼마 더 받아?"}, "q")
        hit = mid is not None and "만기 10일 경과 · 전환 기한 50일 남음" in mid["text"]
        print(f"{'✓' if hit else '✗'} 만기가 지났어도 60일 안이면 남은 기한을 싣는다")
        ok += hit
    finally:
        isa.isa = keep

    # ⑤ ISA 가 없는 고객에게는 축을 늘리지 않는다.
    plain = next(p for p in CUST.PERSONAS if not p.isa and p.room > 0)
    ev2 = tools.TOOLS["tax_credit"].run({"customer_id": plain.id, "question": "300만원 더 넣으면?"}, "q")
    hit = ev2 is not None and "ISA" not in ev2["text"] and "연금계좌에 현금을" not in ev2["text"]
    print(f"{'✓' if hit else '✗'} ISA 가 없는 고객의 재료에는 전환 축도 축 이름도 없다")
    ok += hit
    return ok
