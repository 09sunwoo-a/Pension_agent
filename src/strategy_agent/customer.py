"""고객 프로파일 정의 및 타겟 선정 요건 판정.

요건 판정 로직은 목업(`0. 기획/최종_퇴직연금_에이전트_v0.4.html`)의 구현을 기준으로 하되,
산출물 감사에서 확인된 두 건은 수정하였다.

    · `mat`(만기예금 보유) — 목업은 만기 잔여일수 존재만으로 요건을 성립시켜 D-300 도 대상이
      되었다. 재예치 상담이 실효를 갖는 D-90 이내로 제한한다.
    · `lim`(위험자산 한도 초과) — 신규 요건. DC·개인형IRP 의 위험자산 투자한도 70% 는 투자성향과
      무관한 규정 사항이나 목업에는 판정 자체가 없었다.

목업 대비 추가 필드는 만기 도래 예금액(`matAmt`), 소득 구간(`income_bracket`), 연금계좌 기납입액
(`pension_paid_ytd`) 세 개이다. 각각 재예치 기대효과, 세액공제율, 세액공제 대상액 산출에 필요하며,
필드가 없으면 해당 수치를 근거 없이 가정하게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

TODAY = date(2026, 8, 11)  # 목업 시연 기준일

# 위험등급. 오름차순으로 정의하며, 인덱스 비교로 상한 초과 여부를 판정한다.
RISK = ["매우낮은위험", "낮은위험", "보통위험", "다소높은위험", "높은위험", "매우높은위험"]

# 투자성향별 선호 위험등급. 가입 가능 상한이 아니라 정렬 선호 기준이다.
# 하드 상한은 고객 위험등급(Profile.grade)이 담당한다.
PREF = {
    "안정형": "매우낮은위험",
    "안정추구형": "낮은위험",
    "위험중립형": "보통위험",
    "적극투자형": "다소높은위험",
    "공격투자형": "높은위험",
}

# DC·개인형IRP 위험자산 총 투자한도(적립금 대비 %). 투자성향과 무관한 규정 사항이다.
RISK_ASSET_CAP_PCT = 70

# 총급여 구간별 연금계좌 세액공제율(지방소득세 포함). 출처: ch01_new fact.irp.tax_credit_rate
TAX_CREDIT_RATE = {"5500이하": 0.165, "5500초과": 0.132}

# 만기예금 요건의 인정 범위(잔여일수). 이 기간을 넘으면 재예치 상담의 실효가 없다.
MAT_WINDOW_DAYS = 90


@dataclass
class Profile:
    """타겟리스트·MyStar·CRM 을 조인한 고객 단위 레코드. 본 시스템의 단일 입력이다."""

    id: str
    nm: str
    ag: int
    bal: int  # 적립금(원)
    rk: str  # 투자성향
    grade: str  # 고객 위험등급. 가입 가능한 상품 위험등급의 하드 상한이다.
    port: list[int]  # 자산 구성비(%) [예금, 채권형, TDF/MP, 섹터ETF]
    ret: float  # 1년 수익률(%)
    retPct: int  # 동일 고객군 내 수익률 백분위. 값이 낮을수록 하위 구간이다.
    dopt: str  # 디폴트옵션 설정 여부 ("설정" / "미설정")
    room: int  # 추가납입 여력(만원)
    dorm: int  # 최종 접촉 이후 경과 일수
    nchM: float  # 최종 운용변경 이후 경과 개월수
    matDD: int | None = None  # 만기 잔여일수
    matAmt: int = 0  # 만기 도래 예금액(원)
    nonface: bool = False  # 비대면 거래 채널 고객 여부
    income_bracket: str | None = None  # 총급여 구간 ("5500이하" / "5500초과"). 세액공제율 판정에 사용한다.
    pension_paid_ytd: int = 0  # 당해 연금계좌 기납입액(원). 세액공제 잔여 한도 산출에 사용한다.

    @property
    def risk_asset(self) -> int:
        """위험자산 비중(%). TDF/MP 와 섹터 ETF 의 합계로 산출한다."""
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
        """위험자산 한도까지 남은 여력(원). 한도를 이미 초과한 경우 0 이다.

        위험자산을 증가시키는 제안의 배분 상한으로 사용한다. 이 값을 넘겨 제안하면
        제안 자체가 규정 위반이 된다.
        """
        return max(0, round(self.bal * (RISK_ASSET_CAP_PCT - self.risk_asset) / 100))

    @property
    def tax_credit_rate(self) -> float:
        """세액공제율. 소득 구간이 확인되지 않은 경우 낮은 쪽을 적용한다.

        구간 미확인 상태에서 16.5% 를 적용하면 기대효과가 최대 25% 과대 산출된다.
        과대 산출을 피하는 방향으로 기울인다.
        """
        return TAX_CREDIT_RATE.get(self.income_bracket or "", TAX_CREDIT_RATE["5500초과"])


# ─────────────────────────────────────────────────────────────
# 타겟 선정 요건
# ─────────────────────────────────────────────────────────────

CONDS = {
    "dep": "예금 비중 높음",
    "nod": "디폴트옵션 미설정",
    "low": "수익률 하위 30%",
    "dor": "장기 미접촉",
    "add": "추가입금 여력 보유",
    "nch": "운용변경 없음(6개월+)",
    "tax": "세액공제 활용 가능",
    "mat": "만기예금 보유",
    "chn": "이탈 위험 높음",
    "sec": "특정 섹터 집중",
    "mis": "투자성향 불일치",
    "lim": "위험자산 한도 초과",
}

# 목업의 고정 우선순위에 `lim` 을 추가한 것이다. 본 시스템에서는 요건 표기 순서에만 사용하며,
# 전략 선정 순서는 engine._score() 의 산출 결과를 따른다.
PRIO = ["chn", "dor", "lim", "mis", "sec", "dep", "nod", "low", "mat", "tax", "add", "nch"]


def churn(p: Profile) -> float:
    """이탈 위험 점수. 미접촉·만기임박·성과저조·방치·미설정을 가산한다."""
    v = min(8.0, p.dorm / 60)
    if p.matDD is not None and p.matDD <= 30:
        v += 3
    elif p.matDD is not None and p.matDD <= 90:
        v += 1.5
    if p.retPct <= 30:
        v += 2.5
    if p.nchM >= 6:
        v += 2
    if p.dopt == "미설정":
        v += 1
    return round(v, 1)


def conditions(p: Profile) -> list[str]:
    """성립하는 타겟 선정 요건을 PRIO 순으로 반환한다."""
    a: set[str] = set()
    if p.port[0] >= 60:
        a.add("dep")
    if p.dopt == "미설정":
        a.add("nod")
    if p.retPct <= 30:
        a.add("low")
    if p.dorm >= 180:
        a.add("dor")
    if p.nchM >= 6:
        a.add("nch")
    if p.matDD is not None and p.matDD <= MAT_WINDOW_DAYS:
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
    if p.room > 0:
        a.add("add")
    if p.room >= 300:
        a.add("tax")
    return [k for k in PRIO if k in a]


def days_to_year_end() -> int:
    """연말까지의 잔여일수. 세액공제 전략의 시급성 산출에 사용한다."""
    return (date(TODAY.year, 12, 31) - TODAY).days


# ─────────────────────────────────────────────────────────────
# 검증용 페르소나
#
# C1~C3 은 목업의 정밀 시나리오(FIX 배열)와 동일하다.
# C4 는 적합성 게이트·요건 충돌 검증을 위해 추가한 케이스이다.
# C5~C6 은 '행내 전략 로직에 걸리지 않는' 경우의 산출을 확인하는 케이스이다. 매칭되는
# 플레이북 전략이 없으면 engine.prepare() 의 items 가 비고, agent.propose() 는 LLM 단계를
# 건너뛴 채 '제안 항목 없음' 을 반환한다(LLM 자유 답변 경로 Tier2 는 미구현).
# income_bracket·pension_paid_ytd 는 타겟리스트에서 조인되는 값이며, 아래 값은 예시이다.
# ─────────────────────────────────────────────────────────────

PERSONAS = [
    Profile(
        id="C1", nm="김민수", ag=48, bal=180_000_000, rk="안정형", grade="보통위험",
        port=[82, 16, 2, 0], ret=1.9, retPct=35, dopt="미설정", room=580,
        dorm=10, nchM=0.3, matDD=36, matAmt=50_000_000,
        income_bracket="5500초과", pension_paid_ytd=3_200_000,
    ),
    Profile(
        id="C2", nm="박지영", ag=39, bal=47_000_000, rk="적극투자형", grade="높은위험",
        port=[15, 10, 14, 61], ret=-12.3, retPct=5, dopt="설정", room=0,
        dorm=84, nchM=1.6,
        income_bracket="5500이하",
    ),
    Profile(
        id="C3", nm="이현우", ag=45, bal=230_000_000, rk="안정형", grade="매우낮은위험",
        port=[100, 0, 0, 0], ret=1.8, retPct=17, dopt="미설정", room=0,
        dorm=419, nchM=13.8, matDD=22, matAmt=230_000_000,
        income_bracket="5500초과",
    ),
    Profile(
        id="C4", nm="정수연", ag=52, bal=90_000_000, rk="안정추구형", grade="낮은위험",
        port=[10, 8, 30, 52], ret=-3.2, retPct=12, dopt="미설정", room=400,
        dorm=30, nchM=2.0, nonface=True,
        income_bracket=None, pension_paid_ytd=0,  # 소득 구간 미확인 케이스
    ),
    Profile(
        # 무이슈 모범 관리 고객 — 성립 요건 0건. 어떤 전략도 소집되지 않아 items 가 빈다.
        id="C5", nm="한서진", ag=44, bal=120_000_000, rk="위험중립형", grade="다소높은위험",
        port=[40, 20, 30, 10], ret=6.5, retPct=55, dopt="설정", room=0,
        dorm=30, nchM=1.0, income_bracket="5500초과",
    ),
    Profile(
        # 추가납입 여력만 있는 고객 — 요건은 'add' 하나뿐. add 는 원천 근거가 없어 플레이북
        # 전략이 없으므로(LLM 재량) items 가 비고, 납입여력만 briefing 에 노출된다.
        id="C6", nm="오지호", ag=41, bal=95_000_000, rk="위험중립형", grade="다소높은위험",
        port=[35, 25, 30, 10], ret=5.8, retPct=60, dopt="설정", room=200,
        dorm=20, nchM=2.0, income_bracket="5500초과",
    ),
]
