"""대화 상태(AgentState)와 그것을 다루는 최소한의 것들.

노드도 그래프도 여기에 의존하지만, 여기는 아무에게도 의존하지 않는다(kb 제외) — 순환
임포트가 생길 수 없는 잎 모듈이다.

지식베이스(`KB`)는 이 파일에서 한 번만 적재해 모듈 전역으로 들고 있다. 노드들이 각자
적재하면 같은 카드를 여러 벌 들고 있게 되고, 시효성 판정 결과가 노드마다 갈릴 수 있다.
"""

from __future__ import annotations

from typing import TypedDict

from pension_agent.consult_agent.kb import load_kb

HISTORY_LIMIT = 4  # 프롬프트에 넣는 최근 대화 턴 수 (understand·situation_slots 공통)

#: 공용 지식베이스. 프로세스당 한 번만 적재된다.
KB = load_kb()


# ─────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────

class Turn(TypedDict, total=False):
    """history 한 턴. 답변 원문은 담지 않는다 (프롬프트 비용 억제).

    예외가 둘 있고, 둘 다 **다음 턴의 짧은 대답을 해석하는 데 필요한 최소한**이라는 같은
    이유로 남는다. 답변 원문을 통째로 들고 다니지 않으면서 이것만 남기는 것이다.

      pending_action   이 턴이 "이 화면 연계해드릴까요?" 처럼 제안을 했다면 그 인자.
                       다음 턴의 "네" 가 무엇에 대한 승낙인지 잃지 않기 위해서다.
      pending_clarify  이 턴이 답변 대신 판별 질문으로 끝났다면 무엇을 물었는지(§5).
                       다음 턴의 "타행에서요"는 그 질문의 답이지 새 질문이 아니다 —
                       무엇을 물었는지 모르면 되묻기가 성립하지 않는다.

    답변 원문을 남기는 것은 별개의 결정이다(CLAUDE.md §13 '대화 맥락 기반 답변 정정').
    """

    question: str
    customer_type: str | None
    objection_type: str | None
    stage: str | None
    utterance: str | None
    pending_action: dict | None
    pending_clarify: dict | None


class AgentState(TypedDict, total=False):
    question: str                    # [입력] 직원의 자연어 질문
    history: list[Turn]              # [입력] 이전 대화 턴 (호출자가 세션별로 들고 다님)
    customer_id: str | None          # [입력] 현재 열려 있는 브리핑 화면의 고객 id (호출자가 넘김)
    intent: str                      # understand 가 채움 — routing.INTENTS 중 하나
    customer_type: str | None
    objection_type: str | None
    stage: str | None
    utterance: str | None            # 질문에서 추출한 '고객이 한 말'
    # 이번 턴이 답변 대신 판별 질문으로 끝났다면 그 질문과 선택지(§5). 있으면 compose 를
    # 건너뛰고 턴이 끝나며, 화면 연계 제안도 붙지 않는다 — 되묻기와 연계 확인은 다르다.
    clarify: dict | None
    # 근거 원장 — 이번 턴에 도구들이 반환한 근거의 누적. 답변은 이 안에서만 쓰인다.
    # (예전의 hits·broaden_count·verified 를 대신한다 — 화법 체인이 도구 하나로 접혔다.)
    evidence: list                   # [tools.Evidence, ...] 도구별 근거 블록
    plan_calls: list[str]            # 이번 턴에 부른 "도구:질의" 목록 (반복 호출 차단·상한 계산)
    # 부른 것 중 근거를 내놓지 못한 호출. 원장에는 성공한 재료만 실려서, 이게 없으면
    # 계획이 자기가 뭘 불러봤는지 모른 채 같은 호출을 반복한다(반복은 코드가 끊고, 그러면
    # 턴이 '근거 없음'으로 끝난다) — 계획 프롬프트에 실려 질의·도구를 바꾸게 한다.
    plan_misses: list[str]
    # 근거 0건인 채 계획이 끝나려 해서 코드가 한 번 되돌려 보냈다는 표시(§5). 이 표시가
    # 있는데 또 끝내려 하면 그때는 존중한다 — 정직한 '없음' 경로를 막지 않는다.
    plan_retry: bool
    plan_done: bool                  # 계획 루프 종료 신호 (LLM 의 done, 또는 코드가 상한에서 끊음)
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
