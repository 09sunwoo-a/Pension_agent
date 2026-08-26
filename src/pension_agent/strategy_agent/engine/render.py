"""렌더링 보조 — 확정된 사실을 화면에 올릴 조각으로 바꾼다.

절(clause)·고객 헤더·보유 현황 briefing·관리 사유. 여기서 새로 판단하는 것은 없다.
`_briefing()` 이 조회 지시("MyStar 에서 확인")를 문장이 아니라 데이터로 표현하는 것이
이 계층의 성격을 잘 보여준다 — 이미 아는 값을 직원에게 다시 찾아보라고 하지 않는다.
"""

from __future__ import annotations

from typing import Any

from pension_agent.strategy_agent.customer import (
    RISK_ASSET_CAP_PCT,
    TAX_CREDIT_CAP_WON,
    Profile,
    churn,
    days_to_year_end,
)
from pension_agent.strategy_agent.engine.catalog import (
    ASSETS,
    BRIEFING_SOURCE,
    PORT_LABELS,
)
from pension_agent.strategy_agent.engine.products import _branch_defs
from pension_agent.strategy_agent.engine.text import _Ctx, _eul, _pname, _ro, won


# ─────────────────────────────────────────────────────────────

def customer_facing_asset(branch: str | None = None) -> dict | None:
    """고객 발송이 승인된 자료 1건. 미등록 시 None 을 반환한다.

    content_type 이 있는 레코드(이벤트·세미나 — REQUIREMENTS.md ⑨, next_event_and_seminar() 가
    다룬다)는 여기서 제외한다 — 같은 asset kind·같은 customer_facing 필드를 쓰지만 이 함수가
    찾는 '전략에 첨부할 발송 자료'와는 다른 성격의 콘텐츠다."""
    rows = [a for a in ASSETS if a.get("customer_facing") is True and not a.get("content_type")]
    if branch:
        rows = [a for a in rows if a.get("branch") in (branch, None)] or rows
    return rows[0] if rows else None


def _verb(spec: dict, label: str, row: dict) -> str:
    """상품에 부착할 동작 명사.

    분기 기본값보다 상품 유형별 지정을 우선한다. 같은 원리금보장 분기라도 만기 도래한
    정기예금은 '재예치', 신규 가입하는 GIC·ELB 는 '예치' 가 맞는 표현이다.
    """
    q = _branch_defs(spec).get(label, {})
    return (q.get("verb_by_category") or {}).get(row.get("category")) or q.get("verb", "배분")


def _action(spec: dict, products: dict[str, dict]) -> str:
    """상품과 동작을 결합한 구를 만든다.

    실적배당 상품에 '재예치' 를, 원리금보장 신규 가입에 '매수' 를 붙이지 않도록 동작 명사는
    분기·상품 유형 정의에서 가져온다.
    """
    parts = [(lb, f"{_ro(_pname(r))} {_verb(spec, lb, r)}") for lb, r in products.items() if r]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][1]
    return ", ".join(f"{lb} 희망 시 {txt}" for lb, txt in parts)


def _trim_target(p: Profile, spec: dict, conds: list[str], extra: dict) -> str:
    """위험자산 조정 전략의 축소 대상 표기.

    무엇을 얼마의 근거로 줄이는지를 절 안에 남긴다. 섹터 집중 요건이 함께 성립하면 우선
    처분 대상을 명시하되, 조정 근거(규정 한도 / 성향 기준)는 전략별로 구분해 표기한다.
    흡수 과정에서 이 정보가 사라지면 '위험자산을 줄이라' 는 지시만 남아 실행할 수 없다.
    """
    head = f"섹터 ETF {p.port[3]}%를 우선 처분해 " if ("sec" in conds and p.port[3] >= 50) else ""
    if spec["id"] == "st.limit_fix":
        return f"{head}위험자산 {p.risk_asset}% 중 투자한도({RISK_ASSET_CAP_PCT}%) 초과분"
    return f"{head}위험자산 {p.risk_asset}% 중 성향 기준({extra.get('target', '')}%) 초과분"


_COND_OPS = {
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


def _eval_condition(cond: dict | list, p: Profile) -> bool:
    """resolves 의 조건부 흡수(policy=conditional)를 판정하는 선언적 비교식 평가기.

    {"field": "matAmt", "op": ">=", "value_of": "dep_amt", "mul": 0.9} 는
    p.matAmt >= p.dep_amt * 0.9 를 뜻한다. cond 가 리스트면 전부 참이어야 성립한다(AND).
    새 조건부 흡수 규칙은 engine.py 를 열 필요 없이 strategies.json 에 조건식만 선언하면 된다.
    """
    if isinstance(cond, list):
        return all(_eval_condition(c, p) for c in cond)
    lhs = getattr(p, cond["field"])
    rhs = getattr(p, cond["value_of"]) * cond.get("mul", 1) if "value_of" in cond else cond["value"]
    return _COND_OPS[cond["op"]](lhs, rhs)


_BRACKET_LABEL = {"5500이하": "5,500만원 이하", "5500초과": "5,500만원 초과"}


def _three_way_breakdown(p: Profile) -> dict[str, float] | None:
    """3분류 운용현황 — 고유계정대/실적배당형/원리금보장형(REQUIREMENTS.md ③).

    cash_idle_pct(고유계정대 비중)가 없으면 None — 화면은 이 경우 3분류 표시를 생략한다
    (customer.py::Profile.cash_idle_pct 참고). 고유계정대는 port[0](예금)의 부분집합으로
    다룬다 — port[0] 자체는 위험자산 한도 등 기존 게이팅 로직이 그대로 참조하므로 건드리지
    않는다(port 4분류는 불변, 이 3분류는 순수 표시용 추가 뷰다).
    """
    if p.assets:
        # 원장(assets)이 있으면 거기서 뽑는다. port 4분류는 정수 반올림이라 원장의 7.7% 가
        # 8% 로 나가고, 그러면 같은 재료 안에 고유계정대가 8%(3분류)·7.7%(자산군별) 두 값으로
        # 실린다 — 어느 쪽을 인용해도 다른 줄과 어긋나는 답이 된다.
        by = {a["type"]: a["pct"] for a in p.assets}
        grouped = {
            "고유계정대": by.get("고유계정대", 0.0),
            "실적배당형": round(by.get("수익증권", 0.0) + by.get("ETF", 0.0), 1),
            "원리금보장형": round(by.get("예금", 0.0) + by.get("GIC", 0.0) + by.get("기타", 0.0), 1),
        }
        return {k: v for k, v in grouped.items()}
    if p.cash_idle_pct is None:
        return None
    return {   # 원장 없이 조립된 프로파일(합성 케이스) — 4분류 요약에서 되짚는다
        "고유계정대": p.cash_idle_pct,
        "실적배당형": p.port[1] + p.port[2] + p.port[3],
        "원리금보장형": p.port[0] - p.cash_idle_pct,
    }


def _last_contact_label(p: Profile) -> str:
    """최근 접촉 표기 — "N일 전", 180일 이상이면 "없음(6개월+)"(07_에이전트_기능정의/01 ① 양식
    "최근 접촉: 없음 (6개월+)"). 180 은 customer.conditions() 의 `dor`(장기 미접촉) 경계와 같다."""
    if p.dorm is None:
        return "확인 필요(접촉 이력 없음)"
    return "없음(6개월+)" if p.dorm >= 180 else f"{p.dorm}일 전"


def _return_label(p: Profile, *, long: bool = False) -> str:
    """수익률 표기. retPct 가 없으면 백분위 절을 통째로 뺀다.

    "하위 None%" 가 화면에 나가면 그것대로 근거처럼 읽힌다 — 모르는 값은 말하지 않는다.
    """
    if p.retPct is None:
        return f"{p.ret}%"
    return f"{p.ret}% (유사 고객 기준 하위 {p.retPct}%)" if long else f"{p.ret}% (하위 {p.retPct}%)"


def _customer_header(p: Profile) -> dict[str, Any]:
    """facts["customer"] — 화면 상단·LLM 프롬프트가 공유하는 고객 식별 항목(REQUIREMENTS.md §3.1·§3.2).

    CLI(agent._print)·Streamlit(app.py)이 전부 이 dict 를 읽으므로 상단 항목의
    유일한 출처다. 스타클럽 등급은 값이 있을 때만 키를 만든다 — 없는 값을 "미확인"으로 채우면 화면이
    그 문자열을 노출하게 된다.
    """
    header: dict[str, Any] = {
        "성명": p.nm, "연령": p.ag, "투자성향": p.rk, "위험등급": p.grade,
        "평가금액": (f"{won(p.bal)} (상위 {p.balPct}%)" if p.balPct is not None else won(p.bal)),
        "수익률": _return_label(p),
        "위험자산": f"{p.risk_asset}% (한도 {RISK_ASSET_CAP_PCT}%)",
        "거래채널": "비대면" if p.nonface else "대면",
        "소득구간": _BRACKET_LABEL.get(p.income_bracket or "", "구간 미확인"),
        "최근접촉": _last_contact_label(p),
    }
    if p.club_grade:
        header["스타클럽등급"] = p.club_grade
    return header


def customer_header_line(customer: dict) -> str:
    """화면 상단 한 줄(REQUIREMENTS.md §3.1). 항목 순서는 07/01 ① 양식 "만 57세 · VIP" 를 따라
    연령 다음에 등급을 둔다. CLI·Streamlit 이 공유한다."""
    parts = [f"{customer['연령']}세"]
    if customer.get("스타클럽등급"):
        parts.append(customer["스타클럽등급"])
    parts += [customer["투자성향"], f"평가금액 {customer['평가금액']}", f"최근 접촉 {customer['최근접촉']}"]
    return " · ".join(parts)


def _why_this_customer(p: Profile, conds: list[str]) -> list[str]:
    """AI 브리핑 근거 최대 3개, 정량 중심 1줄씩(REQUIREMENTS.md ② "왜 이 고객님인가요?").

    결정론적 코드 산출. agent._write_why_this_customer() 가 이 값을 LLM 해석 문장으로
    교체할 수 있는 재료 겸 폴백으로 쓴다(REQUIREMENTS.md §15 — 이 항목은 Rule·LLM 이 함께 표시된
    접점이라, LLM 이 없거나 실패해도 이 규칙 문장이 그대로 유효한 결과다). balPct 가
    없는 페르소나는 해당 줄만 생략한다(상류 조인 값이라 이 엔진이 직접 계산할 수 없다).
    """
    lines: list[str] = []
    if p.balPct is not None:
        lines.append(f"평가금액 {won(p.bal)}으로 유사 고객 상위 {p.balPct}% 수준이에요.")
    if p.retPct is not None:
        lines.append(f"최근 1년 수익률은 {p.ret}%로 유사 고객 하위 {p.retPct}% 수준이에요.")
    else:
        lines.append(f"최근 1년 수익률은 {p.ret}%예요.")
    if "mis" in conds:
        # 'mis' 는 성향에 따라 정반대를 뜻한다 — 보수 성향은 위험자산 과다로, 공격 성향은
        # 원리금보장형 과다로 성립한다(customer.conditions()). 한쪽 문구만 쓰면 "안정추구형
        # 인데 원리금보장형 10%가 높다"처럼 사실과 반대인 근거가 화면에 나간다.
        if p.rk in ("적극투자형", "공격투자형"):
            lines.append(f"{p.rk} 성향 대비 원리금보장형 비중이 {p.port[0]}%로 높아요.")
        else:
            lines.append(f"{p.rk} 성향 대비 위험자산 비중이 {p.risk_asset}%로 높아요.")
    return lines[:3]


def _briefing(p: Profile) -> dict:
    """상담 준비용 보유 현황 스냅샷.

    가이드의 '상품 보유 현황 확인' 절차(BRIEFING_SOURCE 참고)는 직원에게 'MyStar 단말에서
    보유 상품·만기·수익률을 조회'하도록 지시한다. 그 데이터는 타겟리스트·MyStar 를 조인한
    Profile 로 이미 시스템에 들어와 있으므로, 조회를 전략 문장의 지시로 남기지 않고 시스템이
    제시하는 사실로 돌린다.
    전략(clause)은 판단·행동만 담고, 이미 보유한 데이터의 제시는 여기(코드)에서 처리한다.
    """
    comp = " · ".join(f"{lbl} {pct}%" for lbl, pct in zip(PORT_LABELS, p.port) if pct)
    snap = {"보유구성": comp, "운용수익률": _return_label(p, long=True)}
    three_way = _three_way_breakdown(p)
    if three_way:
        snap["운용현황(3분류)"] = " · ".join(f"{k} {v}%" for k, v in three_way.items())
    # 자산군별 **금액**. 위의 보유구성·3분류는 비중뿐이라 "고유계정대 얼마야?" 처럼 금액을
    # 묻는 질문에 답할 재료가 없었다(대화형이 "금액은 재료에 없어요" 로 답하던 자리).
    # 비중도 여기 것이 원장값이다 — 4분류 요약은 정수 반올림이라 7.7% 가 8% 로 보인다.
    if p.assets:
        snap["자산군별"] = " · ".join(
            f"{a['type']} {won(a['amount'])}({a['pct']}%)" for a in p.assets)
    if p.matDD is not None:
        # **보유한 만기를 전부 싣는다.** 예금과 GIC 의 만기가 서로 다른 고객이 있어서,
        # 가장 가까운 한 건만 실으면 "만기 언제야?" 에 나머지가 없는 것처럼 답하게 된다.
        # 날짜를 함께 싣는 이유도 같다 — 재료에 없으면 대화형이 기준일에서 계산해 말한다.
        if p.maturities:
            snap["만기도래"] = " · ".join(
                f"{m['date']} (D-{m['dd']}) {m['type']} {won(m['amount'])}"
                for m in p.maturities)
        else:  # 만기 목록 없이 조립된 프로파일(합성 케이스) — 가장 가까운 건만 표기한다
            when = f"{p.matDate} (D-{p.matDD})" if p.matDate else f"D-{p.matDD}"
            snap["만기도래"] = f"{when} · {won(p.matAmt)}"
    # 추가납입 여력은 별도 전략(과거 st.add_invest)이 아니라 briefing 사실로 남긴다.
    # 근거 수치는 여기서 확정하고, 실제 제안 여부는 LLM 이 맥락상 판단한다(prompts.py).
    # 단 연금수령 개시 계좌는 추가입금 자체가 불가하므로(방법론 59) 납입여력을 제시하지 않고,
    # 대신 그 상태를 사실로 남겨 LLM·화면이 추가납을 권하지 않게 한다(07_에이전트_기능정의/01 ①
    # "연금개시 계좌 → 추가납 권유 금지", REQUIREMENTS.md §7).
    if p.pension_started:
        snap["연금수령"] = "수령 중 · 추가납입 불가(연금지급설계 등록 계좌)"
    elif p.room > 0:
        snap["납입여력"] = f"{won(p.room * 10000)} (연 납입한도 1,800만원 이내)"

    # 보유상품 개별 종목. 자산군별 합계로는 "무슨 상품 들고 있어"·"판매중단된 거 있어"에
    # 답할 수 없다. 수익률은 **고객 보유수익률**(그 고객이 실제로 얻은 것)을 쓴다 —
    # 상품의 최근 1년 수익률과 다르고, 직원이 묻는 것은 이 고객의 손익이다.
    if p.holdings:
        snap["보유상품"] = " · ".join(
            f"{h['name']} {won(h['amount'])}"
            + (f" 수익률 {h['ret_own'] * 100:.1f}%" if h.get("ret_own") is not None else "")
            + (f" 금리 {h['rate'] * 100:.2f}%" if h.get("rate") else "")
            + (" ⚠판매중단" if h["discontinued"] else "")
            for h in p.holdings)
    # 동연령대 비교 — 모수가 저장소 밖이라 엔진이 산출할 수 없는 조인값이다.
    if p.peer:
        q, bits = p.peer, []
        if q.get("avg_ret") is not None:
            bits.append(f"동연령 평균 수익률 {q['avg_ret']}%")
        if q.get("top1_ret") is not None:
            bits.append(f"상위1% 평균 수익률 {q['top1_ret']}%")
        if q.get("top1_guaranteed_pct") is not None:
            bits.append(f"상위1% 원리금보장 비중 {q['top1_guaranteed_pct']}%")
        if q.get("top1_funds"):
            bits.append("상위1% 인기 펀드 " + ", ".join(q["top1_funds"]))
        if q.get("top1_etfs"):
            bits.append("상위1% 인기 ETF " + ", ".join(q["top1_etfs"]))
        snap["동연령대비교"] = " · ".join(bits)
    # 거래 활동 — "최근에 거래한 적 있어" 는 운용변경 경과월(nchM)만으로 답할 수 없다.
    if p.activity:
        a, bits = p.activity, []
        for label, key in (("최근 매매", "last_trade"), ("최근 운용지시", "last_order"),
                           ("최근 입금", "last_deposit")):
            if a.get(key):
                bits.append(f"{label} {a[key]}")
        if a.get("trades_1y") is not None:
            bits.append(f"1년 매매 {a['trades_1y']}회")
        if a.get("cash_delta_1m"):
            bits.append(f"최근 1개월 고유계정대 증감 {won(a['cash_delta_1m'])}")
        if bits:
            snap["거래활동"] = " · ".join(bits)

    # ISA 만기자금 — **IRP 계좌 밖의 돈**이라 보유 현황과 갈라 적는다. 추가납입 상담의
    # 재원 후보이고, 만기가 임박하면 그 시점이 상담 창구가 된다(시연 케이스 2건).
    if p.isa:
        dd = f" (D-{p.isa['dd']})" if p.isa.get("dd") is not None else ""
        snap["ISA만기자금(IRP 외부)"] = (
            f"{won(p.isa['amount'])} · 만기 {p.isa['date']}{dd} · {p.isa['org']}")
    # 연도별 납입 이력 — "작년엔 얼마 넣었어" 는 당해 납입액만으로 답할 수 없다.
    if p.paid_by_year:
        snap["납입이력"] = " · ".join(f"{y} {won(v)}" for y, v in p.paid_by_year.items())

    # 「고객이 모르는 자기 현황」 — 07_에이전트_기능정의/01 ① 필수 구성 요소 3.
    # 브리핑 화면에 따로 칸을 만들지 않고 재료로만 싣는다: 이 항목은 화면(왼쪽)이 아니라
    # 대화형(오른쪽)에서 직원이 물었을 때 답하는 몫이기 때문이다.
    # 재료에 없으면 consult_agent 가 답을 써도 verify() 가 "재료 밖 수치"로
    # 거부한다 — 값은 Profile 에 있는데 답을 못 하던 상태가 정확히 그것이었다.
    snap["당해_납입액"] = won(p.pension_paid_ytd)
    credit = int(min(p.pension_paid_ytd, TAX_CREDIT_CAP_WON) * p.tax_credit_rate)
    snap["예상_세액공제액"] = (
        f"{won(credit)} (공제대상 {won(min(p.pension_paid_ytd, TAX_CREDIT_CAP_WON))}"
        f" × {p.tax_credit_rate * 100:.1f}%)"
    )
    snap["source"] = BRIEFING_SOURCE
    return snap


def _build_ctx(p: Profile, spec: dict, products: dict[str, dict], amount: int,
               action: str, conds: list[str], extra: dict) -> tuple[dict | None, _Ctx]:
    """절 템플릿의 슬롯 컨텍스트를 만든다. 선정 항목과 대안 항목이 동일한 규칙으로
    렌더링되도록 공유한다. asset 은 발송 자료 승인 여부(clause_if_asset 분기)를 함께 돌려준다."""
    asset = customer_facing_asset() if spec.get("clause_if_asset") else None
    ctx = _Ctx(
        product_action=action,
        product_bare=", ".join(_pname(r) for r in products.values()),
        asset=_eul(asset["name"]) if asset else "",
        amount=won(amount), matDD=p.matDD, port0=p.port[0], port3=p.port[3],
        risk_asset=p.risk_asset, rk=p.rk, grade=p.grade, ret=p.ret, retPct=p.retPct,
        dorm=p.dorm, nchM=p.nchM, churn=churn(p), year_end_dd=days_to_year_end(),
        trim_target=_trim_target(p, spec, conds, extra),
        income_bracket=_BRACKET_LABEL.get(p.income_bracket or "", "구간 미확인"),
        tax_rate=f"{p.tax_credit_rate:.1%}", **extra,
    )
    return asset, ctx
