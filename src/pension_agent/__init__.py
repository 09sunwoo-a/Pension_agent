"""퇴직연금 AI 사후관리 에이전트.

    config.py          경로·데이터 위치의 단일 출처
    clock.py           «오늘»의 단일 출처 (PENSION_TODAY 로 고정 — 원장 기준일과는 다른 축)
    llm.py             프로바이더 전환식 LLM 클라이언트 (환경 이전 시 여기만 수정)
    verify.py          LLM 산출물의 재료 이탈 판정 — 두 에이전트 공통
    session_store.py   상담 세션·대화이력 (consult 가 쓰고 strategy 가 읽는다)
    tools.py           외부 연동 레지스트리 (LMS 발송 등 — 되돌릴 수 없는 행위의 게이트)
    knowledge/         데이터 접근 계층 (kinds·schema·store·kb + 공용 지식 카드)
    strategy_agent/    AI 브리핑 — 고객 1명 종합 → ①~⑨ 섹션
    consult_agent/     직원 상담 대화 (LangGraph)
    market/            시황·금리 소스 (자리표시자)

설계 원칙과 실행 방법은 ../README.md 참고.
"""
