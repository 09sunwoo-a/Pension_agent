"""부점 브리핑 — 직원이 자기 부점의 IRP 고객을 보는 기능 셋 중 둘(나머지 하나는 customer_solution).

    기능 1 관리 대상 추천  branch_targets  customer_solution/tool.py::_targets (순서는 여기 status 의 CASE)
    기능 2 부점 현황        branch_status   tool.py::_branch_status — status.compute 그대로 [LLM 없음]
    기능 3 고객 검색        branch_search   tool.py::_branch — 질문 → SELECT 생성 → 검사 → 실행 → 결과 표 [LLM]
    화면 진입 전략 브리핑   strategy.build  현황 패널 + 오늘의 관리 방향 문장 [LLM 1회] — main.py mode=branch_briefing

    status.py    rules() · compute()      룰베이스 부점 시트(세그먼트·CASE·임계값) → 지표·변화·CASE 판정
    strategy.py  build() · write()        전략 브리핑 문장 — 숫자는 세그먼트 인원 변화만(코드 검사)
    views.py     VIEWS · catalog()        LLM 용 스키마(고정 뷰 3개 — 오늘 · 전월말 · 부점 집계)
    check.py     check() · SQLRejected    실행 전 SQL 검사(LLM SQL · 코드 SQL 공통)
    run.py       my_branch() · execute()  사번 → 부점 · 고정 뷰 결합 · 행 수 상한 · 백엔드
    demo_db.py   connection()             행외 목업 DB(sqlite 메모리 · 오늘 + 전월말 스냅샷)
    sql/         sqlite/ · sybase/        고정 뷰 SQL(사람 작성, sybase 는 행내 작성)

백엔드: 환경변수 BRANCH_DB(demo · sybase · off).
"""
