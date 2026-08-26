# 전략 제안 에이전트

퇴직연금(IRP) 상담을 앞둔 직원에게, 이 고객에게 **무엇을 어떻게 제안할지**를 한 문장으로
정리해 준다. 고객 프로필 한 건을 넣으면 상담에서 그대로 읽고 실행할 수 있는 전략 제안이
나온다.

```
입력  고객 프로필 한 건 (타겟리스트 · MyStar · CRM 조인 레코드)
출력  전략 제안 문장 + 판단근거 + 근거 문서 + 다른 제안 + 고지·확인·보류 사항
```

산출 예시 — 이현우 (성립 요건 7건):

```
보유현황  예금 100% · 수익률 1.8%(하위 17%) · 만기 D-22 2억 3,000만원

[전략 제안] 금주 내 유선 접촉한 뒤, 만기 D-22 예금 2억 3,000만원을 KB GIC 확정금리
           (연 3.65%)로 예치하도록 제안하고, 실적배당 안내를 위해 투자성향 재진단을 안내하세요.
[판단근거] · 이탈 위험 15.5점 — 419일 미접촉 · 운용변경 13.8개월 · 수익률 하위 17%
           · 만기 D-22 · 예금 100% — 만기 시점이 편중 해소의 유일한 실행 창구
           · 위험등급 매우낮은위험으로 실적배당 전량 차단 — 성향 재진단이 선행
[근거 문서] 개인형IRP 고객관리 가이드 Series 1 (p1/p4/p5/p6)
고지 필요: KB GIC — 예금자보호 한도(5,000만원) 초과분 1억 8,000만원 안내
```

---

## 실행

전부 `src/` 에서 실행한다.

```bash
# 정의·회귀 검증 (외부 의존성 없음 · stdlib 만)
python -m pension_agent.strategy_agent.engine       # 정의 검증 — 근거 교차검증 포함 (ERROR 0건)
python -m pension_agent.strategy_agent.situations   # 페르소나별 문제상황 매칭 결과 확인
python -m tests.test_engine                         # 회귀 — 엔진 결정론 로직 (전건 통과 확인)
python -m tests.test_support                        # 회귀 — ⑥~⑨ 문제상황·후보군
python -m tests.test_strategy_agent                 # 회귀 — LLM 단계 (스텁, API 키 불필요)

# 산출
python -m pension_agent.strategy_agent.agent 이현우  # 단일 고객 전략 제안 (CLI)

# 평가/피드백 대시보드 (Streamlit)
streamlit run app.py        # 페르소나 산출물(AI브리핑 ①~⑨) 리뷰 + consult_agent 대화형 테스트 탭 + 피드백 접수
```

LLM 설정은 이 폴더가 아니라 **`src/.env`** 에 둔다(`pension_agent/llm.py` 가 읽음). 미설정이면
`llm.available()` 이 False → 코드가 만든 문장으로 폴백한다. 교차검증은 공용 지식
(`../knowledge/data`)을 참조한다.

---

## 핵심 아이디어 — 문장을 통째로 쓰지 않고 절(clause)을 이어 붙인다

전략마다 **명사형 동작으로 끝나는 짧은 절**을 하나씩 정의해 둔다("만기 예금을 재예치",
"디폴트옵션을 설정"). 고객에게 해당하는 절만 골라 접속 규칙으로 이어 붙이면, 어떤 고객이
와도 자연스러운 문장이 만들어진다. 전략 정의(`data/strategies.json`)만 갖추면 된다.

## 설계 규약 네 가지

- **A. `actor` — 누가 실행하는가.** 직원 동작(접촉·발송·안내)은 절 그대로, 고객 운용지시
  (재예치·전환·설정)는 `하도록 제안/안내` 를 자동 부착. 산출물은 직원이 읽는 문서다.
- **B. `capabilities`·`assets` — 확인 안 된 것은 격리.** 전략은 `requires` 로 필요한 시스템
  기능을 선언하고, `available` 이 아니면 제안에서 빠지고 사유가 남는다(정의는 삭제 안 함).
- **C. `sources` 교차검증 — 인용 문서를 실제로 열어 본다.** `validate()` 가 pitch KB 를
  재사용해 깨진참조·근거오인용·제공범위위반을 잡는다.
- **D. 조회는 전략이 아니다.** 시스템이 이미 가진 데이터의 제시는 코드(`_briefing()`),
  무엇을 얼마나 할지는 전략(`clause`). `MyStar`·`단말` 같은 조회 지시어가 절에 들어오면 차단.

## 코드가 사실을 정하고, LLM은 표현만 한다

```
고객 프로필
  ├─[코드] 요건 판정 → 기능 확인 → 흡수 → 자금 배분 → 적합성 게이트 → 기대효과·점수 → 상위 N
  ├─[LLM ] 실행 순서 판단 · 인과 해석 · 문장 작성   (코드가 넘긴 사실 밖으로 못 나감)
  ├─[코드] 검증 — 계산에 없던 수치·상품명이 섞였는지 확인
  └─→ 문제없으면 LLM 문장, 아니면(또는 LLM 미가용) 코드가 만든 문장
```

| 단계 | 담당 | 이유 |
|---|---|---|
| 요건·금액·기대효과·적합성·자금배분 | 코드 | 계산·규정이므로 같은 고객엔 늘 같은 결과여야 한다 |
| 실행 순서·인과·문장 | LLM | 점수만으로 정하기 어려운 판단 / 최종 산출물 |
| 검증 | 코드 | LLM 이 사실을 벗어났는지 확인, 벗어나면 코드 문장으로 대체 |

## 안전장치

- **적합성 게이트** — 위험등급·최소금액·거래채널·위험자산 한도 대조, 통과 상품 0이면 전략 제외.
- **자금 배분** — 예금·추가납입·위험자산을 몫으로 관리해 두 전략이 같은 돈을 겹쳐 쓰지 못하게.
- **흡수** — 원인이 같은 전략은 합치고 근거는 보존. **우선순위** — 시급성·기대효과·필수로 채점.
- **고지·확인 분리** — 고지는 `cautions`, 확인 필요 값은 `needs_confirm` 으로 문장 밖에 낸다.

## 핵심 자산은 코드가 아니라 `data/` 의 JSON

| 파일 | 내용 |
|---|---|
| `strategies.json` | 전략 플레이북 — 요건·절·근거·시급성·혜택·출처·행위주체·`pitch_refs`/`objection_refs` (근거 있는 전략만) |
| `system_strategies.json` | 게이트 결과로 발동하는 조건부 전략(예: 투자성향 재진단) |
| `products.json` | 상품 — 위험등급·최소금액·비대면·위험자산 산입·예금자보호(+ ⑤용 지역·자산군·운용전략·특징) |
| `baselines.json` | 기대효과 비교 기준선 (전부 KB 사실 출처) |
| `capabilities.json` | 시스템 기능 지원 여부(예: `cap.lms_send`). consult_agent 의 `agent_help` 의도(메타 질문)·07_에이전트_기능정의/01 ② "가능 여부 즉시 확인"(고객 계좌 상태 조회)과는 다른 개념 |
| `assets.json` | 고객 발송 가능 콘텐츠 + 이벤트·세미나 일정(⑨) |
| `top_holdings.json` | 수익률 상위 1% 고객 상품 사례(④, 비개인화·참고용) |
| `portfolios.json` | 적합 상품 추천의 '포트폴리오' 후보(⑤) |

**상품·전략·기준선·발송자료 추가는 코드 수정 불필요** — JSON 만 채운다. 요건(`CONDS`) 신설만
`customer.py` 수정 필요. 저작 절차와 프롬프트 → **[../AUTHORING.md](../AUTHORING.md)**.

`top_holdings.json`·`portfolios.json`과 `assets.json`의 이벤트·세미나 레코드는
[../../docs/REQUIREMENTS.md](../../docs/REQUIREMENTS.md)의 예시를 옮긴 자리표시자 데이터다 —
실제 콘텐츠로 교체하는 것은 데이터 담당자 몫이다.

## AI 브리핑 — 9개 섹션 + 상담이력

`agent.propose(profile)`은 화면의 "AI 브리핑"이 필요로 하는 조각들을 `facts`에 함께 담아
반환한다(①은 `sentence`, 나머지는 `facts` 키):

| 섹션 | facts 키 | 산출 |
|---|---|---|
| ① AI 브리핑 | `sentence`/`insight` | LLM(재료 안에서) — 실패 시 명시적 미생성(규칙 문장 대체 없음) |
| ② 왜 이 고객님인가요 | `why_this_customer` | 코드(수치) + LLM(해석 문장) + verify, 실패 시 규칙 문장 폴백 |
| ③ 현재 운용상태 | `briefing["운용현황(3분류)"]` | 코드(`cash_idle_pct` 없으면 생략) |
| ④ 상위 1% 상품 사례 | `top_holdings` | 코드(고객 필터 없음) |
| ⑤ 적합 상품·포트폴리오 | `recommendation` | LLM(폐쇄 후보군에서 선택 + verify) |
| ⑥ 이렇게 말해보세요 | `talking_points` | 코드로 2개 보장 + LLM 스크립트(선택적) |
| ⑦ 예상 반론 및 대응 | `objections` | 코드로 2개 보장(저작 우선, 부족 시 폴백) |
| ⑧ 상담에 참고하세요 | `consult_resources` | consult_agent.kb.retrieve() 재사용 |
| ⑨ 고객님께 안내해보세요 | `outreach` | 코드(가장 임박한 이벤트·세미나) |
| §14 상담 이력 | `consult_history` | `pension_agent.session_store` 읽기 전용 |

새 LLM 접점(⑤·⑥)도 기존 `sentence`와 같은 원칙을 따른다 — `pension_agent.verify.verify()`로 재료
밖 수치·상품명을 걸러내고, 실패하면 그 섹션만 조용히 비거나(⑤) 원본 텍스트로 폴백한다(⑥).

## 파일 구조

```
strategy_agent/
├─ data/*.json          ★ 핵심 자산 (위 8개) — 상품·전략 카탈로그
├─ engine/              ①~⑤ 결정적 처리 계층. 공개 이름은 __init__.py 가 전부 재노출한다
│  ├─ catalog.py        데이터와 상수 (잎 — 다른 engine 모듈을 임포트하지 않는다)
│  ├─ text.py           표기 유틸 — 금액·조사·출처 문자열
│  ├─ products.py       상품 질의와 적합성 게이트 (정적 게이트 / 금액 게이트 2단)
│  ├─ scoring.py        금액 · 시급성 · 기대효과 · 점수
│  ├─ render.py         절 · 고객 헤더 · 보유 현황 briefing · 관리 사유
│  ├─ pipeline.py       prepare() — 위 다섯을 한 줄기로 엮는다
│  ├─ compose.py        규칙 문장 합성 · 섹션 요약 · verify()
│  ├─ recommend.py      ⑤ 추천 후보군과 표시용 사실
│  └─ validate.py       정의 무결성 검사 (`python -m ...strategy_agent.engine`)
├─ customer.py          고객 프로파일 · 요건 판정 · 검증용 페르소나 · get_profile()
├─ situations.py        문제상황 정의 — 06/01 고객세그먼트를 요건 판정 결과와 대조
├─ support/             ⑥~⑨ 규칙 계층. 공개 이름은 __init__.py 가 전부 재노출한다
│  ├─ kb.py             근거 대조용 지식베이스 적재 (지연 로딩)
│  ├─ matching.py       문제상황 → 지식 카드 — ⑥⑦⑧ 공통 후보군 산출
│  ├─ talking.py        ⑥ 이렇게 말해보세요
│  ├─ objection.py      ⑦ 예상 반론 및 대응 화법
│  ├─ resource.py       ⑧ 상담에 참고하세요
│  └─ outreach.py       ⑨ 고객님께 안내해보세요
├─ agent.py             LLM 단계 · 최종 조립 · CLI · EDITABLE_FIELDS(대화형 수정 경계)
├─ prompts.py           LLM 프롬프트
└─ sections.py          ①~⑨ 섹션 제목·생성주체의 유일한 출처
```

회귀 테스트는 `src/tests/`(test_engine · test_support · test_strategy_agent), 화면은
`src/app.py`, LLM 클라이언트는 `../llm.py` 에 있다.

**`support/`·`situations.py` 를 engine 에서 떼어 둔 이유**: ①~⑤ 는 요건·금액·기대효과를
계산하는 영역이고 ⑥~⑨ 는 공용 지식(`../knowledge/data`)을 읽는 영역이라 변경 축이 다르다.
한 파일에 두면 서로의 수정이 충돌한다. engine 은 같은 이름으로 재노출하므로 기존 호출부
(`engine.pick_talking_points` 등)는 그대로 동작한다.

의존 방향은 `customer ← situations ← support ← engine ← agent` 로 고정한다 — support 는
engine 을 임포트하지 않는다. engine 의 표기 유틸(`won`·`_pname`)이 필요한 값은
`engine.prepare()` 가 항목에 미리 렌더링해 넘긴다(`amount_fmt`·`products_fmt`).

## 튜닝 포인트

| 위치 | 값 | 의미 |
|---|---|---|
| `engine.py` `TOP_N` / `ALT_N` | 3 / 3 | 문장에 넣을 실행 항목 수 / 「다른 제안」 노출 수 |
| `engine.py` `EFFECT_BANDS` | 1.0/0.5%p | 수익률 개선폭 → 정성 등급(큼·보통·작음) 경계 |
| `engine.py` `MIN_ALLOC` / `PROTECTION_LIMIT` | 100만원 / 5,000만원 | 배분액 하한 / 예금자보호 한도 |
| `customer.py` `RISK_ASSET_CAP_PCT` / `MAT_WINDOW_DAYS` | 70 / 30 | 위험자산 투자한도 / 만기 요건 인정 범위("만기 1개월 전 안내" — 세그먼트 9·방법론 18) |

## 주의

`strategies.json`·`products.json` 은 행내 영업전략·상품조건이 담긴 **대외비**다. 저장소
접근권한을 통제하고, `assets.json` 의 `customer_facing` 은 고객 직접 제공이 허용된 배포본에만
`true` 로 둔다. 내부 조회 경로 자료를 가리키면 `validate()` 가 `[제공범위위반]` 으로 차단한다.
