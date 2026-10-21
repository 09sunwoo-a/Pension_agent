"""지식베이스 검색 — 지식 카드에서 근거를 찾아 원장 항목으로 만든다.

    도구
    cards.py         fact procedure screen channel segment method fieldtip
    market.py        market lineup         시황·투자전략 · 추천펀드·디폴트옵션·TDF
    pitch.py         pitch                 고객 발화로 화법 검색
    playbook.py      playbook              고객 상태별 참고자료
    combine.py       evidence_from_cards   카드 묶음 → 원장 항목

    도구 내부 단계
    kb_index.py      계층 인덱스 · 프롬프트 컨텍스트
    select.py        버킷 → 카드 2단 선택 [LLM]
    facts_qa.py · procedure_qa.py · segment_qa.py   종류별 검색·근거 블록
    pitch_slots.py   화법 검색용 상황 슬롯 추출 [LLM]
"""
