# common — 공용 인프라

세 에이전트(pitch · strategy · 향후 knowhow)가 공유하는 도메인 중립 모듈. 각 에이전트에
복제돼 있던 LLM 클라이언트와 지식베이스 뼈대를 한 곳으로 모은다.

| 파일 | 역할 |
|---|---|
| [llm.py](llm.py) | 프로바이더 전환식 LLM 클라이언트. **환경 이전 시 이 파일만 수정** |
| [kinds.json](kinds.json) | 레코드 종류 레지스트리(선언형). 검증·저작 프롬프트가 여기서 나온다 |
| [store.py](store.py) | 통합 레코드 스토어. 모든 데이터의 단일 로더 (`fields_of`·`records`) |
| [schema.py](schema.py) | 종류 구동 검증 + 단일 저작 프롬프트 생성기 (CLI) |
| [kb_base.py](kb_base.py) | 문자열 유사도 · 검토게이트 로딩 · 범용 검증 (도메인 중립) |

## 데이터 규격 (모든 지식/데이터 공통)

모든 파일이 하나의 형태를 쓴다: `{ meta:{kind,…}, records:[{id, kind, fields, source?, refs?}] }`.
종류(`product`·`pitch`·`fact`·`strategy`…)는 [kinds.json](kinds.json) 에 **데이터로 선언**하며,
새 종류는 여기 한 항목만 추가하면 검증·저작·적재가 코드 수정 없이 붙는다. 데이터 추가·저작
절차는 [../AUTHORING.md](../AUTHORING.md) 참고.

```bash
python -m common.schema kinds                       # 등록된 종류
python -m common.schema prompt <kind>               # 그 종류의 저작 프롬프트
python -m common.schema validate <데이터 루트...>    # 통합 검증 (ERROR 0 확인)
```

`store.fields_of(kind)` 는 `{id, **fields}` flat dict 뷰를 돌려줘 엔진 등 기존 소비부와
호환된다. `store.records(kind)` 는 원본 레코드(+doc 메타)를 준다.

## llm.py — 하나의 인터페이스, 두 프로바이더

호출부는 `generate()` / `agenerate()` 만 쓴다. 백엔드는 환경변수로 정해진다.

```python
from common.llm import generate, agenerate, available

if available():
    text = generate(prompt, system=SYS, max_tokens=900)
else:
    text = rule_based_fallback()   # 망분리/장애 시 규칙 폴백 (strategy 규약)
```

- **genai** (사내 플랫폼): `LLM_BASE_URL` + `LLM_API_KEY` → OpenAI 호환 vLLM, `kb-key`·
  `x-client-user` 헤더. 표준 라이브러리만 사용해 추가 의존성이 없다.
- **anthropic** (외부 테스트): `ANTHROPIC_API_KEY` → Anthropic SDK, `claude-sonnet-5`.
  `anthropic` 패키지가 이 분기에서만 lazy import 된다.

선택은 `LLM_PROVIDER`, 미지정 시 `LLM_BASE_URL` 유무로 자동 판별한다 — **내부로 코드를
들여오면 base_url 이 잡혀 자동으로 genai 로 동작**한다. 외부에서 테스트할 때만 anthropic.

```bash
# 사내 (망분리)
export LLM_BASE_URL=http://<사내-genai-엔드포인트>
export LLM_API_KEY=<kb-key>
export LLM_MODEL=<모델 슬러그>      # 비우면 게이트웨이 기본 라우팅

# 외부 테스트
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

## kb_base.py — 지식베이스 공통 뼈대

`ngram_sim`(유사도), `iter_knowledge_files`(`_` 접두 검토 게이트 로딩 — `store.py` 가 사용),
`check_duplicate_ids`/`check_broken_refs`/`check_fact_conflicts`(범용 검증)를 제공한다.
`pitch_agent/kb.py`·`strategy_agent/engine.py` 모두 이 함수들을 import 해서 쓴다. 화법 태그
스코어링·전략 게이트 같은 도메인 로직은 각 에이전트에 남긴다.
