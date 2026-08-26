"""고객 프로파일 정의 및 타겟 선정 요건 판정.

요건 판정 규칙은 이 파일이 소유한다. 화면·섹션 요건은 docs/REQUIREMENTS.md 를 따른다.

판정 기준 중 근거를 명시해둘 두 건:

    · `mat`(만기예금 보유) — 만기 잔여일수가 남아 있다는 것만으로는 대상이 되지 않는다.
      본부 가이드가 "만기 1개월 전 반드시 만기 안내"를 필수 행동으로 규정하므로 D-30 이내
      (`MAT_WINDOW_DAYS`)로 제한한다(06_주제별_추출지식/01_고객세그먼트 9 · 02_IRP관리방법론 18,
      07_에이전트_기능정의/01 ④ "기한 임박"). 그보다 먼 만기는 지금 접촉해도 고객이 행동할 시점이 아니다.
    · `lim`(위험자산 한도 초과) — DC·개인형IRP 의 위험자산 투자한도 70%(`RISK_ASSET_CAP_PCT`)는
      투자성향과 무관한 규정 사항이라, 성향 적합성과 별개로 판정한다.

`matAmt`(만기 도래 예금액)·`income_bracket`(소득 구간)·`pension_paid_ytd`(연금계좌 기납입액)는
각각 재예치 기대효과·세액공제율·세액공제 대상액 산출의 근거다. 이 필드가 없으면 해당 수치를
근거 없이 가정하게 되므로, 조인되지 않으면 그 산출을 생략한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# 데모 고정 기준일. date.today() 를 쓰지 않는 이유는 산출 재현성이다 — 만기 잔여일수(matDD)·
# 연말까지 남은 일수·이벤트/세미나 임박 순서가 모두 이 값에 상대적이라, 오늘 날짜를 쓰면 같은
# 페르소나가 실행일마다 다른 브리핑을 낸다(test_engine.py 의 단언도 이 값을 전제한다).
# data/assets.json 의 이벤트·세미나 날짜도 이 기준일에 맞춰 배치돼 있다. 실제 배포 시
# date.today() 로 바꾸고 assets.json 의 날짜도 함께 실데이터로 교체한다.
TODAY = date(2026, 8, 11)

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

# 총급여 구간별 연금계좌 세액공제율(지방소득세 포함). 출처: fact.k04.f2 (kb_facts)
TAX_CREDIT_RATE = {"5500이하": 0.165, "5500초과": 0.132}

# 연금계좌 세액공제 대상 납입액 한도(원). 같은 값이 data/strategies.json 의 `credit_limit` 에도
# 있고, test_engine.py 가 둘이 어긋나지 않는지 확인한다 — 세법 개정 시 한쪽만 고치면 화면과
# 대화형 답변이 서로 다른 금액을 말하게 된다.
TAX_CREDIT_CAP_WON = 9_000_000

# 만기예금 요건의 인정 범위(잔여일수) — "만기 1개월 전 만기 안내"(세그먼트 9·방법론 18). churn() 의
# 만기 가산 구간(≤30/≤90)은 이탈 점수용이라 별개다.
MAT_WINDOW_DAYS = 30


@dataclass
class Profile:
    """타겟리스트·MyStar·CRM 을 조인한 고객 단위 레코드. 본 시스템의 단일 입력이다."""

    id: str
    nm: str
    ag: int
    bal: int  # 평가금액(원). REQUIREMENTS.md §3.2 — 화면·프롬프트 표기는 '평가금액'으로 통일한다.
    rk: str  # 투자성향
    grade: str  # 고객 위험등급. 가입 가능한 상품 위험등급의 하드 상한이다.
    port: list[int]  # 자산 구성비(%) [예금, 채권형, TDF/MP, 섹터ETF]
    ret: float  # 1년 수익률(%)
    retPct: int | None  # 동일 고객군 내 수익률 백분위. 값이 낮을수록 하위 구간이다.
    # None 은 "아직 산출되지 않음". 피어그룹 수익률 조인이 늦게 붙는 값이라 실데이터 전환
    # 초기에 비어 올 수 있고, 그때 요건이 성립하지 않을 뿐 브리핑 전체가 죽으면 안 된다.
    dopt: str  # 디폴트옵션 설정 여부 ("설정" / "미설정")
    room: int  # 추가납입 여력(만원)
    dorm: int | None  # 최종 접촉 이후 경과 일수. None 은 "접촉 이력 소스 없음"(CRM 조인 전).
    nchM: float  # 최종 운용변경 이후 경과 개월수
    matDD: int | None = None  # 만기 잔여일수
    matAmt: int = 0  # 만기 도래 예금액(원)
    nonface: bool = False  # 비대면 거래 채널 고객 여부
    income_bracket: str | None = None  # 총급여 구간 ("5500이하" / "5500초과"). 세액공제율 판정에 사용한다.
    customer_type: str | None = None  # "직장인" / "사업자" / "공통". strategy.pitch_refs 에서 화법을
    # 고를 때만 쓴다 — 없으면 "공통" 화법으로 폴백(pitch_talk() 참고). 출처 미정: 타겟리스트·
    # MyStar·CRM 중 실제 직업 구분 값이 있는지 확인이 이 필드 사용의 전제 조건이다.
    pension_paid_ytd: int = 0  # 당해 연금계좌 기납입액(원). 세액공제 잔여 한도 산출에 사용한다.
    balPct: int | None = None  # 평가금액(적립금) 백분위 — 값이 낮을수록 상위 구간이다(예:
    # 9 → "상위 9%"). retPct(낮을수록 하위)와 표기 방향이 반대이니 주의 — "평가금액 상위
    # N%"라는 화면 표현과 필드값이 그대로 일치하도록 관례를 맞췄다. retPct 와 같은 성격의
    # 상류 배치 조인 필드다 — 모수(유사고객 모집단)가 이 저장소 안에 없어 엔진이 직접 계산할
    # 수 없다. 없으면 "왜 이 고객님인가요?" 근거에서 해당 줄만 생략한다(engine._why_this_customer 참고).
    cash_idle_pct: int | None = None  # 고유계정대(미투자 현금) 비중(%). port[0](예금) 중 운용
    # 지시가 없어 대기 중인 부분만 가리키며, port[0] 의 부분집합이다(port[0] 을 대체하지 않음
    # — 위험자산 한도 등 기존 게이팅 로직은 계속 port 4분류만 본다). 없으면 3분류 운용현황
    # 표시를 생략한다(engine._three_way_breakdown 참고).
    invest_period_years: float | None = None  # 투자기간(가입 후 경과연수). 상품추천 LLM 입력.
    pension_started: bool = False  # 연금수령 개시 여부. 참이면 추가납 요건(add·tax)이 성립하지 않는다
    # (conditions() — 07_에이전트_기능정의/01 ① "연금개시 계좌 → 추가납 권유 금지", 방법론 59 "연금개시 →
    # 추가입금 불가"; REQUIREMENTS.md §7). 상품추천 LLM 입력(§9)에도 쓴다.
    club_grade: str | None = None  # 스타클럽 등급(예: "VIP"). CRM 조인 값 — 화면 상단에 값이 있을 때만
    # 붙인다(REQUIREMENTS.md §3.1, 07_에이전트_기능정의/01 ① 양식 "만 57세 · VIP"). 없으면 생략한다.

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

# 요건 '표기' 순서 전용이다 — 전략 선정 순서는 engine._score() 의 산출 결과를 따르므로,
# 이 배열을 바꿔도 어떤 전략이 뽑히는지는 달라지지 않는다.
PRIO = ["chn", "dor", "lim", "mis", "sec", "dep", "nod", "low", "mat", "tax", "add", "nch"]


def churn(p: Profile) -> float:
    """이탈 위험 점수. 미접촉·만기임박·성과저조·방치·미설정을 가산한다."""
    # 값이 없는 항목은 가산하지 않는다 — 모르는 것을 위험으로도 안전으로도 치지 않는다.
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
    """성립하는 타겟 선정 요건을 PRIO 순으로 반환한다."""
    a: set[str] = set()
    if p.port[0] >= 60:
        a.add("dep")
    if p.dopt == "미설정":
        a.add("nod")
    # retPct·dorm 은 None 으로 올 수 있다(§4 소스 미확정 필드). 값이 없으면 요건을 세우지
    # 않는다 — matDD 가 이미 같은 방식이다. 여기서 None 을 비교하면 TypeError 로 브리핑 전체가
    # 죽는다(실제로 그랬다).
    if p.retPct is not None and p.retPct <= 30:
        a.add("low")
    if p.dorm is not None and p.dorm >= 180:
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
    # 연금수령 개시 계좌에는 추가납 요건을 세우지 않는다 — 연금지급설계가 등록되면 추가입금 자체가
    # 불가하고(방법론 59), 권유는 "하지 말 것"이다(07_에이전트_기능정의/01 ①). REQUIREMENTS.md §7.
    if not p.pension_started:
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
# C1~C3 은 요건이 여러 건 겹치는 정밀 시나리오로, 화면 데모의 기준 케이스다.
# C4 는 적합성 게이트·요건 충돌 검증을 위해 추가한 케이스이다.
# C5~C6 은 '행내 전략 로직에 걸리지 않는' 경우의 산출을 확인하는 케이스이다. 매칭되는
# 플레이북 전략이 없으면 engine.prepare() 의 items 가 비고, agent.propose() 는 LLM 단계를
# 건너뛴 채 '제안 항목 없음' 을 반환한다(LLM 자유 답변 경로 Tier2 는 미구현).
# income_bracket·pension_paid_ytd·balPct·cash_idle_pct·invest_period_years·club_grade 는 타겟리스트·
# MyStar·CRM 에서 조인되는 값이며, 아래 값은 예시이다. C1~C5 에는 balPct·cash_idle_pct 를 채워
# AI브리핑 9개 섹션이 실제로 채워지는 모습을 볼 수 있게 하고, C6 만 비워둬 "필드가 없으면
# 해당 화면 요소를 생략한다"는 그레이스풀 폴백 경로를 데모에서도 확인할 수 있게 한다.
# ─────────────────────────────────────────────────────────────

PERSONAS = [
    Profile(
        id="C1", nm="김민수", ag=48, bal=180_000_000, rk="안정형", grade="보통위험",
        port=[82, 16, 2, 0], ret=1.9, retPct=35, dopt="미설정", room=580,
        dorm=10, nchM=0.3, matDD=26, matAmt=50_000_000,
        income_bracket="5500초과", pension_paid_ytd=3_200_000,
        balPct=9, cash_idle_pct=25, invest_period_years=6.4,
    ),
    Profile(
        id="C2", nm="박지영", ag=39, bal=47_000_000, rk="적극투자형", grade="높은위험",
        port=[15, 10, 14, 61], ret=-12.3, retPct=5, dopt="설정", room=0,
        dorm=84, nchM=1.6,
        income_bracket="5500이하",
        balPct=68, cash_idle_pct=8, invest_period_years=3.2,
    ),
    Profile(
        id="C3", nm="이현우", ag=45, bal=230_000_000, rk="안정형", grade="매우낮은위험",
        port=[100, 0, 0, 0], ret=1.8, retPct=17, dopt="미설정", room=0,
        dorm=419, nchM=13.8, matDD=22, matAmt=230_000_000,
        income_bracket="5500초과",
        balPct=3, cash_idle_pct=40, invest_period_years=11.2,
        club_grade="VIP",  # 스타클럽 등급 예시(CRM 조인 값) — 상단 표기 데모용. 다른 페르소나는 비워 생략 경로 확인
    ),
    Profile(
        id="C4", nm="정수연", ag=52, bal=90_000_000, rk="안정추구형", grade="낮은위험",
        port=[10, 8, 30, 52], ret=-3.2, retPct=12, dopt="미설정", room=400,
        dorm=30, nchM=2.0, nonface=True,
        income_bracket=None, pension_paid_ytd=0,  # 소득 구간 미확인 케이스
        balPct=41, cash_idle_pct=6, invest_period_years=4.0,
    ),
    Profile(
        # 무이슈 모범 관리 고객 — 성립 요건 0건. 어떤 전략도 소집되지 않아 items 가 빈다.
        id="C5", nm="한서진", ag=44, bal=120_000_000, rk="위험중립형", grade="다소높은위험",
        port=[40, 20, 30, 10], ret=6.5, retPct=55, dopt="설정", room=0,
        dorm=30, nchM=1.0, income_bracket="5500초과",
        balPct=22, cash_idle_pct=14, invest_period_years=8.5,
    ),
    Profile(
        # 추가납입 여력만 있는 고객 — 요건은 'add' 하나뿐. add 는 원천 근거가 없어 플레이북
        # 전략이 없으므로(LLM 재량) items 가 비고, 납입여력만 briefing 에 노출된다.
        # balPct·cash_idle_pct 를 의도적으로 비워둔 케이스 — 상류 조인 값이 없을 때 ②·③
        # 섹션이 해당 줄만 조용히 생략하는지 확인하는 용도.
        id="C6", nm="오지호", ag=41, bal=95_000_000, rk="위험중립형", grade="다소높은위험",
        port=[35, 25, 30, 10], ret=5.8, retPct=60, dopt="설정", room=200,
        dorm=20, nchM=2.0, income_bracket="5500초과",
    ),
]

_BY_ID = {p.id: p for p in PERSONAS}


def get_profile(customer_id: str) -> Profile | None:
    """고객 id 로 Profile 을 조회한다. 지금은 PERSONAS(테스트 픽스처) 조회의 얇은 래퍼일
    뿐이지만, 대화형 에이전트(consult_agent 의 customer 도구)가 이 함수 하나만 의존하도록 해
    나중에 실제 고객 프로파일 저장소로 교체할 때 이 함수 본문만 바꾸면 되게 한다."""
    return _BY_ID.get(customer_id)
