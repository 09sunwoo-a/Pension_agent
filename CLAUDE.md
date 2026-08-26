# 퇴직연금 AI 사후관리 에이전트 — 작업 규칙

KB국민은행 퇴직연금·개인형IRP 지식베이스(`01`~`09` 폴더)와 그것을 쓰는 에이전트
(`src/pension_agent/`).

## 이 저장소의 지금 상태

**발표용 내부 데모다. 실데이터 전환 전이다.**

- 고객은 시연용 목업 9케이스다 — 원본은 저장소 루트 `IRP_Agent_더미고객_9Cases_v3.xlsx`,
  `scripts/import_customers.py` 가 `customers.json` 으로 내리고 `customer.py` 가 Profile 로
  매핑한다(고칠 값은 xlsx 에 넣고 재생성). 타겟 선정 룰베이스는 더미가 아니다 — 기획자가
  행내 원문을 정규화해 확인해준 표다(`targets.json`). 기준일은 `customer.TODAY`(=원장 기준일 2026-08-24)
  고정, 안내 콘텐츠 일부는 지어낸 더미, 금리는 `market/rates_demo.json` 자리표시자다.
- **화면에는 더미 표시를 붙이지 않는다**(발송문 포함). 발표 산출물에 딱지를 남기지 않기로
  했다. 대신 무엇이 더미인지는 `docs/DEMO_STATUS.md` 가 전담한다.
- `src/app.py`(Streamlit)는 개발·테스트용 화면이다. 실서비스 프론트는 따로 만든다.
- 지금 무엇이 더미이고 무엇이 소스 미확정인지 알고 싶으면:

      cd src && python -m scripts.demo_status     # docs/DEMO_STATUS.md 갱신

## 판단의 근본

**행내 행원들이 정리해둔 지식베이스가 기준이다.** 에이전트가 무엇을 권하고 무엇을 금지할지는
전부 거기서 나온다.

- 지식베이스에 없는 기준은 **만들지 않는다.** 재료가 없는 요건에는 아무것도 띄우지 않는다
  (집행: `consult_agent/CLAUDE.md` §8 금지·주의 안내).
- 값을 특정하지 못하면 지어내지 않고 `source_text` 로 원문 표기를 남기고 리포트에 올린다.

**«누구를 타겟으로 볼 것인가»는 타겟 룰베이스가 정한다.** 기획자가 행내 원문(IRP 텐션
UP-②③④⑤)을 읽고 타겟 14종으로 정규화해 확인해준 표다 — 원본은 저장소 루트
`IRP_타겟고객_룰베이스_v1.xlsx`, `scripts/import_targets.py` 가 `targets.json` 으로 내린다.
요건 임계값이 이 표와 어긋나면 **코드가 틀린 것**이고, `customer.py` 의 각 임계값 상수는
근거 TARGET_ID 를 주석에 단다.

표는 스스로 근거등급을 밝힌다 — **A**(원문에 임계값이 그대로 있음) · **B**(A 의 상위 통합) ·
**C**(이탈고객 조사 «비중»이며 개인 임계값이 아님) · **D**(원문에 없는 기획자 설계 제안,
Pilot). D 를 A 와 같은 얼굴로 화면에 세우면 «행내 기준»으로 오해된다. 지금 코드가 D 에
기대는 자리는 `docs/DEMO_STATUS.md` §7 이 집계한다.

## 절대 규칙

1. **원문은 고치지 않는다.** 카드의 `quotes`·`source_text` 는 행내 문서의 원문 인용이다.
   여기를 고치면 "출처는 진짜인데 수치는 가짜인 카드"가 되고, 그게 신뢰 표시가 거짓말하는
   가장 위험한 형태다. 시효성 수치는 원문 대신 **파생 텍스트**(`content`·`summary`·
   `key_points`·`dialogue`)를 바꾸거나 참고 표시를 얹는다 → `consult_agent/kb.py`.

2. **코드 = 근거의 경계와 계산 / LLM = 경계 안에서의 계획과 표현.**

   원래 이 규칙은 "코드=사실 / LLM=표현"이었다. 계획 루프가 들어오면서 LLM 이 **문장만**
   쓰는 게 아니라 **어떤 근거를 모을지도** 정하게 됐다. 그래도 규칙의 알맹이는 같다 —
   LLM 은 경계를 넓힐 수 없고, 경계를 정하는 것은 전부 코드다.

   | | 누가 정하나 |
   |---|---|
   | 어떤 도구를 어떤 질의로 부를지, 이제 충분한지 | LLM (`nodes/plan.py::plan_step`) |
   | 부를 수 있는 도구가 무엇인지 | 코드 (`consult_agent/tools.py::TOOLS`) |
   | 몇 바퀴까지 도는지 · 같은 호출 반복 차단 | 코드 (`plan.MAX_STEPS`) |
   | 수치·상품·적합성 계산 | 코드 (`strategy_agent`) |
   | 답변 문장 | LLM |
   | 답변이 근거 밖으로 나갔는지 | 코드 (`pension_agent/verify.py`) |

   근거는 턴마다 **원장**(`state["evidence"]`)에 쌓이고 답변은 그 안에서만 나온다.
   `verify_texts()` 가 원장을 재료로 보고 대조한다 — 원장 밖 수치는 답변에서 잘린다.

   이 경계가 consult_agent 안에서 어떻게 지켜져야 하는지는
   `src/pension_agent/consult_agent/CLAUDE.md`(기준서)가 말한다 — 구현이 그 문서와
   어긋나면 구현이 틀린 것이다. 새 기능은 도구 하나를 추가하는 일이고, enum·분기표·노드를
   함께 늘리지 않는다.

3. **생성 JSON 을 손으로 고치지 않는다.** `src/pension_agent/knowledge/data/kb_*.json` 은 전부
   `scripts/kb_build/build_kb.py` 의 산출물이다. 고칠 값은 `scripts/kb_build/config.py` 에 넣고
   다시 생성한다(멱등). `docs/DEMO_STATUS.md` 도 같다 — 생성기를 고친다.

4. **최상위 폴더 번호를 경로에 하드코딩하지 않는다.** 폴더 이름은 이미 **두 번** 바뀌었다
   (`01_사내가이드_연금사업부` → `01_행내가이드문서_…`, 그리고 `05_시황_상품_기반지식` 신설로
   05~07 → 06~08). 두 번째 때 변환기가 죽고 `source.locator` 543 건이 존재하지 않는 경로를
   가리켰는데 **테스트는 654/654 전부 통과했다**. 경로는 `config.kb_folder("주제별_추출지식")`
   처럼 이름으로 찾는다. `scripts/kb_build/test_paths.py` 가 이걸 강제한다.

   **지금 `09_` 가 둘이다**(`09_고객군별_지식데이터` · `09_코어비즈니스로직`). 두 갈래
   작업이 각자 09 를 붙인 채 병합됐다. 이름으로 찾으므로 코드는 멀쩡하지만, 번호로
   부르면 어느 쪽인지 갈리지 않는다 — 정리하려면 **번호를 바꾸는 쪽이 아니라 지금처럼
   이름으로만 부르는 쪽**을 유지한다(재번호는 위 사고를 두 번 낸 행위다).

5. **되돌릴 수 없는 행위는 게이트로 막는다.** 에이전트는 대외로 나가는 행위를 **수행하지
   않는다** — 문자를 보낼지는 직원이 발송 화면에서 정하고, 에이전트는 그 화면을 열어줄
   뿐이다(`consult_agent/CLAUDE.md` §10). 그래도 게이트는 남는다: 화면에 채워 넣으면
   직원이 **그대로 보낼 수 있기 때문**이다. `pension_agent/tools.py::open_lms_screen()` 은
   `dummy: true` 자산에서 온 문구를 발송 화면에 채우는 것을 거부한다. 접두 문자열은
   LLM 이 지울 수 있지만 게이트는 못 지운다.

## 무엇이 어디 있나

| | |
|---|---|
| 요건 기준 | `docs/REQUIREMENTS.md` — 전체 서비스 요건 앵커. 상위 기준은 `07_에이전트_기능정의/` |
| 타겟 선정 기준 | `IRP_타겟고객_룰베이스_v1.xlsx` → `src/pension_agent/strategy_agent/targets.json` (생성물) — 기획자 확인표. 임계값이 어긋나면 코드가 틀린 것 |
| 데모 상태 | `docs/DEMO_STATUS.md` (생성물) |
| 지식 저작 | `src/AUTHORING.md` |
| 실행·테스트 | `src/README.md` |
| consult_agent 기준 | `src/pension_agent/consult_agent/CLAUDE.md` — 대화형 **기준서**(있어야 할 동작 + 구현 gap 목록). 구현과 어긋나면 문서가 기준 |
| 지식 데이터 기준 | `src/pension_agent/knowledge/CLAUDE.md` — 카드가 선언해야 하는 관계 5종 · 실측 · 이행 순서 |

번호 축이 셋 섞여 있으니 주의: `①~④`(영업 시점, 기능정의 문서) · `①~⑨`(브리핑 화면 섹션,
REQUIREMENTS) · 폴더 번호. 문서에서 `07/01 ①` 은 "기능정의 문서의 ① 영업 전"을 뜻한다.

## 작업 전후

```bash
cd src
python -m tests.test_engine               # 엔진 선정 로직
python -m tests.test_support              # ⑥~⑨ · 더미 규약 · 시효성 수치
python -m tests.test_strategy_agent       # LLM 산출 검증·폴백
python -m tests.test_consult_agent        # 라우팅·즉답·도구 루프·하지말것 가드
python -m tests.test_infra                # 공용 인프라 · 임포트 경계
python -m scripts.kb_build.test_paths     # 경로·locator 실재
python -m pension_agent.knowledge.schema validate pension_agent
```

지식베이스 원문(`06_주제별_추출지식/`)을 고쳤으면:

```bash
python -m scripts.kb_build.build_kb             # _draft_ 생성 + 변환 리포트
python -m scripts.kb_build.build_kb --activate  # 검토 후 활성화
python -m scripts.demo_status                   # 리포트 갱신
```

타겟 룰베이스 xlsx 를 기획자가 갱신해 왔으면:

```bash
python -m scripts.import_targets                # xlsx → targets.json (멱등)
python -m scripts.demo_status                   # §7 근거등급 표 갱신
```

테스트는 LLM 키 없이 돈다. `langgraph` 는 설치가 필요하다(`src/requirements.txt`).

## 구조 규칙

`src/` 아래는 **`pension_agent` 단일 패키지 하나**다. 임포트는 전부 절대 경로
(`from pension_agent.strategy_agent import engine`)이고, 실행은 `src/` 에서 `python -m ...` 이다.

- **`sys.path` 를 손대지 않는다.** 예전에는 모듈마다 umbrella 를 올리는 블록이 30곳 있었고,
  두 에이전트의 동명 모듈(`prompts`·`llm`)이 `sys.modules` 를 놓고 경합하는 것을 막으려고
  전용 로더까지 있었다. 패키지화로 둘 다 없앴다 — `tests/test_infra.py` 가 재발을 막는다.
- **경로를 아는 파일은 `config.py` 하나다.** 모듈이 `Path(__file__).parent...` 로 경로를
  되짚으면 파일이 한 칸만 움직여도 조용히 엉뚱한 곳을 가리킨다(규칙 4번이 그 사고 기록이다).
- **데이터는 소유가 있는 곳에 둔다.** 두 에이전트가 함께 읽는 지식 카드는
  `pension_agent/knowledge/data/`, strategy 만 읽는 상품·전략 카탈로그는
  `pension_agent/strategy_agent/data/`. 적재는 `knowledge.shared_store()` 한 곳에서만 한다.
