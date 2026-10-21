"""공용 인프라 회귀 테스트 — 구간별 모듈. 실행: python -m tests.test_infra

모듈 수준 스크립트(임포트 시 실행) — 번호 = 실행 순서. 새 구간은 번호를 뒤에 붙이고 `tests/test_infra.py` 목록에 추가.

    _common.py            check · 결과 누적 · 테스트 세션 id 정리
    s01_sessions.py       session_store — 주인 없는 기록 미저장
    s02_send_gate.py      발송 화면 연계 게이트(발송 미수행, 세션이력 기록)
    s03_boundaries.py     패키지 임포트 경계 · 층 의존 방향
    s04_env_priority.py   env — 값 우선순위 · 실행 단계
    s05_numbers.py        수치 토큰화 — 뒤 쉼표 미포함
    s06_fixtures.py       시연 픽스처 보존
    s07_dates_whole.py    날짜 통짜 대조
    s08_dates_bare.py     연도 없는 날짜 — «오늘 언저리» 해석
    s09_today.py          오늘·기준일 시간축 분리
    s10_llm_retry.py      llm — 429·5xx 재시도
    s11_observability.py  observability — Langfuse 관측
    s12_py310.py          Python 3.10 호환
    s13_scenarios.py      시연 대본 판 이력(tests/debug/scenarios.py)
    s14_note_body.py      WorkB 쪽지 — 오늘의 타겟 고객 본문
    s15_mcp.py            행내 MCP 연동 — 쪽지 발송 층
    s16_doc_paths.py      문서가 가리키는 .py 경로 실재
    s17_privacy.py        개인정보 필터 — 게이트웨이 400 패턴 차단
"""
