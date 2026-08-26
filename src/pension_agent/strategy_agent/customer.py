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

import json
from dataclasses import dataclass, field
from datetime import date

# 데모 고정 기준일. date.today() 를 쓰지 않는 이유는 산출 재현성이다 — 만기 잔여일수(matDD)·
# 연말까지 남은 일수·이벤트/세미나 임박 순서가 모두 이 값에 상대적이라, 오늘 날짜를 쓰면 같은
# 페르소나가 실행일마다 다른 브리핑을 낸다(test_engine.py 의 단언도 이 값을 전제한다).
# 값은 시연 데이터 원장(customers.json meta.as_of ← 원본 xlsx 09_데이터사전 "기준일")과
# 일치해야 한다 — 아래 로더가 불일치를 검출한다. data/assets.json 의 이벤트·세미나 날짜도
# 이 기준일에 유효하도록 배치돼 있다. 실제 배포 시 date.today() 로 바꾸고 assets.json 의
# 날짜도 함께 실데이터로 교체한다.
TODAY = date(2026, 8, 24)

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
    # ── 만기 ─────────────────────────────────────────────────
    # 아래 셋은 **가장 가까운 만기 한 건**이다(요건 판정·재예치 전략의 입력). `mat` 요건은
    # "만기 1개월 전 안내"라 가장 가까운 것만 보면 되고, st.mat_reprice 의 배분액도 그 건이다.
    #
    # 하지만 **고객이 들고 있는 만기가 그 하나라는 뜻은 아니다.** 예금과 GIC 를 함께 들고
    # 만기가 서로 다른 고객이 있다(목업 9케이스 중 3명). "만기 언제야?" 에 가장 가까운 것
    # 하나만 답하면 나머지는 없는 것이 되므로, 전체 목록은 `maturities` 가 따로 들고 있다.
    matDD: int | None = None  # 가장 가까운 만기까지의 잔여일수
    matDate: str | None = None  # 가장 가까운 만기일(ISO). 잔여일수만으로는 "언제야?"에 날짜로
    # 답할 수 없다 — 재료에 없으면 LLM 이 TODAY 에서 계산해 말하게 되고, 그 계산은 근거가 아니다.
    matAmt: int = 0  # 그 만기일에 도래하는 금액(원)
    assets: list[dict] = field(default_factory=list)  # 자산군별 보유 — 원장 그대로.
    # [{"type": 자산군, "amount": 평가금액(원), "pct": 비중(%)}]. `port` 4분류는 전략 로직이
    # 쓰는 **요약**이라 정수 비중이고 여러 자산군을 한 칸에 접는다(port[0] 은 예금·GIC·
    # 고유계정대·기타의 합). 그 요약만으로는 "고유계정대 얼마야?" 에 답할 수 없고 — 금액이
    # 아예 없고, 비중도 반올림된 값이라 원장의 7.7% 가 8% 로 나간다. 원장 값은 여기 둔다.
    maturities: list[dict] = field(default_factory=list)  # 만기 보유 전체.
    # [{"date": ISO, "dd": 잔여일수, "type": 상품유형, "name": 상품명, "amount": 평가금액}] 을
    # 만기일 오름차순으로. 대화형이 "만기 뭐뭐 있어?"에 빠짐없이 답하기 위한 재료다.
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
# 고객 로스터 — 시연용 더미 9케이스 (customers.json ← IRP_Agent_더미고객_9Cases_v3.xlsx)
#
# 원장은 `config.CUSTOMERS_JSON` 이다(scripts/import_customers.py 가 원본 xlsx 에서 생성 —
# 손으로 고치지 않는다). 여기서는 그 레코드를 Profile 로 **매핑만** 한다. 매핑 규약:
#
#   · port 4분류 [원리금보장(예금+GIC)+현금, 채권형, 실적배당 수익증권, ETF] — 원본의
#     5분류(고유계정대/예금/GIC/수익증권/ETF)에서 접는다. 채권형은 수익증권 중 위험등급
#     매우낮은위험·낮은위험인 상품의 평가금액 합. cash_idle_pct(고유계정대)는 port[0] 의
#     부분집합이라는 기존 규약 그대로다.
#   · grade(고객 위험등급)는 원본에 없다 → PREF[투자성향] 으로 **보수적으로 파생**한다.
#     실데이터 전환 시 실제 고객 위험등급 컬럼으로 교체한다(docs/DEMO_STATUS.md §4).
#   · retPct·balPct·income_bracket·customer_type 은 원본에 모수·컬럼이 없다 → None 으로
#     둔다(각각 그레이스풀 생략·보수적 공제율 경로가 이미 있다).
#   · matDD/matAmt 는 만기일 있는 보유상품(예금·GIC) 중 최근접 만기의 잔여일수·평가금액 합.
#
# 원본의 나머지 재료(배지 4종·판매중단·ISA·동연령 비교·상담이력)는 Profile 로 접지 않고
# customers.json 에 그대로 남아 있다 — 요건화는 지식베이스 근거 확인이 선행돼야 한다
# (루트 CLAUDE.md "지식베이스에 없는 기준은 만들지 않는다").
#
# customers.json 이 없으면 로스터는 빈 리스트다 — 브리핑 CLI·Streamlit 화면·문제상황
# 덤프는 "등록된 고객 없음" 으로 빠지고 `get_profile()` 은 None 을 반환한다.
# ─────────────────────────────────────────────────────────────

_BOND_GRADES = ("매우낮은위험", "낮은위험")  # 수익증권 중 채권형으로 접는 위험등급


def _days_since(iso: str | None) -> int | None:
    return (TODAY - date.fromisoformat(iso)).days if iso else None


def _port(rec: dict) -> tuple[list[int], int]:
    """원본 5분류 → port 4분류(%)와 cash_idle_pct. 정수 반올림 후 합 100 을 보정한다."""
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
    port[port.index(max(port))] += 100 - sum(port)  # 반올림 오차는 최대 슬롯에서 흡수
    return port, round(sm["고유계정대평가금액"] * 100 / bal)


#: 원장 요약(02_IRP_SUMMARY)의 자산군. 값이 0 인 자산군은 싣지 않는다.
_ASSET_TYPES = ("고유계정대", "예금", "GIC", "수익증권", "ETF", "기타")


def _assets(rec: dict) -> list[dict]:
    """자산군별 평가금액·비중을 원장 그대로. 비중은 원장의 소수값을 소수점 한 자리로만
    옮긴다 — 반올림해 정수로 만들면 7.7% 가 8% 로 나가 원장과 어긋난 값이 답변에 실린다."""
    sm = rec["summary"]
    out = []
    for t in _ASSET_TYPES:
        amt = sm.get(f"{t}평가금액") or 0
        if amt:
            out.append({"type": t, "amount": amt, "pct": round((sm.get(f"{t}비중") or 0) * 100, 1)})
    return sorted(out, key=lambda r: -r["amount"])


def _maturities(rec: dict) -> list[dict]:
    """만기일이 있는 보유상품 전체를 만기일 오름차순으로. 예금만이 아니다 — GIC 처럼
    만기가 있는 다른 상품도 함께 담는다(상품 유형을 같이 실어야 어느 만기인지 분간된다)."""
    rows = [{"date": p["약정만기년월일"],
             "dd": (date.fromisoformat(p["약정만기년월일"]) - TODAY).days,
             "type": p["상품유형"], "name": p["상품명"], "amount": p["평가금액"]}
            for p in rec["products"] if p.get("약정만기년월일")]
    return sorted(rows, key=lambda r: (r["date"], r["name"]))


def _to_profile(rec: dict) -> Profile:
    basic, sm, act = rec["basic"], rec["summary"], rec["activity"]
    port, cash_pct = _port(rec)
    mats = _maturities(rec)
    assets = _assets(rec)
    # 가장 가까운 만기 = 요건 판정·재예치 전략의 입력. 같은 날짜에 여러 건이면 합산한다.
    nearest = mats[0]["date"] if mats else None
    mat_dd = mats[0]["dd"] if mats else None
    mat_amt = sum(m["amount"] for m in mats if m["date"] == nearest)
    rk = basic["투자성향"]
    return Profile(
        id=rec["id"], nm=basic["고객명"], ag=basic["만나이"],
        bal=sm["전체평가금액"], rk=rk,
        grade=PREF[rk],  # 파생값 — 위 매핑 규약 참고
        port=port,
        ret=round(sm["최근1년IRP수익률"] * 100, 1),
        retPct=None, balPct=None,
        dopt="설정" if sm["디폴트옵션등록여부"] == "Y" else "미설정",
        room=rec["tax_isa"]["세액공제잔여한도"] // 10_000,
        dorm=_days_since(basic.get("최근상담일")),
        nchM=round((_days_since(act["최근운용지시일"]) or 0) / 30.44, 1),
        matDD=mat_dd, matDate=nearest, matAmt=mat_amt, maturities=mats, assets=assets,
        cash_idle_pct=cash_pct,
        pension_paid_ytd=rec["tax_isa"]["당해년도세액공제인정납입액"],
        invest_period_years=round((_days_since(rec["pension"]["IRP가입일"]) or 0) / 365.25, 1),
        pension_started=rec["pension"]["연금개시여부"] == "Y",
        club_grade=basic["KB스타클럽등급"],
    )


def _load_personas() -> list[Profile]:
    from pension_agent import config  # noqa: PLC0415 — 경로는 config 소유(구조 규칙)

    if not config.CUSTOMERS_JSON.is_file():
        return []
    doc = json.loads(config.CUSTOMERS_JSON.read_text(encoding="utf-8"))
    as_of = doc.get("meta", {}).get("as_of")
    if as_of != TODAY.isoformat():
        # 기준일이 어긋나면 만기 잔여일수·미접촉 일수가 전부 밀린다. 조용히 계속하지 않는다.
        raise ValueError(
            f"customers.json 기준일({as_of})이 customer.TODAY({TODAY})와 다릅니다 — "
            "원본 xlsx 교체 시 TODAY 를 함께 맞추세요.")
    return [_to_profile(r) for r in doc["records"]]


PERSONAS: list[Profile] = _load_personas()

_BY_ID = {p.id: p for p in PERSONAS}


def get_profile(customer_id: str) -> Profile | None:
    """고객 id 로 Profile 을 조회한다. 지금은 PERSONAS 조회의 얇은 래퍼일 뿐이지만, 대화형
    에이전트(consult_agent 의 customer 도구)가 이 함수 하나만 의존하도록 해 나중에 실제 고객
    프로파일 저장소로 교체할 때 이 함수 본문만 바꾸면 되게 한다.

    로스터가 비어 있는 지금은 어떤 id 로도 None 이 나온다 — 호출부는 이미 None 을 다룬다."""
    return _BY_ID.get(customer_id)
