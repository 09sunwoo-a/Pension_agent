"""세액공제 환급 예상액 도구(tax_credit) — 검색 없이 코드가 계산해 적재.

- 입력 금액은 직원 원문(state["question"])에서 추출 — 계획 LLM 의 재작성 query 는 숫자를 흘림
- 공제율: 직원이 이번 턴에 말한 구간 > 원장 구간 > 둘 다 없으면 두 구간 모두
- 납입 계좌(개인형IRP)는 확정값으로 명시, 한도는 연금저축과 공유임을 명시
- ISA 만기자금 전환은 별도 축·별도 블록
"""

from __future__ import annotations

from pension_agent.tools.kb_search import kb_index
from pension_agent.state import KB, AgentState
from pension_agent.evidence.record import Evidence, _ev, _scope


#: 공제율·한도·단서의 근거 카드.
TAX_FACT_ID = "fact.k04.f2"

# ISA 전환액을 tax_credit() 에 그대로 넣지 말 것 — «전환금 전액이 공제 대상»(카드 등록 오답)이 됨
#: ISA 전환 특례(10%·300만원·60일)의 근거 카드.
ISA_FACT_ID = "fact.k04.f4"


def _won(v: int) -> str:
    """engine.text.won 지연 임포트 래퍼."""
    from pension_agent.tools.customer_solution.engine.text import won  # noqa: PLC0415

    return won(v)


def _extra_paid(state: AgentState) -> tuple[str, int] | None:
    """추가 납입액(라벨, 원). 되묻기 다음 턴이면 이번 말의 수치는 갈래 라벨이라 앞 질문에서 추출."""
    from pension_agent.evidence.verify import first_amount  # noqa: PLC0415

    history = state.get("history") or []
    if not (history and (history[-1] or {}).get("pending_clarify")):
        return first_amount(state.get("question") or "")
    # 금액을 말한 가장 가까운 앞 질문. 없으면 None(잔여한도로 계산)
    for turn in reversed(history):
        found = first_amount(turn.get("question") or "")
        if found:
            return found
    return None


def _tax_credit(state: AgentState, query: str) -> Evidence | None:
    """세액공제 환급 예상액(계산은 customer_solution 함수 재사용)."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.tools.customer_solution import customer as CUST  # noqa: PLC0415
    from pension_agent.evidence.verify import first_amount  # noqa: PLC0415

    p = CUST.get_profile(customer_id)
    card = KB.facts.get(TAX_FACT_ID)
    if p is None or card is None:
        return None

    paid, cap = p.pension_paid_ytd, CUST.TAX_CREDIT_CAP_WON
    # 잔여한도는 원장 값 — 연금저축과 공유 한도라 IRP 납입액으로 재계산 금지
    room = p.room * 10_000
    # 금액 미언급이면 잔여한도 전액으로 계산
    said = _extra_paid(state)
    extra = said[1] if said else room
    gain_base = min(extra, room)          # 잔여한도를 넘는 납입은 공제 대상이 아니다
    target = paid + gain_base
    gain = CUST.tax_credit(target, 1.0) - CUST.tax_credit(paid, 1.0)

    # ISA 축 포함 여부는 원장으로 판정 — 질문 낱말로 정하면 «초과야» 같은 짧은 답에서 누락
    isa_card = KB.facts.get(ISA_FACT_ID)
    isa_used = bool(p.isa) and isa_card is not None and _isa_convertible(p.isa)
    # 납입 계좌·재원(현금/ISA) 둘 다 명시 — 블록마다 금액 뜻이 달라짐
    axis = " · 현금을 더 납입하는 경우" if isa_used else ""
    lines = [f"■ 세액공제 환급 예상액 — {p.nm} 고객{axis} (시스템 계산 — 검색 결과가 아니다)",
             "· 어디에 넣는 금액인가: 이 고객의 **개인형IRP 계좌 추가 납입**이다",
             f"· 당해 납입액 {_won(paid)} · 세액공제 한도 {_won(cap)} · 잔여한도 {_won(room)} "
             f"— 한도와 잔여한도는 연금저축 납입분까지 합산한 값이다(원장 값)",
             f"· 계산에 쓴 추가 납입액 {_won(extra)}"
             + ("" if said else " (질문에 금액이 없어 잔여한도로 계산했다)")]

    cards = [card]
    notices: list[str] = []
    if gain <= 0:
        # 공제 한도(900만)와 납입한도(1,800만) 혼동 금지 — 납입은 가능, 공제만 없음
        lines.append(f"· 세액공제 잔여한도가 {_won(room)}이라 **추가 공제 대상이 없다** — "
                     f"더 납입해도 올해 세액공제로 돌아오는 금액은 늘지 않는다 "
                     f"(세액공제 한도 {_won(cap)}은 연금저축과 함께 쓴다. 납입 자체는 "
                     f"연 납입한도 {_won(CUST.DEPOSIT_CAP_WON)}까지 가능하다)")
        # 납입 가능액은 원장 값으로 적재(LLM 계산 금지). 연금 개시 계좌는 납입 불가라 제외
        if not p.pension_started:
            lines.append(f"· 올해 더 납입할 수 있는 금액 {_won(p.deposit_room)} "
                         f"(연 납입한도 {_won(CUST.DEPOSIT_CAP_WON)} − 당해 납입 "
                         f"{_won(p.paid_ytd_total)}) — 이 금액은 세액공제 대상이 아니다")
    else:
        lines.append(f"· 공제 대상 {_won(min(paid, cap))} → {_won(min(target, cap))} "
                     f"(잔여한도 {_won(room)}까지)")
        rates = _rates(p, state)
        for when, rate in rates:
            now, after = CUST.tax_credit(paid, rate), CUST.tax_credit(target, rate)
            lines.append(f"· {when}({rate * 100:.1f}%): 환급 예상 {now:,}원 → {after:,}원 "
                         f"(늘어나는 금액 {after - now:,}원)")
        src = "직원이 이번 턴에 말한" if _said_bracket(state) else "원장의"
        lines.append(f"· 공제율은 **{src} 총급여 구간**에서 나온 값이다 — 되물을 것이 없다"
                     if len(rates) == 1 else
                     "· 이 고객의 총급여 구간은 원장에 없어 두 경우를 다 실었다 — "
                     "어느 구간인지 확인하면 하나로 좁혀진다")

    if isa_used:
        lines += _isa_rollover_lines(p, said, state)
        cards.append(isa_card)
    if gain > 0 or isa_used:
        # 결정세액 단서는 환급 금액을 단정할 때만, 한 번만
        notices.append(_caveat(card))
    return _ev("tax_credit", query, "\n".join(lines),
               kb_index.sources_of(KB, [(1.0, c) for c in cards]), notices=notices,
               scopes=[_scope(card.get("label") or TAX_FACT_ID, [], notices)] if notices else None,
               cards=cards)


def _isa_convertible(isa: dict) -> bool:
    """ISA 만기 후 60일 이내인지. 만기일 미상이면 True."""
    from pension_agent.tools.customer_solution import customer as CUST  # noqa: PLC0415

    dd = isa.get("dd")
    return dd is None or dd >= -CUST.ISA_ROLLOVER_DEADLINE_DAYS


#: 총급여 구간 라벨.
_BRACKET_LABEL = {"5500이하": "총급여 5,500만원 이하", "5500초과": "총급여 5,500만원 초과"}


def _said_bracket(state: AgentState) -> str | None:
    """직원이 이번 턴에 말한 총급여 구간(원장보다 우선).

    되묻기 다음 턴 또는 «총급여·소득»이 함께 있을 때만 판정 — 아니면 «한도 초과» 같은 말을 오독.
    """
    q = state.get("question") or ""
    history = state.get("history") or []
    after_clarify = bool(history and (history[-1] or {}).get("pending_clarify"))
    if not (after_clarify or any(w in q for w in ("총급여", "소득"))):
        return None
    if "초과" in q:
        return "5500초과"
    if "이하" in q or "미만" in q:
        return "5500이하"
    return None


def _rates(p, state: AgentState | None = None) -> list[tuple[str, float]]:
    """(구간 라벨, 공제율) 목록 — 직원 발화 > 원장 > 두 구간 모두."""
    from pension_agent.tools.customer_solution import customer as CUST  # noqa: PLC0415

    key = (_said_bracket(state) if state is not None else None) or p.income_bracket
    if key in _BRACKET_LABEL:
        return [(_BRACKET_LABEL[key], CUST.TAX_CREDIT_RATE[key])]
    return [(lab, CUST.TAX_CREDIT_RATE[k]) for k, lab in _BRACKET_LABEL.items()]


def _isa_rollover_lines(p, said: tuple | None, state: AgentState) -> list[str]:
    """ISA 만기자금 전환 축 재료(현금 납입과 별도로 더해지는 몫)."""
    from pension_agent.tools.customer_solution import customer as CUST  # noqa: PLC0415

    amt, dd = p.isa["amount"], p.isa.get("dd")
    cap = CUST.ISA_ROLLOVER_CREDIT_CAP_WON
    # 상한에 닿는 전환액
    at_cap = int(cap / CUST.ISA_ROLLOVER_CREDIT_RATE)

    # 만기 경과 시 표시값은 전환 기한까지 남은 날
    left = CUST.ISA_ROLLOVER_DEADLINE_DAYS + dd if dd is not None and dd < 0 else dd
    if dd is None:
        window = ""
    elif dd < 0:
        window = f" (만기 {-dd}일 경과 · 전환 기한 {left}일 남음)"
    else:
        window = f" (D-{dd})"
    lines = [
        "■ ISA 만기자금을 전환하는 경우 — 위 계산과 **다른 축**이다 "
        "(잔여한도 안에서 나눠 쓰는 것이 아니라, 공제 대상 한도 자체가 늘어난다)",
        f"· ISA 만기자금 {_won(amt)} · 만기 {p.isa['date']}{window} · {p.isa['org']} — "
        f"만기일로부터 {CUST.ISA_ROLLOVER_DEADLINE_DAYS}일 이내에 전환한다",
        f"· 전환금은 연 납입한도 {_won(CUST.DEPOSIT_CAP_WON)}과 무관하다 — "
        f"만기금액의 전부 또는 일부를 넣을 수 있다",
        # 카드 등록 오답 문구(«전환금 전액이 공제 대상»)는 부정문으로도 쓰지 말 것 — LLM 이 옮겨 폐기됨
        f"· 전환액의 {CUST.ISA_ROLLOVER_CREDIT_RATE * 100:.0f}%가 세액공제 대상에 "
        f"**더해진다**(상한 {_won(cap)}) — 전환액 {_won(at_cap)}에서 상한에 닿는다. "
        f"더해지는 것은 전환액의 {CUST.ISA_ROLLOVER_CREDIT_RATE * 100:.0f}%뿐이고 "
        f"전환액 자체가 공제 대상이 되지는 않는다",
        f"· 그래서 공제 대상 한도가 {_won(CUST.TAX_CREDIT_CAP_WON)}에서 최대 "
        f"{_won(CUST.TAX_CREDIT_CAP_WON + cap)}으로 늘어난다",
    ]
    # 전환액별 구간표
    tiers = [t for t in (10_000_000, 20_000_000, at_cap) if t <= amt] or [amt]
    if amt not in tiers:
        tiers.append(amt)
    lines.append("· 전환액별 추가 공제 대상: " + " · ".join(
        f"{_won(t)} → {_won(CUST.isa_rollover_credit(t))}"
        + ("(상한)" if CUST.isa_rollover_credit(t) >= cap else "")
        for t in tiers))

    # 전환액 ≤ 만기금액
    conv = min(said[1], amt) if said else amt
    add_room = CUST.isa_rollover_credit(conv)
    # 10% 원값도 적재 — 상한 설명 시 그 수치가 없으면 검증에 걸림
    raw = int(conv * CUST.ISA_ROLLOVER_CREDIT_RATE)
    cut = f"10%는 {_won(raw)}이지만 상한에서 잘려 " if raw > add_room else ""
    lines.append(f"· 계산에 쓴 전환액 {_won(conv)} → {cut}추가 공제 대상 {_won(add_room)}"
                 + ("" if said else " (질문에 금액이 없어 만기금액 전부로 계산했다)"))
    for label, rate in _rates(p, state):
        lines.append(f"· {label}({rate * 100:.1f}%): 이 전환으로 늘어나는 환급 "
                     f"{CUST.tax_credit(add_room, rate):,}원")
    lines.append("· 이 금액은 위의 «현금을 더 납입하는 경우»와 별개로 더해지는 몫이다 — "
                 "둘을 같은 한도 안에서 저울질하지 않는다")
    return lines


#: 카드 value 에서 단서를 떼는 표지(단서 문장은 카드 소유).
_CAVEAT_MARK = "단, "


def _caveat(card: dict) -> str:
    """공제율 카드의 단서 문장. 표지 없으면 value 전체."""
    value = card.get("value") or ""
    at = value.find(_CAVEAT_MARK)
    return value[at:].strip() if at >= 0 else value.strip()
