"""렌더링 보조 — 절·고객 헤더·보유 현황 briefing·계좌 상태·관리 사유. 새 판단 없음."""

from __future__ import annotations

from typing import Any

from pension_agent.tools.customer_solution.customer import (
    DEPOSIT_CAP_WON,
    DORMANT_DAYS,
    RISK_ASSET_CAP_PCT,
    TAX_CREDIT_CAP_WON,
    Profile,
    churn,
    days_to_year_end,
    tax_credit,
    tenure_text,
)
from pension_agent.tools.customer_solution.engine.catalog import (
    ASSETS,
    BRIEFING_SOURCE,
    PORT_LABELS,
)
from pension_agent.tools.customer_solution.engine.products import _branch_defs
from pension_agent.tools.customer_solution.engine.text import _Ctx, _eul, _pname, _ro, dday, won


def customer_facing_asset(branch: str | None = None) -> dict | None:
    """전략 첨부용 고객 발송 승인 자료 1건(없으면 None). 이벤트·세미나(content_type) 제외.

    dummy 도 제외 — customer_facing(내용 적합)과 dummy(미확정)는 별개 선언.
    """
    rows = [a for a in ASSETS
            if a.get("customer_facing") is True and not a.get("content_type")
            and not a.get("dummy")]
    if branch:
        rows = [a for a in rows if a.get("branch") in (branch, None)] or rows
    return rows[0] if rows else None


def _verb(spec: dict, label: str, row: dict) -> str:
    """상품 동작 명사 — 상품 유형별 지정(verb_by_category) 우선, 없으면 분기 기본값."""
    q = _branch_defs(spec).get(label, {})
    return (q.get("verb_by_category") or {}).get(row.get("category")) or q.get("verb", "배분")


def _action(spec: dict, products: dict[str, dict]) -> str:
    """상품 + 동작 명사 구."""
    parts = [(lb, f"{_ro(_pname(r))} {_verb(spec, lb, r)}") for lb, r in products.items() if r]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][1]
    return ", ".join(f"{lb} 희망 시 {txt}" for lb, txt in parts)


def _trim_target(p: Profile, spec: dict, conds: list[str], extra: dict) -> str:
    """위험자산 조정 전략의 축소 대상·근거(규정 한도 / 성향 기준) 표기. 'sec' 성립 시 섹터 ETF 우선."""
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
    """resolves 조건식 평가. {"field","op","value"|"value_of","mul"}, 리스트는 AND."""
    if isinstance(cond, list):
        return all(_eval_condition(c, p) for c in cond)
    lhs = getattr(p, cond["field"])
    rhs = getattr(p, cond["value_of"]) * cond.get("mul", 1) if "value_of" in cond else cond["value"]
    return _COND_OPS[cond["op"]](lhs, rhs)


_BRACKET_LABEL = {"5500이하": "5,500만원 이하", "5500초과": "5,500만원 초과"}


def _three_way_breakdown(p: Profile) -> dict[str, float] | None:
    """표시용 3분류(고유계정대/실적배당형/원리금보장형). 원장·cash_idle_pct 모두 없으면 None."""
    if p.assets:
        # 원장 우선 — port 는 정수 반올림이라 자산군별 값과 어긋남
        by = {a["type"]: a["pct"] for a in p.assets}
        grouped = {
            "고유계정대": by.get("고유계정대", 0.0),
            "실적배당형": round(by.get("수익증권", 0.0) + by.get("ETF", 0.0), 1),
            "원리금보장형": round(by.get("예금", 0.0) + by.get("GIC", 0.0) + by.get("기타", 0.0), 1),
        }
        return {k: v for k, v in grouped.items()}
    if p.cash_idle_pct is None:
        return None
    return {   # 합성 프로필 — 4분류에서 역산
        "고유계정대": p.cash_idle_pct,
        "실적배당형": p.port[1] + p.port[2] + p.port[3],
        "원리금보장형": p.port[0] - p.cash_idle_pct,
    }


def _last_contact_label(p: Profile) -> str:
    """최근 접촉 표기 — "N일 전", 180일 이상(`dor` 경계)이면 "없음(6개월+)"."""
    if p.dorm is None:
        return "확인 필요(접촉 이력 없음)"
    return "없음(6개월+)" if p.dorm >= DORMANT_DAYS else f"{p.dorm}일 전"


def _return_label(p: Profile, *, long: bool = False) -> str:
    """수익률 표기. retPct 없으면 백분위 생략."""
    if p.retPct is None:
        return f"{p.ret}%"
    return f"{p.ret}% (유사 고객 기준 하위 {p.retPct}%)" if long else f"{p.ret}% (하위 {p.retPct}%)"


def _customer_header(p: Profile) -> dict[str, Any]:
    """facts["customer"] — 고객 식별 항목의 유일한 출처. 스타클럽 등급은 값 있을 때만."""
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


def _account_state(p: Profile) -> dict[str, Any]:
    """facts["account_state"] — 대화형 재료. 정상 상태도 값으로 수록("없음"·"미설정"도 답), 화면 미표시."""
    blocked = [h["name"] for h in p.holdings if h.get("discontinued")]
    state: dict[str, Any] = {
        "디폴트옵션": p.dopt,
        "연금개시": "개시" if p.pension_started else "미개시",
        "연금개시요건": "충족" if p.pension_eligible else "미충족",
        # 값만 — 잔여한도 0 사유 해석 금지(원장 미제공)
        "세액공제_잔여한도": f"{p.room:,}만원",
        "판매중단_보유상품": " · ".join(blocked) if blocked else "없음",
        # «없음»에 판정 범위 명시(만기 예정분만 원장 수록). 값 안 `·` 금지 — settled_block 구분자
        "ISA_만기자금": "보유" if p.isa else (
            "없음(원장의 만기 예정 ISA 자금 기준이다. ISA 계좌 보유 여부와 계좌 안 상품은 "
            "원장에 없다)"),
    }
    # 납입여력 — 잔여한도 소진 고객용(briefing 미수록분만, 중복 금지)
    # «세액공제 잔여한도» 문구 금지 — relations.checkable 이 위 항목 오짝 검사를 끔
    if not p.pension_started and p.room <= 0:
        state["납입여력"] = (
            f"{won(p.deposit_room)} (연 납입한도 {won(DEPOSIT_CAP_WON)} − 당해 납입 "
            f"{won(p.paid_ytd_total)}), 이 금액은 올해 세액공제 대상이 아니다"
            f"(초과분은 과세이연·이연공제)")
    # 원장 값 있을 때만 — «미확인» 수록 시 되묻기 판정이 정해진 축으로 오독
    if p.pension_savings is not None:
        state["연금저축_보유"] = "보유" if p.pension_savings else "없음"
    # 부담금 재원별 구성 — 카드 행 이름(괄호 별칭 대조용)과 원장 컬럼명 병기, 둘 다 0 이면 생략
    # 구분자는 쉼표 — `·` 는 settled_block 항목 구분자
    if p.severance_amt or p.own_contrib_amt:
        state["부담금별_구성"] = (
            f"사용자부담금(퇴직급여) {won(p.severance_amt)}, "
            f"가입자부담금(개인부담금) {won(p.own_contrib_amt)}")
    # 날짜로 수록 — 경과연수만 주면 LLM 이 날짜를 역산해 지어냄
    if p.joined:
        tenure = tenure_text(p.joined)
        state["IRP_가입일"] = f"{p.joined}" + (f" (가입 후 {tenure})" if tenure else "")
    return state


def customer_header_line(customer: dict) -> str:
    """화면 상단 한 줄(연령 · 등급 · 성향 · 평가금액 · 최근 접촉)."""
    parts = [f"{customer['연령']}세"]
    if customer.get("스타클럽등급"):
        parts.append(customer["스타클럽등급"])
    parts += [customer["투자성향"], f"평가금액 {customer['평가금액']}", f"최근 접촉 {customer['최근접촉']}"]
    return " · ".join(parts)


def _why_this_customer(p: Profile, conds: list[str]) -> list[str]:
    """② 왜 이 고객 — 정량 근거 최대 3줄(LLM 교체 재료 겸 폴백). 값 없는 줄 생략."""
    lines: list[str] = []
    if p.balPct is not None:
        lines.append(f"평가금액 {won(p.bal)}으로 유사 고객 상위 {p.balPct}% 수준이에요.")
    if p.retPct is not None:
        lines.append(f"최근 1년 수익률은 {p.ret}%로 유사 고객 하위 {p.retPct}% 수준이에요.")
    else:
        lines.append(f"최근 1년 수익률은 {p.ret}%예요.")
    if "mis" in conds:
        # 'mis' 는 성향별로 반대 의미 — 공격 성향은 원리금보장 과다, 보수 성향은 위험자산 과다
        if p.rk in ("적극투자형", "공격투자형"):
            lines.append(f"{p.rk} 성향 대비 원리금보장형 비중이 {p.port[0]}%로 높아요.")
        else:
            lines.append(f"{p.rk} 성향 대비 위험자산 비중이 {p.risk_asset}%로 높아요.")
    return lines[:3]


def _experience(name: str, holds, traded) -> str:
    """보유 중 / 과거 매매 이력 있음 / 경험 없음. 둘 다 None(미수록)이면 빈 문자열."""
    if holds is None and traded is None:
        return ""
    if holds:
        return f"{name} 보유 중"
    if traded:
        return f"{name} 과거 매매 이력 있음(현재 미보유)"
    return f"{name} 경험 없음"


def _briefing(p: Profile) -> dict:
    """상담 준비용 보유 현황 스냅샷 — 조회 지시 대신 보유 데이터를 사실로 제시."""
    comp = " · ".join(f"{lbl} {pct}%" for lbl, pct in zip(PORT_LABELS, p.port) if pct)
    snap = {"보유구성": comp, "운용수익률": _return_label(p, long=True)}
    three_way = _three_way_breakdown(p)
    if three_way:
        snap["운용현황(3분류)"] = " · ".join(f"{k} {v}%" for k, v in three_way.items())
    # 자산군별 금액·원장 비중(금액 질문 대응)
    if p.assets:
        snap["자산군별"] = " · ".join(
            f"{a['type']} {won(a['amount'])}({a['pct']}%)" for a in p.assets)
    if p.matDD is not None:
        # 만기 전부 + 날짜 수록 — 최근접 1건만이면 나머지 누락, 날짜 없으면 LLM 이 역산
        if p.maturities:
            snap["만기도래"] = " · ".join(
                f"{m['date']} ({dday(m['dd'])}) {m['type']} {won(m['amount'])}"
                for m in p.maturities)
        else:  # 합성 프로필 — 최근접 1건만
            when = f"{p.matDate} ({dday(p.matDD)})" if p.matDate else dday(p.matDD)
            snap["만기도래"] = f"{when} · {won(p.matAmt)}"
    # 추가납입 여력은 사실로만 수록. 연금개시 계좌는 납입 불가 사실로 대체(추가납 권유 금지)
    if p.pension_started:
        snap["연금수령"] = "수령 중 · 추가납입 불가(연금지급설계 등록 계좌)"
    elif p.room > 0:
        # 값은 납입한도 축(deposit_room), 공제 대상분은 같은 줄에 분리 — 공제한도를 납입여력으로 표기 금지
        snap["납입여력"] = (
            f"{won(p.deposit_room)} (연 납입한도 {won(DEPOSIT_CAP_WON)} − 당해 납입 "
            f"{won(p.paid_ytd_total)}) · 이 중 세액공제 대상은 잔여한도 "
            f"{won(p.room * 10_000)}까지 (초과분은 과세이연·이연공제)")

    # 보유상품 개별 종목. 수익률은 상품 1년 수익률이 아닌 고객 보유수익률
    if p.holdings:
        snap["보유상품"] = " · ".join(
            f"{h['name']} {won(h['amount'])}"
            + (f" 수익률 {h['ret_own'] * 100:.1f}%" if h.get("ret_own") is not None else "")
            + (f" 금리 {h['rate'] * 100:.2f}%" if h.get("rate") else "")
            + (" ⚠판매중단" if h["discontinued"] else "")
            for h in p.holdings)
    # 동연령대 비교(외부 조인값)
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
    # 거래 활동
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
        # 펀드·ETF 경험 — 화법 분기 기준(pitch.k03.025 경험 없음 ↔ 026 경험 있음)
        # «없음»도 수록(025 의 조건), 보유와 과거 매매 이력은 구분
        exp = [t for t in (_experience("펀드", a.get("holds_fund"), a.get("traded_fund")),
                           _experience("ETF", a.get("holds_etf"), a.get("traded_etf"))) if t]
        if exp:
            snap["펀드·ETF 경험"] = " · ".join(exp)

    # ISA 만기자금 — IRP 외부 자금이라 별도 키
    if p.isa:
        dd = f" ({dday(p.isa['dd'])})" if p.isa.get("dd") is not None else ""
        snap["ISA만기자금(IRP 외부)"] = (
            f"{won(p.isa['amount'])} · 만기 {p.isa['date']}{dd} · {p.isa['org']}")
    # 연도별 납입 이력
    if p.paid_by_year:
        snap["납입이력"] = " · ".join(f"{y} {won(v)}" for y, v in p.paid_by_year.items())

    # 고객이 모르는 자기 현황 — 대화형 재료(미수록 시 verify 가 재료 밖 수치로 거부)
    snap["당해_납입액"] = won(p.pension_paid_ytd)
    credit = tax_credit(p.pension_paid_ytd, p.tax_credit_rate)
    snap["예상_세액공제액"] = (
        f"{won(credit)} (공제대상 {won(min(p.pension_paid_ytd, TAX_CREDIT_CAP_WON))}"
        f" × {p.tax_credit_rate * 100:.1f}%)"
    )
    snap["source"] = BRIEFING_SOURCE
    return snap


def _build_ctx(p: Profile, spec: dict, products: dict[str, dict], amount: int,
               action: str, conds: list[str], extra: dict) -> tuple[dict | None, _Ctx]:
    """절 템플릿 슬롯 컨텍스트(선정·대안 공용) + clause_if_asset 분기용 asset."""
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
