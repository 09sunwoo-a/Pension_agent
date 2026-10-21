"""상담 이력 도구 — history(지난 상담, 이번 세션 제외) · transcript(이번 세션만)."""

from __future__ import annotations

import re
from pension_agent.state import FENCE, AgentState
from pension_agent.evidence.record import Evidence, _ev


# 운영 기록이라 kinds.json 미등록.

#: 과거 상담 기록의 시효 표시. notices 가 아닌 marks — 본문에 넣으면 아래 문단 전체가 지난 상담으로 읽힘.
HISTORY_MARK = "※ 지난 상담 기록입니다 — 그때 나눈 이야기이지 지금 기준 값이 아닐 수 있습니다."

#: 기록 0건 줄 — 확인된 0건(None 이면 «질의 빗나감»과 구별 불가). 이번 세션 제외 명시.
HISTORY_NONE = ("· 기록 없음 — 이 고객과의 지난 상담 기록이 0건입니다"
                "(지금 진행 중인 이번 상담은 제외한 값입니다).")

#: 적재 범위(과거 상담 세션 · 대화 세션 · 세션당 턴 · 발췌 길이). 과거 상담과 대화는 예산 분리 —
#: 합치면 매 턴 쌓이는 대화가 과거 상담을 밀어냄.
HISTORY_SESSIONS, HISTORY_DIALOG_SESSIONS = 3, 1
HISTORY_TURNS, HISTORY_EXCERPT = 8, 120

#: 턴 역할 → 표시 이름. record 는 과거 상담 결과 요약(CRM 자리).
_HISTORY_ROLE = {"user": "직원", "agent": "에이전트", "correction": "수정요청",
                 "tool": "도구실행", "record": "상담기록"}


def _history(state: AgentState, query: str) -> Evidence | None:
    """이 고객의 지난 상담 이력(이번 세션·감사로그 제외).

    - 답변은 발췌만(전문 적재 시 무관 수치까지 인용 허용)
    - query 는 정렬에만 사용(필터 금지 — 표현 차이로 있는 기록을 없다고 답함)
    - 0건은 HISTORY_NONE 으로 반환(확인된 0건). 고객 미선택·저장소 오류는 None
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.common import session_store  # noqa: PLC0415
    try:
        sessions = session_store.list_sessions(customer_id)
    except Exception:
        return None

    # 과거 상담 기록도 세션 저장소 경유 — 원장에서 따로 읽으면 중복 적재
    def _turns(session: dict) -> list[dict]:
        return [t for t in (session.get("turns") or []) if (t.get("text") or "").strip()]

    # 이번 세션 제외 — 방금 한 답변이 «지난 상담»으로 나감
    current = state.get("session_id")
    if current:
        sessions = [s for s in sessions if s.get("session_id") != current]

    # 감사로그 세션(correction-log·tool-log) 제외 — id 가 아닌 역할로 판정
    sessions = [s for s in sessions
                if any((t.get("role") or "") not in _AUDIT_ROLES for t in _turns(s))]

    recent = sorted(sessions, key=lambda s: s.get("started_at") or "", reverse=True)
    records = [s for s in recent if any(t.get("role") == "record" for t in _turns(s))]
    dialogs = [s for s in recent if s not in records and _turns(s)]

    # 2자 이상 질의 토큰이 걸리는 세션 우선
    tokens = [w for w in (query or "").split() if len(w) >= 2]

    def _hits(session: dict) -> int:
        joined = " ".join(t.get("text") or "" for t in _turns(session))
        return sum(1 for w in tokens if w in joined)

    if tokens and any(_hits(s) for s in records):
        records.sort(key=_hits, reverse=True)  # 동점은 sort 안정성으로 최신순 유지

    def _render(session: dict, lines: list[str]) -> None:
        turns = _turns(session)
        lines.append(f"· {(session.get('started_at') or '')[:10]} 상담 ({len(turns)}턴)")
        for turn in turns[-HISTORY_TURNS:]:
            text = " ".join((turn.get("text") or "").split())
            if len(text) > HISTORY_EXCERPT:
                text = text[:HISTORY_EXCERPT] + "…"
            role = _HISTORY_ROLE.get(turn.get("role"), turn.get("role") or "?")
            lines.append(f"  - {role}: {text}")

    # 과거 상담·에이전트 대화 구획 분리. KB-PIN 미포함 — 게이트웨이 개인정보 필터가 400 응답
    lines = ["■ 지금 열려 있는 고객 — 상담 이력 기록"]
    if records:
        lines.append("[과거 상담 기록]")
        for session in records[:HISTORY_SESSIONS]:
            _render(session, lines)
    if dialogs:
        lines.append("[에이전트와 나눈 최근 대화]")
        for session in dialogs[:HISTORY_DIALOG_SESSIONS]:
            _render(session, lines)
    if len(lines) == 1:
        lines.append(HISTORY_NONE)

    # 시효 표시는 과거 상담이 실렸을 때만
    return _ev("history", query, "\n".join(lines),
               # 제목·id 에 KB-PIN 미포함 — 프롬프트·응답 sources 로 나감
               [{"id": "session", "title": "이 고객의 상담 이력",
                 "doc": "상담 이력 기록(과거 상담 + 에이전트가 턴마다 남긴 대화)",
                 "score": None, "page": None}],
               marks=[HISTORY_MARK] if records else None)


#: 이번 상담 대화 적재 범위(턴 수 · 발화 길이). 요약 재료라 길게 — 잘린 수치는 인용 불가.
TRANSCRIPT_TURNS, TRANSCRIPT_EXCERPT = 16, 1500

#: 대화 0건 줄(확인된 0건).
TRANSCRIPT_NONE = "· 기록 없음 — 이번 상담에서 아직 오간 대화가 없습니다."

#: 답변 끝 제안 문구(화면 장치) — 재료에서 제거.
_OFFER_TRAILER = re.compile(r"\n*— [^\n]*\(네 / 아니오\)\s*$")


#: 실행 기록 역할(도구 실행·쪽지 본문·브리핑 수정). 이 역할만 있는 세션은 상담 아님.
_AUDIT_ROLES = frozenset({"tool", "note", "correction"})


def _strip_devices(text: str) -> str:
    """기록된 답변에서 화면 장치(제안 문구·코드블록 펜스)를 뗀 본문."""
    text = _OFFER_TRAILER.sub("", text.strip())
    return "\n".join(ln for ln in text.splitlines() if ln.strip() != FENCE)


def _transcript(state: AgentState, query: str) -> Evidence | None:
    """이번 세션의 직원 발화·에이전트 답변(요약·쪽지 재료).

    고객·세션 미지정이면 None, 대화 0건이면 TRANSCRIPT_NONE.
    """
    customer_id, session_id = state.get("customer_id"), state.get("session_id")
    if not customer_id or not session_id:
        return None
    from pension_agent.common import session_store  # noqa: PLC0415
    try:
        sessions = session_store.list_sessions(customer_id)
    except Exception:
        return None
    current = next((s for s in sessions if s.get("session_id") == session_id), None)
    turns = [t for t in ((current or {}).get("turns") or [])
             if t.get("role") in ("user", "agent") and (t.get("text") or "").strip()]

    lines = [f"■ 이번 상담 대화 기록 — 지금 진행 중인 상담에서 오간 대화 ({len(turns)}턴)"]
    for turn in turns[-TRANSCRIPT_TURNS:]:
        text = " ".join(_strip_devices(turn.get("text") or "").split())
        if len(text) > TRANSCRIPT_EXCERPT:
            text = text[:TRANSCRIPT_EXCERPT] + "…"
        lines.append(f"- {_HISTORY_ROLE.get(turn.get('role'), '?')}: {text}")
    if len(lines) == 1:
        lines.append(TRANSCRIPT_NONE)
    return _ev("transcript", query, "\n".join(lines),
               # KB-PIN 미포함. history 의 id 와 달라야 중복 제거에서 구분됨
               [{"id": f"session.{session_id}", "title": "이번 상담 대화 기록",
                 "doc": "상담 세션 기록(에이전트가 턴마다 남긴 이번 상담의 대화)",
                 "score": None, "page": None}])
