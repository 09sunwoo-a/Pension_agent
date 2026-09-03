# 퇴직연금 에이전트 (Pension Agent)

퇴직연금(IRP) 상담 전, 직원에게 **이 고객에게 무엇을 어떻게 제안할지**를 정리해 주는
에이전트 묶음. **경계는 코드가 정하고 LLM 은 그 안에서만 말한다** — 어떤 근거를 모을지는
LLM 이 계획하되, 부를 수 있는 도구·바퀴 수·수치 계산은 코드가 쥔다([../CLAUDE.md](../CLAUDE.md) §2).
요건 기준은 [../docs/REQUIREMENTS.md](../docs/REQUIREMENTS.md) 하나다.

## 실행

명령은 **전부 `src/` 에서** 실행한다 — 그래야 `src/` 가 임포트 루트가 된다. 설치는 필요 없다.

```bash
cd src
pip install -r requirements.txt
cp .env.example .env      # 사내 게이트웨이 URL·키 (pension_agent/llm.py 가 읽음)

CA="python -m pension_agent.consult_agent"     # 상담 대화 (LangGraph)
CAD="python -m tests.debug"                    # 같은 것 + 트레이스
CADR="python -m tests.debug.reps"              # 대표 질문 묶음 (검토 · 시연 대본)
```

```bash
# ── 브리핑 · 대화 · 화면
python -m pension_agent.strategy_agent.agent 이준호    # AI 브리핑 (①~⑨ 섹션)
$CA "ETF로 직접 굴리겠다고 증권사로 옮기겠다는 고객, 뭐라고 하지?"   # 단발 — 고객 화면 없이
$CA -c 198734-1205842                                 # REPL — 고객 화면이 열린 상태
$CA -c 198734-1205842 "투자성향 뭐야?" "만기 자금은?"  # 멀티턴을 한 줄로 (맥락 이어서)
streamlit run app.py                                  # 평가 대시보드 (개발용 화면)

# ── 디버그: 이 답이 어디서 갈렸나 (인자 규약이 $CA 와 같다 — 모듈만 바꾸고 --debug)
$CAD --debug "세액공제 한도가 얼마야?"
$CAD --debug -c 198734-1205842                        # REPL — 턴마다 방금 턴만 찍는다
$CAD --script tax_credit_asserts_wrong --debug --show-llm   # 키 없이 재현
$CAD --list                                           # 시나리오 목록

# ── 묶음 실행: 검토(독립 케이스 10개) · 시연 대본(docs/DEMO_SCENARIO.md)
$CADR                                                 # 검토 10케이스 + 요약표
$CADR --brief                                         # 요약표만
$CADR --demo                                          # 시연 대본 14턴 (청중이 보는 화면)
$CADR --demo --debug                                  # + 「무엇을 찾아봤나 → LLM 이 썼다」
$CADR --demo --debug --show-llm                       # + 폐기된 생성문까지 (왜 잘렸나)
$CADR --scenario                                      # 고객별 대표 시나리오 5종 (docs/DEMO_CUSTOMER_SCENARIOS.md)
$CADR --scenario 김서연 정민석 --debug                 # 이름·번호로 골라서 (옵션은 --demo 와 동일)

# ── 리허설을 빨리 시작하기 (브리핑 한 편 = 순차 LLM 11회 · 고객 블록마다 화면을 열 때 든다)
python -m scripts.prebuild_briefings                  # 9케이스 브리핑을 미리 만들어 둔다
python -m scripts.prebuild_briefings --status         # 무엇이 저장돼 있고 지금 읽히는가
python -m scripts.prebuild_briefings --clear          # 지우고 저장소를 끈다(예전 동작)

# ── 테스트 · 점검 (LLM 키 없이 돈다)
python -m tests.test_engine            # ①~⑤ 결정론 로직
python -m tests.test_support           # ⑥~⑨ 후보군 · 더미 규약 · 시효성 수치
python -m tests.test_strategy_agent    # LLM 산출 검증 · 폴백
python -m tests.test_consult_agent     # 라우팅 · 도구 루프 · 재계획 · 하지말것 가드
python -m tests.test_infra             # 공용 인프라 · 임포트 경계
python -m tests.debug.test_trace       # 트레이스 — 노드 · 게이트 · 폐기 사유
python -m scripts.kb_build.test_paths  # 경로 · locator 실재
python -m pension_agent.knowledge.schema validate pension_agent   # 전 데이터 검증
python -m pension_agent.consult_agent.kb                          # 지식베이스 리포트

# ── 재생성 (생성물은 손으로 고치지 않는다 — 생성기를 고친다)
python -m scripts.kb_build.build_kb [--activate]   # 06_주제별_추출지식 → 카드
python -m scripts.import_targets                   # 타겟 룰베이스 xlsx → targets.json
python -m scripts.demo_status                      # docs/DEMO_STATUS.md 갱신
```

**고객 지정(`-c/--customer`)** 은 고객 id(KB-PIN)다. 없으면 브리핑질의·LMS발송·수정이
"고객 화면을 먼저 열어주세요"로 답한다. id 는 `strategy_agent/customer.py` 의 `PERSONAS`
(예: 이준호=`198734-1205842`). 실행 조합은
[consult_agent/README.md](pension_agent/consult_agent/README.md).

### 디버그 모드

`tests/debug/` 는 파이프라인을 **밖에서 감싸서 보기만** 한다 — 값은 그대로 통과시키고
운영 코드는 고치지 않는다.

| 옵션 | |
|---|---|
| `--debug` | 답변 아래에 트레이스 (REPL 에서는 방금 턴만) |
| `--show-llm` | compose 가 LLM 에게 받은 문장을 폐기됐어도 그대로 |
| `--script N` | 캔드 LLM 시나리오 — API 키 없이 돈다 (`--list` 로 7종 확인) |
| `--any-customer` | 이 체크아웃에 없는 고객 id 로도 진행(경고만) |

트레이스는 노드 순서·상태 변화, LLM 호출 자리(understand·plan·clarify·tools·select),
그리고 compose 게이트 `verify_texts`(원장 밖 수치) → `relations`(근거와의 관계) → `span`
을 찍는다. **앞에서 끊기면 뒤는 아예 안 불리고, 그게 진단의 핵심이다.**

없는 고객 id 는 시작할 때 끊는다 — 그냥 두면 «재료 0건»이 되어 오타와 구분되지 않는다.

### 평가 대시보드 (`streamlit run app.py`) — 기획자용 테스트 환경

기획자가 **키보드만으로** 대화형 에이전트를 두드려 보고, 이상한 답을 그 자리에서 남길 수
있게 해 둔 화면이다. 개발자가 CLI 로 하는 일(`$CA` · `$CAD --debug`)과 같은 경로를 탄다 —
디버그 모드는 `tests/debug/runner.session()` 을 그대로 부르고, 그 runner 는 운영 진입점
`graph.ask()` 를 부른다.

| 💬 대화형 에이전트 테스트 탭 | |
|---|---|
| 대상 고객 선택 | 브리핑질의·화면연계·수정 요청이 그 고객으로 간다. 화법·절차·메타 질문은 고객 없이도 답한다 |
| 테스트 질문 리스트 | 갈래별(화법·절차·메타·브리핑질의·LMS·수정) 예시 질문. 누르면 바로 전송 |
| 💡 추천 질문 칩 | 상황이 맞는 고객에게만 뜬다(지난 상담 · 열려 있는 세미나) — `suggest.py` |
| **🔍 디버그 모드** | 답변 아래에 실행 트레이스. 노드 순서 · 도구 호출과 채택 카드 · compose 게이트(`verify_texts` → `relations` → `span`) 통과/폐기/**미실행** · 턴별 소요 시간 |
| └ 폐기된 LLM 생성문까지 보기 | 게이트가 버린 문장의 원문. 「왜 이 답이 안 나갔나」 |
| **🚩 이상해요** | 그 답변을 신고. 질문·답변 원문·근거 카드 id·intent·트레이스가 **자동으로 함께** 저장된다 → `chat_feedback.csv` |
| ↪ 추천질문 · 되묻기 선택지 | 눌러서 이어간다(칩 UI 로 쓸 자리의 예행) |
| ⬇ 대화 로그 내려받기 | 대화 전체를 마크다운으로. 신고 한 건에 담기 애매한 흐름을 그대로 넘길 때 |

신고는 「📋 피드백 관리 보드」 탭 **2) 대화형 에이전트 신고** 에 쌓인다 — 결재 상태를 바꾸고
CSV 로 내려받는다. 브리핑 산출물 피드백(`feedback_log.csv`)과 **파일이 다르다**: 재현에
필요한 것이 다르기 때문이다(대화는 질문·트레이스가 있어야 같은 자리를 다시 밟는다).

**«오늘»은 앱을 켠 시각에 고정된다.** 사이드바 「실행 조건」이 오늘·원장 기준일(`AS_OF`)·
LLM 연결 여부를 항상 보여준다 — 답이 이상할 때 «에이전트가 틀렸다»와 «기준일이 어긋났다»를
화면에서 갈라야 신고가 재현 가능해진다. 특정 날짜로 얼려 보려면
`PENSION_TODAY=YYYY-MM-DD streamlit run app.py`.

두 CSV 는 `.gitignore` 에 있다. 개발자에게 넘길 때는 화면의 내려받기 버튼을 쓴다.

## 최근에 들어온 것

- **재계획.** 근거를 못 낸 호출을 `plan_misses` 로 계획에 싣고, 근거 0건인 채 끝내려 하면
  안 써 본 도구와 함께 **한 번만** 재계획시킨다. 두 번째 끝내기는 존중한다(정직한 '없음').
  루프 상한 `plan.MAX_STEPS`=4.
- **표기가 판정을 뒤집지 않는다.** `verify` 가 값 보존 정규형으로 대조 — `15.0%`=`15%`,
  `1,485,000원`=`148만 5천원`, `2026-09-10`=`2026.09.10.`=`2026년 9월 10일`. 값이 다르면
  여전히 폐기된다.
- **시간축이 둘이다.** `customer.AS_OF`(원장 스냅샷이 찍힌 날 — 잔액·수익률의 시점)와
  `clock.today()`(상담 시점 — 잔여일수·경과일의 기준). 하나로 붙여 두면 원장이 사흘만
  묵어도 "만기 D-17"(실제 D-14)·"연말까지 129일"(실제 126일)이 나간다. `PENSION_TODAY=
  YYYY-MM-DD` 로 고정하며, 테스트는 `tests/__init__.py` 가 `AS_OF` 로 고정한 채 돈다.
  오늘이 며칠인지는 `date` 도구가 **재료로** 싣는다 — 재료 밖 날짜 계산은 금지이므로
  (§5) 싣지 않으면 시한을 아예 말하지 못한다.
- **날짜는 통짜로 대조한다.** 연·월·일로 흩으면 원장 어딘가에 2026 과 11 과 10 이 있다는
  이유로 "만기는 2026년 11월 10일"(오답)이 통과한다. 정규형 하나로 맞추되 표기(ISO·한글·
  점)는 가리지 않고, 연도를 뺀 날짜는 **오늘 언저리(±1년)** 로만 읽는다 — 3년 전
  납입이력에서 월일만 빌려 오는 말은 사람이 하는 해석이 아니다.
- **인용은 주장이 아니다.** 원문의 오기를 짚는 정정은 통과하고, 사실로 주장하면 폐기된다.
- **상담이력 선별.** 도구가 계획의 `query` 를 읽어 걸리는 기록을 앞세우되 걸러내지는 않고,
  과거 상담과 오늘 대화를 예산·구획으로 가른다. `suggest.history_chips` 는 기록 있는
  고객에게만, 고정 템플릿 + 계산값으로만 칩을 띄운다.
- **답변 끝 추천질문**(`suggest.followup_questions`). 이번 턴에 쓴 재료마다 다음 질문
  후보를 세우되, **띄우기 전에 그 질문에 답할 재료가 있는지 LLM 없이 확인**하고 없으면
  안 띄운다 — 눌렀을 때 '근거 없음'이 나오는 추천질문은 안 띄우느니만 못하다. 확인은
  **짧은 말(카드 제목·확인어)을 먼저** 대본다: n-gram 유사도는 질의가 길수록 희석돼
  (`kb._sim`) 자연스러운 문장이 문턱 아래로 떨어지고, 그러면 있는 자료를 없다고 판정한다.
  개수는 3개까지이되 **억지로 채우지 않는다**(못 채우는 턴은 그대로 둔다). 되묻기·
  확인대기·LLM실패·근거0건 턴에는 붙지 않는다. `graph.FOLLOWUP_HEADER` 블록으로 답변
  끝에 실리고, `ask()` 반환의 `followups` 로도 따로 준다(프론트가 칩 UI 로 쓸 자리).
- **타겟 룰베이스.** 임계값의 기준은 기획자 확인표(`strategy_agent/targets.json`)다 —
  어긋나면 코드가 틀린 것. 근거등급은 [../docs/DEMO_STATUS.md](../docs/DEMO_STATUS.md) §7.

## 디렉토리

```
src/
├─ pension_agent/          단일 패키지. 임포트는 전부 절대 경로, 경로를 아는 파일은 config.py 하나
│  ├─ config.py            경로·데이터 위치의 단일 출처
│  ├─ clock.py             «오늘»의 단일 출처 — 원장 스냅샷 기준일과는 다른 축
│  ├─ llm.py               프로바이더 전환식 클라이언트 (환경 이전 시 여기만)
│  ├─ verify.py            LLM 산출물의 재료 이탈 판정 — 두 에이전트 공통
│  ├─ session_store.py     상담 세션·이력 (consult 가 쓰고 strategy 는 읽는다)
│  ├─ tools.py             외부 연동 레지스트리 (LMS 발송 게이트)
│  ├─ knowledge/           데이터 접근 계층 — kinds·schema·store + data/
│  ├─ strategy_agent/      AI 브리핑 → ①~⑨ · engine/ · targets.json
│  ├─ consult_agent/       상담 대화 (LangGraph) — routing·graph·suggest + nodes/
│  └─ market/              시황·금리 (자리표시자)
├─ tests/                  회귀 5종 + debug/(트레이스 — 운영 코드 무수정)
├─ scripts/                kb_build(지식 변환) · import_*(xlsx 적재) · demo_status
├─ app.py                  Streamlit 평가 대시보드
└─ AUTHORING.md            데이터 소스 추가 · 저작 프롬프트
```

`sys.path` 를 손대는 모듈은 없다 — `tests/test_infra.py` 가 회귀로 고정한다.

## 핵심 설계

- **근거는 원장에만 쌓인다.** 답변은 `state["evidence"]` 안에서만 나오고, `verify.py` 가
  원장 밖 수치를 잘라낸다. 실패하면 규칙 결과를 남기거나 섹션을 비운다.
- **저작은 검증을 통과해야 활성화된다.** 사람이 검토하고 `schema.py`(필수필드·enum·참조·
  사실충돌·개인정보)를 통과해야 적재된다. 단일 규격이라 새 종류는 선언만 더한다 →
  [AUTHORING.md](AUTHORING.md).
- **검색의 불확실성을 브리핑에 들이지 않는다.** strategy_agent 는 화법을 검색하지 않고
  저작 시점에 연결된 `pitch_refs`/`objection_refs` 를 실시간 조회한다.
- **⑥~⑨ 는 문제상황에서 출발한다.** "왜 관리 대상인가"를 먼저 확정하고 그 사유에 맞는
  화법·반론·자료를 모은다. 사유가 없는 고객은 비운다 — 만들어내지 않는다.
- **출처는 원본 문서 이름으로 말한다.** `consult_agent/kb.py::origin_of()` 한 곳에서만
  만들고, 못 찾으면 "확인 필요"라고 한다. 적재 json 의 이름표로 물러서지 않는다.
- **되돌릴 수 없는 행위는 사람이 정한다.** 에이전트는 제안(`act.offer`)하고, 발송은 확인을
  한 턴 거친다(`act.confirm_action`). 편집 가능 범위도 코드가 못박는다
  (`EDITABLE_FIELDS` — 코드가 계산한 수치·상품명은 대화로 못 고친다).
