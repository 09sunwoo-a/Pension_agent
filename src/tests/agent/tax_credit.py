"""세액공제 계산기 — 환급 예상액 · ISA 만기자금 전환.

`test_agent.py::main` 이 호출(스텁 설치·정리 포함). 직접 실행 금지.
"""

from __future__ import annotations

from pension_agent import tools

from tests.agent._common import print, _vt  # noqa: A001 — 집계용 print


def check_tax_credit_calc() -> int:
    """환급 예상액 계산기 검사.

      ① 입력 금액은 직원 질문에서 추출(계획 LLM 재작성본 아님)
      ② 총급여 구간 미확인 시 두 경우 모두
      ③ 한도 소진 시 «추가 공제 없음» — 결정세액 단서 미부착
      ④ 계산 결과만 인용 가능, 계산 밖 금액은 잘림
      ⑤ 납입 계좌(개인형IRP)·한도의 연금저축 합산 여부를 재료에 명시
    """
    ok = 0
    from pension_agent.evidence import relations as REL
    from pension_agent.tools.customer_solution import customer as CUST

    room = next(p for p in CUST.PERSONAS if p.room > 0)          # 잔여한도 있음
    full = next(p for p in CUST.PERSONAS if p.room == 0)         # 한도 소진

    # ① 금액은 직원 질문에서 — 계획 query 의 수는 무시
    ev = tools.TOOLS["tax_credit"].run(
        {"customer_id": room.id, "question": "300만원 더 넣으면 얼마 받아?"}, "세액공제 900만원")
    hit = ev is not None and "추가 납입액 300만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 입력 금액은 직원 질문에서 뽑는다(계획의 재작성본이 아니다)")
    ok += hit

    # 단위 없는 맨숫자는 금액 아님 — 300원/300만원 구분 불가
    bare = tools.TOOLS["tax_credit"].run(
        {"customer_id": room.id, "question": "300 더 넣으면 얼마 받아?"}, "q")
    hit = bare is not None and "질문에 금액이 없어 잔여한도로 계산했다" in bare["text"]
    print(f"{'✓' if hit else '✗'} 단위 없는 맨숫자는 금액으로 읽지 않는다(잔여한도로 떨어진다)")
    ok += hit

    # ② 원장에 구간 있으면 그 하나만, 없으면 두 경우 모두(실데이터에 컬럼이 없을 수 있음)
    mine = CUST.TAX_CREDIT_RATE[room.income_bracket]
    other = next(r for k, r in CUST.TAX_CREDIT_RATE.items() if k != room.income_bracket)
    hit = (bool(room.income_bracket)
           and f"{mine * 100:.1f}%" in ev["text"] and f"{other * 100:.1f}%" not in ev["text"]
           and "원장에 없어" not in ev["text"])
    print(f"{'✓' if hit else '✗'} 원장이 총급여 구간을 알면 그 공제율 하나만 싣는다")
    ok += hit

    import dataclasses  # noqa: PLC0415
    blind = dataclasses.replace(room, income_bracket=None)
    _orig = CUST.get_profile
    CUST.get_profile = lambda cid: blind if cid == room.id else _orig(cid)
    try:
        ev2 = tools.TOOLS["tax_credit"].run(
            {"customer_id": room.id, "question": "300만원 더 넣으면 얼마 받아?"}, "q")
    finally:
        CUST.get_profile = _orig
    hit = (ev2 is not None
           and all(f"{r * 100:.1f}%" in ev2["text"] for r in CUST.TAX_CREDIT_RATE.values())
           and "원장에 없어" in ev2["text"])
    print(f"{'✓' if hit else '✗'} 총급여 구간 미확인이면 두 공제율을 다 싣는다")
    ok += hit

    # 직원이 이번 턴에 말한 구간이 원장보다 우선
    other_key = next(k for k in CUST.TAX_CREDIT_RATE if k != room.income_bracket)
    word = "초과야" if other_key == "5500초과" else "5,500 이하야"
    ev3 = tools.TOOLS["tax_credit"].run(
        {"customer_id": room.id, "question": f"(총급여 구간 되묻기 뒤) {word}",
         "history": [{"pending_clarify": True}]}, "q")
    hit = (ev3 is not None
           and f"{CUST.TAX_CREDIT_RATE[other_key] * 100:.1f}%" in ev3["text"]
           and f"{mine * 100:.1f}%" not in ev3["text"])
    print(f"{'✓' if hit else '✗'} 직원이 말한 구간이 원장 구간을 이긴다")
    ok += hit

    # 되묻기 뒤가 아니면 낱말로 구간 판정 금지 — 「위험자산 한도 초과」 오인 방지
    ev4 = tools.TOOLS["tax_credit"].run(
        {"customer_id": room.id, "question": "위험자산 한도 초과라는데 300만원 더 넣으면?"}, "q")
    hit = ev4 is not None and f"{mine * 100:.1f}%" in ev4["text"]
    print(f"{'✓' if hit else '✗'} 되묻기 뒤가 아니면 「초과」를 구간으로 읽지 않는다")
    ok += hit

    # ③ 한도 소진 갈래 — 결정세액 단서 없음
    done = tools.TOOLS["tax_credit"].run(
        {"customer_id": full.id, "question": "500만원 더 넣으면 얼마 받아?"}, "q")
    hit = done is not None and "추가 공제 대상이 없다" in done["text"] and not done["notices"]
    print(f"{'✓' if hit else '✗'} 한도를 채웠으면 «추가 공제 없음» + 결정세액 단서를 붙이지 않는다"
          + ("" if hit else f" — notices={(done or {}).get('notices')}"))
    ok += hit

    # 금액 갈래에는 단서 필수 — 문장 출처는 카드
    card = tools.KB.facts[tools.TAX_FACT_ID]
    hit = bool(ev["notices"]) and ev["notices"][0] in card["value"]
    print(f"{'✓' if hit else '✗'} 금액을 내놓는 갈래에는 카드가 못박은 단서가 따라붙는다")
    ok += hit

    # ④ 계산 결과 인용 가능, 계산 밖 금액 잘림
    gain = CUST.tax_credit(min(room.pension_paid_ytd + room.room * 10_000,
                               CUST.TAX_CREDIT_CAP_WON), CUST.TAX_CREDIT_RATE["5500이하"]) \
        - CUST.tax_credit(room.pension_paid_ytd, CUST.TAX_CREDIT_RATE["5500이하"])
    hit = (_vt(f"16.5% 구간이면 {gain:,}원 더 돌려받으세요.", ev["allow"])[0]
           and not _vt(f"16.5% 구간이면 {gain + 70_000:,}원 더 돌려받으세요.", ev["allow"])[0])
    print(f"{'✓' if hit else '✗'} 계산 결과는 인용되고 계산 밖 금액은 잘린다 ({gain:,}원)")
    ok += hit

    # 공제율 조건–값 오짝은 수치 검사로 못 잡음 — 관계 검사로
    cards = tools.ledger_related([ev])
    hit = (not REL.check(ev["text"], cards)
           and bool(REL.check("총급여 5,500만원 초과면 16.5% 적용돼요.", cards)))
    print(f"{'✓' if hit else '✗'} 재료는 자기대조를 통과하고, 공제율 오짝은 잡힌다")
    ok += hit

    # ⑤ 납입 계좌 명시
    hit = "개인형IRP 계좌 추가 납입" in ev["text"]
    print(f"{'✓' if hit else '✗'} 어느 계좌에 넣는 금액인지를 재료가 적는다")
    ok += hit

    # 확정값이라 가정 어법 금지 — 직원이 확인 필요로 오해
    hit = not any(w in ev["text"] for w in ("가정", "~라면", "로 보고 계산"))
    print(f"{'✓' if hit else '✗'} 계좌를 가정 어법으로 적지 않는다(정해져 있는 값이다)")
    ok += hit

    # 한도는 연금저축 합산 — 「IRP 에 900만원까지」 오독 방지
    hit = "연금저축 납입분까지 합산한 값" in ev["text"]
    print(f"{'✓' if hit else '✗'} 한도·잔여한도가 연금저축과 합산된 값이라는 것을 함께 적는다")
    ok += hit

    # 브리핑·계산기 동일 산식 — 각자 계산 시 화면과 답변 불일치
    from pension_agent.tools.customer_solution import solution as SOL
    shown = SOL.facts(room)["briefing"]["예상_세액공제액"]
    now = CUST.tax_credit(room.pension_paid_ytd, room.tax_credit_rate)
    hit = f"{now // 10_000:,}만원" in shown or f"{now:,}" in shown
    print(f"{'✓' if hit else '✗'} 화면의 예상 세액공제액과 같은 산식을 쓴다 ({shown})")
    ok += hit

    # 잔여한도 0 고객에도 추가 납입 가능액 — 1,800 − 당해 납입은 계산이라 LLM 불가
    nopen = next(p for p in CUST.PERSONAS
                 if p.room == 0 and not p.pension_started and p.deposit_room > 0)
    ev = tools.TOOLS["tax_credit"].run(
        {"customer_id": nopen.id, "question": "올해 절세 가능 금액은?"}, "q")
    hit = ev is not None and f"더 납입할 수 있는 금액 {nopen.deposit_room // 10_000:,}만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 잔여한도 0 고객에게도 더 납입할 수 있는 금액(1,800만원 − 당해 납입)을 싣는다")
    ok += hit
    return ok + check_tax_credit_isa()


def check_tax_credit_isa() -> int:
    """ISA 만기자금 전환 — 900만원 한도에 더해지는 별도 축.

      ① ISA 보유 고객이면 질문에 'ISA' 가 없어도 전환 축 포함(되묻기 답 "초과야" 대응)
      ② 증가분은 전환액의 10%(300만원 상한), 전환액 전체 아님
      ③ 잔여한도 0이어도 전환 축은 별도
      ④ 60일 경과 자금 미포함
      ⑤ ISA 미보유 고객은 축 미추가
    """
    ok = 0
    from pension_agent.tools.customer_solution import customer as CUST

    isa = next(p for p in CUST.PERSONAS if p.isa and p.room > 0)
    ev = tools.TOOLS["tax_credit"].run({"customer_id": isa.id, "question": "초과야"}, "세액공제")
    text = (ev or {}).get("text", "")

    hit = ev is not None and "ISA 만기자금을 전환하는 경우" in text
    print(f"{'✓' if hit else '✗'} 질문에 'ISA' 가 없어도 보유 고객이면 전환 축이 실린다")
    ok += hit

    # ② 10%·300만원 상한 + 상한 도달 전환액(「일부만 옮기면?」 대응)
    cap = CUST.ISA_ROLLOVER_CREDIT_CAP_WON
    at_cap = int(cap / CUST.ISA_ROLLOVER_CREDIT_RATE)
    add = CUST.isa_rollover_credit(isa.isa["amount"])
    hit = (add == cap and CUST.isa_rollover_credit(10_000_000) == 1_000_000
           and f"{at_cap // 10_000:,}만원에서 상한에 닿는다" in text
           and "전환액 자체가 공제 대상이 되지는 않는다" in text)
    print(f"{'✓' if hit else '✗'} 늘어나는 몫은 전환액의 10%(상한 {cap // 10_000:,}만원)다")
    ok += hit

    # 환급액 = 늘어난 공제 대상 × 공제율 — 전환액 그대로 넣으면 한도 소진 오답
    gain = CUST.tax_credit(add, CUST.TAX_CREDIT_RATE["5500초과"])
    hit = f"{gain:,}원" in text and _vt(f"이 전환으로 {gain:,}원 더 돌려받아요.", ev["allow"])[0]
    print(f"{'✓' if hit else '✗'} 전환 환급액은 늘어난 공제 대상 × 공제율이다 ({gain:,}원)")
    ok += hit

    # 카드의 1,200만원 한도 = 코드 두 상수 합
    hit = f"{(CUST.TAX_CREDIT_CAP_WON + cap) // 10_000:,}만원" in text
    print(f"{'✓' if hit else '✗'} 공제 대상 한도가 900만원 → 1,200만원으로 늘어난다고 싣는다")
    ok += hit

    # ③ 잔여한도 0 + ISA 보유
    full_isa = next((p for p in CUST.PERSONAS if p.isa and p.room == 0), None)
    if full_isa is not None:
        zero = tools.TOOLS["tax_credit"].run({"customer_id": full_isa.id, "question": "얼마 더 받아?"}, "q")
        hit = (zero is not None and "추가 공제 대상이 없다" in zero["text"]
               and "ISA 만기자금을 전환하는 경우" in zero["text"] and bool(zero["notices"]))
        print(f"{'✓' if hit else '✗'} 잔여한도 0이어도 전환 축은 따로 답한다 + 결정세액 단서가 붙는다")
        ok += hit

    # ④ 60일 경과 시 미포함
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

    # ⑤ ISA 미보유
    plain = next(p for p in CUST.PERSONAS if not p.isa and p.room > 0)
    ev2 = tools.TOOLS["tax_credit"].run({"customer_id": plain.id, "question": "300만원 더 넣으면?"}, "q")
    hit = ev2 is not None and "ISA" not in ev2["text"] and "연금계좌에 현금을" not in ev2["text"]
    print(f"{'✓' if hit else '✗'} ISA 가 없는 고객의 재료에는 전환 축도 축 이름도 없다")
    ok += hit
    return ok
