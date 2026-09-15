"""consult_agent 회귀 테스트 — 주제별 검사 모듈.

한 파일(6,500줄)에 검사 함수 66개가 있던 것을 주제로 갈랐다. 실행 진입점과 순서는
그대로 `python -m tests.test_consult_agent` 다 — 그 파일의 `main()` 이 스텁을 설치하고
여기 모듈들의 `check_*` 를 정해진 순서로 부른 뒤 잔여물을 지운다.

    _common.py     집계용 print · 전역 스텁 · CASES (모든 검사 모듈이 여기서 가져다 쓴다)
    routing.py     그래프 배선 — 라우팅 · 즉답 · 게이트 · 가드
    screens.py     화면 연계 — ⑨ 안내 콘텐츠 · 발송 화면 · 화면번호
    material.py    재료 — 브리핑·고객·화법·계좌·오늘·이력·시황 도구의 근거와 표시
    clarify.py     되묻기 — 맥락 · 충분성 · 골든셋 · 등급형 판정
    plan_loop.py   계획 루프 — 결합 · 재계획 · 비용 · 실패 경로
    answer.py      답변 문장 — 인용 범위 · 상품 조언 · 반복 · 수정 · 형태
    index.py       카드 색인 — 팩트 · 계층 인덱스 · 도달 · 검색 입구
    tax_credit.py  세액공제 계산기
    memo.py        WorkB 쪽지
    meta.py        생성물·배선 정합 — 다이어그램 · 라벨 충돌 · 리허설 기대값

검사를 추가할 때: 맞는 모듈에 `check_*` 를 쓰고 `test_consult_agent.py::main` 의 호출
목록에 넣는다. 부르지 않으면 돌지 않는다 — 통과 수는 출력에서 세므로 상수를 고칠 것은 없다.
"""
