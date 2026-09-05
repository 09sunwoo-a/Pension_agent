"""상담 이력 도구 — history(지난 상담) · transcript(이번 상담).

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

import re
from pension_agent.consult_agent.state import AgentState
from pension_agent.consult_agent.tools.base import Evidence, _ev


# ─────────────────────────────────────────────────────────────
# 상담 이력 — "지난번에 무슨 얘기 했지"
#
# 기록은 이미 턴마다 쌓이고 있었다(graph.ask → session_store.append_turn). 없던 것은
# **읽는 도구**였고, 능력 표면은 도구 목록이므로(§3) 없는 도구는 없는 능력이다. 그래서
# 그 질문은 "제가 도와드릴 수 있는 것" 안내로 끝났고, 다음 턴 답변에는 LLM 이 지어낸
# "이전 대화 내용은 기억하지 못해요"가 따라 나왔다 — 사실도 아니었다. 재료를 주지 않으면
# LLM 은 없는 재료에 대해 말을 만든다.
#
# 지식 카드가 아니라 **운영 기록**이라 kinds.json 에 등록하지 않는다(session_store 참고).
# ─────────────────────────────────────────────────────────────

#: 상담 기록 재료에 항상 붙는 표시. `notices` 라서 답변에서 빠지면 코드가 채워 넣는다.
#: 지난 상담의 안내가 지금도 맞다는 보장은 없다 — 기록은 "그때 무슨 얘기를 했나"의
#: 근거이지 현재 기준 값의 근거가 아니고, 둘을 섞으면 낡은 값이 오늘의 답으로 나간다.
HISTORY_MARK = "※ 지난 상담 기록입니다 — 그때 나눈 이야기이지 지금 기준 값이 아닐 수 있습니다."

#: 기록이 0건일 때 싣는 줄. **없다는 것도 이 도구가 확인한 값이다**(_history 머리말) —
#: 이 줄이 없던 동안 0건은 «질의가 빗나감»과 구별되지 않아 계획이 다른 도구를 헛돌았다.
#: «이번 세션 제외»를 밝힌다: 방금 나눈 대화는 재료에서 빼기 때문에(아래), 그 사실을
#: 안 적으면 조금 전 대화를 기억하는 직원에게는 이 줄이 거짓말로 읽힌다.
HISTORY_NONE = ("· 기록 없음 — 이 고객과의 지난 상담 기록이 0건입니다"
                "(지금 진행 중인 이번 상담은 제외한 값입니다).")

#: 재료에 싣는 범위(과거 상담 세션 수 · 대화 세션 수 · 세션당 턴 수 · 발췌 길이). 상담 중에
#: 읽을 분량을 넘기면 아무도 안 읽고 원장만 무거워진다.
#:
#: 과거 상담(record)과 에이전트 대화(user/agent)의 예산을 **따로** 둔다. 하나의 최신순
#: 창을 같이 쓰면 graph.ask 가 매 턴 2턴씩 쌓는 대화 세션이 금방 창을 차지해, 정작
#: "지난번에 무슨 얘기 했지"의 지난번(과거 상담)이 밀려난다 — 이 도구를 쓰는 이유가
#: 사라지는 순서다.
HISTORY_SESSIONS, HISTORY_DIALOG_SESSIONS = 3, 1
HISTORY_TURNS, HISTORY_EXCERPT = 8, 120

#: 세션 턴의 역할 → 사람이 읽는 이름. `record` 는 발화가 아니라 «과거 상담 결과 요약»이다
#: (실서비스의 CRM 상담 기록 자리 — scripts/seed_sessions.py 가 목업으로 심는다).
_HISTORY_ROLE = {"user": "직원", "agent": "에이전트", "correction": "수정요청",
                 "tool": "도구실행", "record": "상담기록"}


def _history(state: AgentState, query: str) -> Evidence | None:
    """이 고객과 지난 상담에서 무슨 얘기를 했는지. 고객 화면이 닫혀 있으면 없다(§3).

    에이전트 답변은 **발췌만** 싣는다. 통째로 실으면 원장이 지난 상담의 문장으로 뒤덮여
    이번 질문과 무관한 수치까지 검증을 통과하게 된다. 발췌라도 그 안의 값은 원장에
    들어가므로, 재료가 시효 표시를 달고 나온다(HISTORY_MARK) — 답변이 그 값을 '지금
    기준'으로 말하지 않게 하는 것은 그 표시다.

    계획 루프가 정한 `query` 는 **선별에만** 쓴다: 질의어가 걸리는 과거 상담을 최신순보다
    앞세운다(코드 매칭 — LLM 아님). 매칭 0건은 이력 0건이 아니므로 최신순 그대로 싣는다 —
    "관련 상담이 없습니다"를 도구가 지어내면 그게 근거가 되기 때문이다. 걸러내지 않고
    순서만 바꾸는 이유도 같다: 질의어는 표현이 다를 수 있고, 뺐다가 틀리면 있는 기록을
    없다고 답하게 된다.

    **기록이 0건인 것도 재료다**(2026-09-02 — 그 전에는 None 이었다). 근거는 §3 의
    「«정상»인 상태도 재료다」와 §5 의 「재료는 0건일 때 침묵하지 않는다」이고, 고친 이유는
    실측이다: 상담 기록이 없는 고객에게 「지난 상담에서 무슨 얘기 했지?」를 물으면 이 도구가
    None 을 돌려주고, 그러면 «질의를 잘못 골라 0건» 과 구별되지 않아 `_wrap_up` 이 재계획을
    걸었다(gap 23 의 장치가 정상 동작한 것이다). 재계획은 안 써 본 `customer` 를 골랐고,
    그 도구는 브리핑 한 편(LLM 11 회)을 통째로 끌어와 원장에 실었다 — 답은 "지난 상담 기록이
    없어요" 한 줄인데 계획이 세 바퀴 돌고, 출처에는 질문과 무관한 ⑥⑦⑧ 화법 카드 다섯 장이
    «근거»로 나란히 섰다.

    저장소를 읽어 0건임을 확인한 것은 **판정**이지 지어낸 문장이 아니다 — 계좌 상태를
    "미설정"으로 싣는 것과 같은 자리다. 지어내지 않는다는 규약이 지키는 것은 여전히 남는다:
    이 도구는 고객 화면이 닫혀 있거나(어느 고객인지 없다) 저장소를 읽지 못하면 그대로
    None 이다 — 그 둘은 «확인한 없음»이 아니라 «확인하지 못함»이라 다른 도구를 써 볼
    여지가 남아 있다.
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent import session_store  # noqa: PLC0415
    try:
        sessions = session_store.list_sessions(customer_id)
    except Exception:
        return None

    # 읽는 곳은 **여기 하나**다. 과거 상담 기록(직원이 고객과 나눈 것)도 세션 저장소에
    # 들어와 있다 — 원장에서 따로 읽는 두 번째 경로를 두면 같은 상담이 두 번 실린다
    # (scripts/seed_sessions.py 가 목업을 심고, 실서비스에서는 CRM 이 같은 자리를 채운다).
    def _turns(session: dict) -> list[dict]:
        return [t for t in (session.get("turns") or []) if (t.get("text") or "").strip()]

    # **지금 진행 중인 세션은 «지난번»이 아니다.** graph.ask 가 턴마다 기록하므로 직전
    # 턴이 이미 이 저장소에 있는데, 그걸 재료로 실으면 30초 전 자기 답변이 «지난 상담
    # 기록»으로 나간다(시연 대본 T5 실측 — "두 번의 상담 기록" 중 하나가 방금 한 T4 였다).
    # 이번 세션의 직전 턴들은 대화 맥락(history)으로 이미 프롬프트에 실려 있어서 재료로
    # 중복할 이유도 없다.
    current = state.get("session_id")
    if current:
        sessions = [s for s in sessions if s.get("session_id") != current]

    recent = sorted(sessions, key=lambda s: s.get("started_at") or "", reverse=True)
    records = [s for s in recent if any(t.get("role") == "record" for t in _turns(s))]
    dialogs = [s for s in recent if s not in records and _turns(s)]

    # 질의어 매칭 — 2자 이상 토큰이 상담 텍스트에 부분일치하면 그 세션을 앞세운다.
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

    # 구획을 나눠 싣는다 — 오늘 나눈 대화가 「과거 상담」으로 오독되면 방금 한 말이
    # 지난 상담의 근거처럼 인용된다.
    lines = [f"■ 고객 {customer_id} — 상담 이력 기록"]
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

    # 시효 표시는 **과거 상담이 실제로 실렸을 때만** 단다. 방금 나눈 대화에 "지난 상담
    # 기록입니다"를 붙이면 표시가 거짓말을 하고, 매번 붙는 표시는 읽히지 않는다 —
    # 정작 낡은 값이 실린 턴에서도 그냥 지나가게 된다.
    return _ev("history", query, "\n".join(lines),
               [{"id": f"session.{customer_id}", "title": f"고객 {customer_id} 상담 이력",
                 "doc": "상담 이력 기록(과거 상담 + 에이전트가 턴마다 남긴 대화)",
                 "score": None, "page": None}],
               notices=[HISTORY_MARK] if records else [])


#: 이번 상담 대화 재료의 범위 — 실을 턴 수와 발화 한 건의 길이. `history` 의 발췌(120자)보다
#: 훨씬 길다. 그쪽은 «그때 무슨 얘기를 했나»의 실마리면 되지만, 이쪽은 **요약의 재료**라
#: 답변이 말한 수치·화면번호가 잘리면 요약이 그것을 옮길 수 없다(§6 — 원장 밖 수치는 잘린다).
TRANSCRIPT_TURNS, TRANSCRIPT_EXCERPT = 16, 1500

#: 이번 상담에서 아직 오간 대화가 없을 때 싣는 줄. 0건도 확인한 값이다(`HISTORY_NONE` 과 같은
#: 이유) — 비워 두면 «질의가 빗나감»과 구별되지 않아 계획이 다른 도구를 헛돈다.
TRANSCRIPT_NONE = "· 기록 없음 — 이번 상담에서 아직 오간 대화가 없습니다."

#: 기록된 답변 끝에 붙어 있는 제안 문구(act.offer — 화면 연계·화법 제시·쪽지 보내기). 안내가
#: 아니라 화면 장치라 요약 재료에서 뗀다 — 추천질문을 기록에서 빼는 것과 같은 이유다(graph.ask).
#: 쪽지 본문을 감싼 코드블록 펜스(memo.FENCE)도 같은 이유로 뗀다(_strip_devices).
_OFFER_TRAILER = re.compile(r"\n*— [^\n]*\(네 / 아니오\)\s*$")


def _strip_devices(text: str) -> str:
    """기록된 답변에서 화면 장치(제안 문구·코드블록 펜스)를 뗀 본문."""
    from pension_agent.consult_agent import memo  # noqa: PLC0415
    text = _OFFER_TRAILER.sub("", text.strip())
    return "\n".join(ln for ln in text.splitlines() if ln.strip() != memo.FENCE)


def _transcript(state: AgentState, query: str) -> Evidence | None:
    """**이번 상담**(지금 진행 중인 세션)에서 지금까지 오간 대화 — `history` 의 반대편이다.

    `history` 는 이번 세션을 제외한다(방금 한 말이 «지난 상담»으로 나가면 안 되므로). 그러면
    「지금까지 얘기한 거 정리해서 쪽지로 보내줘」에 쓸 재료가 어디에도 없다 — 대화 맥락
    (`format_history`)은 직원 질문만 싣고 답변 원문을 들지 않기 때문이다(state.Turn). 요약은
    답변에 무엇이 있었는지를 봐야 쓸 수 있고, 그 원문은 진입점이 턴마다 세션 저장소에 남긴
    것 하나뿐이다(graph.ask — 추천질문을 붙이기 전의 답변).

    싣는 것은 **직원 발화와 에이전트 답변**이다. 도구 실행 기록(role=tool)은 대화가 아니라
    빼고, 기록에 남은 화면 연계 제안 문구도 뗀다. 답변 원문이 원장에 실리므로 요약이 그
    안의 수치·화면번호를 옮겨도 검증을 통과한다 — 원장 밖에서 새로 계산하지는 못한다.

    고객 화면이 닫혀 있거나 세션 구분자가 없으면 None 이다 — «확인하지 못함»이라 다른
    도구를 써 볼 여지를 남긴다. 세션은 있는데 대화가 0건이면 그것도 재료다(TRANSCRIPT_NONE).
    """
    customer_id, session_id = state.get("customer_id"), state.get("session_id")
    if not customer_id or not session_id:
        return None
    from pension_agent import session_store  # noqa: PLC0415
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
               [{"id": f"session.{customer_id}.{session_id}", "title": "이번 상담 대화 기록",
                 "doc": "상담 세션 기록(에이전트가 턴마다 남긴 이번 상담의 대화)",
                 "score": None, "page": None}])
