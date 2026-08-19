# 퇴직연금 사후관리 에이전트 (Pension Aftercare Agent)

퇴직연금(IRP) 상담 전, 직원에게 **이 고객에게 무엇을 어떻게 제안할지**를 정리해 주는
에이전트 묶음. **코드가 사실을 확정하고 LLM은 표현만 맡는** 구조라, 규제 도메인에서
환각·불완전판매를 구조적으로 막는다.

## 디렉토리

```
pension_aftercare_agent/
├─ common/          공용 인프라 — LLM 클라이언트 · 데이터 규격(store·schema·kinds)
├─ pitch_agent/     화법 에이전트 — 상황을 물으면 상담 화법을 코칭 (LangGraph)
├─ strategy_agent/  전략 제안 에이전트 — 고객 1명 종합 → 전략 문장·근거·TOP3 (+평가 대시보드)
├─ market/          시황 소스 — 뉴스·금리를 지식으로 정제 (플레이스홀더)
├─ .env             공용 LLM 설정 (커밋 금지) · .env.example 참고
└─ AUTHORING.md     데이터 소스 추가 가이드 (사내 규정·상품 등 + 저작 프롬프트)
```

향후: `knowhow_agent/`(영업 노하우, 팀원) · `bff/`(프론트 서빙 게이트웨이)는 같은
`retrieve()` 계약으로 붙는다.

## 핵심 설계

- **코드 = 사실 / LLM = 표현.** 수치·상품·적합성은 코드가 정하고, LLM은 문장만 쓴다.
  LLM이 없거나 장애여도 규칙 기반으로 답이 나온다.
- **모든 데이터가 단일 레코드 규격.** `{meta, records:[{id, kind, fields, …}]}` 하나로
  통일 — 종류(`product`·`pitch`·`fact`·`strategy`…)는 `common/kinds.json` 에 선언한다.
  새 데이터·새 종류는 파일/선언만 추가하면 검증·저작·적재가 붙는다 → [AUTHORING.md](AUTHORING.md).
- **에이전트 간 결합은 `retrieve()` 계약으로.** strategy 는 pitch 의 검색 결과만 가져다
  자기 규율로 한 번 합성한다(하위 LLM 중복 호출 없음).

## 설정 · 실행

```bash
cp .env.example .env      # 사내 GenAI 게이트웨이 URL·키 입력 (common/llm.py 가 읽음)

# 각 에이전트 실행법은 해당 디렉토리 README 참고
python strategy_agent/agent.py 이현우          # 단일 고객 전략 제안
streamlit run strategy_agent/app.py            # 전략 평가/피드백 대시보드
python pitch_agent/graph.py "손실 난 고객 어떻게 상담하죠?"
```

LLM 은 `.env` 의 `LLM_BASE_URL` 유무로 사내(genai)/외부테스트(anthropic)를 자동 전환한다 →
[common/README.md](common/README.md).
