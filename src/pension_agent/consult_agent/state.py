"""대화 상태(AgentState)와 그것을 다루는 최소한의 것들.

노드도 그래프도 여기에 의존하지만, 여기는 아무에게도 의존하지 않는다(kb 제외) — 순환
임포트가 생길 수 없는 잎 모듈이다.

지식베이스(`KB`)는 이 파일에서 한 번만 적재해 모듈 전역으로 들고 있다. 노드들이 각자
적재하면 같은 카드를 여러 벌 들고 있게 되고, 시효성 판정 결과가 노드마다 갈릴 수 있다.
"""

from __future__ import annotations

from typing import TypedDict

from pension_agent.knowledge.kb import load_kb

#: 프롬프트에 넣고 다음 턴에 넘기는 최근 대화 턴 수(understand·plan·compose·clarify 공통).
#:
#: 오래 4 였다. 한 턴이 질문 한 줄이라 넷이면 «직전 맥락»은 덮였지만, 실제 상담은 고객
#: 하나를 열어 두고 열 턴 넘게 이어진다 — 다섯 턴 전에 되물었던 갈래·여섯 턴 전에 나열한
#: 상품 목록이 창 밖으로 밀려 「그때 그거」가 새 질문으로 읽혔다. 프롬프트에 실리는 것은
#: 턴마다 한두 줄뿐이라(format_history — 답변 원문은 싣지 않는다) 12 로 늘려도 토큰
#: 비용은 몇백 자다. 답변 원문(`Turn.answer`)은 프롬프트가 아니라 `last_answer` 도구가
#: 읽으므로 이 수와 무관하게 프롬프트 비용에 안 잡힌다.
HISTORY_LIMIT = 12

#: 턴 기록에 남기는 답변 원문의 길이 상한. 프롬프트 비용이 아니라 **메모리** 상한이다 —
#: 기록은 프로세스 메모리(context_store)에 세션 수 × 턴 수만큼 쌓이고, 게이트웨이 경로에서는
#: 호출자가 들고 다닐 수도 있다. 화면 답변은 길어야 2천 자 안팎이라 넉넉하다.
ANSWER_KEEP = 6000

#: 공용 지식베이스. 프로세스당 한 번만 적재된다.
KB = load_kb()


# ─────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────

class Turn(TypedDict, total=False):
    """history 한 턴. **프롬프트에는** 답변 원문을 싣지 않는다 (프롬프트 비용 억제 · §6).

    예외가 둘 있고, 둘 다 **다음 턴의 짧은 대답을 해석하는 데 필요한 최소한**이라는 같은
    이유로 남는다. 답변 원문을 프롬프트에 통째로 들고 다니지 않으면서 이것만 남기는 것이다.

      pending_action   이 턴이 "이 화면 연계해드릴까요?" 처럼 제안을 했다면 그 인자.
                       다음 턴의 "네" 가 무엇에 대한 승낙인지 잃지 않기 위해서다.
      pending_clarify  이 턴이 답변 대신 판별 질문으로 끝났다면 무엇을 물었는지(§5).
                       다음 턴의 "타행에서요"는 그 질문의 답이지 새 질문이 아니다 —
                       무엇을 물었는지 모르면 되묻기가 성립하지 않는다.
      tools            이 턴이 무슨 재료로 답했는지(도구 이름만). 답변 원문이 없으니
                       **다음 턴은 자기가 방금 무엇을 나열했는지 볼 수 없고**, 그래서
                       "그 중에 ~" 같은 좁히는 후속 질문에 앞 답을 통째로 다시 세웠다
                       (실측: T11 — 목록 8종 + 제외 4종을 그대로 반복, 1,021자).
                       수치가 아니라 **이름만** 남긴다: 답변 원문을 프롬프트에 넣으면
                       LLM 이 그 수치를 되받고, 그 수치는 이번 턴 원장 밖이라
                       `verify` 가 답을 통째로 버린다(§6).

    ━━ 답변 원문은 기록에는 남고 프롬프트에는 안 실린다 (2026-09-10) ━━
    「고객에게 할 말 좀 더 짧게 줄여줘」는 직전 답변을 **재료**로 써야 답할 수 있는데, 그
    원문이 어디에도 없었다(고객 화면이 열린 세션의 상담이력에만 있고, 그것도 고객 화면 없는
    대화에는 없다). 그래서 답변 원문을 여기 남긴다 — 다만 **`format_history` 는 여전히
    싣지 않는다.** 프롬프트에 실으면 LLM 이 그 수치를 되받고 그 수치는 이번 턴 원장 밖이라
    §6 이 답을 통째로 버린다(위 tools 주석의 그 사고). 읽는 곳은 `last_answer` 도구 하나이고,
    도구가 읽으면 그것이 원장이 되어 인용이 허용된다 — `transcript` 와 같은 규약이다.

      answer   이 턴이 화면에 내보낸 답변(추천질문을 붙이기 전, ANSWER_KEEP 자로 자른다).
               되묻기·LLM 장애로 끝난 턴에는 없다 — 그 턴은 «답변»이 아니다.
      sources  그 답변의 출처(역할 포함). 다시 쓴 답변의 근거는 원래 답변의 근거와 같다.
      marks    그 답변에 붙은 재료 성격 표시(§7) — 표시는 재료에 걸리므로 다시 써도 붙는다.
    """

    question: str
    customer_type: str | None
    objection_type: str | None
    stage: str | None
    utterance: str | None
    pending_action: dict | None
    pending_clarify: dict | None
    tools: list[str]
    answer: str | None
    sources: list[dict]
    marks: list[str]


class AgentState(TypedDict, total=False):
    question: str                    # [입력] 직원의 자연어 질문
    history: list[Turn]              # [입력] 이전 대화 턴 (호출자가 세션별로 들고 다님)
    customer_id: str | None          # [입력] 현재 열려 있는 브리핑 화면의 고객 id (호출자가 넘김)
    # [입력] 지금 진행 중인 상담 세션 구분자(graph.ask 가 넘김). history 도구가 «지난번»을
    # 답할 때 이 세션을 제외하기 위해 있다 — 이번 세션의 직전 턴들은 이미 대화 맥락으로
    # 프롬프트에 실려 있어서, 상담 기록 재료에 다시 실리면 방금 한 말이 «지난 상담»이 된다.
    session_id: str | None
    # [입력] 로그인한 직원의 사번(호출자가 넘김). WorkB 쪽지의 수신자가 이 값이다 —
    # 코드가 정하므로 LLM 이 수신자를 만들어낼 자리가 없다. 없으면 환경변수로 떨어지고,
    # 그것도 없으면 쪽지 발송을 제안하지 않는다(workb.employee_id).
    employee_id: str | None
    intent: str                      # understand 가 채움 — routing.INTENTS 중 하나
    customer_type: str | None
    objection_type: str | None
    stage: str | None
    utterance: str | None            # 질문에서 추출한 '고객이 한 말'
    # 이번 턴이 답변 대신 판별 질문으로 끝났다면 그 질문과 선택지(§5). 있으면 compose 를
    # 건너뛰고 턴이 끝나며, 화면 연계 제안도 붙지 않는다 — 되묻기와 연계 확인은 다르다.
    clarify: dict | None
    # 적합성 게이트가 표시한 «답이 갈리는 축»(tools.record_branches). 근거가 아니라 후보가
    # 어떻게 갈렸나의 기록이라 원장(evidence)에 싣지 않는다 — 원장에 실으면 답변 재료가
    # 되어 compose 가 그 문구를 인용한다. 쓰는 곳은 되묻기 판정 하나다(nodes/clarify.py).
    branches: list
    # 판정이 «전제를 밝히고 답하라»(assume) 또는 «핵심 대상이 자료에 없다»(none)로 끝났을
    # 때 작성 프롬프트에 끼울 블록(§5). 판정과 작성이 동시에 도는 구조라(nodes/answer.py)
    # 이 값이 있으면 이미 써 둔 답을 버리고 블록을 얹어 한 번 다시 쓴다.
    judge_note: str
    # 판정이 실제로 돈 턴의 등급(answer/assume/ask/none). 관문에서 걸러 판정을 **안 돌린**
    # 턴에는 없다 — 「판정 안 함」과 「answer 로 판정」은 다른 사건이라 계측이 갈라 센다.
    judge_verdict: str
    # 근거 원장 — 이번 턴에 도구들이 반환한 근거의 누적. 답변은 이 안에서만 쓰인다.
    # (예전의 hits·broaden_count·verified 를 대신한다 — 화법 체인이 도구 하나로 접혔다.)
    evidence: list                   # [tools.Evidence, ...] 도구별 근거 블록
    # 이번 턴에 **해 본 것**의 기록 — 호출 하나가 한 항목이다.
    #   {"tool": 이름, "query": 물은 말, "outcome": found|miss|failed, "reason": 원인(failed)}
    #
    # 예전에는 이 한 사건이 리스트 셋에 흩어져 있었다(`plan_calls`·`plan_misses`·
    # `plan_failed`). 그러면 «저 호출은 어떻게 됐나»를 알려면 서명 문자열(`"screen:질의"`)을
    # 잘라 다른 리스트의 dict 와 맞춰 봐야 하고, 결과 종류가 하나 늘 때마다 리스트가 하나씩
    # 늘었다. 지금은 종류가 `outcome` 한 칸이라, 새 결과를 더해도 늘어나는 것은 값 하나다.
    #
    # **원장(evidence)은 여기 접지 않는다.** 승낙 턴(nodes/act.py)은 도구 호출 없이 원장에
    # 근거를 싣는다 — «모든 근거에는 그것을 만든 스텝이 있다»가 성립하지 않는다. 게다가
    # 원장은 인용 허용 집합의 재료라(§6) 소비처가 많고, 그 경계를 이 리팩터로 건드리면
    # «옳은 문장을 거부하는» 쪽으로 틀릴 수 있다. 여기 있는 것은 계획의 장부뿐이다.
    steps: list[dict]
    # 근거 0건인 채 계획이 끝나려 해서 코드가 한 번 되돌려 보냈다는 표시(§5). 이 표시가
    # 있는데 또 끝내려 하면 그때는 존중한다 — 정직한 '없음' 경로를 막지 않는다.
    plan_retry: bool
    plan_done: bool                  # 계획 루프 종료 신호 (LLM 의 done, 또는 코드가 상한에서 끊음)
    # 이번 턴이 «직전 제안을 승낙받아 재료를 싣는 턴»이면 그 제안 문구(act._show_playbook 이
    # 채운다). 이 턴의 질문은 "네" 한 마디라, 이게 없으면 작성 LLM 은 무엇을 쓰라는 것인지
    # 알 방법이 없어 <자료> 를 **직전 턴의 질문**에 대고 재고 "그 자료는 없어요"로 답한다
    # (nodes/plan.py::compose 의 ACCEPTED_BLOCK 주석). 무엇을 보여주기로 했는지는 제안한
    # 턴이 이미 정했으므로, 그 사실을 코드가 실어 준다(CLAUDE.md §10).
    accepted: str | None
    # LLM 단계가 **깨져서** 끝났을 때의 이유(호출 실패·규격 밖 응답). 정상이면 비어 있다.
    # 슬롯 분해(situation_slots)·계획(plan_step)·문장 작성(compose) 어디서 실패해도 같은
    # 키에 남긴다 — 어느 단계에서 실패했든 직원이 받는 답은 같아야 한다(CLAUDE.md §11).
    # 이 값이 있으면 compose 가 '근거 없음'이 아니라 'LLM 실패'라고 답한다 — 찾아보고 없는
    # 것과 찾아보지도 못한 것을 같은 문장으로 말하면 있는 자료를 없다고 말하게 된다.
    llm_error: str
    answer: str                      # [출력] 최종 화법
    sources: list[dict]              # [출력] 근거 카드 (역추적용)
    pending_action: dict | None      # [출력] 확인을 기다리는 도구 실행 제안 (act.offer 가 채움)
    # [출력] 「하지 말 것」 — 코드가 붙이는 경고와 대안 화법. 프롬프트 지시만으로는 LLM 이
    # 무시해도 아무도 모르므로(verify 는 톤을 보지 않는다) 화면이 함께 띄워야 하는 값이다.
    # 선언이 없으면 LangGraph 가 노드 반환값에서 조용히 버린다 — 그래서 여기 있어야 한다.
    guards: list
    guard_alternatives: list


# ─────────────────────────────────────────────────────────────
# 대화 이력 → 프롬프트 조각
# ─────────────────────────────────────────────────────────────

def format_history(history: list[Turn] | None) -> str:
    """최근 대화를 프롬프트에 넣을 짧은 텍스트로 요약한다.

    라우팅(understand)·슬롯 분해(situation_slots)뿐 아니라 **근거 수집 계획과 답변
    작성**도 이걸 받는다(§2-1 · §12 gap 1). 후속 질문("그럼 안 된다고 하면요?")은 이전
    턴을 이어받아야 무엇을 묻는지 정해지는데, 계획·작성이 그 맥락을 못 보면 이번 질문
    한 줄만으로 재료를 고르게 된다.

    **답변 원문(`Turn.answer`)은 싣지 않는다** — 그것은 `last_answer` 도구가 원장으로
    읽는다(Turn 주석). 여기 실리는 것은 턴마다 질문 한 줄과 짧은 표시뿐이다.
    """
    if not history:
        return ""
    lines = ["이전 대화:"]
    for i, turn in enumerate(history[-HISTORY_LIMIT:], 1):
        parsed = " / ".join(
            f"{label} {turn[key]}"
            for label, key in (("고객유형", "customer_type"), ("거절유형", "objection_type"), ("단계", "stage"))
            if turn.get(key)
        )
        lines.append(f"[{i}] 직원: {turn['question']}" + (f" → {parsed}" if parsed else ""))
        # 도구 실행 제안이 걸려 있으면 드러낸다 — 이게 있어야 understand 가 이번의 "네" 를
        # 새 질문이 아니라 그 제안에 대한 확인(confirm_action)으로 읽는다.
        pending = turn.get("pending_action")
        if pending:
            lines.append(f"    (에이전트가 '{pending['label']}' 을 제안하고 답을 기다리는 중)")
        # 판별 질문으로 끝난 턴 — 다음 줄의 짧은 대답은 이 질문의 답이다.
        asked = turn.get("pending_clarify")
        if asked:
            options = " / ".join(asked.get("options") or [])
            lines.append(f"    (에이전트가 되물음: {asked.get('question')}"
                         + (f" — 선택지 {options}" if options else "") + ")")
    return "\n".join(lines) + "\n"
