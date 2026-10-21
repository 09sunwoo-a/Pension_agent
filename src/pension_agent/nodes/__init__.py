"""그래프 노드 — LangGraph add_node 대상 함수만. 조립은 ../graph.py, 분기는 ../routing.py.

흐름: understand → (plan ⇄ tools) → compose → offer. [LLM] 없는 노드는 LLM 미호출.

    understand.py   understand      intent·utterance 분류. 라우팅 전용 [LLM]
    plan.py         plan_step       도구 하나 선택·실행 후 원장 적재. 상한 MAX_STEPS [LLM]
                    compose         원장만으로 답변 작성 + 근거 대조 [LLM]
                    screen          화면 답변·쪽지 초안 공용 근거 검사
                    llm_down        LLM 미연결 턴 — 원인 안내
    answer.py       answer          clarify·compose 동시 실행 후 택일 [LLM×2]
    clarify.py      clarify         답 형태 판정 — 답함·전제·되물음·없음 [LLM]
    meta.py         agent_help      에이전트 자신에 대한 질문 응답 [LLM]
    lms.py          lms_link        인용 문구로 LMS 발송 화면 연계 제안. 발송 안 함
    correction.py   correction      브리핑 산문 수정. 편집 불가 필드는 거절 [LLM]
    act.py          offer           답변이 가리키는 화면 연계 제안
                    confirm_action  직전 제안에 대한 네/아니오 — 연계 또는 철회
"""
