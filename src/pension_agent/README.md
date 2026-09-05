# pension_agent — 패키지 개요

두 에이전트(consult · strategy)와 그것들이 공유하는 인프라. `src/` 에서
`python -m pension_agent...` 로 실행하고, 임포트는 전부 `from pension_agent...` 절대 경로다.

## 공용 인프라 (패키지 최상단)

| 파일 | 역할 |
|---|---|
| [config.py](config.py) | 경로·데이터 위치의 단일 출처. 모듈이 `__file__` 로 경로를 되짚지 않는다 |
| [llm.py](llm.py) | 프로바이더 전환식 LLM 클라이언트. **환경 이전 시 이 파일만 수정** |
| [verify.py](verify.py) | LLM 산출물의 재료(facts) 이탈 여부 판정 — 두 에이전트 공통 |
| [session_store.py](session_store.py) | 상담 세션/대화이력 — consult_agent 가 기록, strategy_agent 가 브리핑에 노출 |
| [tools.py](tools.py) | 외부 연동 레지스트리. 되돌릴 수 없는 행위(LMS 발송)의 게이트가 여기 있다 |

## knowledge/ — 데이터 접근 계층

| 파일 | 역할 |
|---|---|
| [knowledge/kinds.json](knowledge/kinds.json) | 레코드 종류 레지스트리(선언형). 검증·저작 프롬프트가 여기서 나온다 |
| [knowledge/store.py](knowledge/store.py) | 통합 레코드 스토어. 모든 데이터의 단일 로더 (`fields_of`·`records`) |
| [knowledge/schema.py](knowledge/schema.py) | 종류 구동 검증 + 단일 저작 프롬프트 생성기 (CLI) |
| [knowledge/similarity.py](knowledge/similarity.py) | 문자열 n-gram 유사도 — 검색 채점의 기초 |
| [knowledge/checks.py](knowledge/checks.py) | 범용 무결성 검증 (ID 중복 · 깨진 참조 · 사실충돌) |
| [knowledge/data/](knowledge/data/) | 공용 지식 카드 — 두 에이전트가 함께 읽는다 (`scripts/kb_build` 산출물) |

`knowledge.shared_store()` 가 `config.DATA_ROOTS`(공용 지식 + 상품·전략 카탈로그) 전체를
프로세스당 한 번만 적재한다. engine·support·situations 가 각자 `Store(...)` 를 만들던 것을
한 곳으로 모은 것 — 같은 JSON 을 세 번 파싱하지 않고, 루트 목록도 한 곳에만 적혀 있다.

## 데이터 규격 (모든 지식/데이터 공통)

모든 파일이 하나의 형태를 쓴다: `{ meta:{kind,…}, records:[{id, kind, fields, source?, refs?}] }`.
종류(`product`·`pitch`·`fact`·`strategy`…)는 [knowledge/kinds.json](knowledge/kinds.json) 에
**데이터로 선언**하며, 새 종류는 여기 한 항목만 추가하면 검증·저작·적재가 코드 수정 없이
붙는다. 데이터 추가·저작 절차는 [../AUTHORING.md](../AUTHORING.md) 참고.

```bash
python -m pension_agent.knowledge.schema kinds                    # 등록된 종류
python -m pension_agent.knowledge.schema prompt <kind>            # 그 종류의 저작 프롬프트
python -m pension_agent.knowledge.schema validate <데이터 루트...>  # 통합 검증 (ERROR 0 확인)
```

`store.fields_of(kind)` 는 `{id, **fields}` flat dict 뷰를 돌려줘 엔진 등 기존 소비부와
호환된다. `store.records(kind)` 는 원본 레코드(+doc 메타)를 준다.

## llm.py — 하나의 인터페이스, 세 프로바이더

호출부는 `generate()` / `agenerate()` 만 쓴다. 백엔드는 환경변수로 정해진다.

```python
from pension_agent.llm import agenerate, available, generate

if available():
    text = generate(prompt, system=SYS, max_tokens=900)
else:
    text = rule_based_fallback()   # 망분리/장애 시 규칙 폴백 (strategy 규약)
```

- **genai** (사내 플랫폼): `LLM_BASE_URL` + `LLM_API_KEY` → OpenAI 호환 vLLM, `kb-key`·
  `x-client-user` 헤더. 표준 라이브러리만 사용해 추가 의존성이 없다.
- **gemma** (외부 사전점검): `GEMINI_API_KEY` → Google generativelanguage API 의
  Gemma(`GEMMA_MODEL`, 기본 `gemma-4-31b-it`). 사내 플랫폼이 서빙하는 것과 같은 계열
  모델이라, 내부 이관 전에 gemma 기반으로도 답이 잘 나오는지 사외에서 확인하는 경로다.
  표준 라이브러리만 사용. 이 API 는 Gemma 에 systemInstruction 을 허용하지 않아
  시스템 프롬프트를 사용자 프롬프트 앞에 이어 붙인다(genai 로 가면 system 메시지로 실림).
- **anthropic** (외부 테스트): `ANTHROPIC_API_KEY` → Anthropic SDK, `claude-sonnet-5`.
  `anthropic` 패키지가 이 분기에서만 lazy import 된다.

선택은 `LLM_PROVIDER`, 미지정 시 자동 판별 — `LLM_BASE_URL` 이 있으면 genai(**내부로
코드를 들여오면 base_url 이 잡혀 자동으로 이쪽**), 없고 `GEMINI_API_KEY` 가 있으면 gemma,
둘 다 없으면 anthropic.

```bash
# 사내 (망분리)
export LLM_BASE_URL=http://<사내-genai-엔드포인트>
export LLM_API_KEY=<kb-key>
export LLM_MODEL=<모델 슬러그>      # 비우면 게이트웨이 기본 라우팅

# 외부 gemma 사전점검 (내부 이관 전 품질 확인)
export GEMINI_API_KEY=...           # Google AI Studio 발급 키
export GEMMA_MODEL=gemma-4-31b-it   # 생략 시 이 값

# 외부 테스트 (anthropic)
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

설정 파일은 `src/.env`(공통) + `src/.env.<프로파일>`(환경별) 이다. 실제 환경변수가 있으면
그쪽이 이긴다.

### 실행 환경 셋 — 파일 하나씩

LLM 을 쓸 수 있는 환경이 셋이고 환경마다 프로바이더·엔드포인트·키가 다르다. 한 파일에 세
벌을 넣고 주석을 바꿔 가며 쓰지 않는다 — **환경마다 파일 하나**다(`env.py` 머리말).

| 프로파일 | 환경 | 파일 | 프로바이더 |
|---|---|---|---|
| `bank` | 행내(망분리) | `src/.env.bank` | genai — `LLM_BASE_URL`·`LLM_API_KEY`·`LLM_MODEL` |
| `local` | 개발 PC | `src/.env.local` | anthropic — `ANTHROPIC_API_KEY` |
| `aiden` | 행내·외부 중간, Sonnet | `src/.env.aiden` | genai — OpenAI 호환 게이트웨이. `LLM_BASE_URL`·`LLM_API_KEY`·`LLM_MODEL`(Sonnet 슬러그) |

어느 파일을 읽을지는 이 순서로 정한다: 실제 환경변수 `PENSION_ENV` → `src/.env` 안의
`PENSION_ENV=` 줄 → `.env.<이름>` 파일이 하나뿐이면 그것. 행내 머신에는 `.env.bank` 만
두면 아무것도 지정하지 않아도 그쪽이 잡힌다. 여러 개를 두는 개발 PC 는 `.env` 에
`PENSION_ENV=local` 로 기본을 고정하고, 잠깐 바꿔 돌릴 때만 `PENSION_ENV=aiden python -m …`
으로 앞에 붙인다. 프로파일 파일이 공통 파일을 덮는다.

```bash
cp .env.example .env && cp .env.aiden.example .env.aiden   # 견본을 복사해 채운다
python -m pension_agent.env                                # 어느 파일·프로바이더가 잡혔나
```
