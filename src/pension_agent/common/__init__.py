"""공용 기반 — 에이전트의 어느 층에서나 쓰는 것. 이 패키지는 위층(tools·nodes 등)을 임포트하지 않는다.
예외 하나: note.py 가 쪽지 본문의 타겟·잔여일수를 위해 tools/customer_solution 을 읽는다.

    config.py         경로·데이터 위치의 단일 출처
    clock.py          «오늘»의 단일 출처 (PENSION_TODAY 로 고정)
    env.py            .env 로딩과 실행 단계
    privacy.py        나가는 글의 개인정보 패턴 가리기
    llm.py            LLM 클라이언트 (외부 연동)
    mcp/              행내 MCP 연동: WorkB 쪽지 (외부 연동)
    observability/    Langfuse 트레이스 (외부 연동)
    session_store.py  상담 세션·대화이력 파일
    note.py           WorkB 쪽지의 꼴과 발송
    progress.py       진행 표시 이벤트
"""
