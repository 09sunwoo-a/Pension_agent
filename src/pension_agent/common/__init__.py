"""공용 기반 — 모든 층이 쓰는 모듈. 위층(tools·nodes 등) 임포트 금지.
예외: note.py 가 쪽지 본문의 타겟·잔여일수용으로 tools/customer_solution 을 읽음.

    config.py         경로·데이터 위치
    clock.py          «오늘» (PENSION_TODAY 로 고정)
    env.py            .env 로딩·실행 단계
    privacy.py        나가는 글의 개인정보 마스킹
    llm.py            LLM 클라이언트
    mcp/              행내 MCP 연동 (WorkB 쪽지·Sybase)
    observability/    Langfuse 트레이스
    session_store.py  상담 세션·대화이력 파일
    note.py           WorkB 쪽지 꼴·발송
    progress.py       진행 표시 이벤트
"""
