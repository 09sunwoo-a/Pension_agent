"""근거 층 — 도구가 근거를 **찾고**, 원장 항목으로 **맞추고**, 답변을 그것과 **대조**하는 것.

LLM 이 고르는 도구는 여기 없다(그건 `tools/`). 여기 있는 것은 도구들이 공통으로 쓰는
검색·규약·검사이고, 이 층은 `tools/`·`effects/`·`nodes/` 를 임포트하지 않는다
(`tests/infra/s03_boundaries.py` 가 고정한다).

    검색 — 지식베이스에서 카드를 고른다
    kb_index.py      LLM 카드 선택용 계층 인덱스(버킷) · 프롬프트 컨텍스트 (적재·검색은 ../../knowledge/kb.py)
    select.py        카드 선택 — LLM 버킷→카드 2단, LLM 이 0건일 때만 n-gram (종류 무관)
    facts_qa.py      fact 도구의 검색·근거 블록 조립
    procedure_qa.py  procedure·screen·channel 도구의 검색·근거 블록 조립
    segment_qa.py    segment 도구의 검색·근거 블록 조립
    pitch_slots.py   화법 검색 전용 슬롯 분해 (tools/pitch·playbook 이 부른다)
    guard.py         「하지 말 것」 — 고객 요건으로 지식베이스를 뒤져 주의 카드를 찾는다. 금지 문장은 만들지 않는다

    규약 — 원장(state["evidence"])에 무엇이 어떤 꼴로 쌓이나
    record.py        원장 한 항목(Evidence)의 규약 · 근거 블록 조립(_ev)
    ledger.py        원장 helper — 이번 턴의 근거에서 텍스트·출처·표시를 꺼낸다

    검사 — 답변이 근거 안에 있나 (수치 집합 검사는 ../../verify.py)
    relations.py     관계 기반 점검 — 값–조건 오짝 · 알려진 오답 대조 (CLAUDE.md §6)
    marks.py         재료 성격 표시 — 신뢰 등급 · 내부용 주의 (CLAUDE.md §7)
"""
