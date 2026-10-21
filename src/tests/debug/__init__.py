"""consult_agent 실행 트레이스 — 테스트 전용, 운영 코드 수정 금지(모듈 속성 임시 감싸기만).

    cd src
    python -m tests.debug --script tax_credit_asserts_wrong --debug --show-llm
    python -m tests.debug --debug "세액공제 한도가 얼마야?"    # 실제 LLM
    python -m tests.debug.test_trace                          # 회귀 검사

보는 것: 노드 순서 · 계획 단계별 도구·채택 카드 · compose 게이트별 통과/위반/미실행 · 폐기된 생성문 · 최종 답 출처(생성문/폴백)
못 보는 것: 이 하네스 밖의 실행
"""
