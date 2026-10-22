"""고정 뷰 목록 — LLM 이 보는 유일한 스키마(결과 컬럼 이름·뜻, 프롬프트에 그대로 적재).

뷰 SQL 은 sql/<백엔드>/<뷰>.sql. 이 목록은 백엔드와 무관하게 동일해야 함.
"""

from __future__ import annotations

#: 부점 브리핑 세그먼트용 컬럼(2026-10-22). 원천 테이블 미확정 — 행외 목업 값
_BRIEFING_COLUMNS: list[tuple[str, str, str]] = [
    ("idle_cash_amt", "정수(원)", "운용되지 않는 현금성자산(고유계정대) 금액"),
    ("idle_cash_days", "정수(일)", "현금성자산(고유계정대) 금액이 변동 없이 지난 일수 — 미운용 기간(없으면 NULL)"),
    ("tax_credit_room", "정수(원)", "올해 세액공제 잔여한도(연금저축과 함께 쓰는 900만원 한도에서 남은 금액)"),
    ("transfer_inquiry", "문자", "최근 30일 안 계약이전 사전조회 신청 여부('신청' 또는 '없음')"),
    ("risk_mismatch", "문자", "투자성향 대비 자산구성 불일치 여부('불일치' 또는 '일치')"),
    ("isa_maturity_date", "날짜 문자 YYYY-MM-DD", "ISA 만기일(IRP 밖 계좌, 없으면 NULL)"),
    ("isa_maturity_amt", "정수(원)", "ISA 만기자금 금액(IRP 밖, 없으면 NULL)"),
    ("isa_days_to_maturity", "정수(일)", "오늘부터 ISA 만기일까지 남은 일수. 음수면 이미 지난 것"),
    ("etf_eval_amt", "정수(원)", "IRP 안에서 보유한 ETF 평가금액 합계(없으면 0) — «ETF 투자 금액»"),
]

#: 뷰 이름 → (설명, [(컬럼, 타입, 뜻)]). 컬럼이 None 이면 바로 앞 뷰와 같은 컬럼
#: (그 경우 전월말 뷰처럼 «같은 꼴의 다른 시점»이다 — catalog 가 컬럼을 다시 적지 않는다)
VIEWS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "v_irp_customer": (
        "우리 부점의 IRP 고객 — 고객 한 명당 한 행. 다른 부점 고객은 들어 있지 않다", [
            ("customer_name", "문자", "고객 이름"),
            ("age", "정수", "나이(만)"),
            ("star_grade", "문자", "스타클럽 등급(패밀리·베스트·그랜드·로얄·MVP)"),
            ("risk_profile", "문자", "투자성향(안정형·안정추구형·위험중립형·적극투자형·공격투자형)"),
            ("branch_name", "문자", "관리 부점 이름"),
            ("manager_emp_no", "문자", "관리 직원 사번"),
            ("joined_date", "날짜 문자 YYYY-MM-DD", "IRP 가입일"),
            ("eval_amt", "정수(원)", "IRP 평가금액"),
            ("return_rate", "실수(%)", "운용 수익률"),
            ("safe_asset_pct", "실수(%)", "원리금보장·현금성 자산 비중"),
            ("idle_cash_pct", "실수(%)", "운용되지 않는 현금성자산(고유계정대) 비중"),
            ("default_option", "문자", "디폴트옵션 설정 여부('설정' 또는 '미설정')"),
            ("pension_started", "문자", "연금 개시 여부('개시' 또는 '미개시')"),
            ("pension_eligible", "문자", "연금 개시 요건 충족 여부('충족' 또는 '미충족')"),
            ("next_maturity_date", "날짜 문자 YYYY-MM-DD", "가장 이른 만기 상품의 만기일(없으면 NULL)"),
            ("next_maturity_amt", "정수(원)", "그 만기 상품의 금액"),
            ("days_to_maturity", "정수(일)", "오늘부터 그 만기일까지 남은 일수. 음수면 만기가 지난 것"),
            ("last_contact_days", "정수(일)", "마지막 접촉 후 지난 일수(없으면 NULL)"),
            ("target_reasons", "문자", "관리 대상으로 선정된 사유들(쉼표로 이어 붙임, 없으면 빈 문자열)"),
            ("is_target", "정수 0/1", "관리 대상(타겟) 여부"),
            *_BRIEFING_COLUMNS,
        ]),
    "v_irp_customer_prev": (
        "우리 부점의 IRP 고객 — **전월말 기준** 스냅샷. 컬럼은 v_irp_customer 와 같고 남은 일수는 전월말 "
        "기준이다. «지난달보다 늘었나»처럼 전월말과 견줄 때만 쓴다", None),
    "v_transfer_out": (
        "우리 부점에서 다른 금융기관으로 계약이전(유출)된 IRP 계좌 — 한 건당 한 행. «올해 계약이전 몇 좌»·"
        "«어디로 옮겼나»가 여기다. 떠난 계좌라 v_irp_customer 에는 없다", [
            ("customer_name", "문자", "고객 이름"),
            ("branch_name", "문자", "관리 부점 이름"),
            ("transfer_date", "날짜 문자 YYYY-MM-DD", "계약이전 일자"),
            ("transfer_amt", "정수(원)", "이전 금액"),
            ("to_institution", "문자", "이전 받은 기관 업권('증권'·'은행'·'보험')"),
        ]),
    "v_branch_summary": (
        "부점별 집계 — 부점 하나당 한 행. 개인 정보 없이 다른 부점과 견줄 때 쓴다", [
            ("branch_name", "문자", "부점 이름"),
            ("is_my_branch", "정수 0/1", "질문한 직원의 부점이면 1"),
            ("customer_count", "정수", "IRP 고객 수"),
            ("total_eval_amt", "정수(원)", "평가금액 합계"),
            ("avg_return_rate", "실수(%)", "평균 수익률"),
            ("target_count", "정수", "관리 대상(타겟) 고객 수"),
            ("default_option_unset_count", "정수", "디폴트옵션 미설정 고객 수"),
            ("maturity_30d_count", "정수", "30일 안에 만기가 오는 고객 수"),
        ]),
}

#: 사번 → 부점 조회 SQL 이름(코드 전용, LLM 미노출).
MY_BRANCH = "my_branch"


def columns(view: str) -> list[tuple[str, str, str]]:
    """뷰 컬럼 목록. 컬럼이 None 인 뷰는 바로 앞 뷰의 컬럼."""
    names = list(VIEWS)
    i = names.index(view)
    while VIEWS[names[i]][1] is None:
        i -= 1
    return VIEWS[names[i]][1]


def catalog() -> str:
    """프롬프트에 싣는 뷰 목록."""
    out = []
    for name, (desc, cols) in VIEWS.items():
        out.append(f"### {name} — {desc}")
        out += [f"- {c} ({t}): {d}" for c, t, d in cols] if cols else ["- (컬럼은 바로 위 뷰와 같다)"]
    return "\n".join(out)
