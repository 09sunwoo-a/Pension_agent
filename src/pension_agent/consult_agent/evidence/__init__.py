"""근거 층 — 도구가 근거를 **찾고**, 원장 항목으로 **맞추고**, 답변을 그것과 **대조**하는 것.

LLM 이 고르는 도구는 여기 없다(그건 `tools/`). 여기 있는 것은 도구들이 공통으로 쓰는
검색·규약·검사이고, 이 층은 `tools/`·`effects/`·`nodes/` 를 임포트하지 않는다
(`tests/infra/s03_boundaries.py` 가 고정한다). [LLM] 은 그 함수가 LLM 을 부른다는 뜻이다.

    검색 — 지식베이스에서 카드를 고른다
    kb_index.py      buckets · index_catalog · index_slice · whole_index
                                              LLM 카드 선택용 계층 인덱스(버킷). 적재·검색 자체는 ../../knowledge/kb.py
                     build_context · sources_of  고른 카드와 근거 자료를 프롬프트 텍스트로 · 검색 결과의 출처 표기
    select.py        pick · llm_pick          카드 선택 — 버킷 → 카드 2단, LLM 이 0건일 때만 n-gram (종류 무관) [LLM]
    facts_qa.py      search · render          fact 도구의 검색과 근거 블록 조립 (select 를 거친다)
    procedure_qa.py  search · render          procedure·screen·channel 도구의 검색과 근거 블록 조립 (select 를 거친다)
    segment_qa.py    search · render          segment 도구의 검색과 근거 블록 조립 (select 를 거친다)
    pitch_slots.py   extract_slots · situation_line
                                              화법 검색용 상황 슬롯(고객유형·거절유형·상담단계) 추출과 «파악된 상황» 한 줄 [LLM]
    guard.py         conditions_of · cautions_for · sensitive_cards · prompt_note
                                              「하지 말 것」 — 고객 요건으로 지식베이스를 뒤져 주의 카드를 찾는다. 금지 문장은 만들지 않는다 (§8)

    규약 — 원장(state["evidence"])에 무엇이 어떤 꼴로 쌓이나
    record.py        Evidence · _ev           원장 한 항목의 규약 · 근거 블록 조립 (표시·관계 선언을 함께 싣는다)
    ledger.py        ledger_texts · ledger_sources · ledger_related · ledger_marks · ledger_slots · source_lines · summarize
                                              이번 턴의 원장에서 텍스트·출처·관계·표시·슬롯을 꺼낸다 · 출처 한 줄 표기

    검사 — 답변이 근거 안에 있나 (수치 집합 검사는 ../../verify.py)
    relations.py     check · declared         값–조건 오짝 · 표 행 오짝 · 알려진 오답 주장 (§6). 선언이 있는 카드에 한해 원문 강제를 대신한다
    marks.py         notes_for · tier_of · facing_note
                                              재료 성격 표시 — 문서 레지스트리의 신뢰 등급 · 카드의 customer_facing 선언으로 내부용 주의 (§7)
"""
