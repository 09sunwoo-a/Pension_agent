"""공용 인프라 회귀 테스트 — 구간별 모듈. 실행은 `python -m tests.test_infra` 한 가지다.

각 파일은 모듈 수준 스크립트다(함수가 아니라 임포트되는 순간 검사가 돈다). 그래서 번호가
곧 실행 순서이고, 앞 구간이 남긴 값을 뒤 구간이 `from tests.infra.sNN import …` 으로
이어받는 자리가 있다. 새 구간은 번호를 뒤에 붙이고 `tests/test_infra.py` 의 목록에 넣는다.

    _common.py          check · 결과 누적 · 이 스위트가 만드는 세션 id 정리
    s01_sessions.py     session_store — 주인 없는 기록은 남기지 않는다
    s02_send_gate.py    tools — 발송 화면 연계 게이트 (발송하지 않는다, 세션이력에만 기록)
    s03_boundaries.py   패키지 임포트 경계 — 두 에이전트를 한 프로세스에서 함께 써도 이름이 겹치지 않는다
    s04_env_priority.py env — 값의 우선순위 · 실행 단계 (env.py 머리말)
    s05_numbers.py      수치 토큰화 — 뒤따르는 쉼표를 숫자에 붙이지 않는다
    s06_fixtures.py     시연 픽스처는 테스트가 지우지 않는다
    s07_dates_whole.py  날짜는 통짜로 대조한다 — 흩어 놓으면 오답 날짜가 집합 검사를 통과한다
    s08_dates_bare.py   연도 없이 말한 날짜 — 사람이 읽는 대로 «오늘 언저리»로 읽는가
    s09_today.py        오늘·기준일 — 시간축 두 개가 섞이지 않는가
    s10_llm_retry.py    llm — 429(속도 제한)·5xx(서버 오류) 재시도
    s11_observability.pyobservability — Langfuse 관측
    s12_py310.py        Python 3.10 호환 — 3.11+ 전용 이름을 임포트하지 않는가
    s13_scenarios.py    시연 대본의 판 이력 — tests/debug/scenarios.py
    s14_note_body.py    WorkB 쪽지 — 오늘의 타겟 고객 본문
    s15_mcp.py          행내 MCP 연동 (pension_agent/mcp) — 쪽지가 실제로 나가는 층
"""
