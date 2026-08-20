# IRP 상담 대화 에이전트 (LangGraph)

직원이 자연어로 묻는 것을 받아 처리하는 에이전트. 이름 그대로("consult_agent", 구
"pitch_agent") 화법 검색을 넘어 브리핑/고객정보 질의(`briefing_qa`)·대화형 LMS 발송
(`lms_send`)·상담이력 기록(§14, 모든 턴 공통)·브리핑 수정 요청(`correction`)까지 다루는
자리로 커졌다 — `router.py`가 의도를 분류해 기능별 노드로 보내는 구조라 새 기능은 새 노드
파일 하나 추가로 붙는다.

```python
from graph import ask

r = ask("사업자 고객인데 수수료 부담된다고 하시네요. 뭐라고 답변하죠?")
r["answer"]    # 핵심 포인트 1문장 + 근거 2~3문장, 직원에게 코칭하는 해요체
r["sources"]   # [{"id": "ch01_new.p02", "title": "...", "score": 6.33, "page": 4}]

r2 = ask("그럼 안 된다고 하면요?", history=r["history"])   # 후속 질문 — 이전 맥락 이어받음

# 브리핑질의·LMS발송·수정은 customer_id 가 필요하다(현재 열려 있는 브리핑 화면의 고객).
# 넘기면 모든 턴이 common/session_data 에 상담이력으로도 함께 기록된다(요건정의서 §14).
r3 = ask("이 고객 평가금액 얼마야?", customer_id="C1", session_id="branch-101-2026-08-20")
```

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

python graph.py "고객이 주식이 더 낫다는데 뭐라고 하지?"   # 단발 실행
python graph.py                                          # REPL (후속 질문 확인용)
python test_agent.py                                     # API 키 없이 검색·라우팅 검증
python kb.py                                              # 지식베이스 점검 리포트
```

---

## 파일 구조

```
consult_agent/
├── graph.py                그래프 조립 · ask() · CLI 진입점 (모든 턴을 session_store 에 기록)
├── router.py                 의도분류(understand, 도메인 어휘 없는 라우팅 전용) · 상태정의(AgentState/Turn) · 모든 분기(route_*) predicate
├── pitch.py                  화법 검색 노드 7개 — situation_slots/retrieve/broaden/llm_rerank/verify/respond/fallback
├── meta.py                    메타 질문("뭘 도와줄 수 있어?") 응답 노드
├── briefing_qa.py             브리핑/고객정보 질의 — strategy_agent.agent.propose() 를 그대로 호출
├── lms.py                     대화형 LMS 발송 명령 — common.tools.send_lms 스텁 호출
├── correction.py              브리핑 수정 요청 — 편집 가능 필드만, 이번 범위는 감사로그까지
├── prompts.py                LLM 프롬프트 템플릿 (기능별 섹션으로 구분)
├── llm.py                   LLM 클라이언트·모델 설정 (환경 이전 시 여기만 수정)
├── kb.py                   지식베이스 로드 · 검색 · 검증
├── data/
│   ├── ch01_new.json        1장 「신규」— 연금왕찐천재_1장_신규_마케팅화법.pdf
│   ├── ch02_retirement.json 2장 「퇴직금」— 연금왕찐천재_2장_퇴직금 마케팅화법.pdf
│   ├── ch03_transfer.json   3장 「계약이전」— 연금왕찐천재_3장_계약이전 화법.pdf
│   ├── guide01_yield_mgmt.json  IRP 수익률 관리 가이드
│   └── _TEMPLATE.json       새 PDF 추가용 템플릿 ('_' 로 시작하면 로더가 건너뜀)
├── test_agent.py            검색·라우팅 테스트 (API 키 불필요)
└── requirements.txt         langgraph, anthropic
```

지식베이스(`_kb`)는 `router.py`에서 한 번만 적재해 `pitch.py`·`meta.py`가 가져다 쓴다(순환
임포트 없이 한 방향으로만 의존). `briefing_qa.py`·`correction.py`는 `common.agent_loader`로
`strategy_agent`(engine·agent·customer)를 같은 프로세스에서 안전하게 불러온다 — 두 에이전트가
`prompts`/`llm` 같은 동명 모듈을 갖고 있어(각자 평평한 스크립트 디렉터리), 그냥 임포트하면
sys.modules 를 놓고 경합한다(자세한 이유는 `common/agent_loader.py` docstring 참고).

화법 문구만 고칠 땐 `prompts.py`, 화법 검색 로직은 `pitch.py`, 의도분류·분기는 `router.py`,
그래프 흐름은 `graph.py`, LLM 클라이언트·모델·키는 `llm.py`. 새 기능은 자기 노드 파일을
추가하고 `router.py`의 의도 분류(`INTENTS`)·`graph.py`의 그래프 조립에 연결하면 된다 — LMS
발송처럼 실제 외부 시스템 연동이 필요한 기능은 `common/tools.py`의 스텁 함수를 실제 MCP
호출로 교체하는 식으로 라우팅 로직 변경 없이 갈아끼울 수 있게 설계했다.

**`router.py::understand`는 라우팅만 한다 — 특정 기능의 어휘를 모른다.** 고객유형·거절유형·
상담단계 같은 화법 검색 전용 슬롯은 `understand`가 채우지 않는다. `intent`가 situation/guide
로 확정된 뒤에만 `pitch.py::situation_slots`가 그 슬롯을 스스로 분해한다 —
`briefing_qa.py`/`correction.py`가 각자 자기 프롬프트로 스스로 해석하는 것과 같은 원칙이다.
이렇게 분리해두면 새 기능이 늘어나도 `understand`가 모든 기능의 도메인 어휘를 알아야 하는
단일 병목이 되지 않는다 — 각 기능이 자기 슬롯 추출을 스스로 소유한다.

---

## 그래프 구조

```
START → understand ─┬─(situation/guide) → situation_slots → retrieve ─┬─(카드 있음)→ verify ─┬─(의도 맞음)→ respond → END
                     │                                      ↑          │                     └─(의도 안맞음)─┐
                     │                                      │          ├─(없음, 1회차)→ broaden ─┘            │
                     │                                      └──────────┤                                      │
                     │                                                 └─(없음, 2회차)────→ fallback ←─────────┘  → END
                     ├─(capability)──────────────────────────────────────────────────────→ capabilities            → END
                     ├─(briefing_qa)─────────────────────────────────────────────────────→ briefing_qa             → END
                     ├─(lms_send)────────────────────────────────────────────────────────→ lms_send                → END
                     └─(correction)──────────────────────────────────────────────────────→ correction               → END
```

| 노드 | 하는 일 | LLM |
|---|---|:---:|
| `understand` | 질문(+이전 대화) → `intent`·`utterance`만 판단(도메인 어휘 없는 라우팅 전용) | ○ |
| `capabilities` | "뭘 도와줄 수 있어?" 같은 메타 질문에 KB 메타데이터로 안내 | ✕ |
| `situation_slots` | situation/guide 확정 후에만 호출 — 고객유형·거절유형·단계를 화법 검색용으로 분해 | ○ |
| `retrieve` | 스코프(고객유형·단계) + 발화 유사도로 화법 카드 top-3 선별 | ✕ |
| `broaden` | 못 찾으면 1차로 고객유형·단계, 2차로 거절유형까지 풀고 재검색 (최대 2회) | ✕ |
| `verify` | 검색된 카드가 질문 '의도'에 실제로 맞는지 판정 — 아니면 fallback (오답 차단) | ○ |
| `respond` | 선별된 카드 + 근거 사실만 넣어 화법 생성 | ○ |
| `fallback` | 지식베이스에 없다고 정직하게 답변 | ✕ |
| `briefing_qa` | `strategy_agent.agent.propose()` 재료만 근거로 브리핑/고객정보 질문에 답변 | ○ |
| `lms_send` | 인용부호로 명시된 문구를 `common.tools.send_lms`(스텁)로 전달 | ✕ |
| `correction` | 수정 요청을 편집 가능 필드로 분류 → 편집 가능하면 재작성+검증, 아니면 거절 | ○ |

- `intent`: `situation`(특정 고객 상담) / `guide`(직원 업무 절차 질문) / `capability`(메타 질문) /
  `briefing_qa`(브리핑·고객정보 질의) / `lms_send`(LMS 발송 명령) / `correction`(브리핑 수정
  요청). `guide`도 `retrieve`는 동일하게 타되, 응답 프롬프트 톤만 다름.
- `briefing_qa`·`lms_send`·`correction`은 `state["customer_id"]`(호출자가 `ask(..., customer_id=...)`로
  넘김)가 있어야 동작한다 — 없으면 "고객을 찾을 수 없다"고 안전하게 답한다.
- **오답 차단 2단계**: ① `retrieve`가 발화·주제 유사도(`MIN_TOPICAL`)로 무관한 질문을 거르고, ② 그래도 주제어만 겹쳐 딸려온 카드는 `verify`가 질문 의도와 대조해 걸러 `fallback`시킴. 확신이 낮으면 억지 답변 대신 "화법 없음"으로 정직하게 응답.
- 검색은 결정적(deterministic) — 같은 질문엔 같은 카드. 재검색은 `broaden_count`로 최대 2회 제한.
- `retrieve`는 `stage`/`customer_type`으로 먼저 후보를 좁힌 뒤 채점 — 챕터 간 같은 라벨(예: "수수료 비교"가 퇴직금·계약이전에 둘 다 있음)이 섞이지 않음. 세부 로직·근거는 `nodes.py`/`kb.py` 코드 주석 참고.
- 후속 질문은 `ask()`가 돌려준 `history`를 다음 호출에 그대로 넘기면 됨(세션 유지는 호출자 책임, 최근 4턴만 반영).

---

## 지식베이스 구조

3계층이고, 화법 카드는 사실을 **ID로 참조만** 합니다(복붙 안 함).

| 계층 | 내용 | 이유 |
|---|---|---|
| `facts` | 세액공제율, 납입한도 등 재사용 사실 | 세법 개정 시 한 줄만 고치면 전체 반영, 문서 간 수치 충돌 자동 탐지 |
| `pitches` | 화법 카드 (검색 최소 단위) | 태그로 필요한 것만 프롬프트에 넣어 PDF가 늘어도 프롬프트 크기 일정 |
| `resources` | 상품안내장, 원픽 가이드 등 | `customer_facing`으로 내부용 자료 유출 방지 |

**새 PDF 추가** (코드 수정 불필요): `_TEMPLATE.json` 복사 → 내용 채우기(기존 fact는 재사용, id는 `{doc_id}.pNN`) → `python kb.py`로 ERROR 0건 확인.

### 화법 카드 목록

`python kb.py` 가 챕터별 카드 전체(ID·제목·단계·거절유형·대화/근거/트리거 수)를 최신
상태로 출력합니다. 카드 추가·수정 시 별도 문서 갱신 없이 이 명령으로 확인하세요.

---

## 검증 & 환각 방지

`python kb.py`가 ERROR로 잡는 것: 필수 필드 누락·잘못된 값, ID 중복, 깨진 fact/resource 참조, 같은 항목에 문서마다 다른 값(개정 반영 누락), objection 카드인데 거절유형 태그 없음. 현재 **ERROR 0건**.

- 프롬프트에 넣는 수치는 `facts`에 있는 값으로 한정, 예시 수치는 전제조건(`assumptions`)과 함께 전달
- 검색 채택 기준을 **내용 관련도**로 걸어 무관한 질문엔 억지로 카드를 붙이지 않고 `fallback`
- `customer_facing: false` 자료는 "고객 직접 제공 금지"로 표기, 응답의 `sources`로 근거 카드 역추적 가능
- `capabilities` 응답도 LLM 호출 없이 KB 메타데이터만으로 생성 — 없는 기능을 있다고 답할 수 없음

---

## 튜닝 포인트

| 위치 | 값 | 의미 |
|---|---|---|
| `nodes.py` `TOP_K` | 3 | 프롬프트에 넣을 카드 수. 늘리면 맥락↑ 토큰↑ |
| `kb.py` `MIN_TOPICAL` | 0.5 | 낮추면 fallback이 줄고 오답이 늘어남 (실측: 유관 0.55~2.1 / 무관 0.00~0.42) |

---

## 주의

원본 PDF는 당행 영업전략·타사대응 노하우가 포함된 대외비 자료입니다. `data/`에 그 내용이
그대로 들어 있으므로 저장소 접근권한을 반드시 통제하세요.

---

## 상담이력 · LMS · 브리핑 수정 (에이전틱 확장, 이번 범위)

- **상담이력(§14)**: `graph.py::ask()`가 `customer_id`를 받으면 매 턴(질문+답변)을
  `common/session_data/{customer_id}.json`에 기록한다. `strategy_agent.engine.prepare()`는 이
  기록을 `facts["consult_history"]`로 **읽기만** 한다 — consult_agent 가 기록하고
  strategy_agent 가 읽는 단방향 관계라 "코드=사실" 경계를 대화이력에도 그대로 적용한다.
- **LMS 발송**: `common/tools.py::send_lms`는 아직 스텁이다 — 실제 발송 없이 호출 사실만
  세션이력에 남긴다. MCP 연동이 준비되면 그 함수 **본문만** 실제 클라이언트 호출로 교체하면
  된다(레지스트리 키·시그니처 불변 — `lms.py`도 이 화면의 "LMS 발송" 버튼도 다시 손댈 필요
  없음).
- **브리핑 수정**: 편집 가능 항목은 `strategy_agent.agent.EDITABLE_FIELDS`(AI브리핑
  문장·근거해설·카드 한줄혜택 — 전부 LLM 이 쓴 산문)로 코드가 못박아둔다. 수치·상품명·전략
  선정처럼 시스템이 계산한 값을 고쳐달라는 요청은 조용히 수용하지 않고 명확히 거절한다.
  승인된 수정은 세션이력에 감사로그로만 남고, 다음 브리핑 생성에 자동 반영되는 재적용 루프는
  아직 없다 — 그 루프는 "코드=사실" 경계를 실제로 어떻게 지킬지 더 구체적인 설계가 필요한
  다음 단계 항목이다.
