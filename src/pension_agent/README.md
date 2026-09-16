# pension_agent — 패키지 개요

두 에이전트(consult · strategy)와 그것들이 공유하는 인프라. `src/` 에서
`python -m pension_agent...` 로 실행하고, 임포트는 전부 `from pension_agent...` 절대 경로다.

## 파일 구조

최상단 모듈·패키지의 역할과 층은 [`__init__.py`](__init__.py) 머리말이 지도다 — 지도에 빠진
항목과 층을 거스르는 임포트는 `tests/infra/s03_boundaries.py` 가 잡는다. 하위 패키지의 파일별
설명은 각 폴더의 `__init__.py` 머리말에 있다(`knowledge/` · `consult_agent/` 등).

층은 세 겹이다 — 단일 출처(config·clock) → 실행 환경(env ← observability ← llm) → 기록·행위
(session_store·note·mcp). 두 에이전트는 `knowledge ← strategy_agent ← consult_agent` 한 방향이다.
승낙 뒤 실행하는 행위의 게이트(발송 화면·쪽지)는 consult 만 부르므로
`consult_agent/effects/actions.py` 에 있다.

## 데이터 규격 (모든 지식/데이터 공통)

적재는 `knowledge.shared_store()` 한 곳에서만 한다(`knowledge/__init__.py`).
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

설정 파일은 `src/.env` **하나**다 — 행내 워크스페이스·배포 이미지·사외 개발 PC 모두. 실제
환경변수가 있으면 파일보다 이긴다(`env.py` 머리말).

행내 GenAI 플랫폼은 분석계(`…/trnn/…`)와 서빙계(`…/serv/…`)의 APIM 경로가 달라 URL 이 두 벌이고
(`LLM_BASE_URL_TRNN` · `_SERV`, 키도 같다), 실행 단계 `ENV_PATH`(워크스페이스엔 없다 → 분석계,
배포 때 Jenkins 가 `serving` → 서빙계)가 어느 것을 읽을지 정한다 — `env.staged()`.
`LLM_MODEL` 은 비운다. 다른 환경(LLM Gateway · 사외 anthropic)은 같은 파일의 다른 구역을
채운다 — 견본 `.env.example` 의 ①②③. 다른 설정을 잠깐 쓸 때는 `LLM_DOTENV=<경로>` 로 앞에 붙인다.

```bash
cp .env.example .env                 # 하나
python -m pension_agent.env          # 어느 파일·단계·프로바이더가 잡혔나
```

**URL 과 키는 각자 폴백한다** — 단계별 이름(`_TRNN`·`_SERV`)이 없으면 접미사 없는 이름을 읽는다.
그래서 `LLM_BASE_URL_SERV` 만 채우고 `LLM_API_KEY_SERV` 를 비워 두면 **서빙계 URL 에 분석계 키가
실린다.** 두 단계가 APIM 의 다른 제품이면 게이트웨이가 이렇게 끊는다:

```
HTTP 401 Access Denied — https://…/serv/gemma-4/chat/completions
응답: {"statusCode": 401, "message": "Access denied due to invalid subscription key. …"}
```

`/health` 의 `api_key_set` 은 이때도 참이다(키가 들어 있기는 하다). 그래서 **어느 변수에서
읽었는지**를 함께 내보낸다 — `base_url_from` · `api_key_from` · `key_stage_mismatch`. 어긋나 있으면
기동 로그에도 경고가 한 줄 남고, 401·403 응답에도 같은 진단이 붙는다(`llm.key_stage_mismatch`).
고치는 법은 하나다 — 그 단계의 키를 `LLM_API_KEY_SERV`(또는 `_TRNN`)에 넣는다.
