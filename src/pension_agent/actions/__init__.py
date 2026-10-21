"""효과 층 — 답변 뒤 또는 그래프 밖에서 부르는 행위. LLM 선택 없음, 직원 승낙 뒤 호출.

    actions.py   ACTIONS                    행위 레지스트리(nodes/act 가 이름으로 호출)
                 open_lms_screen            발송 화면 문구 채움 판정. dummy 자산 거부
                 register_consult_note      상담 이력 등록 (스텁)
                 send_memo                  WorkB 쪽지 발송 — MCP 연결 시에만
    memo.py      draft · Draft              이번 턴 재료로 쪽지 제목·본문 [LLM]
                 material · table_for       쓸 수 있는 재료 · 본문 아래 값 표
    screens.py   link · lms_screen          mystar-link:// 딥링크 · LMS 발송 화면번호 조회
    messages.py  messages_in                답변이 인용한 고객 발송 문구(복사 블록 재료)
    suggest.py   history_chips              지난 상담이 있는 고객에게만
                 outreach_chips             걸린 세미나·이벤트가 열려 있을 때만
                 followup_questions         답변 끝 추천 질문
    render.py    sources_block              답변 + 출처 블록 텍스트 (CLI)
"""
