"""고객 프로필(Profile)·원장 매핑·타겟 선정 요건 판정.

임계값 상위 기준은 타겟 룰베이스(targets.json) — 어긋나면 코드 오류. 상수마다 근거 TARGET_ID·근거등급(A~D) 표기.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import date

from pension_agent.common.clock import TODAY_ENV, today  # noqa: F401 — 공용 재노출

# 두 시간축 — AS_OF: 원장 스냅샷 기준일(customers.json meta.as_of 와 일치 필수) · today(): 상담일(잔여일수·경과일 기준)
# 하나로 합치면 원장이 묵은 만큼 잔여일수가 틀림. 재현은 PENSION_TODAY=YYYY-MM-DD
AS_OF = date(2026, 8, 24)


def ledger_age_days() -> int:
    """원장 스냅샷 경과 일수(오늘 − AS_OF)."""
    return (today() - AS_OF).days

# 위험등급(오름차순, 인덱스 비교로 상한 판정)
RISK = ["매우낮은위험", "낮은위험", "보통위험", "다소높은위험", "높은위험", "매우높은위험"]

# 투자성향별 선호 위험등급 — 정렬 선호 기준(하드 상한은 Profile.grade)
PREF = {
    "안정형": "매우낮은위험",
    "안정추구형": "낮은위험",
    "위험중립형": "보통위험",
    "적극투자형": "다소높은위험",
    "공격투자형": "높은위험",
}

# DC·개인형IRP 위험자산 투자한도(%) — 성향 무관 규정
RISK_ASSET_CAP_PCT = 70

# 총급여 구간별 세액공제율(지방소득세 포함). fact.k04.f2
TAX_CREDIT_RATE = {"5500이하": 0.165, "5500초과": 0.132}

# 세액공제 대상 납입액 한도(원). strategies.json `credit_limit` 와 함께 수정(test_engine 검사)
TAX_CREDIT_CAP_WON = 9_000_000

# 한도 3축 — 축 미표기 시 최소값이 상한으로 오독
#   TAX_CREDIT_CAP_WON 900만원(공제 대상, f2) · DEPOSIT_CAP_WON 1,800만원(납입 가능, f1) · ISA_ROLLOVER_CREDIT_* 300만원(전환 추가 공제, f4)
# 연간 납입한도(원, 연금저축·DC 개인부담금 합산). fact.k04.f1
DEPOSIT_CAP_WON = 18_000_000

# ISA 만기자금 전환 특례(fact.k04.f4 · proc.048) — 전환액 10%(최대 300만원)가 공제 한도에 추가, 납입한도 미적용
ISA_ROLLOVER_CREDIT_RATE = 0.10
ISA_ROLLOVER_CREDIT_CAP_WON = 3_000_000
#: 전환 기한(ISA 만기일로부터 일수). fact.k04.f4
ISA_ROLLOVER_DEADLINE_DAYS = 60


def tax_credit(paid_won: int, rate: float) -> int:
    """세액공제 환급 상한(원, 한도까지). 화면·계산기 공용. 결정세액 단서는 호출부가 병기."""
    return int(min(paid_won, TAX_CREDIT_CAP_WON) * rate)


def isa_rollover_credit(converted_won: int) -> int:
    """ISA 전환으로 추가되는 공제 대상액(원). 전환금을 tax_credit() 에 직접 넣는 것 금지."""
    return min(int(converted_won * ISA_ROLLOVER_CREDIT_RATE), ISA_ROLLOVER_CREDIT_CAP_WON)


#: 운용변경 없음 임계(개월) — TARGET_ID TG-201(근거등급 D). churn() 의 6개월 구간과 별개
NO_CHANGE_MONTHS = 12

# 만기예금 요건 범위(잔여일수) — «만기 1개월 전 안내»(세그먼트 9·방법론 18). churn() 구간과 별개
MAT_WINDOW_DAYS = 30


@dataclass
class Profile:
    """타겟리스트·MyStar·CRM 조인 고객 레코드(시스템 단일 입력)."""

    id: str
    nm: str
    ag: int
    bal: int  # 평가금액(원)
    rk: str  # 투자성향
    grade: str  # 고객 위험등급(가입 상품 하드 상한)
    port: list[int]  # 자산 구성비(%) [예금, 채권형, TDF/MP, 섹터ETF]
    ret: float  # 1년 수익률(%)
    retPct: int | None  # 수익률 백분위(낮을수록 하위). None = 미산출
    dopt: str  # 디폴트옵션 설정 여부 ("설정" / "미설정")
    room: int  # 당해 세액공제 잔여한도(만원, 900만원 축). 납입 여력 아님(→ deposit_room)
    dorm: int | None  # 최종 접촉 후 경과 일수. None = 소스 없음
    nchM: float  # 최종 운용변경 이후 경과 개월수
    # 아래 셋은 최근접 만기 1건(요건 판정·재예치용). 만기 전체는 `maturities`
    matDD: int | None = None  # 가장 가까운 만기까지의 잔여일수
    matDate: str | None = None  # 최근접 만기일(ISO) — 없으면 LLM 이 날짜 역산
    matAmt: int = 0  # 그 만기일에 도래하는 금액(원)
    holdings: list[dict] = field(default_factory=list)  # 보유상품 개별 종목(평가금액 내림차순)
    # 상담 기록은 미수록 — 세션 저장소 단일 경로(중복 노출 방지)
    peer: dict | None = None  # 동연령대 비교(외부 조인값)
    activity: dict = field(default_factory=dict)  # 거래 활동(원장)
    pension_eligible: bool = False  # 연금개시 요건 충족(원장 산출값, 재판정 금지)
    isa: dict | None = None  # ISA 만기자금(IRP 외부). 0 이면 None
    paid_by_year: dict[str, int] = field(default_factory=dict)  # 연도별 IRP 납입액(원)
    assets: list[dict] = field(default_factory=list)  # 자산군별 금액·비중(원장 소수값). port 는 정수 요약
    maturities: list[dict] = field(default_factory=list)  # 만기 보유 전체(만기일 오름차순)
    nonface: bool = False  # 비대면 거래 채널 고객 여부
    income_bracket: str | None = None  # 총급여 구간 ("5500이하" / "5500초과")
    customer_type: str | None = None  # "직장인"/"사업자"/"공통" — 화법 선택용, 소스 미정
    # 부담금 재원별 구성(수수료율표 f50 분기 기준)
    # 주의: 목업은 합 = 평가금액, 실데이터 대응 컬럼(userBram·apctBram)은 납입 누계 — 전환 시 축 확인
    severance_amt: int = 0  # 퇴직급여(사용자부담금) 몫(원)
    own_contrib_amt: int = 0  # 개인부담금(가입자부담금) 몫(원)
    pension_paid_ytd: int = 0  # 당해 세액공제 인정 납입액(원)
    paid_ytd_total: int = 0  # 당해 연금계좌 실납입액 합계(원) — IRP + 연금저축.
    # 연금저축 계좌 보유. None = 미확인(당해 납입 0원으로 대체 판정 금지)
    pension_savings: bool | None = None
    balPct: int | None = None  # 평가금액 백분위(낮을수록 상위 — retPct 와 방향 반대)
    cash_idle_pct: int | None = None  # 고유계정대 비중(%) — port[0] 의 부분집합
    invest_period_years: float | None = None  # 가입 후 경과연수(반올림 — 문장 표기는 tenure_text)
    joined: str | None = None  # IRP 가입일(ISO)
    pension_started: bool = False  # 연금수령 개시 — 참이면 add·tax 미성립(추가납 권유 금지)
    club_grade: str | None = None  # 스타클럽 등급 — 값 있을 때만 표시

    @property
    def risk_asset(self) -> int:
        """위험자산 비중(%) = TDF/MP + 섹터 ETF."""
        return self.port[2] + self.port[3]

    @property
    def dep_amt(self) -> int:
        """예금 평가금액(원)."""
        return round(self.bal * self.port[0] / 100)

    @property
    def risk_amt(self) -> int:
        """위험자산 평가금액(원)."""
        return round(self.bal * self.risk_asset / 100)

    @property
    def risk_headroom_amt(self) -> int:
        """위험자산 한도까지 여력(원, 초과 시 0) — 위험자산 증가 제안의 배분 상한."""
        return max(0, round(self.bal * (RISK_ASSET_CAP_PCT - self.risk_asset) / 100))

    @property
    def tax_credit_rate(self) -> float:
        """세액공제율. 구간 미확인 시 낮은 쪽(과대 산출 방지)."""
        return TAX_CREDIT_RATE.get(self.income_bracket or "", TAX_CREDIT_RATE["5500초과"])

    @property
    def deposit_room(self) -> int:
        """연 납입한도(1,800만원)까지 남은 금액(원). `room`(공제 축)과 별개, ISA 전환액 미차감."""
        return max(0, DEPOSIT_CAP_WON - self.paid_ytd_total)


CONDS = {
    "dep": "원리금보장상품 편중(80% 이상)",
    "nod": "디폴트옵션 미설정",
    "low": "수익률 하위 30%",
    "dor": "장기 미접촉",
    "add": "추가입금 여력 보유",
    "nch": "운용변경 없음(12개월+)",
    "tax": "세액공제 활용 가능",
    "mat": "만기예금 보유",
    "chn": "이탈 위험 높음",
    "sec": "특정 섹터 집중",
    "mis": "투자성향 불일치",
    "lim": "위험자산 한도 초과",
    # 배지 요건 5종 — 세그먼트 condition_text 원문 기준, 배지 골든셋 재현(test_engine)
    "idl": "미운용 현금성자산",
    "hlt": "판매중단·환매추천 펀드 보유",
    "pen": "연금개시 요건충족 후 미개시",
    "isa": "ISA 만기자금 보유",
    "out": "이탈위험 관찰(현금화 신호)",
}

#: 원리금보장 편중 임계(%) — TARGET_ID TG-202(D, 80~100%) ∪ TG-003(A, 100%) 합집합이라 상한 없음
PRINCIPAL_HEAVY_PCT = 80

#: 미운용 현금성자산 임계 — 고유계정대 비중(%), TARGET_ID TG-001 · 세그먼트 1·3 선별 기준
CASH_IDLE_PCT = 50

# 요건 표기·타겟 목록 우선순위(전략 선정은 engine._score)
PRIO = ["chn", "out", "dor", "lim", "mis", "sec", "hlt", "dep", "idl", "nod", "low",
        "mat", "isa", "pen", "tax", "add", "nch"]


def churn(p: Profile) -> float:
    """이탈 위험 점수 — 미접촉·만기임박·성과저조·방치·미설정 가산(None 항목 미가산)."""
    v = min(8.0, p.dorm / 60) if p.dorm is not None else 0.0
    if p.matDD is not None and p.matDD <= 30:
        v += 3
    elif p.matDD is not None and p.matDD <= 90:
        v += 1.5
    if p.retPct is not None and p.retPct <= 30:
        v += 2.5
    if p.nchM >= 6:
        v += 2
    if p.dopt == "미설정":
        v += 1
    return round(v, 1)


def conditions(p: Profile) -> list[str]:
    """성립 타겟 요건(PRIO 순)."""
    a: set[str] = set()
    # port[0] = 룰베이스 «원리금보장_합산비율»
    if p.port[0] >= PRINCIPAL_HEAVY_PCT:
        a.add("dep")
    if p.dopt == "미설정":
        a.add("nod")
    # None 가드 필수 — None 비교 시 TypeError 로 브리핑 전체 중단
    if p.retPct is not None and p.retPct <= 30:
        a.add("low")
    if p.dorm is not None and p.dorm >= 180:
        a.add("dor")
    if p.nchM >= NO_CHANGE_MONTHS:
        a.add("nch")
    # 하한 0 필수 — 지난 만기(음수) 제외
    if p.matDD is not None and 0 <= p.matDD <= MAT_WINDOW_DAYS:
        a.add("mat")
    if p.port[3] >= 50:
        a.add("sec")
    if p.risk_asset > RISK_ASSET_CAP_PCT:
        a.add("lim")
    if (
        (p.rk in ("안정형", "안정추구형") and p.risk_asset >= 35)
        or (p.rk == "위험중립형" and p.risk_asset >= 68)
        or (p.rk in ("적극투자형", "공격투자형") and p.port[0] >= 65)
    ):
        a.add("mis")
    if churn(p) >= 10:
        a.add("chn")

    # 고유계정대 과다 — 1개월 변동 없음이면 방치(idl), 유입이면 현금화 신호(out). 세그먼트 26·34
    if p.cash_idle_pct is not None and p.cash_idle_pct >= CASH_IDLE_PCT:
        delta = (p.activity or {}).get("cash_delta_1m")
        if delta:
            # 유입 임계값 미정(원문 없음) — 지어내지 않고 >0
            if delta > 0:
                a.add("out")
        else:
            a.add("idl")
    # 세그먼트 8 — PB센터 제외 조건은 원장 컬럼 없어 미적용
    if any(h.get("discontinued") for h in p.holdings):
        a.add("hlt")
    # 세그먼트 19-1 — 요건 충족은 원장 산출값
    if p.pension_eligible and not p.pension_started:
        a.add("pen")
    # 세그먼트 17 — 만기 예정분만(해지 후 경과일 컬럼 없음)
    if p.isa:
        a.add("isa")
    # 연금개시 계좌는 추가납 요건 미성립(추가입금 불가 — 방법론 59)
    if not p.pension_started:
        if p.room > 0:
            a.add("add")
        if p.room >= 300:
            a.add("tax")
    return [k for k in PRIO if k in a]


def days_to_year_end(base: date | None = None) -> int:
    """연말까지 잔여일수(오늘 미포함, 12/31 당일 0). 오늘 포함은 +1."""
    base = base or today()
    return (date(base.year, 12, 31) - base).days


# 고객 로스터 — customers.json(생성물) → Profile 매핑만
#   · port 4분류 [원리금보장+현금, 채권형(저위험 수익증권), 실적배당 수익증권, ETF], cash_idle_pct ⊂ port[0]
#   · grade 는 원본에 없음 → PREF[투자성향] 으로 보수적 파생(실데이터 전환 시 교체)
#   · retPct·balPct·customer_type 은 None
#   · income_bracket 은 총급여에서 구간으로(기획자 확인 전 부여 컬럼)
#   · matDD/matAmt 는 만기 보유상품 중 최근접 만기
#
# 접지 않은 원장 컬럼(이유)
#   badges 블록               골든셋 — 판정 입력으로 쓰면 정답을 보고 답을 씀
#   tax_isa.세액공제기준한도   TAX_CREDIT_CAP_WON 과 중복(이중 관리 금지)
#   tax_isa.총급여            구간만 사용 — 금액 노출 요건 없음
#   basic.관리점              사용처 없음
#   basic.투자성향분석일       유효기간 규칙 없음(LLM 이 지어냄)
#   summary.디폴트옵션위험등급  등급별 판정 기준 없음
#   pension.연금개시일·연금지급설계등록여부  전원 미개시라 값 없음
#   pension.퇴직급여포함여부   severance_amt > 0 과 동일
#   products.판매상태          판매중단여부로 판정(가상 상태 컬럼)
#   history 블록              seed_sessions 가 세션 저장소로 수록(중복 노출 방지)
# customers.json 없으면 빈 로스터, get_profile() 은 None

_BOND_GRADES = ("매우낮은위험", "낮은위험")  # 수익증권 중 채권형으로 접는 위험등급


def _days_since(iso: str | None) -> int | None:
    """그 날짜부터 오늘까지 경과 일수(AS_OF 기준 아님)."""
    return (today() - date.fromisoformat(iso)).days if iso else None


def tenure_text(iso: str | None) -> str | None:
    """오늘까지 경과 기간 표기(「3년」·「2년 10개월」·「7개월」) — 날짜에서 직접 계산(invest_period_years 사용 금지)."""
    if not iso:
        return None
    start, now = date.fromisoformat(iso), today()
    if now < start:
        return None
    months = (now.year - start.year) * 12 + (now.month - start.month)
    if now.day < start.day:
        months -= 1
    months = max(months, 0)
    y, m = divmod(months, 12)
    if not y:
        return f"{m}개월"
    return f"{y}년" if not m else f"{y}년 {m}개월"


def _port(rec: dict) -> tuple[list[int], int]:
    """원본 5분류 → port 4분류(%, 합 100 보정)와 cash_idle_pct."""
    sm, bal = rec["summary"], rec["summary"]["전체평가금액"]
    bond = sum(p["평가금액"] for p in rec["products"]
               if p["상품유형"] == "수익증권"
               and p["퇴직연금상품위험등급구분코드"] in _BOND_GRADES)
    amounts = [
        sm["예금평가금액"] + sm["GIC평가금액"] + sm["고유계정대평가금액"] + sm["기타평가금액"],
        bond,
        sm["수익증권평가금액"] - bond,
        sm["ETF평가금액"],
    ]
    port = [round(a * 100 / bal) for a in amounts]
    port[port.index(max(port))] += 100 - sum(port)  # 반올림 오차는 최대 슬롯이 흡수
    return port, round(sm["고유계정대평가금액"] * 100 / bal)


#: 원장 요약 자산군(0 인 것 제외)
_ASSET_TYPES = ("고유계정대", "예금", "GIC", "수익증권", "ETF", "기타")


def _assets(rec: dict) -> list[dict]:
    """자산군별 평가금액·비중(소수 한 자리, 정수 반올림 금지)."""
    sm = rec["summary"]
    out = []
    for t in _ASSET_TYPES:
        amt = sm.get(f"{t}평가금액") or 0
        if amt:
            out.append({"type": t, "amount": amt, "pct": round((sm.get(f"{t}비중") or 0) * 100, 1)})
    return sorted(out, key=lambda r: -r["amount"])


def _holdings(rec: dict) -> list[dict]:
    """보유상품 개별 종목(평가금액 내림차순)."""
    rows = [{"name": h["상품명"], "type": h["상품유형"], "amount": h["평가금액"],
             "principal": h["매입원금잔액"], "ret_1y": h.get("최근1년수익률"),
             "ret_own": h.get("고객보유수익률"), "pct": round((h.get("PF비중") or 0) * 100, 1),
             "grade": h.get("퇴직연금상품위험등급구분코드"), "rate": h.get("적용금리"),
             "discontinued": h.get("판매중단여부") == "Y",
             "opened": h.get("약정신규년월일"), "matures": h.get("약정만기년월일")}
            for h in rec["products"]]
    return sorted(rows, key=lambda r: -r["amount"])


def _peer(rec: dict) -> dict | None:
    """동연령대 비교(외부 조인값)."""
    q = rec.get("peer") or {}
    if not q:
        return None
    def _pct(key: str) -> float | None:
        v = q.get(key)
        return round(v * 100, 1) if v is not None else None
    funds = [q.get(f"상위1%펀드TOP{i}") for i in (1, 2, 3)]
    etfs = [q.get(f"상위1%ETF_TOP{i}") for i in (1, 2, 3)]
    return {"avg_ret": _pct("동연령대평균수익률"),
            "top1_ret": _pct("동연령대상위1%평균수익률"),
            "top1_guaranteed_pct": _pct("동연령대상위1%평균원리금보장비중"),
            "top1_funds": [f for f in funds if f], "top1_etfs": [e for e in etfs if e]}


def _activity(rec: dict) -> dict:
    """거래 활동(원장 그대로)."""
    a = rec.get("activity") or {}
    return {"last_trade": a.get("최근상품매매일"), "last_order": a.get("최근운용지시일"),
            "last_deposit": a.get("최근입금일"), "trades_1y": a.get("최근1년상품매매횟수"),
            "holds_etf": a.get("ETF현재보유여부") == "Y",
            "traded_etf": a.get("ETF과거매매이력여부") == "Y",
            "holds_fund": a.get("수익증권현재보유여부") == "Y",
            "traded_fund": a.get("수익증권과거매매이력여부") == "Y",
            "cash_delta_1m": a.get("최근1개월고유계정대증감액"),
            "autopay": a.get("입금예정상품등록여부") == "Y"}


def _isa(rec: dict) -> dict | None:
    """ISA 만기자금. 만기금액 없으면 None."""
    t = rec["tax_isa"]
    amt = t.get("ISA만기금액") or 0
    if not amt:
        return None
    when = t.get("ISA만기일")
    return {"amount": amt, "date": when,
            "dd": (date.fromisoformat(when) - today()).days if when else None,
            "org": t.get("ISA기관구분"), "within_1m": t.get("ISA만기1개월이내보유여부") == "Y"}


def _paid_by_year(rec: dict) -> dict[str, int]:
    """연도별 IRP 납입액(컬럼명에서 연도 추출)."""
    return {k.replace("IRP납입액", ""): v
            for k, v in rec["tax_isa"].items()
            if k.endswith("IRP납입액") and not k.startswith("당해") and v}


def next_maturity(mats: list[dict]) -> dict | None:
    """다가올 만기 중 최근접 1건, 전부 지났으면 가장 최근 지난 것(맨 앞 선택 금지 — 다음 만기 가려짐)."""
    if not mats:
        return None
    upcoming = [m for m in mats if m["dd"] >= 0]
    return upcoming[0] if upcoming else mats[-1]


def _maturities(rec: dict) -> list[dict]:
    """만기 있는 보유상품 전체(예금·GIC 등, 만기일 오름차순)."""
    rows = [{"date": p["약정만기년월일"],
             "dd": (date.fromisoformat(p["약정만기년월일"]) - today()).days,
             "type": p["상품유형"], "name": p["상품명"], "amount": p["평가금액"]}
            for p in rec["products"] if p.get("약정만기년월일")]
    return sorted(rows, key=lambda r: (r["date"], r["name"]))


def _paid_ytd_total(tax_isa: dict) -> int:
    """당해 실납입액 합계(IRP + 연금저축). 컬럼 누락 시 인정액으로 폴백(여력 과대 방지)."""
    irp, sav = tax_isa.get("당해년도IRP납입액"), tax_isa.get("당해년도연금저축납입액")
    if irp is None or sav is None:
        return tax_isa["당해년도세액공제인정납입액"]
    return irp + sav


#: 총급여 구간 경계(원) — 경계값은 «이하» 구간(`<` 금지)
INCOME_BRACKET_EDGE_WON = 55_000_000


def _income_bracket(tax_isa: dict) -> str | None:
    """총급여(원) → 공제율 구간. 컬럼 없으면 None."""
    salary = tax_isa.get("총급여")
    if salary is None:
        return None
    return "5500이하" if salary <= INCOME_BRACKET_EDGE_WON else "5500초과"


def _to_profile(rec: dict) -> Profile:
    basic, sm, act = rec["basic"], rec["summary"], rec["activity"]
    port, cash_pct = _port(rec)
    mats = _maturities(rec)
    assets = _assets(rec)
    isa = _isa(rec)
    # 최근접 만기 1건(같은 날짜 여러 건은 합산)
    focus = next_maturity(mats)
    nearest = focus["date"] if focus else None
    mat_dd = focus["dd"] if focus else None
    mat_amt = sum(m["amount"] for m in mats if m["date"] == nearest)
    rk = basic["투자성향"]
    return Profile(
        id=rec["id"], nm=basic["고객명"], ag=basic["만나이"],
        bal=sm["전체평가금액"], rk=rk,
        grade=PREF[rk],  # 파생값
        port=port,
        ret=round(sm["최근1년IRP수익률"] * 100, 1),
        retPct=None, balPct=None,
        dopt="설정" if sm["디폴트옵션등록여부"] == "Y" else "미설정",
        room=rec["tax_isa"]["세액공제잔여한도"] // 10_000,
        income_bracket=_income_bracket(rec["tax_isa"]),
        dorm=_days_since(basic.get("최근상담일")),
        nchM=round((_days_since(act["최근운용지시일"]) or 0) / 30.44, 1),
        matDD=mat_dd, matDate=nearest, matAmt=mat_amt, maturities=mats, assets=assets, isa=isa, paid_by_year=_paid_by_year(rec),
        holdings=_holdings(rec),
        peer=_peer(rec), activity=_activity(rec),
        cash_idle_pct=cash_pct,
        pension_paid_ytd=rec["tax_isa"]["당해년도세액공제인정납입액"],
        # 인정액 아닌 실납입액(납입한도 여력 입력)
        paid_ytd_total=_paid_ytd_total(rec["tax_isa"]),
        pension_savings={"Y": True, "N": False}.get(rec["tax_isa"].get("연금저축보유여부")),
        invest_period_years=round((_days_since(rec["pension"]["IRP가입일"]) or 0) / 365.25, 1),
        joined=rec["pension"].get("IRP가입일"),
        severance_amt=rec["pension"].get("퇴직급여금액") or 0,
        own_contrib_amt=rec["pension"].get("개인부담금금액") or 0,
        pension_started=rec["pension"]["연금개시여부"] == "Y",
        pension_eligible=rec["pension"]["연금개시요건충족여부"] == "Y",
        club_grade=basic["KB스타클럽등급"],
    )


def _load_personas() -> list[Profile]:
    from pension_agent.common import config  # noqa: PLC0415 — 경로는 config 소유

    if not config.CUSTOMERS_JSON.is_file():
        return []
    doc = json.loads(config.CUSTOMERS_JSON.read_text(encoding="utf-8"))
    as_of = doc.get("meta", {}).get("as_of")
    if as_of != AS_OF.isoformat():
        # 기준일 불일치는 중단(오늘과의 차이는 정상)
        raise ValueError(
            f"customers.json 기준일({as_of})이 customer.AS_OF({AS_OF})와 다릅니다 — "
            "원본 xlsx 교체 시 AS_OF 를 함께 맞추세요.")
    return [_to_profile(r) for r in doc["records"]]


PERSONAS: list[Profile] = _load_personas()

_BY_ID = {p.id: p for p in PERSONAS}

#: KB-PIN 하이픈 없는 13자리. 원장 표기(6-7자리)는 주민번호 꼴이라 개인정보 필터에 차단됨
_ID_COMPACT = re.compile(r"^\d{13}$")
#: 프론트 전송 꼴 접두 — `b64:` + 원장 표기 base64(접두 없는 값은 디코딩 안 함)
ID_B64_PREFIX = "b64:"


def normalize_id(value: str | None) -> str | None:
    """요청 고객 id(원장 표기 · 13자리 · `b64:`) → 원장 표기. 그 밖의 꼴은 그대로, 빈 값은 None."""
    text = (value or "").strip()
    if not text:
        return None
    if text.startswith(ID_B64_PREFIX):
        try:
            decoded = base64.b64decode(text[len(ID_B64_PREFIX):], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return text          # 디코딩 실패 → 그대로(없는 고객)
        return normalize_id(decoded)
    if _ID_COMPACT.match(text):
        return f"{text[:6]}-{text[6:]}"
    return text


def encode_id(customer_id: str) -> str:
    """원장 표기 → `b64:…`(normalize_id 의 역, 프론트 전송 기준)."""
    return ID_B64_PREFIX + base64.b64encode(customer_id.encode("utf-8")).decode("ascii")


def get_profile(customer_id: str) -> Profile | None:
    """고객 id → Profile(없으면 None). 실 저장소 교체 시 이 함수만 수정."""
    return _BY_ID.get(customer_id)
