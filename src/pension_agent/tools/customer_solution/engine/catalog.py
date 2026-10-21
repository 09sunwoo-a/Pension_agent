"""엔진 데이터·상수 — `knowledge/` 적재 레코드와 지식베이스 근거 임계값.

다른 engine 모듈 임포트 금지(잎 모듈).
"""

from __future__ import annotations

from typing import Any

from pension_agent.knowledge import shared_store

_STORE = shared_store()

PRODUCTS: list[dict] = _STORE.fields_of("product")
SPECS: list[dict] = _STORE.fields_of("strategy")
CAPS: dict[str, dict] = {r["id"]: r for r in _STORE.fields_of("capability")}
ASSETS: list[dict] = _STORE.fields_of("asset")
BASELINES: dict[str, dict] = {r["id"]: r for r in _STORE.fields_of("baseline")}
TOP_HOLDINGS: list[dict] = _STORE.fields_of("top_holding")
PORTFOLIOS: list[dict] = _STORE.fields_of("portfolio")

# 시스템 조건부 전략(confidence=추정) — 요건이 아니라 게이트 결과로만 발동, 플레이북과 분리
#   · st.risk_reassess: 실적배당 상품 전량 차단 시 발동(_perf_branch_blocked)
SYSTEM_STRATEGIES: list[dict] = _STORE.fields_of("system_strategy")

# 플레이북 + 시스템 전략 통합 인덱스
BY_ID = {s["id"]: s for s in SPECS + SYSTEM_STRATEGIES}

MIN_ALLOC = 1_000_000  # 배분액 하한. 미만 전략 제외
# 오늘의 제안 개수 — 제안 1개(+예비 1개). 근거: 기능정의 ① «제안 5개 나열은 안 하느니만 못함»
TOP_N = 1

# 예비 제안 노출 최대 개수(수익률 개선폭 순)
ALT_N = 1

# 수익률 개선 효과 정성 등급 경계(%p, 하한 포함, 큰 하한부터). 원화 기대효과 수치 표기 금지
EFFECT_BANDS = ((1.0, "큼"), (0.5, "보통"), (0.0, "작음"))


def _load_kb_index() -> tuple[dict[str, str], dict[str, tuple[str, Any]]]:
    """출처 표기용 색인 — DOC_TITLES(doc_id·`doc.…` → 제목), CARD_INDEX(모든 종류 카드 id → (문서 키, page))."""
    titles = dict(_STORE.doc_titles())
    for r in _STORE.records("doc"):
        title = (r.get("fields") or {}).get("title")
        if r.get("id") and title:
            titles[r["id"]] = title
    index: dict[str, tuple[str, Any]] = {}
    for r in _STORE.records():
        rid = r.get("id")
        if not rid:
            continue
        # 우선순위: 레코드 source.doc → meta.source_doc → 적재 파일 doc_id
        doc = (r.get("source") or {}).get("doc") or r.get("_doc_ref") or r.get("_doc_id")
        index[rid] = (doc, (r.get("fields") or {}).get("page"))
    return titles, index


DOC_TITLES, CARD_INDEX = _load_kb_index()

# 퇴직연금 예금자보호 한도(원, 1인당, 일반 예금과 별도)
PROTECTION_LIMIT = 50_000_000

# 상품 부착 동작 명사. 절은 이것 또는 CLAUSE_ENDINGS 로 종결
VERBS = ("매수", "재예치", "예치", "전환", "납입", "재배분", "설정", "편입", "배분")

# 상품 슬롯 없는 절의 허용 어미
CLAUSE_ENDINGS = ("접촉", "발송", "조회", "확인", "안내", "권유", "재진단",
                  "등록", "재구성", "정리", "점검", "조정", "축소")

# actor='고객' 절 어미 — 직원 행동(제안·안내)으로 감쌈
ACTOR_SUFFIX = {"운용": "하도록 제안", "납입": "하도록 제안", "설정": "하도록 안내"}

# Profile.port 인덱스별 라벨. port[0] = 예금·GIC·고유계정대·기타 합(TG-202 원리금보장_합산비율)
# 라벨 앞머리 «예금» 금지 — 예금 비중으로 오독. render._three_way «원리금보장형»(고유계정대 제외)과 다른 값
PORT_LABELS = ("원리금보장·현금성", "채권형", "TDF·MP", "섹터ETF")

# 보유 현황 briefing 근거 카드 id('적립금·수익률 조회' 절차 — 코드가 대체)
BRIEFING_SOURCE = "proc.001"

# 보유 데이터 조회·확인 지시어 — 절 대신 briefing(코드)으로 표현
LOOKUP_MARKERS = ("MyStar", "단말", "조회 화면")

# 데이터 근거 없는 시한 표현 — 절에 금지. 시급성은 urgency·실행 순서로, 기한은 'D-{matDD}' 만
TIME_PRESSURE_MARKERS = ("금주", "이번 주", "당장", "오늘 중", "지금 바로", "즉시")
