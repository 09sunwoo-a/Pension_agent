"""대화 상태(AgentState)·턴 기록·대화 이력 포맷. 잎 모듈(kb 외 의존 금지), KB 는 여기서 1회 적재."""

from __future__ import annotations

from typing import TypedDict

from pension_agent.knowledge.kb import load_kb

#: 프롬프트에 싣고 다음 턴에 넘기는 최근 대화 턴 수(understand·plan·compose·clarify 공통).
HISTORY_LIMIT = 12

#: 질문을 원문 그대로 싣는 최근 턴 수. 그 앞 턴의 질문은 HISTORY_OLD_CHARS 자에서 접음(기록은 유지).
HISTORY_VERBATIM = 4
HISTORY_OLD_CHARS = 40

#: 턴 기록의 답변 원문 길이 상한 — 메모리 상한(세션 수 × 턴 수만큼 누적).
ANSWER_KEEP = 6000

#: 공용 지식베이스(프로세스당 1회 적재).
KB = load_kb()

#: 화면에서 쪽지 초안을 감싸는 펜스 — 화면 장치라 발송 본문·세션 재료에서는 제거(tools/history._strip_devices).
FENCE = "```"


class Turn(TypedDict, total=False):
    """history 한 턴. 답변 원문(answer)은 기록만 하고 프롬프트에는 미포함 — 실으면 원장 밖 수치로 답 폐기."""

    question: str
    customer_type: str | None
    objection_type: str | None
    stage: str | None
    utterance: str | None
    pending_action: dict | None      # 이 턴의 제안 인자 — 다음 턴 "네" 해석용
    pending_clarify: dict | None     # 이 턴의 판별 질문 — 다음 턴 짧은 답 해석용
    tools: list[str]                 # 이 턴에 쓴 도구 이름(수치 미포함) — 좁히는 후속 질문의 겹침 판정용
    answer: str | None               # 화면에 나간 답변(추천질문 전, ANSWER_KEEP 자). 되묻기·LLM 장애 턴엔 없음. last_answer 도구만 읽음
    sources: list[dict]              # 그 답변의 출처(역할 포함)
    marks: list[str]                 # 그 답변의 재료 성격 표시


class AgentState(TypedDict, total=False):
    question: str                    # [입력] 직원의 자연어 질문
    history: list[Turn]              # [입력] 이전 대화 턴 (호출자가 세션별로 들고 다님)
    customer_id: str | None          # [입력] 현재 열려 있는 브리핑 화면의 고객 id (호출자가 넘김)
    # [입력] 진행 중인 상담 세션 구분자 — history 도구가 «지난번»에서 이 세션 제외
    session_id: str | None
    # [입력] 로그인 직원 사번 — WorkB 쪽지 수신자(기본 본인)·발송 주체. 없으면 환경변수, 그것도 없으면 쪽지 미제안
    employee_id: str | None
    intent: str                      # understand 가 채움 — routing.INTENTS 중 하나
    customer_type: str | None
    objection_type: str | None
    stage: str | None
    utterance: str | None            # 질문에서 추출한 '고객이 한 말'
    # 판별 질문으로 끝난 턴의 질문·선택지. 있으면 compose 생략, 연계 제안 미부착
    clarify: dict | None
    # 적합성 게이트가 표시한 갈래 축 — 원장에 싣지 않음(실으면 compose 가 인용). 되묻기 판정 전용
    branches: list
    # 판정 assume/none 시 작성 프롬프트에 끼울 블록 — 있으면 써 둔 답을 버리고 재작성
    judge_note: str
    # 판정이 실제로 돈 턴의 등급(answer/assume/ask/none). 판정 생략 턴엔 없음
    judge_verdict: str
    # 근거 원장 — 이번 턴 도구 근거의 누적. 답변은 이 안에서만
    evidence: list                   # [tools.Evidence, ...] 도구별 근거 블록
    # 이번 턴 도구 호출 장부 — {"tool", "query", "outcome": found|miss|failed, "reason"}
    # 원장(evidence)은 여기 접지 않음 — 승낙 턴은 호출 없이 원장에 근거를 실음
    steps: list[dict]
    # 근거 0건 종료를 코드가 한 번 되돌렸다는 표시 — 두 번째 종료는 존중
    plan_retry: bool
    plan_done: bool                  # 계획 루프 종료 신호 (LLM 의 done, 또는 코드가 상한에서 끊음)
    # 승낙 턴의 제안 문구(act._show_playbook) — 없으면 작성 LLM 이 «네» 를 직전 질문에 대고 판정
    accepted: str | None
    # LLM 단계 실패 이유(어느 단계든 같은 키). 있으면 '근거 없음' 대신 'LLM 실패' 응답
    llm_error: str
    answer: str                      # [출력] 최종 화법
    # [출력] "raw_evidence"(근거 원문 덤프) 또는 빈 문자열 — 폴백도 answer 를 채우므로 구분 필요
    fallback: str
    sources: list[dict]              # [출력] 근거 카드 (역추적용)
    pending_action: dict | None      # [출력] 확인을 기다리는 도구 실행 제안 (act.offer 가 채움)
    # [출력] 단말 화면 딥링크 {screen, url, label}. 보통 graph.ask 가, 승낙 턴만 노드가 채움(원장이 비어 있음)
    links: list[dict]
    # [출력] 「하지 말 것」 경고·대안 화법. 선언이 없으면 LangGraph 가 노드 반환값에서 버림
    guards: list
    guard_alternatives: list


def numbered_history(history: list[Turn] | None) -> list[tuple[int, Turn]]:
    """프롬프트 턴과 번호 — format_history 의 [n] 과 last_answer 가 같은 번호 사용."""
    return list(enumerate((history or [])[-HISTORY_LIMIT:], 1))


def format_history(history: list[Turn] | None) -> str:
    """최근 대화를 프롬프트용 짧은 텍스트로. 턴마다 질문 한 줄 + 표시, 답변 원문 미포함.

    [n] 은 last_answer 가 턴을 가리키는 좌표. 오래된 턴의 질문은 HISTORY_OLD_CHARS 자에서 접음.
    """
    if not history:
        return ""
    numbered = numbered_history(history)
    verbatim_from = len(numbered) - HISTORY_VERBATIM
    lines = ["이전 대화:"]
    for pos, (i, turn) in enumerate(numbered):
        parsed = " / ".join(
            f"{label} {turn[key]}"
            for label, key in (("고객유형", "customer_type"), ("거절유형", "objection_type"), ("단계", "stage"))
            if turn.get(key)
        )
        question = turn["question"]
        if pos < verbatim_from and len(question) > HISTORY_OLD_CHARS:
            question = question[:HISTORY_OLD_CHARS] + "…"
        lines.append(f"[{i}] 직원: {question}" + (f" → {parsed}" if parsed else ""))
        # 걸린 제안 표시 — understand 가 «네» 를 confirm_action 으로 읽는 단서
        pending = turn.get("pending_action")
        if pending:
            lines.append(f"    (에이전트가 '{pending['label']}' 을 제안하고 답을 기다리는 중)")
        # 판별 질문으로 끝난 턴 — 다음 짧은 대답은 이 질문의 답
        asked = turn.get("pending_clarify")
        if asked:
            options = " / ".join(asked.get("options") or [])
            lines.append(f"    (에이전트가 되물음: {asked.get('question')}"
                         + (f" — 선택지 {options}" if options else "") + ")")
    return "\n".join(lines) + "\n"
