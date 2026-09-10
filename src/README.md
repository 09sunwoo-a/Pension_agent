# 퇴직연금 에이전트 (Pension Agent)

퇴직연금(IRP) 상담 전, 직원에게 **이 고객에게 무엇을 어떻게 제안할지**를 정리해 주는
에이전트 묶음. **경계는 코드가 정하고 LLM 은 그 안에서만 말한다**([../CLAUDE.md](../CLAUDE.md) §2).
요건 기준은 [../docs/REQUIREMENTS.md](../docs/REQUIREMENTS.md) 하나다.

이 문서는 **설치 · 설정 · 실행 · 배포** 순서다. 설계 원칙은 `CLAUDE.md`(루트 ·
`pension_agent/consult_agent/`), 패키지 구조는 [pension_agent/README.md](pension_agent/README.md),
지식 저작은 [AUTHORING.md](AUTHORING.md) 에 있다.

## 1. 설치

명령은 **전부 `src/` 에서** 실행한다 — 그래야 `src/` 가 임포트 루트가 된다.

```bash
cd src
pip install -r requirements.txt      # 행내 배포 이미지와 같은 목록 (Python 3.10)
                                     #   fastapi · uvicorn · python-dotenv · langgraph==0.4.8
pip install streamlit pandas anthropic openpyxl   # 개발용 — Streamlit 화면 · 사외 프로바이더 · xlsx 변환기
```

개발용 패키지 목록과 외부망 Dockerfile 은 저장소에 넣지 않는다(`requirements-dev.txt` ·
`Dockerfile.local` — `.gitignore`). 각자 로컬에 둔다.

행내에서 공개 PyPI 가 막혀 있으면 Nexus 를 지정한다.

```bash
pip install -r requirements.txt \
  --index-url https://stg-nexus-genaihub.kbonecloud.com/repository/pypi/simple \
  --trusted-host stg-nexus-genaihub.kbonecloud.com
```

폴더를 **복사해 올린** 경우(Azure ML 컴퓨트 인스턴스 등)는 Windows 를 거치며 붙은 CRLF 를
먼저 벗긴다 — `find . -name "*.sh" -exec sed -i 's/\r$//' {} + && chmod +x *.sh`.
git 으로 받으면 `.gitattributes` 가 알아서 한다.

## 2. 설정 — `.env` 하나

```bash
cp .env.example .env                 # 어디서나 이 파일 하나 — 구역 ①행내 ②Gateway ③사외 중 하나를 채운다
python -m pension_agent.env          # 어느 파일이 읽혔고 어느 프로바이더가 잡혔나
```

무엇을 채우고 무엇을 비워 두는지는 [.env.example](.env.example) 머리말이 전부 말한다.
`.env` 는 **프로세스 기동 때 한 번만** 읽는다 — 고쳤으면 서버를 다시 띄운다(`--reload` 는
`.py` 변경만 본다).

## 3. 실행

```bash
source ./cli.sh                      # CA · CAD · CADR 정의 + 사용법 출력
```

셋 다 **HTTP 를 타지 않고** `graph.ask()` 를 직접 부르므로 서버와 무관하고, `.env` 만 잡혀
있으면 행내에서도 사외에서와 똑같이 돈다.

```bash
CA="python -m pension_agent.consult_agent"     # 상담 대화 (LangGraph)
CAD="python -m tests.debug"                    # 같은 것 + 트레이스
CADR="python -m tests.debug.reps"              # 대표 질문 묶음 (검토 · 시연 대본)
```

**처음이라면 이 순서로 확인한다.** LLM 없이 도는 검사에서 깨지면 키를 봐도 소용없다.

```bash
python -m pension_agent.env          # «프로바이더 genai · LLM 호출 가능 예» 가 나와야 한다
python -m tests.test_api             # HTTP 스키마 계약
python -m tests.test_infra           # 429 호출 게이트
python -m tests.test_consult_agent   # 통과하면 langgraph 0.4.8 에서 그래프가 선다는 뜻
$CA "세액공제 한도가 얼마야?"
$CA -c 198734-1205842 "이 고객 왜 관리 대상이야?"   # 브리핑 경로(LLM 9~11 연쇄)
```

**고객 지정(`-c/--customer`)** 은 고객 id(KB-PIN)다. 없으면 브리핑질의·LMS발송·수정이
"고객 화면을 먼저 열어주세요"로 답한다. id 는 `strategy_agent/customer.py` 의 `PERSONAS`
(예: 이준호=`198734-1205842`).

```bash
# ── 브리핑 · 대화 · 화면
python -m pension_agent.strategy_agent.agent 이준호    # AI 브리핑 (①~⑨ 섹션)
$CA "ETF로 직접 굴리겠다고 증권사로 옮기겠다는 고객, 뭐라고 하지?"   # 단발
$CA -c 198734-1205842                                 # REPL — 고객 화면이 열린 상태
$CA -c 198734-1205842 "투자성향 뭐야?" "만기 자금은?"  # 멀티턴을 한 줄로 (맥락 이어서)
streamlit run app.py                                  # 개발·테스트 화면

# ── 행내 플랫폼용 HTTP API (main.py) — 실서비스가 붙는 진입점
./run_local.sh                                        # uvicorn main:app :8000
./test_local.sh "IRP 수수료 부담된다는데 뭐라고 답하죠?"   # /health + /chat 한 턴
CUSTOMER_ID=198734-1205842 ./test_local.sh "이 고객 왜 관리 대상이야?"

# ── 디버그: 이 답이 어디서 갈렸나 (인자 규약이 $CA 와 같다)
$CAD --debug "세액공제 한도가 얼마야?"
$CAD --debug -c 198734-1205842                        # REPL — 턴마다 방금 턴만 찍는다
$CAD --script tax_credit_asserts_wrong --debug --show-llm   # 캔드 시나리오 — 키 없이 재현
$CAD --list                                           # 시나리오 7종

# ── 묶음 실행: 첫 인자가 «어떤 대본», --옵션이 «얼마나 보여주나» ($CADR --help)
$CADR                                                 # cases — 검토 11케이스 + 요약표
$CADR demo --why                                      # 시연 대본 16턴 (docs/DEMO_SCENARIO.md)
$CADR library 김서연 정민석 --why                      # 고객별 시나리오 5종 (docs/DEMO_CUSTOMER_SCENARIOS.md)
$CADR review 이수민 --why                              # 중간점검 시연본 (docs/DEMO_REVIEW.md)
PENSION_TODAY=2026-09-07 $CADR qa --pause=20          # 예상질문 83턴 (docs/QA_CUSTOMER_QUESTIONS.md)
$CADR --versions · --diff v5 v6 · review@v3           # 중간점검본 판 이력 · 차이 · 옛 판

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

# ── 재생성 (생성물은 손으로 고치지 않는다 — 생성기를 고친다)
python -m scripts.kb_build.build_kb [--activate]   # 06_주제별_추출지식 → 카드
python -m scripts.import_targets                   # 타겟 룰베이스 xlsx → targets.json
python -m scripts.demo_status                      # docs/DEMO_STATUS.md 갱신
```

### 디버그 옵션

`$CAD` 는 턴 하나를 파고들고 `$CADR` 은 대본을 묶어 돌린다. 이름이 같으면 뜻도 같다.

| 옵션 | $CAD | $CADR |
|---|---|---|
| `--debug` | 답변 아래에 **전체 트레이스** (REPL 에서는 방금 턴만) | 없다 — `cases` 에 기본으로 붙는다 |
| `--why` | 없다 | 턴마다 «무엇을 찾아봤나 → LLM 이 몇 자 썼나» |
| `--show-llm` | compose 가 LLM 에게 받은 문장을 폐기됐어도 그대로 | 같다 (`--why` 를 함께 켠다) |
| `--brief` · `--time` · `--pause=N` · `--retry-down=N` | 없다 | 요약표만 · 턴별 소요 시간 · 턴 사이 대기 · LLM 실패 턴 재시도 |
| `--script N` · `--list` | 캔드 LLM 시나리오 — API 키 없이 돈다 | 없다 |
| `--any-customer` | 이 체크아웃에 없는 고객 id 로도 진행(경고만) | 없다 |

트레이스는 노드 순서 · LLM 호출 자리 · compose 게이트 `verify_texts` → `relations` → `span`
을 찍는다. **앞에서 끊기면 뒤는 아예 안 불리고, 그게 진단의 핵심이다.**

## 4. 막히면

| 증상 | 원인 | 조치 |
|---|---|---|
| `/bin/bash^M: bad interpreter` | CRLF | §1 의 `sed` 한 줄 |
| `프로바이더 anthropic` | `.env` 가 안 읽혔거나 구역 ① 이 비어 있음 | `python -m pension_agent.env` 로 읽힌 파일 확인 |
| `Name or service not known` | DNS | `getent hosts <호스트>`. Gateway 면 클러스터 밖이라 원래 안 된다 |
| `HTTP 404` | 경로 또는 모델 | 오류에 응답 본문과 부른 URL 이 찍힌다. 「Resource not found」면 `LLM_BASE_URL`, 「model_not_found」면 `LLM_MODEL` |
| `HTTP 429` | 호출이 몰림 | `.env` 「호출 게이트」 구역 — 간격을 늘리거나(`LLM_MIN_INTERVAL_SEC`) 버킷을 나눈다(`LLM_CLIENT_USER_SPREAD`). 재시작 필요 |

**STG 는 분당 10회다.** 브리핑 한 편이 순차 11회라 한 편이 한도를 넘는다 —
`LLM_MAX_CONCURRENCY=1` · `LLM_MIN_INTERVAL_SEC=6.5` 로 조이면 한 편이 약 70초에 완주하고,
그 속도에서는 호출 중에 브리핑을 만들 수 없으니 **미리 만들어 둔다**(§5).

## 5. 브리핑을 미리 만들어 둔다

대화형은 브리핑이 **이미 있다고 보고** 답한다. 배포 컨테이너가 직접 만들면 고객당 순차
LLM 11회를 외부 호출마다 물기 때문에 `briefing_cache/` 를 이미지에 함께 넣는다. 왜 그런지,
무엇이 저장되고 안 되는지는 `scripts/prebuild_briefings.py` 머리말과 `.gitignore` 주석에 있다.

```bash
# 게이트를 STG 한도 아래로 조인 뒤(§4), 코드·데이터를 확정하고
python -m scripts.prebuild_briefings          # 고객당 11회 · 12명이면 20분 넘게 걸린다
python -m scripts.prebuild_briefings --status # 전원 «있음(지문 일치)» 인지 확인
git add briefing_cache && git commit          # 커밋 전에 읽는다 — 그 파일이 곧 화면에 뜨는 문장이다
```

- 코드·데이터를 한 줄이라도 고치면 지문이 어긋나 저장분이 버려진다 — 다시 만들어 다시 커밋한다.
- 지문에는 **오늘 날짜**가 들어간다. 같은 이미지를 다음 날 부르면 고객당 첫 호출이 다시
  11회를 치른다(컨테이너 안에 저장되어 두 번째부터는 읽힌다). 피하려면 `.env` 에
  `PENSION_TODAY` 를 고정해 빌드한다 — 대신 만기 D-day 가 그 날짜로 굳는다.
- 지금 무엇이 읽히는지는 `/health` 의 `briefing_cache` 가 답한다:
  `{"enabled": true, "stored": 12, "usable": 12, "writable": true, "today": "2026-09-08"}`.
  `usable < stored` 면 그만큼이 낡은 지문, `usable: 0` 이면 하나도 안 읽히는 상태,
  `writable: false` 면 재기동할 때마다 처음부터 다시 만든다. 셋 다 답변은 정상으로 나가고
  화면에는 «느리다»로만 보인다.

## 6. 배포

```bash
docker build -f Dockerfile -t pension-agent .   # 내부망 (STG 기준 · PRD 는 주석 줄로 교체)
```

이미지가 COPY 하는 설정 파일은 `.env` 하나다 — 없으면 빌드가 실패한다. 배포용 `.env` 를
따로 만들지 않는다 — 워크스페이스에서 쓰던 파일이 그대로 들어가고, 어느 URL 을 읽을지는
Jenkins 가 넣는 환경변수가 정한다(`Dockerfile` 주석). 플랫폼 규격(I/O 스키마 · Dockerfile ·
승인 패키지)은 [../skills/genai-platform-agent-dev/refs/](../skills/genai-platform-agent-dev/refs/).

## 7. 관측 (Langfuse)

`.env` 에 `LANGFUSE_PUBLIC_KEY` · `LANGFUSE_SECRET_KEY` 를 넣으면 켜지고, **없으면 통째로
꺼진다**(테스트·시연은 그대로 돈다). 브리핑 한 건 · 대화 한 턴이 트레이스 하나로 묶이고,
점수는 전부 코드가 아는 사실이다. 환경변수 목록과 설계는 `pension_agent/observability.py` 머리말.

```bash
python -m pension_agent.observability   # 대시보드에 안 찍히면 — 설정을 찍고 이벤트 한 건을 실제로 보낸다
```

프롬프트에는 고객 원장이 실린다 — 실데이터 전환 때 정할 것은
[../docs/PRODUCTION_RISKS.md](../docs/PRODUCTION_RISKS.md) §9.

## 8. 행내 MCP — WorkB 쪽지 발송 붙이기

`.env` 에 셋을 넣으면 붙는다. 진입점(`main.py`·`app.py`)이 기동할 때 `mcp.install()` 을
한 번 부르고, 그 뒤 승낙받은 쪽지는 MCP 도구 `send_memo` 로 나간다.

```bash
MCP_SERVER_URL=...      # 게이트웨이 주소 (분석계·서빙계가 갈리면 _TRNN · _SERV 두 벌)
MCP_USER_ID=...         # 발급받은 클라이언트 id
MCP_SECRET_KEY=...      # 발급받은 시크릿 키
WORKB_EMP_NO=3902172    # 로그인 사번이 없을 때의 폴백. 이 값이 «보내는 주체»다
```

```bash
python -m pension_agent.mcp          # 지금 붙는지 — 설정·패키지·서버·도구 목록을 한 화면에
curl -s localhost:8000/health | jq .mcp
```

- 설정이 하나라도 비면 **보내지 않고 «미연결»이라고 답한다**(본문은 그대로 만든다).
  행내 패키지(`mcp_sdk`·`langchain-mcp-adapters`)가 없는 환경도 같다 — 그래서 사외 개발
  PC 와 테스트는 이 설정 없이 그대로 돈다(`requirements.txt` 의 주석 참고).
- 받는 사람은 로그인 사번이고, 없으면 `WORKB_EMP_NO`, 그것도 없으면 발송을 제안하지
  않는다. 다른 직원에게 보내는 것은 직원이 **사번을 적었을 때만**이다.
- **로그인 사번은 호출이 넘겨준다.** 게이트웨이의 `x_client_user` 는 「사번 7자리 +
  uuid」꼴이라(`3902172-550e8400-…`) **앞 7자리**를 사번으로 읽는다. 앞이 숫자 7자리가
  아니면 읽지 않는다 — 그 값은 LLM 쿼터 버킷 이름이기도 해서 `pension-agent` 같은 값도
  들어온다. 사번을 다른 데서 받는 배포는 `input_value` 에 `employee_id` 를 실으면 그것이
  먼저다. 진입점 → 상태 → 발송이 같은 값을 보고, 그 값이 **누구 이름으로 나가나**
  (MCP 인증·행내 감사 기록)도 정한다. 요청 로그의 `emp_no=` 로 확인한다 — `-` 면
  환경변수 폴백이다.
- **발송은 재시도하지 않는다.** 타임아웃은 «안 나갔다»가 아니라 «나갔는지 모른다»이고,
  다시 부르면 같은 쪽지가 두 통 간다. 붙는 단계(토큰·도구 목록)의 실패만 다시 시도한다.
- 다른 전송 수단을 끼우려면 `workb.use_sender(fn)` 로 직접 등록한다 — `install()` 이
  하는 일이 그 등록이다(`send(recipients: list[str], title, body)`).

**다른 행내 기능(사내 DB·메일·뉴스…)을 붙일 때** 고치는 자리는 셋으로 갈라 뒀다 —
서버 표(`pension_agent/mcp/servers.py`) · 연결과 호출(`client.py`, 고치지 않는다) ·
도구 어댑터(`workb.py` 같은 파일 하나). 자세한 것은 `pension_agent/mcp/__init__.py` 머리말.
