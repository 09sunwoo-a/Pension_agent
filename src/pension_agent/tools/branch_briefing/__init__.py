"""메인 브리핑 — 직원이 자기 부점의 IRP 고객 데이터를 자연어로 묻는 도구(`branch`).

    tool.py      _branch                  질문 → SELECT 생성 → 검사 → 실행 → 결과 표 [LLM]
    views.py     VIEWS · catalog()        LLM 용 스키마(고정 뷰 2개)
    check.py     check() · SQLRejected    실행 전 SQL 검사
    run.py       my_branch() · execute()  사번 → 부점 · 고정 뷰 결합 · 행 수 상한 · 백엔드
    demo_db.py   connection()             행외 목업 DB(sqlite 메모리)
    sql/         sqlite/ · sybase/        고정 뷰 SQL(사람 작성, sybase 는 행내 작성)

백엔드: 환경변수 BRANCH_DB(demo · sybase · off).
"""
