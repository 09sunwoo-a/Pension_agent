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
```

```bash
# ── 브리핑 · 대화 · 화면
python -m pension_agent.strategy_agent.agent 이준호    # AI 브리핑 (①~⑨ 섹션)
$CA "고객이 주식이 더 낫다는데 뭐라고 하지?"           # 단발 — 고객 화면 없이
$CA -c 198734-1205842                                 # REPL — 고객 화면이 열린 상태
$CA -c 198734-1205842 "투자성향 뭐야?" "만기 자금은?"  # 멀티턴을 한 줄로 (맥락 이어서)
streamlit run app.py                                  # 평가 대시보드 (개발용 화면)

# ── 디버그: 이 답이 어디서 갈렸나 (인자 규약이 $CA 와 같다 — 모듈만 바꾸고 --debug)
$CAD --debug "세액공제 한도가 얼마야?"
$CAD --debug -c 198734-1205842                        # REPL — 턴마다 방금 턴만 찍는다
$CAD --script tax_credit_asserts_wrong --debug --show-llm   # 키 없이 재현
$CAD --list                                           # 시나리오 목록

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

## 최근에 들어온 것

- **재계획.** 근거를 못 낸 호출을 `plan_misses` 로 계획에 싣고, 근거 0건인 채 끝내려 하면
  안 써 본 도구와 함께 **한 번만** 재계획시킨다. 두 번째 끝내기는 존중한다(정직한 '없음').
  루프 상한 `plan.MAX_STEPS`=4.
- **표기가 판정을 뒤집지 않는다.** `verify` 가 값 보존 정규형으로 대조 — `15.0%`=`15%`,
  `1,485,000원`=`148만 5천원`. 값이 다르면 여전히 폐기된다.
- **인용은 주장이 아니다.** 원문의 오기를 짚는 정정은 통과하고, 사실로 주장하면 폐기된다.
- **상담이력 선별.** 도구가 계획의 `query` 를 읽어 걸리는 기록을 앞세우되 걸러내지는 않고,
  과거 상담과 오늘 대화를 예산·구획으로 가른다. `suggest.history_chips` 는 기록 있는
  고객에게만, 고정 템플릿 + 계산값으로만 칩을 띄운다.
- **타겟 룰베이스.** 임계값의 기준은 기획자 확인표(`strategy_agent/targets.json`)다 —
  어긋나면 코드가 틀린 것. 근거등급은 [../docs/DEMO_STATUS.md](../docs/DEMO_STATUS.md) §7.

## 디렉토리

```
src/
├─ pension_agent/          단일 패키지. 임포트는 전부 절대 경로, 경로를 아는 파일은 config.py 하나
│  ├─ config.py            경로·데이터 위치의 단일 출처
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
