"""근거 층 — 도구가 근거를 찾고, 원장 항목으로 맞추고, 답변을 그것과 대조하는 것.

LLM 이 고르는 도구는 여기 없다(그건 tools/). 이 층은 tools/·effects/·nodes/ 를 임포트하지
않는다(tests/infra/s03_boundaries.py). [LLM] 은 그 함수가 LLM 을 부른다는 뜻이다.

    검색 — 지식베이스에서 카드를 고른다
    kb_index.py      buckets · index_slice · build_context   카드 선택용 계층 인덱스 · 프롬프트 컨텍스트
    select.py        pick · llm_pick          버킷 → 카드 2단 선택, 0건이면 n-gram [LLM]
    facts_qa.py      search · render          fact 도구의 검색·근거 블록
    procedure_qa.py  search · render          procedure·screen·channel 도구의 검색·근거 블록
    segment_qa.py    search · render          segment 도구의 검색·근거 블록
    pitch_slots.py   extract_slots            화법 검색용 상황 슬롯 추출 [LLM]
    guard.py         cautions_for · sensitive_cards   고객 요건으로 찾는 「하지 말 것」 카드

    규약 — 원장(state["evidence"])에 무엇이 어떤 꼴로 쌓이나
    record.py        Evidence · _ev           원장 한 항목의 규약 · 근거 블록 조립
    ledger.py        ledger_* · source_lines  이번 턴의 원장에서 텍스트·출처·관계·표시를 꺼낸다

    검사 — 답변이 근거 안에 있나 (수치 집합 검사는 ../../verify.py)
    relations.py     check · declared         값–조건 오짝 · 알려진 오답 주장
    marks.py         notes_for · tier_of      재료 성격 표시 — 신뢰 등급 · 내부용 주의
"""
