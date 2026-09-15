"""효과 층 — 답변이 만들어진 뒤, 또는 그래프 밖에서 부르는 것.

근거를 찾는 쪽이 아니라 답을 세상에 내보내는 쪽이다. tools/·evidence/ 는 이 층을 임포트하지
않는다(tests/infra/s03_boundaries.py). LLM 이 고르는 것은 없다 — 부르는 것은 직원의 승낙을
받은 노드(nodes/act)·CLI·app.py 다.

    승낙 뒤 행위 — 세상에 흔적을 남긴다
    actions.py   ACTIONS                    행위 레지스트리. nodes/act 가 이름을 박아 부른다
                 open_lms_screen            발송 화면에 문구를 채워도 되는지. dummy 자산은 거부
                 register_consult_note      상담 이력 등록 (스텁)
                 send_memo                  WorkB 쪽지 발송 — MCP 연결 시에만

    쪽지 초안 — 무엇을 쓸지 (꼴과 발송은 ../../note.py)
    memo.py      draft · Draft              이번 턴의 재료로 제목·본문. 근거 밖이면 안 만든다 [LLM]
                 material · table_for       쓸 수 있는 재료 · 본문 아래 값 표

    화면 연계
    screens.py   link · lms_screen          mystar-link:// 딥링크 · LMS 발송 화면번호 조회

    화면에 먼저 내미는 것 — 문구는 전부 코드가 조립한다
    suggest.py   history_chips              지난 상담이 있는 고객에게만
                 outreach_chips             걸린 세미나·이벤트가 열려 있을 때만
                 followup_questions         답변 끝에 붙일 추천 질문

    글자만 낼 수 있는 매체
    render.py    sources_block              답변 + 출처 블록을 텍스트 한 덩어리로 (CLI)
"""
