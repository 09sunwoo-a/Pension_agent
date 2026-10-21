"""고객별 솔루션 — 지금 열려 있는 고객의 현황·타겟 근거·추천을 계산하고 브리핑을 만든다.

    계산 (LLM 없음)
    customer.py      고객 원장 → Profile · 원장 기준일 AS_OF
    target_list.py   타겟 14종 판정 (targets.json — 기획자 확인 룰베이스)
    situations.py    문제상황 매칭
    engine/          추천 엔진 — 상품 카탈로그·적합성·전략 문장 조립
    support/         ⑥~⑨ 지원 섹션 — 반론 대응·참고자료·안내 콘텐츠
    data/            상품·전략·안내 콘텐츠 카탈로그

    도구 (plan 이 부른다)
    briefing.py      customer      브리핑 재료 — 잔액·수익률·요건·타겟 근거
    suitability.py   suitable      투자성향으로 어디까지 안내할 수 있는지
    outreach.py      outreach      안내할 세미나·이벤트와 발송 문구
    targets.py       targets       오늘의 타겟 고객 목록
    guard.py                       고객 요건으로 찾는 「하지 말 것」 카드

    브리핑 생성
    agent.py         propose       ①~⑨ 브리핑 [LLM]
    sections.py · briefing_store.py
"""
