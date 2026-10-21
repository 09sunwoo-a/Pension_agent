"""메인 브리핑 — 직원이 자기 부점의 IRP 고객 데이터를 자연어로 묻는 도구(`branch`).

    tool.py      _branch                  질문 → SELECT 한 문장 → 검사 → 실행 → 결과 표 [LLM]
    views.py     VIEWS · catalog()        LLM 이 볼 수 있는 유일한 스키마(고정 뷰 두 개)
    check.py     check() · SQLRejected    실행 전 검사 — SELECT 하나 · 고정 뷰만 · 쓰기 낱말 없음
    run.py       my_branch() · execute()  사번 → 부점 · 고정 뷰 SQL 붙이기 · 행 수 상한 · 백엔드
    demo_db.py   connection()             행외 목업 DB(sqlite 메모리) — 전부 시연값
    sql/         sqlite/ · sybase/        고정 뷰 SQL. 사람이 쓴다. sybase 쪽은 행내에서 채운다

백엔드는 환경변수 `BRANCH_DB` 가 고른다(demo · sybase · off) — run.py 머리말.
"""
