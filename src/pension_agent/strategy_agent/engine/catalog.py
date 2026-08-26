"""데이터와 상수 — 엔진이 판단의 재료로 삼는 모든 것.

여기 있는 값의 출처는 두 가지뿐이다: `knowledge/` 가 적재한 레코드(상품·전략·기준선…)와,
지식베이스 문서에 근거해 사람이 정한 임계값. 어느 쪽도 코드가 만들어낸 숫자가 아니다.

이 모듈은 다른 engine 모듈을 임포트하지 않는다 — 잎이다.
"""

from __future__ import annotations

from typing import Any

from pension_agent.knowledge import shared_store


# 통합 스토어 — 자체 data(운영 데이터)와 consult_agent/data(사실·화법)를 함께 적재한다. 모든
# 데이터는 단일 레코드 형태이며, fields_of(kind) 가 기존 flat dict 뷰({id, **fields})를 그대로 준다.
_STORE = shared_store()

PRODUCTS: list[dict] = _STORE.fields_of("product")
SPECS: list[dict] = _STORE.fields_of("strategy")
CAPS: dict[str, dict] = {r["id"]: r for r in _STORE.fields_of("capability")}
ASSETS: list[dict] = _STORE.fields_of("asset")
BASELINES: dict[str, dict] = {r["id"]: r for r in _STORE.fields_of("baseline")}
TOP_HOLDINGS: list[dict] = _STORE.fields_of("top_holding")
PORTFOLIOS: list[dict] = _STORE.fields_of("portfolio")

# 시스템 생성 조건부 전략 — 요건(when)이 아니라 엔진 게이트 결과의 판정으로만 발동한다.
# strategies.json 플레이북은 원천 소스·규정에 근거한 전략만 담으므로, 근거가 아니라 게이트
# 결과에 대한 '추론'인 전략(confidence=추정)은 별도 자산 파일로 분리해 관리한다. 데이터는
# 코드가 아니라 data/ 의 JSON 에 둔다는 원칙에 따라 engine 에 리터럴로 박지 않는다.
#   · st.risk_reassess: 적합성 게이트가 실적배당 상품을 전량 차단했을 때만 발동(_perf_branch_blocked).
SYSTEM_STRATEGIES: list[dict] = _STORE.fields_of("system_strategy")

# 조회용 통합 인덱스. 플레이북(SPECS)과 시스템 전략(SYSTEM_STRATEGIES)을 모두 담는다.
BY_ID = {s["id"]: s for s in SPECS + SYSTEM_STRATEGIES}

MIN_ALLOC = 1_000_000  # 배분액 하한. 미만인 전략은 실행 실익이 없는 것으로 보아 제외한다.
# 오늘의 제안 개수 — "제안 1개 (+예비 1개)".
# 07_에이전트_기능정의/01 ① 필수 구성 요소 4 가 근거다: "우선순위가 정해져서 옴. 제안 5개
# 나열은 안 하느니만 못함." 51억 유치 사례에서도 니즈 3개 중 하나를 핵심으로 판단하고
# 나머지를 먼저 정리해준 것이 성공 요인이었다. 제안이 많으면 행원도 고객도 길을 잃는다.
TOP_N = 1

# 예비 제안(메인 문장 밖 전략)을 산출물에 노출하는 최대 개수. 수익률 개선폭 순으로 상위만.
ALT_N = 1

# 수익률 개선 효과의 정성 등급 경계(%p, 하한 포함). 원화 기대효과는 원금 이동 가정에 의존해
# 근거가 불명확하므로 산출물에는 수치 대신 이 등급으로만 표기한다. 큰 하한부터 판정한다.
EFFECT_BANDS = ((1.0, "큼"), (0.5, "보통"), (0.0, "작음"))


def _load_kb_index() -> tuple[dict[str, str], dict[str, tuple[str, Any]]]:
    """지식베이스를 색인한다. 출처를 코드용 doc_id 가 아니라 원본 문서명으로 표기하기 위함이다.

    · DOC_TITLES  문서 키 → 제목. 두 종류의 키를 함께 담는다 — 적재 파일의 `doc_id` 와
      원천 문서 레지스트리 id(`doc.…`). 후자는 카드가 `source.doc` 로 가리키는 값이다.
    · CARD_INDEX  카드 id → (문서 키, page). **종류를 가리지 않는다** — 화법·팩트뿐 아니라
      세그먼트·절차·방법론도 전략의 근거가 되기 때문이다. 예전에는 fact 만 색인해서, 그
      밖의 카드를 근거로 걸면 id 앞토막("pitch"·"seg")이 문서명 자리에 그대로 찍혔다.

    경로가 없으면 빈 맵을 반환해 doc_id 로 폴백한다(단독 실행 가능)."""
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
        # 레코드가 자기 원천 문서를 밝히면 그쪽이 이긴다(store.py 규약). 없으면 파일 단위
        # 기본값(meta.source_doc), 그것도 없으면 적재 파일 이름표로 물러선다.
        doc = (r.get("source") or {}).get("doc") or r.get("_doc_ref") or r.get("_doc_id")
        index[rid] = (doc, (r.get("fields") or {}).get("page"))
    return titles, index


DOC_TITLES, CARD_INDEX = _load_kb_index()

# 퇴직연금 예금자보호 한도(원). 일반 예금과 별도로 1인당 이 금액까지 보호된다.
PROTECTION_LIMIT = 50_000_000

# 상품에 부착 가능한 동작 명사. 절은 이 중 하나 또는 CLAUSE_ENDINGS 로 종결해야 한다.
VERBS = ("매수", "재예치", "예치", "전환", "납입", "재배분", "설정", "편입", "배분")

# 상품 슬롯 없이 종결되는 절의 허용 어미.
CLAUSE_ENDINGS = ("접촉", "발송", "조회", "확인", "안내", "권유", "재진단",
                  "등록", "재구성", "정리", "점검", "조정", "축소")

# actor='고객' 인 절에 부착하는 어미. 산출 문장은 직원이 읽는 문서이므로,
# 고객만 실행할 수 있는 동작은 직원의 행동(제안·안내)으로 감싼다.
ACTOR_SUFFIX = {"운용": "하도록 제안", "납입": "하도록 제안", "설정": "하도록 안내"}

# 자산 구성비(Profile.port) 인덱스별 라벨. 보유 현황 briefing 렌더링에 사용한다.
# port[0] 은 예금만이 아니라 원리금보장·현금성(예금·GIC·고유계정대·기타)의 합이다.
# "예금" 이라고만 쓰면 예금 92% + 고유계정대 8% 인 고객이 "예금 100%" 로 표기돼, 직원이
# 예금 잔액을 물었을 때 실제보다 큰 값을 읽는다. 세그먼트 원문도 "현금성자산·정기예금
# 편중"으로 둘을 함께 본다(06_주제별_추출지식/01_고객세그먼트 1).
PORT_LABELS = ("예금·현금성", "채권형", "TDF·MP", "섹터ETF")

# 보유 현황 briefing 의 근거. '적립금·수익률 조회'([04-12-642]) 절차를 코드로 대체한다 —
# 조회 데이터가 이미 Profile 에 들어와 있으므로 조회를 전략 문장의 지시로 남기지 않는다.
# 값은 KB 카드 id 이며(knowledge/data/kb_procedures.json), 원문 문서는 카드의 source.doc
# 가 가리키는 원천 문서다(출처 표기는 format_sources 가 그 제목으로 만든다).
BRIEFING_SOURCE = "proc.001"

# 시스템이 이미 보유한 데이터의 조회·확인 지시어. 절이 아니라 briefing(코드)으로 표현한다.
LOOKUP_MARKERS = ("MyStar", "단말", "조회 화면")

# 데이터로 확정되지 않은 시한(마감) 표현. 절에 넣지 않는다. 시급성은 urgency 필드와 실행
# 순서로 표현하며, 'D-{matDD}' 처럼 Profile 값으로 산출되는 기한만 절에 담긴다.
TIME_PRESSURE_MARKERS = ("금주", "이번 주", "당장", "오늘 중", "지금 바로", "즉시")
