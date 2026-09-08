"""세액공제 환급 예상액 도구(tax_credit) — 검색하지 않고 코드가 계산해 싣는다.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from pension_agent.consult_agent import kb as KBMOD
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.consult_agent.tools.base import Evidence, _ev, _scope


# ─────────────────────────────────────────────────────────────
# 세액공제 환급 예상액 — `date` 와 같은 부류. 검색하지 않고 코드가 계산해 싣는다.
#
# 07/01 ② 가 정한 「계산기」의 첫 조각이다. 그 장이 근거로 든 것이 이것이다 — 직원 두 명이
# 각자 엑셀로 세액공제 계산기를 만들어 배포했다(핫팁 199713·200518). 도구가 없어 사비로
# 만들 만큼 강한 니즈인데, 지금 재료에는 **현재 납입액 기준 한 값**만 있어서
# ("예상 세액공제액 118만원") "300만원 더 넣으면 얼마 더 받아?" 에 답할 수 없었다.
#
# ━━ 입력 수치는 **직원이 친 말**에서 뽑는다 ━━
# 계획 LLM 이 넘기는 `query` 는 직원 질문의 재작성본이라, 줄여 쓰는 과정에서 말을 흘린다
# (`run()` 주석의 화면번호 사례). 단어를 흘릴 수 있으면 숫자도 흘리는데, 검색은 0건으로
# 티가 나는 반면 계산기는 **조용히 다른 답**을 낸다. 게다가 계산 결과는 원장에 실려 인용이
# 허가되므로, 틀린 입력이 그대로 «승인된 숫자»가 된다 — LLM 이 경계를 넓히는 자리다.
# 그래서 금액은 `state["question"]` 에서 코드가 뽑는다(`verify.first_amount`).
#
# ━━ 공제율은 두 구간을 다 낸다 ━━
# 총급여 구간은 원장에 없다(demo_status §4 — 목업 9명 전원 미확인). 코드는 브리핑에서
# 보수적으로 낮은 쪽을 쓰지만(과대산출 회피), 계산기가 그 값 하나만 내놓으면 16.5% 구간
# 고객에게 "왜 적게 나와?" 가 된다. 두 경우를 다 싣고 어느 쪽인지는 직원이 가른다.
#
# ━━ «어디에 넣는 금액인가»를 재료가 적는다 ━━
# "300만원 더 넣으면 얼마 돌려받아?" 의 300만원은 어느 계좌에 넣는 돈인지가 **질문에**
# 없다. 그렇다고 갈래가 있는 것은 아니다 — 열려 있는 고객의 계좌가 개인형IRP 이고, 이
# 계산의 여력(잔여한도)도 그 원장에서 온다. **정해져 있는 것을 «가정하면» 으로 말하지
# 않는다**(§4 "제도·요건을 임의로 넓히거나 추정하지 않는다" 와 같은 자리 — 확정된 값을
# 추측처럼 적으면 직원은 확인해야 할 것이 있는 줄 안다). 갈래가 없으므로 되묻지도 않는다.
#
# 재료가 그 자리를 비워 두면 답변도 비운다 — 직원은 어느 계좌에 넣는 300만원인지 적히지
# 않은 금액을 고객에게 옮기게 된다. 그래서 계좌를 한 줄로 못박아 싣는다.
#
# 한도 쪽은 반대로 **계좌를 가리지 않는다.** 세액공제 한도 900만원은 IRP 단독이 아니라
# 연금저축과 함께 쓰는 한도라(fact.k04.f2 "연금저축 세액공제 포함") 잔여한도가 그만큼
# 줄어 있을 수 있다. 그것도 함께 적는다 — 「IRP 에 900만원까지 넣을 수 있다」로 읽히면
# 안 된다.
# ─────────────────────────────────────────────────────────────

#: 공제율의 근거 카드. 세율·한도·아래 단서가 전부 여기서 온다.
TAX_FACT_ID = "fact.k04.f2"

# ━━ ISA 만기자금 전환은 **다른 축이다** ━━
# 위 계산은 900만원 한도 «안에서» 현금을 더 넣으면 얼마인가다. ISA 만기자금 전환은 그
# 한도에 전환액의 10%(300만원)를 **더하고**, 전환금 자체는 연 1,800만원 납입한도에 걸리지
# 않는다(fact.k04.f4). 이 갈래가 없던 동안 「ISA 8,000만원 중 일부만 옮기면?」에 잔여한도
# 500만원으로 답했고 — 같은 대화에서 이미 인용한 카드(최대 1,200만원)와 어긋났다.
#
# **전환액을 `tax_credit()` 에 그대로 넣지 않는다.** 그러면 8,000만원 전환이 한도를 채운
# 것으로 계산되는데, 그것이 카드가 못박은 오답이다("전환금 전액이 공제 대상" = 오답).
# 늘어나는 것은 «공제 대상 납입액»뿐이고, 공제율은 거기에만 곱한다.
#: 전환 특례의 근거 카드. 10%·300만원·60일·납입한도 예외가 전부 여기서 온다.
ISA_FACT_ID = "fact.k04.f4"


def _won(v: int) -> str:
    from pension_agent.strategy_agent.engine.text import won  # noqa: PLC0415

    return won(v)


def _extra_paid(state: AgentState) -> tuple[str, int] | None:
    """계산에 쓸 «추가 납입액». 직원이 친 말에서 뽑되, **되물은 갈래를 고르는 답이면 그 말의
    수치는 «갈래»이지 납입액이 아니다** — 원래 질문으로 거슬러 올라간다.

    이게 없던 동안 되묻기 다음 턴이 **틀린 금액을 답했다**(2026-09-02 실측 — 이수민).
    「300만원 더 넣으면 얼마 돌려받아?」가 총급여 구간을 되물었고, 직원이 「5,500만원
    이하야」라고 답하자 그 5,500만원을 추가 납입액으로 읽었다. 잔여한도(900만원)로 잘려
    화면에는 **1,485,000원**이 떴다 — 물어본 300만원의 답(495,000원)이 아니라 한도를 다
    채웠을 때의 값이고, 되묻기 선택지에 방금 495,000원이라 적어 놓고 그랬다.

    기준서 §5 가 정한 것이 그것이다 — 「되물은 다음 턴의 짧은 답은 그 질문의 답이므로,
    **원래 질문과 고른 갈래를 합쳐** 답한다」. 도구가 이번 턴 질문만 보면 원래 질문이 없다.

    직전 턴이 되묻기였는지는 코드가 아는 값이다(`pending_clarify`) — LLM 에 맡기지 않는다.
    """
    from pension_agent.verify import first_amount  # noqa: PLC0415

    history = state.get("history") or []
    if not (history and (history[-1] or {}).get("pending_clarify")):
        return first_amount(state.get("question") or "")
    # 갈래를 고르는 턴이다. 이번 말의 수치는 선택지 라벨이므로 보지 않고, 금액을 말한
    # 가장 가까운 앞 질문을 쓴다. 없으면 None 이라 호출부가 잔여한도로 읽는다.
    for turn in reversed(history):
        found = first_amount(turn.get("question") or "")
        if found:
            return found
    return None


def _tax_credit(state: AgentState, query: str) -> Evidence | None:
    """세액공제 환급 예상액. 계산은 strategy_agent 것을 쓰고 여기서는 재료로 편다."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.strategy_agent import customer as CUST  # noqa: PLC0415
    from pension_agent.verify import first_amount  # noqa: PLC0415

    p = CUST.get_profile(customer_id)
    card = KB.facts.get(TAX_FACT_ID)
    if p is None or card is None:
        return None

    paid, cap = p.pension_paid_ytd, CUST.TAX_CREDIT_CAP_WON
    # 잔여한도는 **원장 값을 쓴다**(`p.room`). 한도에서 IRP 납입액을 빼서 다시 계산하면
    # 안 된다 — 한도 900만원은 연금저축과 **공유**라(fact.k04.f2 "연금저축 세액공제 포함")
    # 연금저축에서 이미 쓴 몫을 IRP 납입액만으로는 알 수 없다. 실제로 당해 납입 0원인데
    # 잔여한도가 0인 고객이 목업에 있다 — 다시 계산하면 그 고객에게 "900만원 더 넣으면
    # 148.5만원" 이라고 말하게 된다(§3 "같은 판정을 두 번 구현하지 않는다").
    room = p.room * 10_000
    # 금액을 안 말했으면 «잔여한도를 채우면» 으로 읽는다. 원장 값이라 지어낸 수가 아니고,
    # 직원이 실제로 묻는 것도 대개 그것이다("얼마나 더 받을 수 있어?").
    said = _extra_paid(state)
    extra = said[1] if said else room
    gain_base = min(extra, room)          # 잔여한도를 넘는 납입은 공제 대상이 아니다
    target = paid + gain_base
    gain = CUST.tax_credit(target, 1.0) - CUST.tax_credit(paid, 1.0)

    # ISA 만기자금이 있으면 **질문에 ISA 라는 말이 없어도** 전환 축을 함께 싣는다. 되묻기
    # 뒤의 답("초과야")처럼 질문이 한 마디로 줄어드는 턴이 있어서, 말에서 찾으면 정작 그
    # 축이 필요한 턴에 빠진다 — 이 고객에게 성립한 상태인지는 코드가 이미 아는 값이다(§8).
    isa_card = KB.facts.get(ISA_FACT_ID)
    isa_used = bool(p.isa) and isa_card is not None and _isa_convertible(p.isa)
    # 전제를 밝히는 축이 **둘**이다. 하나는 «어디에 넣나»(IRP 냐 연금저축이냐 — 아래 f3
    # 갈래), 다른 하나는 «어디서 온 돈인가»(현금이냐 ISA 만기자금이냐). 둘 다 밝히지 않으면
    # 직원이 말한 금액(「8천만원」)이 블록마다 다른 뜻으로 쓰이는데 화면에서는 분간되지 않는다.
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
        # 이 블록은 환급 «금액»을 새로 단정하지 않으므로 결정세액 단서가 무관하다
        # (CLAUDE.md §7). 단서를 붙일지는 아래에서 **두 블록을 다 보고** 정한다 — ISA 전환
        # 축이 실리면 그쪽이 금액을 단정하므로, 이 블록만 보고 생략하면 단서가 빠진다.
        #
        # 아래 줄은 「연 납입한도 900만원」이라 적혀 있었다. 900만원은 **공제 한도**이고 납입한도는
        # 1,800만원이라(fact.k04.f1), 그 이름으로 부르면 «더 넣을 수 없다»로 읽힌다 —
        # 실제로는 더 넣을 수 있고 공제가 안 될 뿐이다(초과분은 과세이연·이연공제, f5).
        lines.append(f"· 세액공제 잔여한도가 {_won(room)}이라 **추가 공제 대상이 없다** — "
                     f"더 납입해도 올해 세액공제로 돌아오는 금액은 늘지 않는다 "
                     f"(세액공제 한도 {_won(cap)}은 연금저축과 함께 쓴다. 납입 자체는 "
                     f"연 납입한도 {_won(CUST.DEPOSIT_CAP_WON)}까지 가능하다)")
    else:
        lines.append(f"· 공제 대상 {_won(min(paid, cap))} → {_won(min(target, cap))} "
                     f"(잔여한도 {_won(room)}까지)")
        for when, rate in (("총급여 5,500만원 이하", CUST.TAX_CREDIT_RATE["5500이하"]),
                           ("총급여 5,500만원 초과", CUST.TAX_CREDIT_RATE["5500초과"])):
            now, after = CUST.tax_credit(paid, rate), CUST.tax_credit(target, rate)
            lines.append(f"· {when}({rate * 100:.1f}%): 환급 예상 {now:,}원 → {after:,}원 "
                         f"(늘어나는 금액 {after - now:,}원)")
        lines.append("· 이 고객의 총급여 구간은 원장에 없어 두 경우를 다 실었다 — "
                     "어느 구간인지 확인하면 하나로 좁혀진다")

    if isa_used:
        lines += _isa_rollover_lines(p, said)
        cards.append(isa_card)
    if gain > 0 or isa_used:
        # 환급 «금액»을 단정하는 갈래에만 붙는다(§7). 두 축이 다 나와도 단서는 하나다 —
        # 같은 카드의 같은 문장이라 두 번 실으면 화면에 같은 경고가 겹쳐 선다.
        notices.append(_caveat(card))
    return _ev("tax_credit", query, "\n".join(lines),
               KBMOD.sources_of(KB, [(1.0, c) for c in cards]), notices=notices,
               scopes=[_scope(card.get("label") or TAX_FACT_ID, [], notices)] if notices else None,
               cards=cards)


def _isa_convertible(isa: dict) -> bool:
    """아직 전환할 수 있는 ISA 만기자금인가 — 만기일로부터 60일 이내(fact.k04.f4).

    기한이 지난 자금에 «옮기면 얼마 더 받는다»를 실으면 직원이 안내할 수 없는 것을
    안내하게 된다. 잔여일수를 모르면(원장에 만기일이 없으면) 막지 않는다 — 확인하지
    못한 것과 기한이 지난 것은 다르고, 앞엣것을 뒤엣것으로 다루면 있는 기회가 사라진다.
    """
    from pension_agent.strategy_agent import customer as CUST  # noqa: PLC0415

    dd = isa.get("dd")
    return dd is None or dd >= -CUST.ISA_ROLLOVER_DEADLINE_DAYS


def _isa_rollover_lines(p, said: tuple | None) -> list[str]:
    """ISA 만기자금 전환 축의 재료. 위 계산과 **더해지는** 몫이라 블록을 갈라 싣는다."""
    from pension_agent.strategy_agent import customer as CUST  # noqa: PLC0415

    amt, dd = p.isa["amount"], p.isa.get("dd")
    cap = CUST.ISA_ROLLOVER_CREDIT_CAP_WON
    # 상한에 닿는 전환액. 10%·300만원에서 나오는 값이라 코드가 다시 정하지 않는다.
    at_cap = int(cap / CUST.ISA_ROLLOVER_CREDIT_RATE)

    # 기한은 «만기까지 며칠»이 아니라 «전환할 수 있는 날이 며칠 남았나»다. 만기가 지난
    # 자금도 60일 안이면 전환할 수 있고, 그때 직원이 봐야 하는 수는 남은 날이다.
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
        f"· 전환액의 {CUST.ISA_ROLLOVER_CREDIT_RATE * 100:.0f}%가 세액공제 대상에 "
        f"**더해진다**(상한 {_won(cap)}) — 전환액 {_won(at_cap)}에서 상한에 닿는다. "
        f"전환금 전액이 공제 대상이 되는 것이 아니다",
        f"· 그래서 공제 대상 한도가 {_won(CUST.TAX_CREDIT_CAP_WON)}에서 최대 "
        f"{_won(CUST.TAX_CREDIT_CAP_WON + cap)}으로 늘어난다",
    ]
    # 「일부만 옮기면?」의 답은 금액 하나가 아니라 **전환액과의 관계**다. 직원이 금액을
    # 말했으면 그 금액으로, 안 말했으면 구간표로 답한다.
    tiers = [t for t in (10_000_000, 20_000_000, at_cap) if t <= amt] or [amt]
    if amt not in tiers:
        tiers.append(amt)
    lines.append("· 전환액별 추가 공제 대상: " + " · ".join(
        f"{_won(t)} → {_won(CUST.isa_rollover_credit(t))}"
        + ("(상한)" if CUST.isa_rollover_credit(t) >= cap else "")
        for t in tiers))

    # 전환액은 만기금액을 넘을 수 없다. 직원이 만기금액 전체를 말한 경우(「8천만원 전부는
    # 부담스럽다」)도 여기로 들어와 상한에서 잘린다 — 지어낸 수가 아니라 원장 값이다.
    conv = min(said[1], amt) if said else amt
    add_room = CUST.isa_rollover_credit(conv)
    lines.append(f"· 계산에 쓴 전환액 {_won(conv)} → 추가 공제 대상 {_won(add_room)}"
                 + ("" if said else " (질문에 금액이 없어 만기금액 전부로 계산했다)"))
    for label, rate in (("총급여 5,500만원 이하", CUST.TAX_CREDIT_RATE["5500이하"]),
                        ("총급여 5,500만원 초과", CUST.TAX_CREDIT_RATE["5500초과"])):
        lines.append(f"· {label}({rate * 100:.1f}%): 이 전환으로 늘어나는 환급 "
                     f"{CUST.tax_credit(add_room, rate):,}원")
    lines.append("· 이 금액은 위의 «현금을 더 납입하는 경우»와 별개로 더해지는 몫이다 — "
                 "둘을 같은 한도 안에서 저울질하지 않는다")
    return lines


#: 환급액에 따라붙는 단서를 카드에서 떼어 오는 표지. 코드가 문장을 갖지 않는다 —
#: 세법이 바뀌면 카드가 바뀌고 답변도 함께 바뀌어야 한다(§7 "표시는 데이터 선언이 정한다").
_CAVEAT_MARK = "단, "


def _caveat(card: dict) -> str:
    """공제율 카드가 못박은 단서. 없으면 카드 원문을 그대로 쓴다(지어내지 않는다)."""
    value = card.get("value") or ""
    at = value.find(_CAVEAT_MARK)
    return value[at:].strip() if at >= 0 else value.strip()
