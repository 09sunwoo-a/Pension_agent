"""그래프 노드 — LangGraph 에 add_node 되는 함수만. 조립은 ../graph.py, 분기는 ../routing.py.

한 턴은 understand → (plan ⇄ tools) → compose → offer 순서로 흐르고, 나머지 노드는 understand
가 가려낸 특수 턴이다. [LLM] 표시가 없는 노드는 LLM 을 부르지 않는다.

    understand.py   understand      질문(+이전 대화)에서 intent·utterance 만 가린다. 도메인 어휘가 없는 라우팅 전용 [LLM]
    plan.py         plan_step       계획 루프 — 다음에 부를 도구 하나를 고르고 실행해 원장(state["evidence"])에 쌓는다. 상한 MAX_STEPS [LLM]
                    compose         원장만으로 답변을 쓰고, 코드가 근거와 대조한다 — 원장 밖 수치·값–조건 오짝·빠진 필수 표시 [LLM]
                    screen          화면 답변과 쪽지 초안(effects/memo)이 같이 거치는 근거 검사
                    llm_down        LLM 미연결 턴 — 답 대신 원인을 말한다 (§11)
    answer.py       answer          형태 판정(clarify)과 답변 작성(compose)을 동시에 돌리고 하나를 고른다. assume·none 이면 블록을 얹어 한 번 다시 쓴다 [LLM×2]
    clarify.py      clarify         답의 형태 판정 — 답한다 · 전제를 밝히고 답한다 · 되묻는다 · 핵심 대상이 없다 (§5) [LLM]
    meta.py         agent_help      「뭘 도와줄 수 있어」 — 지식베이스 메타데이터로 답한다
    lms.py          lms_link        인용된 문구로 LMS 발송 화면 연계를 제안한다. 보내지 않는다 (§10)
    correction.py   correction      브리핑 산문 수정 — 편집 가능 필드면 재작성+검증, 아니면 거절. 「방금 한 답변을 고쳐 달라」는 plan 으로 넘긴다 [LLM]
    act.py          offer           답변이 가리키는 단말 화면이 있으면 「연계해드릴까요」를 붙이고 pending_action 을 둔다
                    confirm_action  직전 턴 제안에 대한 네/아니오 — 딥링크 연계 또는 철회. 승낙 뒤 실행은 effects/actions
"""
