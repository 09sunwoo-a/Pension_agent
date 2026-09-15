"""그래프 노드 — LangGraph 에 add_node 되는 함수만. 조립은 ../graph.py, 분기는 ../routing.py.

한 턴은 understand → (plan ⇄ tools) → compose → offer 로 흐르고, 나머지는 understand 가
가려낸 특수 턴이다. [LLM] 이 없는 노드는 LLM 을 부르지 않는다.

    understand.py   understand      질문에서 intent·utterance 만 가린다. 라우팅 전용 [LLM]
    plan.py         plan_step       도구 하나를 고르고 실행해 원장에 쌓는다. 상한 MAX_STEPS [LLM]
                    compose         원장만으로 답을 쓰고 코드가 근거와 대조한다 [LLM]
                    screen          화면 답변과 쪽지 초안이 같이 거치는 근거 검사
                    llm_down        LLM 미연결 턴 — 답 대신 원인을 말한다
    answer.py       answer          clarify 와 compose 를 동시에 돌리고 하나를 고른다 [LLM×2]
    clarify.py      clarify         답의 형태 — 답한다·전제를 밝힌다·되묻는다·없다 [LLM]
    meta.py         agent_help      「뭘 도와줄 수 있어」 — KB 메타데이터로 답한다
    lms.py          lms_link        인용된 문구로 LMS 발송 화면 연계를 제안한다. 보내지 않는다
    correction.py   correction      브리핑 산문 수정. 편집 불가 필드면 거절 [LLM]
    act.py          offer           답변이 가리키는 화면이 있으면 「연계해드릴까요」
                    confirm_action  직전 제안에 대한 네/아니오 — 연계 또는 철회
"""
