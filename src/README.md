# 퇴직연금 에이전트 (Pension Agent)

퇴직연금(IRP) 상담 전, 직원에게 **이 고객에게 무엇을 어떻게 제안할지**를 정리해 주는
에이전트 묶음. **경계는 코드가 정하고 LLM 은 그 안에서만 말한다** — 어떤 근거를 모을지는
LLM 이 계획하되, 부를 수 있는 도구·바퀴 수·수치 계산은 코드가 쥔다([../CLAUDE.md](../CLAUDE.md) §2).
요건 기준은 [../docs/REQUIREMENTS.md](../docs/REQUIREMENTS.md) 하나다.

## 실행

명령은 **전부 `src/` 에서** 실행한다 — 그래야 `src/` 가 임포트 루트가 된다. 설치는 필요 없다.

```bash
cd src
pip install -r requirements.txt      # 행내 배포 이미지와 같은 목록 (Python 3.10)
pip install -r requirements-dev.txt  # + Streamlit 화면·변환기·사외 프로바이더 (개발용)

cp .env.example .env                 # 어디서나 이 파일 하나 — 안의 구역 ①행내 ②Gateway ③사외 중 채운다
python -m pension_agent.env          # 어느 파일이 읽혔고 어느 프로바이더가 잡혔나

source ./cli.sh                      # CA · CAD · CADR 정의 + 사용법 출력
```

`cli.sh` 가 정의하는 것은 셋뿐이다. 셋 다 **HTTP 를 타지 않고** `graph.ask()` 를 직접
부르므로 서버(`run_local.sh`)와 무관하고, `.env` 만 잡혀 있으면 행내에서도 사외에서와
똑같이 돈다.

```bash
CA="python -m pension_agent.consult_agent"     # 상담 대화 (LangGraph)
CAD="python -m tests.debug"                    # 같은 것 + 트레이스
CADR="python -m tests.debug.reps"              # 대표 질문 묶음 (검토 · 시연 대본)
```

## 행내에서 처음 실행

저장소를 clone 하는 대신 **폴더를 복사해 올리는** 경우(Azure ML 컴퓨트 인스턴스 등)를 기준으로
적는다. clone 이 되면 0번은 건너뛴다.

```bash
# 0. 붙여넣기로 올렸다면 줄바꿈부터 — Windows 를 거치면 CRLF 가 붙는다.
#    셰방이 «/bin/bash^M» 이 되어 bad interpreter 로 죽는데 chmod 로는 안 고쳐진다.
#    올릴 때마다 필요하다(git 으로 받으면 .gitattributes 가 알아서 한다).
cd src
find . -name "*.sh" -exec sed -i 's/\r$//' {} +
chmod +x *.sh

# 1. 패키지 — 공개 PyPI 가 막혀 있으면 Nexus 를 지정한다.
pip install -r requirements.txt \
  --index-url https://stg-nexus-genaihub.kbonecloud.com/repository/pypi/simple \
  --trusted-host stg-nexus-genaihub.kbonecloud.com
#    설치되는 것은 셋뿐이다: fastapi · uvicorn · langgraph==0.4.8
#    (Streamlit 화면까지 쓰려면 requirements-dev.txt 도. API·CLI 만 쓸 거면 불필요)

# 2. LLM 설정 — .env 하나. 워크스페이스에서도 배포 이미지에서도 같은 파일을 쓴다.
cp .env.example .env
```

### 2. `.env` 하나 — 단계(ENV_PATH)가 URL 을 고른다

행내 GenAI 플랫폼은 분석계(`…/trnn/…`)와 서빙계(`…/serv/…`)의 APIM 경로가 달라 URL 이 **두 벌**이다. 둘 다
`.env` 에 두고, 어느 것을 읽을지는 실행 단계 `ENV_PATH` 가 정한다:

| | 워크스페이스·행내 로컬 | 배포된 컨테이너 |
|---|---|---|
| `ENV_PATH` | **없음**(→ 분석계) | Jenkins 가 실제 환경변수로 `serving` 을 넣는다. 그 외 값은 전부 분석계 |
| 읽는 URL | `LLM_BASE_URL_TRNN` | `LLM_BASE_URL_SERV` |
| 읽는 키 | `LLM_API_KEY_TRNN` (없으면 `LLM_API_KEY`) | `LLM_API_KEY_SERV` (없으면 `LLM_API_KEY`) |
| `.env` 파일 | 이것 | **같은 파일** — `Dockerfile` 이 그대로 COPY 한다 |

그래서 배포용 `.env` 를 따로 만들지 않는다. `.env` 에 `ENV_PATH` 를 적지도 않는다 —
실제 환경변수가 파일보다 이기므로, 적어 두면 Jenkins 가 넣는 값과 헷갈릴 뿐이다.
어느 단계·URL 을 읽었는지는 `python -m pension_agent.env` 와 `/health` 의 `stage` 가 보여준다.

**LLM Gateway(LiteLLM)** 를 쓰는 경우는 같은 `.env` 의 구역 ② 를 채운다.
단계 구분이 없어 URL 하나(`LLM_BASE_URL`)이고 `LLM_MODEL` 을 **채운다**(`claude-sonnet-4-6`).
base_url 이 클러스터 내부 이름이라 컴퓨트 인스턴스에서는 이름이 안 풀린다 — 배포된 컨테이너
안에서만 설 수 있고, 미실측이다.

`LLM_MODEL` 이 서로 반대인 이유는 `llm.py` 의 `MODEL` 상수 주석에 있다. **SKILL.md 는 이
값을 「필수」로 적는데 그쪽은 Gateway 기준이다** — GenAI 플랫폼에서 콘솔이 알려준 모델
이름을 채워 넣으면 404 로 막힌다(실제로 그랬다).

`Dockerfile` 이 COPY 하는 설정 파일은 `.env` 하나다 — 없으면 COPY 단계에서 빌드가 실패한다
(refs/dockerfile.md). 디렉터리로는 `briefing_cache/` 가 하나 더 있고 같은 이유로 없으면
빌드가 실패한다. 미리 만들지 않고 빌드하려면 `mkdir -p briefing_cache` 로 빈 디렉터리만
만든다(아래 「미리 만들어 두기」).

```bash
# 3. 무엇이 잡혔는지 — 여기서 «프로바이더 genai · LLM 호출 가능 예» 가 나와야 한다
python -m pension_agent.env

# 4. LLM 없이 도는 검사부터. 여기서 깨지면 키를 봐도 소용없다.
python -m tests.test_api          # HTTP 스키마 계약
python -m tests.test_infra        # 429 호출 게이트
python -m tests.test_consult_agent   # 통과하면 langgraph 0.4.8 에서 그래프가 선다는 뜻

# 5. 돌려보기 — CLI 는 서버가 필요 없다
source ./cli.sh
$CA "세액공제 한도가 얼마야?"
$CA -c 198734-1205842 "이 고객 왜 관리 대상이야?"   # 브리핑 경로(LLM 9~11 연쇄)

# 6. HTTP API 로도 볼 거면 — 터미널 둘
./run_local.sh                                     # 터미널 A
./test_local.sh "IRP 수수료 부담된다는데 뭐라고 답하죠?"  # 터미널 B
```

### 막히면

| 증상 | 원인 | 조치 |
|---|---|---|
| `/bin/bash^M: bad interpreter` | CRLF | 위 0번 |
| `프로바이더 anthropic` | `.env` 가 안 읽혔거나 구역 ① 이 비어 있음 | `python -m pension_agent.env` 로 읽힌 파일 확인 |
| `Name or service not known` | DNS | `getent hosts <호스트>`. Gateway 면 클러스터 밖이라 원래 안 된다 |
| `HTTP 404` | 경로 또는 모델 | 오류에 응답 본문과 부른 URL 이 함께 찍힌다. 「Resource not found」면 `LLM_BASE_URL`, 「model_not_found」면 `LLM_MODEL` |
| `HTTP 429` | 호출이 몰림 | `.env` 에서 `LLM_MAX_CONCURRENCY=1` · `LLM_MIN_INTERVAL_SEC=1.0` 후 재시작. 서버가 Retry-After 를 주면 그만큼 쉰 뒤 재시도한다(로그에 «서버 Retry-After»). `prebuild_briefings` 는 429 면 그 자리에서 멈춘다 — 잠시 뒤 다시 실행하면 저장된 고객은 건너뛴다 |

**STG 는 분당 10회다.** 브리핑 한 편이 순차 11 회라 **한 편이 한도를 넘는다** — 게이트를
안 조이면 브리핑을 끝까지 만들 수 없다. `LLM_MAX_CONCURRENCY=1` · `LLM_MIN_INTERVAL_SEC=6.5`
로 두면 분당 9 회로 내려가 한 편이 약 70 초에 완주한다. 이 값에서는 **브리핑을 호출 중에
만들면 안 된다** — 대화 한 턴이 3~14 회를 더 쓰므로 한 번의 `/chat` 이 2 분을 넘긴다.
미리 만들어 두는 것이 선택이 아니라 전제다(아래 「미리 만들어 두기」).

`.env` 는 **프로세스 기동 때 한 번만** 읽는다. 고쳤으면 서버를 다시 띄워야 한다
(`--reload` 는 `.py` 변경만 본다).

```bash
# ── 브리핑 · 대화 · 화면
python -m pension_agent.strategy_agent.agent 이준호    # AI 브리핑 (①~⑨ 섹션)
$CA "ETF로 직접 굴리겠다고 증권사로 옮기겠다는 고객, 뭐라고 하지?"   # 단발 — 고객 화면 없이
$CA -c 198734-1205842                                 # REPL — 고객 화면이 열린 상태
$CA -c 198734-1205842 "투자성향 뭐야?" "만기 자금은?"  # 멀티턴을 한 줄로 (맥락 이어서)
streamlit run app.py                                  # 평가 대시보드 (개발용 화면)

# ── 행내 플랫폼용 HTTP API (main.py) — 실서비스가 붙는 진입점
./run_local.sh                                        # uvicorn main:app :8000
./test_local.sh "IRP 수수료 부담된다는데 뭐라고 답하죠?"   # /health + /chat 한 턴
CUSTOMER_ID=198734-1205842 ./test_local.sh "이 고객 왜 관리 대상이야?"   # 고객 화면이 열린 상태
docker build -f Dockerfile.local -t pension-agent:local .   # 외부망 로컬 빌드
#   내부망 배포 이미지는 Dockerfile (STG 기준 · PRD 는 주석 줄로 교체)

# ── 디버그: 이 답이 어디서 갈렸나 (인자 규약이 $CA 와 같다 — 모듈만 바꾸고 --debug)
$CAD --debug "세액공제 한도가 얼마야?"
$CAD --debug -c 198734-1205842                        # REPL — 턴마다 방금 턴만 찍는다
$CAD --script tax_credit_asserts_wrong --debug --show-llm   # 키 없이 재현
$CAD --list                                           # 시나리오 목록

# ── 묶음 실행: 첫 인자가 «어떤 대본», --옵션이 «얼마나 보여주나» ($CADR --help)
$CADR                                                 # cases — 검토 11케이스 + 요약표
$CADR --brief                                         # 요약표만
$CADR demo                                            # 시연 대본 16턴 (docs/DEMO_SCENARIO.md)
$CADR demo --why                                      # + 「무엇을 찾아봤나 → LLM 이 썼다」
$CADR demo --why --show-llm                           # + 폐기된 생성문까지 (왜 잘렸나)
$CADR library                                         # 고객별 시나리오 5종 (docs/DEMO_CUSTOMER_SCENARIOS.md)
$CADR library 김서연 정민석 --why                      # 이름·번호로 골라서 (옵션은 대본과 무관하게 같다)
$CADR review                                          # 중간점검 시연본 지금 판 (docs/DEMO_REVIEW.md)
$CADR review 이수민 --why                              # 고객 골라서
PENSION_TODAY=2026-09-07 $CADR qa                     # 고객 12명 예상질문 83턴 — 턴마다 «기대» 표시 (docs/QA_CUSTOMER_QUESTIONS.md)
PENSION_TODAY=2026-09-07 $CADR qa 김현수 윤가영 --why --pause=20   # 고객 골라서 · 턴 사이 20초(분당 한도 키)
$CADR --versions                                      # 중간점검본 판 이력 — 무엇을 왜 바꿨나
$CADR --diff v5 v6                                    # 두 판의 질문 차이
$CADR review@v3                                       # 옛 판 그대로 돌려보기

# ── 미리 만들어 두기 (브리핑 한 편 = 순차 LLM 11회 · 고객 블록마다 화면을 열 때 든다)
python -m scripts.prebuild_briefings                  # 9케이스 브리핑을 미리 만들어 둔다
python -m scripts.prebuild_briefings --status         # 무엇이 저장돼 있고 지금 읽히는가
python -m scripts.prebuild_briefings --clear          # 지우고 저장소를 끈다(예전 동작)

# ── 테스트 · 점검 (LLM 키 없이 돈다)
python -m tests.test_engine            # ①~⑤ 결정론 로직
python -m tests.test_support           # ⑥~⑨ 후보군 · 더미 규약 · 시효성 수치
python -m tests.test_strategy_agent    # LLM 산출 검증 · 폴백
python -m tests.test_consult_agent     # 라우팅 · 도구 루프 · 재계획 · 하지말것 가드
python -m tests.test_infra             # 공용 인프라 · 임포트 경계 · 429 호출 게이트
python -m tests.test_api               # HTTP 진입점 — 플랫폼 I/O 스키마 계약
python -m tests.debug.test_trace       # 트레이스 — 노드 · 게이트 · 폐기 사유
python -m scripts.kb_build.test_paths  # 경로 · locator 실재
python -m pension_agent.knowledge.schema validate pension_agent   # 전 데이터 검증
python -m pension_agent.knowledge.kb                              # 지식베이스 리포트

# ── 재생성 (생성물은 손으로 고치지 않는다 — 생성기를 고친다)
python -m scripts.kb_build.build_kb [--activate]   # 06_주제별_추출지식 → 카드
python -m scripts.import_targets                   # 타겟 룰베이스 xlsx → targets.json
python -m scripts.demo_status                      # docs/DEMO_STATUS.md 갱신
```

### 미리 만들어 두기 — 배포 이미지에 브리핑을 넣는다

대화형은 브리핑이 **이미 있다고 보고** 답한다(고객 재료 도구가 `strategy_agent.propose()`
를 부른다). 그런데 브리핑 한 편이 순차 LLM 11 회라, 배포한 컨테이너가 그것을 직접 만들면
외부 호출마다 그 시간을 문다. STG 는 분당 10 회라 애초에 한 편이 완주하지 못한다.
`briefing_cache/` 를 이미지에 함께 넣는 이유다.

```bash
# 1. 게이트를 STG 한도(분당 10회) 아래로. .env 는 기동 때 한 번만 읽는다.
#    LLM_MAX_CONCURRENCY=1 · LLM_MIN_INTERVAL_SEC=6.5
# 2. 코드·데이터를 확정한다 — 이 뒤로 한 줄이라도 고치면 지문이 어긋나 저장분이 버려진다
python -m scripts.prebuild_briefings          # 고객당 11회 · 12명이면 20분 넘게 걸린다
python -m scripts.prebuild_briefings --status # 전원 «있음(지문 일치)» 인지 확인하고 빌드한다
docker build -f Dockerfile -t pension-agent .
```

지문에는 **오늘 날짜**가 들어간다(잔여일수·미접촉 일수가 오늘 기준이다). 그래서 같은
이미지를 다음 날 부르면 구워 넣은 저장분은 버려지고 고객당 첫 호출이 다시 11 회를 치른다.
그 11 회는 **컨테이너 안에 저장돼** 두 번째 호출부터는 읽힌다(디렉터리가 이미지에 있으므로
저장소가 켜져 있다). 다음 날 첫 호출까지 빠르길 원하면 `.env` 에 `PENSION_TODAY` 를 고정해
빌드한다 — 대신 만기 D-day·연말까지 며칠이 그 날짜로 굳는다.

**지금 무엇이 읽히고 있는지는 `/health` 의 `briefing_cache` 가 답한다.**

```json
{"enabled": true, "stored": 12, "usable": 12, "writable": true, "today": "2026-09-08"}
```

`usable` 이 `stored` 보다 작으면 그만큼이 낡은 지문이다(날짜가 넘어갔거나 코드·데이터를
고치고 다시 만들지 않았다). `usable: 0` 이면 구워 넣은 것이 하나도 안 읽히는 상태다.
`writable: false` 면 런타임에 만든 브리핑을 저장하지 못해 **재기동할 때마다** 처음부터
다시 만든다. 셋 다 답변은 정상으로 나가고 화면에는 «느리다»로만 보이므로 여기서 가른다.

**고객 지정(`-c/--customer`)** 은 고객 id(KB-PIN)다. 없으면 브리핑질의·LMS발송·수정이
"고객 화면을 먼저 열어주세요"로 답한다. id 는 `strategy_agent/customer.py` 의 `PERSONAS`
(예: 이준호=`198734-1205842`). 실행 조합은
[consult_agent/README.md](pension_agent/consult_agent/README.md).

### 디버그 모드

`tests/debug/` 는 파이프라인을 **밖에서 감싸서 보기만** 한다 — 값은 그대로 통과시키고
운영 코드는 고치지 않는다.

**두 CLI 의 옵션은 이름이 같으면 뜻도 같다.** `$CAD`(턴 하나를 파고든다)와
`$CADR`(대본을 묶어 돌린다)는 보는 단위가 달라 붙는 옵션도 다르다.

| 옵션 | $CAD | $CADR |
|---|---|---|
| `--debug` | 답변 아래에 **전체 트레이스** (REPL 에서는 방금 턴만) | 없다 — 트레이스는 `cases` 에 기본으로 붙는다 |
| `--why` | 없다 | 턴마다 «무엇을 찾아봤나 → LLM 이 몇 자 썼나» 한 묶음 |
| `--show-llm` | compose 가 LLM 에게 받은 문장을 폐기됐어도 그대로 | 같다 (`--why` 를 함께 켠다) |
| `--brief` · `--time` | 없다 | 요약표만 · 턴별 소요 시간 |
| `--script N` | 캔드 LLM 시나리오 — API 키 없이 돈다 (`--list` 로 7종 확인) | 없다 |
| `--any-customer` | 이 체크아웃에 없는 고객 id 로도 진행(경고만) | 없다 |

`$CADR` 의 `--why` 는 예전 이름이 `--debug` 였다 — 같은 이름이 두 CLI 에서 다른 것
(전체 트레이스 / 재료 한 줄 로그)을 가리켜 갈랐다. 대본 선택도 예전에는
`--demo`·`--scenario`·`--final` 이라 옵션처럼 생겼는데, 지금은 첫 인자에 이름으로 쓴다
(`demo` · `library` · `review`). 예전 이름을 넣으면 바뀐 이름을 알려주고 멈춘다.

트레이스는 노드 순서·상태 변화, LLM 호출 자리(understand·plan·clarify·tools·select),
그리고 compose 게이트 `verify_texts`(원장 밖 수치) → `relations`(근거와의 관계) → `span`
을 찍는다. **앞에서 끊기면 뒤는 아예 안 불리고, 그게 진단의 핵심이다.**

없는 고객 id 는 시작할 때 끊는다 — 그냥 두면 «재료 0건»이 되어 오타와 구분되지 않는다.

### 관측 (Langfuse)

`--debug` 트레이스가 **한 턴을 이 화면에서** 보는 것이라면, Langfuse 는 **여러 실행을
쌓아 두고 나중에** 보는 것이다. 브리핑 한 건이 LLM 11회, 대화 한 턴이 4~7회라 "어제 그
답이 왜 그랬지"를 로그로 되짚기 어렵다 — 트레이스 하나(브리핑 한 건 · 대화 한 턴) 아래
그 호출들이 프롬프트·응답·토큰·소요시간과 함께 묶여 남는다.

무엇이 나가나 — 네 가지다.

| | |
|---|---|
| **트레이스** | 대화 한 턴(`consult.turn`) · 브리핑 한 건(`briefing.generate`). 세션 id 로 같은 상담의 턴들이 이어지고, user 는 **고객 id** 라 「이 고객 관련 실행 전부」를 한 번에 부른다 |
| **span** | LLM 호출이 아닌 단계 — `tool:<도구명>`(무엇을 어떤 질의로 불러 몇 건 얻었나 · 원문 재검색으로 건졌나) · `compose`(몇 번 다시 썼나 · 무엇에 걸렸나) |
| **generation** | LLM 호출 한 건. 프롬프트·응답·모델·토큰·소요시간. span 안에서 난 호출은 그 밑에 붙어 **트레이스가 실행 구조를 닮은 트리**가 된다 |
| **score** | 이 실행이 어땠나. 아래 표 |

점수는 **전부 코드가 아는 사실**이다 — LLM 이 자기 답을 채점하지 않는다.

| 점수 | |
|---|---|
| `turn_outcome` | `answer` · `clarify`(되묻기) · `llm_down`. 「되묻기가 몇 %인가」 |
| `evidence_count` | 그 턴이 모은 근거 수. 0건이 잦으면 계획·검색을 봐야 한다 |
| `compose_passed` | 작성이 게이트를 통과했나(0 이면 근거 원문이 나갔다). comment 에 **무엇에 걸렸는지** |
| `compose_retries` | 재작성 횟수 |
| `briefing_tier` | `행내전략` · `LLM판단` · `미매칭`. comment 에 사유 |
| `briefing_source` | `LLM` · `규칙` · `미생성` |
| `sections_skipped` | LLM 이 못 채운 섹션 수. comment 에 어느 섹션인지 |

「지난 30번 중 몇 번이 게이트에 걸렸나」는 트레이스를 한 건씩 열어서는 못 센다 — 그 집계를
대시보드에 맡기는 것이 점수다.

**한 고객의 실행 전부를 보려면** — 브리핑과 대화 턴이 셋으로 함께 묶여 있다.

| 대시보드에서 | 쓰는 것 |
|---|---|
| **Users** 탭 → 그 고객 행 | `userId` = `김서연(171203-4815062)`. **이름이 목록에 그대로 뜬다** — 그 탭은 이 문자열 하나만 보여주고 이름을 담을 칸이 따로 없어서 id 와 함께 넣는다 |
| **Tracing** → Filter → `Tags` = `고객:김서연` | 이름으로 거를 때 |
| **Tracing** → Filter → `User ID` | 위 Users 탭과 같은 것을 필터로 |

**고객 «상태»로도 거른다.** 트레이스에 그 고객의 성립 요건이 `요건:idl` · `요건:hlt` 처럼
태그로 붙는다(판정은 strategy_agent 것을 그대로 옮긴다 — 두 번 구현하지 않는다). 대화 턴에는
`intent:situation` 도 붙는다. 「어떤 상태의 고객에게 무슨 일이 생기나」가 이 저장소에서 가장
쓸모 있는 축이라 태그로 둔다 — 메타데이터에만 있으면 한 건씩 열어야 보인다.

| 물음 | 거르는 법 |
|---|---|
| 지식베이스에 **없는 것**은 무엇인가 | `evidence_count = 0` 인 트레이스의 질문들. **저작 우선순위가 여기서 나온다** |
| 어떤 고객군에서 답이 게이트에 걸리나 | `요건:hlt` + `compose_passed = 0` |
| 어떤 고객군에서 질문이 모호해지나 | `요건:idl` + `turn_outcome = clarify` |
| 어떤 도구가 자주 헛도나 | span `tool:*` 의 `found = false` |
| 계획이 고른 질의가 얼마나 빗나가나 | span `tool:*` 의 `retried = true` 비율 |

**지금 쌓인 것으로 «고객군별 질문량»을 결론내지 않는다.** 시연 목업 12명에 리허설을 몇 번
돌렸는가가 그대로 분포로 보인다 — 실직원이 쓰기 시작해야 의미가 생기는 축이다.

`LANGFUSE_CAPTURE_CONTENT=0` 이면 **이름이 전부 빠지고 id 만 남는다**(`user_id` · 태그 ·
메타데이터). 그 스위치는 «개인정보를 내보내지 않는다»는 약속이라, 본문만 가리고 이름을
태그로 내보내면 약속이 거짓이 된다.

브리핑(`briefing.generate`)과 대화 턴(`consult.turn`)이 **같은 id · 같은 태그**로 붙으므로
셋 중 무엇으로 걸러도 그 고객의 브리핑 한 건과 대화 턴들이 한 목록에 선다. 한 상담만
따로 보려면 거기서 `Session` 을 좁힌다 — 세션은 실행마다 갈린다(`debug-…` · `streamlit-…`).

**`src/.env`** 에 키 두 개를 넣으면 켜진다(저장소 루트가 아니라 `src/` 다).
**없으면 통째로 꺼진다** — 테스트·시연은 그대로 돈다.

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com   # 자체 호스팅이면 그 주소
```

**대시보드에 안 찍히면 먼저 이것을 돌린다** — 설정을 찍고 이벤트 한 건을 실제로 보낸다.

```bash
python -m pension_agent.observability
```

`.env` 를 읽었는지 · 키가 들어왔는지 · 어느 host 로 보내는지 · 전송이 성공했는지를
한 번에 가른다. 흔한 원인 넷은 **`.env` 를 저장소 루트에 둠** · **키를 안 넣음** ·
**host 지역이 다름**(Langfuse Cloud 는 EU `https://cloud.langfuse.com` · US
`https://us.cloud.langfuse.com` 로 갈리고, 다른 지역 키로는 401 이 난다) ·
**망이 막힘**이다.

| 환경변수 | |
|---|---|
| `LANGFUSE_ENABLED=0` | 키가 있어도 끈다 |
| `LANGFUSE_CAPTURE_CONTENT=0` | 프롬프트·응답 본문을 보내지 않고 길이만 남긴다 |
| `LANGFUSE_ENVIRONMENT` | 기본 `demo`. 시연/스테이징/운영 구분 |
| `LANGFUSE_RELEASE` · `LANGFUSE_MAX_CHARS` · `LANGFUSE_TIMEOUT` · `LANGFUSE_DEBUG` | 버전 태그 · 본문 상한(20000자) · 전송 타임아웃(10초) · 전송 실패를 stderr 로 |

- **의존성을 늘리지 않는다.** langfuse SDK 대신 표준 라이브러리로 수집 API 를 부른다 —
  사내 genai 경로가 urllib 만 쓰는 것과 같은 이유다(망분리).
- **에이전트를 세우지 않는다.** 전송은 백그라운드 워커가 하고, 큐가 차면 이벤트를 버리며,
  전송 실패는 예외로 올라오지 않는다. 관측이 죽어서 상담이 죽지 않는다.
- **삼키되 침묵하지는 않는다.** 첫 실패는 stderr 에 한 줄 남고(두 번째부터는 잠잠하다 —
  턴마다 쌓이면 그게 다시 노이즈다), 원인은 `observability.last_error()` 에 남는다.
  전부 보려면 `LANGFUSE_DEBUG=1`.
- **프롬프트에는 고객 원장이 실린다.** 지금 고객은 시연용 목업이라 그대로 보내지만,
  실데이터 전환 때 정할 것은 [../docs/PRODUCTION_RISKS.md](../docs/PRODUCTION_RISKS.md) §9.

### WorkB 쪽지 발송 붙이기

직원이 「쪽지로 보내줘」라고 하면 에이전트가 초안을 세우고, 승낙하면 행내 메신저(WorkB)로
보낸다. **보내는 쪽은 주입받는다** — 행내 MCP 클라이언트(`mcp_sdk`)는 저장소 밖 패키지라
여기서 임포트하면 그 패키지 없이는 테스트도 임포트도 안 되기 때문이다(망분리 밖에서는 설치도
못 한다). 앱 시작 시 한 번 등록한다:

```python
from pension_agent import workb
workb.use_sender(MCPClient(emp_no).send_message)   # send(recipients: list[str], title, body)
```

등록하지 않으면 **보내지 않고 «미연결»이라고 답한다** — 조용히 성공처럼 끝나지 않는다.
받는 사람은 로그인 사번(`AgentState["employee_id"]`)이고, 없으면 `WORKB_EMP_NO` 환경변수,
그것도 없으면 발송을 제안하지 않는다. 다른 직원에게 보내는 것은 직원이 **사번을 적었을
때만**이다(「사번 3902172한테 쪽지로 보내줘」).

| 환경변수 | |
|---|---|
| `WORKB_EMP_NO` | 로그인 사번이 없을 때의 수신자 사번(개발·시연용 폴백) |

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

- **재계획.** 근거를 못 낸 호출을 장부(`state["steps"]`)에서 뽑아 계획에 싣고, 근거 0건인 채 끝내려 하면
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
│  ├─ env.py               .env 로딩 — 설정 환경변수의 단일 출처
│  ├─ llm.py               프로바이더 전환식 클라이언트 (환경 이전 시 여기만)
│  ├─ observability.py     Langfuse 관측 — LLM 호출을 트레이스로 묶어 보낸다 (키 없으면 꺼짐)
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
- **출처는 원본 문서 이름으로 말한다.** `knowledge/kb.py::origin_of()` 한 곳에서만
  만들고, 못 찾으면 "확인 필요"라고 한다. 적재 json 의 이름표로 물러서지 않는다.
- **되돌릴 수 없는 행위는 사람이 정한다.** 에이전트는 제안(`act.offer`)하고, 발송은 확인을
  한 턴 거친다(`act.confirm_action`). 편집 가능 범위도 코드가 못박는다
  (`EDITABLE_FIELDS` — 코드가 계산한 수치·상품명은 대화로 못 고친다).
