"""퇴직연금 AI 사후관리 에이전트.

최상단의 공용 모듈·패키지는 폴더로 묶지 않고 평평하게 둔다 — 각자 책임이 하나씩이고,
층은 아래 지도와 `tests/infra/s03_boundaries.py` 의 의존 표가 고정한다(새 모듈을 더하면
둘 다 고쳐야 검사가 통과한다).

  단일 출처 — 아무것도 임포트하지 않는다
    config.py          경로·데이터 위치의 단일 출처
    clock.py           «오늘»의 단일 출처 (PENSION_TODAY 로 고정 — 원장 기준일과는 다른 축)

  실행 환경 — env ← observability ← llm 한 방향
    env.py             .env 로딩과 실행 단계(train | serving)
    observability/     Langfuse 관측 — 트레이스·span·score·전송 워커 (자가진단: python -m pension_agent.observability)
    privacy.py         행내 개인정보 필터 규칙표 — 나가는 글에서 그 필터가 잡는 꼴을 가린다
                       (자가진단: python -m pension_agent.privacy "<글>")
    llm.py             프로바이더 전환식 LLM 클라이언트 (환경 이전 시 여기만 수정) · 응답 JSON 추출

  검증
    verify.py          LLM 산출물의 재료 이탈 판정 — 두 에이전트 공통

  기록·행위 — 승낙 뒤 실행하는 행위의 게이트는 consult_agent/effects/actions.py (부르는 쪽이 거기뿐이다)
    session_store.py   상담 세션·대화이력 (consult 가 쓰고 strategy 가 읽는다)
    note.py            WorkB 쪽지의 꼴과 발송 (본문 표·마스킹·길이 상한·결과 판정)
    mcp/               행내 시스템 연동 — 서버 표·클라이언트·도구 어댑터(workb.py) (진단: python -m pension_agent.mcp)

  데이터와 에이전트 — knowledge ← strategy_agent ← consult_agent 한 방향
    market/            시황·금리 소스 (자리표시자)
    knowledge/         데이터 접근 계층 (kinds·schema·store·kb + 공용 지식 카드)
    strategy_agent/    AI 브리핑 — 고객 1명 종합 → ①~⑨ 섹션
    consult_agent/     직원 상담 대화 (LangGraph)

설계 원칙과 실행 방법은 ../README.md 참고.
"""
