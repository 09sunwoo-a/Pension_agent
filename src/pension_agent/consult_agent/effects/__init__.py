"""효과 층 — 답변이 만들어진 **뒤**, 또는 그래프 **밖**에서 부르는 것.

근거를 찾는 쪽(`evidence/`·`tools/`)이 아니라 답을 세상에 내보내는 쪽이다. `tools/`·`evidence/`
는 이 층을 임포트하지 않는다(`tests/infra/s03_boundaries.py`). LLM 이 고르는 것은 하나도
없다 — 부르는 것은 직원의 승낙을 받은 노드(nodes/act)·CLI·app.py 다. [LLM] 은 그 함수가
LLM 을 부른다는 뜻이다.

    승낙 뒤 행위 — 세상에 흔적을 남긴다 (CLAUDE.md §10)
    actions.py   ACTIONS                    행위 레지스트리. nodes/act 가 이름을 박아 부른다 — LLM 이 고르지 않는다
                 open_lms_screen            LMS 발송 화면에 문구를 채워도 되는지 판정한다. dummy 자산의 문구는 거부. 발송은 하지 않는다
                 register_consult_note      상담 이력 등록 (스텁 — 호출 사실만 세션이력에 남긴다)
                 send_memo                  행내 WorkB 쪽지 발송 — MCP 가 연결돼 있을 때만, 본문은 만들지도 고치지도 않는다

    쪽지 초안 — 무엇을 쓸지 (꼴과 발송은 ../../note.py)
    memo.py      draft · Draft              이번 턴의 재료로 제목·본문을 만든다. 근거를 벗어나면 만들지 않는다(nodes/plan.screen) [LLM]
                 material · table_for · to_html
                                            쓸 수 있는 재료(이번 턴 원장 + 화면이 정한 하나) · 본문 아래 값 표 · 평문 → 쪽지 HTML

    화면 연계 — 단말 딥링크
    screens.py   link · lms_screen          mystar-link:// 딥링크 조립 · LMS 발송 화면번호 조회. 화면번호는 코드가 아니라 KB 가 갖는다
                 normalize · scn_no         KB 의 화면번호 표기 정규화 · 7자리/11자리 판별

    화면에 먼저 내미는 것 — 문구는 전부 코드가 조립한다
    suggest.py   history_chips              지난 상담 기록이 있는 고객에게만 — 날짜·경과일을 담은 추천 질문 칩
                 outreach_chips             이 고객 상태에 걸린 세미나·이벤트가 열려 있을 때만
                 followup_questions         답변 끝에 붙일 추천 질문. 조건이 아니면 []

    글자만 낼 수 있는 매체
    render.py    sources_block              답변 + 출처 블록(근거 / 이 고객 상담에서 지켜야 할 것)을 텍스트 한 덩어리로. CLI(__main__)가 쓴다
"""
