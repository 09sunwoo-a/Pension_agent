# 퇴직연금 사후관리 에이전트 (Pension Aftercare Agent)

퇴직연금(IRP) 상담 전, 직원에게 **이 고객에게 무엇을 어떻게 제안할지**를 정리해 주는
에이전트 묶음. **코드가 사실을 확정하고 LLM은 표현만 맡는** 구조라, 규제 도메인에서
환각·불완전판매를 구조적으로 막는다.

## 디렉토리

```
pension_aftercare_agent/
├─ common/          공용 인프라 — LLM 클라이언트 · 데이터 규격(store·schema·kinds) · 세션이력 · 두 에이전트 교차 로더
├─ consult_agent/   직원 상담 대화 에이전트 — 화법 코칭을 시작으로 브리핑 질의·상담이력 등으로 넓어지는 자리 (LangGraph)
├─ strategy_agent/  전략 제안 에이전트 — 고객 1명 종합 → 전략 문장·근거·TOP3 (+평가 대시보드)
├─ market/          시황 소스 — 뉴스·금리를 지식으로 정제 (플레이스홀더)
├─ .env             공용 LLM 설정 (커밋 금지) · .env.example 참고
└─ AUTHORING.md     데이터 소스 추가 가이드 (사내 규정·상품 등 + 저작 프롬프트)
```

`consult_agent`는 원래 화법(pitch) 코칭 전용이었으나("pitch_agent"), 직원과의 대화 전반을
다루는 방향으로 넓어지고 있어 이름을 바꿨다. 화법 검색 자체("pitch" 종류의 카드, `pitch_refs`)는
여전히 이 에이전트가 다루는 기능 중 하나라 그 이름들은 그대로 남아 있다.

향후: `knowhow_agent/`(영업 노하우, 팀원) · `bff/`(프론트 서빙 게이트웨이)는 같은 방식으로 붙는다.

## 구조

```mermaid
flowchart TB
    DOC["행내 원본 문서<br/>(스캔·PDF·PPT)"] -->|"LLM 저작 + 사람 검토<br/>(AUTHORING.md)"| KINDS

    subgraph COMMON["common — 공용 인프라"]
        KINDS["kinds.json<br/>종류별 스키마 선언"]
        SCHEMA["schema.py<br/>검증기 · 저작프롬프트 생성"]
        STORE["store.py<br/>단일 레코드 로더"]
        KINDS --- SCHEMA
    end

    STORE --> PDATA
    STORE --> SDATA

    subgraph PITCH["consult_agent — 화법 코칭 (LangGraph)"]
        PDATA[("data/*.json<br/>fact · pitch · resource")]
        PDATA --> FLOW1["understand → retrieve(n-gram+태그)<br/>→ broaden ×2 → llm_rerank(안전망)<br/>→ verify → respond"]
    end

    subgraph STRATEGY["strategy_agent — 전략 제안"]
        SDATA[("data/*.json<br/>product · strategy · baseline · asset")]
        SDATA --> FLOW2["적합성 게이트(risk_asset 등)<br/>→ 전략 카드 합성 → LLM 문장 생성<br/>→ verify(숫자·상품명 재료 대조)"]
    end

    PDATA -.->|"strategy.pitch_refs로<br/>실시간 조회(정적 참조)"| FLOW2
```

- **저작**: 원본 문서를 LLM이 읽고 `kinds.json`에 선언된 종류(kind)의 JSON으로 뽑아낸 뒤,
  사람이 검토하고 `schema.py`의 검증기(필수필드·enum·참조·사실충돌·최신성·개인정보 의심 패턴)를
  통과해야 활성화된다.
- **consult_agent**는 결정론적 검색(n-gram+태그)을 우선 쓰고, 그마저 0건일 때만 이미 저작된
  카드 목록으로 검색 범위를 한정한 LLM 재랭킹(`llm_rerank`)이 보조한다 — 저장소 전체가 아니라
  검증을 통과한 카드만 후보가 되므로, 미승인 내용이 답변에 섞일 수 없다.
- **strategy_agent**는 consult_agent의 데이터를 실시간 검색하지 않는다. 전략마다 어떤 화법
  카드를 쓸지 저작 시점에 `pitch_refs`로 미리 연결해두고, 요청마다 그 카드의 최신 내용을
  그대로 가져온다(복사하지 않으므로 두 시스템이 따로 놀다 어긋날 일이 없다). 최종 문장은
  `verify()`가 재료(숫자·상품명)를 벗어났는지 한 번 더 대조한다.

## 핵심 설계

- **코드 = 사실 / LLM = 표현.** 수치·상품·적합성은 코드가 정하고, LLM은 문장만 쓴다.
  LLM이 없거나 장애여도 규칙 기반으로 답이 나온다.
- **모든 데이터가 단일 레코드 규격.** `{meta, records:[{id, kind, fields, …}]}` 하나로
  통일 — 종류(`product`·`pitch`·`fact`·`strategy`…)는 `common/kinds.json` 에 선언한다.
  새 데이터·새 종류는 파일/선언만 추가하면 검증·저작·적재가 붙는다 → [AUTHORING.md](AUTHORING.md).
- **에이전트 간 결합은 정적 참조로.** strategy 는 pitch 를 검색하지 않고, 저작 시점에
  연결해둔 카드(`pitch_refs`)를 요청마다 실시간으로 가져와 자기 규율로 합성한다 — 검색의
  불확실성을 strategy 쪽에 들이지 않으면서도, 카드 내용은 항상 최신이다.

## 설정 · 실행

```bash
cp .env.example .env      # 사내 GenAI 게이트웨이 URL·키 입력 (common/llm.py 가 읽음)

# 각 에이전트 실행법은 해당 디렉토리 README 참고
python strategy_agent/agent.py 이현우          # 단일 고객 전략 제안
streamlit run strategy_agent/app.py            # 전략 평가/피드백 대시보드
python consult_agent/graph.py "손실 난 고객 어떻게 상담하죠?"
```

LLM 은 `.env` 의 `LLM_BASE_URL` 유무로 사내(genai)/외부테스트(anthropic)를 자동 전환한다 →
[common/README.md](common/README.md).
