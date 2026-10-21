"""답 형태 판정 — 답함(answer) · 전제 밝히고 답함(assume) · 되물음(ask) · 없음(none).

계획 루프 뒤·작성 전에 실행. 되물을지는 LLM, 되물을 수 있는 자리(관문)는 코드가 결정.
되물음 턴은 offer 미경유(graph.py).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pension_agent.common import observability
from pension_agent import tools
from pension_agent.prompts import (
    CLARIFY_PROMPT, COMPOSE_MISSING_BLOCK, COMPOSE_PREMISE_BLOCK, JUDGE_BRANCHES_BLOCK,
)
from pension_agent.state import AgentState, format_history
from pension_agent.common.llm import LLMError, generate
from pension_agent.evidence.verify import numbers

#: 판정 응답 토큰 상한 — 등급 + (되물음 시) 질문·선택지.
CLARIFY_MAX_TOKENS = 250

#: 판정 등급. ASSUME·NONE 은 작성 프롬프트 블록으로 재작성.
ANSWER, ASSUME, ASK, NONE = "answer", "assume", "ask", "none"
_VERDICTS = (ANSWER, ASSUME, ASK, NONE)

#: 되물음 선택지 최소 개수.
MIN_OPTIONS = 2

#: 갈래 미생성 재료 — 고객·날짜·직전 답변처럼 값이 이미 하나로 정해진 것.
#: playbook 포함 — 카드별 고객 상태 차이를 갈래로 읽어 직원에게 고객 상태를 되묻는 오판 방지.
_NO_BRANCH = frozenset({"customer", "history", "transcript", "date", "playbook", "last_answer"})

#: «이미 정해진 것» 블록에서도 제외 — 원장 계산값이 아닌 재료.
_NOT_SETTLING = frozenset({"playbook", "last_answer"})

#: «이미 정해진 것» 블록의 계좌 상태 항목 — 갈래로 오독될 수 있는 축만.
_SETTLED_STATE_KEYS = ("디폴트옵션", "연금개시", "연금개시요건", "세액공제_잔여한도",
                       "판매중단_보유상품", "ISA_만기자금", "부담금별_구성", "연금저축_보유")


def settled_block(state: AgentState) -> str:
    """판정 프롬프트의 «이미 정해진 것» 블록 — 열린 고객에 대해 코드가 아는 값. 고객 없으면 "".

    원장의 _NO_BRANCH 재료 본문 + 프로파일에서 직접 계산한 상태(LLM 미호출).
    근거 블록과 분리 — 섞으면 갈래 재료로 오독.
    """
    lines: list[str] = []
    customer_id = state.get("customer_id")
    if customer_id:
        try:
            from pension_agent.tools.customer_solution import customer as SC  # noqa: PLC0415
            from pension_agent.tools.customer_solution.engine.render import _account_state  # noqa: PLC0415
            from pension_agent.tools.customer_solution.situations import problem_situations  # noqa: PLC0415
            profile = SC.get_profile(customer_id)
        except Exception:  # noqa: BLE001 — 프로파일 없으면 빈 블록
            profile = None
        if profile is not None:
            from pension_agent.tools.customer_solution.engine.render import _customer_header, won  # noqa: PLC0415
            # 모르는 값(소득구간 None) 미포함 — 「미확인」을 넣으면 정해진 축으로 오독
            header = _customer_header(profile)
            if profile.income_bracket:
                header_keys = ("평가금액", "투자성향", "거래채널", "소득구간")
            else:
                header_keys = ("평가금액", "투자성향", "거래채널")
            lines.append("· 고객: " + " · ".join(f"{k} {header[k]}" for k in header_keys if k in header))
            lines.append(f"· 당해 연금계좌 납입액: {won(profile.paid_ytd_total)}")
            conds = SC.conditions(profile)
            if conds:
                lines.append("· 성립 요건: " + ", ".join(SC.CONDS.get(c, c) for c in conds))
            sits = problem_situations(profile, conds)
            if sits:
                lines.append("· 문제상황: " + " / ".join(f"{s['no']}. {s['title']}" for s in sits))
            account = _account_state(profile)
            lines.append("· 계좌 상태: " + " · ".join(
                f"{k.replace('_', ' ')} {account[k]}" for k in _SETTLED_STATE_KEYS if k in account))
    for e in state.get("evidence") or []:
        if e["tool"] in _NO_BRANCH and e["tool"] not in _NOT_SETTLING and e.get("text"):
            lines.append(e["text"])
    if not lines:
        return ""
    return ("<이미 정해진 것>\n열려 있는 고객에 대해 코드가 원장에서 계산한 값이다. 갈래가 아니다 —\n"
            "여기 적힌 축(어느 고객인가 · 고객 상태 · 계좌 상태 · 보유 금액 · 거래채널)으로는 되묻지 않는다.\n"
            "근거가 고객 상태별로 갈리면 여기 적힌 상태에 해당하는 쪽이 이미 정해진 것이다.\n"
            "여기 없는 값(예: 총급여 구간)은 모르는 값이다 — 그 값으로 답이 갈리면 되묻는다.\n"
            + "\n".join(lines) + "\n</이미 정해진 것>\n")


def asked_last_turn(history: list[dict] | None) -> bool:
    """직전 턴이 되물음인지 — 연속 되물음 차단(직전 한 턴만 확인)."""
    return bool((history or []) and (history[-1] or {}).get("pending_clarify"))


def _render(ask: str, options: list[str]) -> str:
    return "\n".join([ask, "", *(f"· {o}" for o in options)])


def applicable(state: AgentState) -> bool:
    """이 턴에 되물음 판정 실행 가능 여부(LLM 없이 결정). answer 의 병행 여부 판단용."""
    # 승낙 턴 제외 — 판정할 질문 없음
    if state.get("intent") == "confirm_action":
        return False
    evidence = [e for e in (state.get("evidence") or []) if e["tool"] not in _NO_BRANCH]
    return bool(evidence) and not asked_last_turn(state.get("history"))


def _branches_block(state: AgentState) -> str:
    """게이트가 표시한 갈래 축. 없으면 "" — 빈 블록을 세우면 LLM 이 갈래를 지어냄."""
    axes = [b for b in (state.get("branches") or [])
            if isinstance(b, dict) and b.get("axis") and b.get("options")]
    if not axes:
        return ""
    lines = [f"- {b['axis']}: " + " / ".join(b["options"]) for b in axes]
    return JUDGE_BRANCHES_BLOCK.format(branches="\n".join(lines))


def _quotable(text: str, evidence: list) -> bool:
    """판정 문장(premise·missing)에 원장 밖 수치가 없는지 — 있으면 작성자가 되받아 답 폐기."""
    said = numbers(text)
    if not said:
        return True
    return said <= numbers("\n".join(tools.ledger_texts(evidence)))


def _can_settle(state: AgentState) -> bool:
    """갈래를 정해 줄 재료(열린 고객·이전 대화) 유무. 없으면 assume 은 지어낸 전제."""
    return bool(state.get("customer_id") or state.get("history"))


def _verdict_of(parsed: dict) -> str:
    """등급 판독. verdict 칸 없으면 옛 규격(`{"ask": …}`)으로 폴백."""
    said = str(parsed.get("verdict") or "").strip().lower()
    if said in _VERDICTS:
        return said
    return ASK if parsed.get("ask") else ANSWER


def clarify(state: AgentState) -> dict[str, Any]:
    """되물음 판정. 되묻지 않으면 상태 변경 없음."""
    evidence = [e for e in (state.get("evidence") or []) if e["tool"] not in _NO_BRANCH]
    if not applicable(state):
        return {}

    prompt = CLARIFY_PROMPT.format(
        context="\n\n".join(e["text"] for e in evidence),
        branches_block=_branches_block(state),
        settled_block=settled_block(state),
        history_block=format_history(state.get("history")),
        question=state["question"],
    )
    try:
        raw = generate(prompt, max_tokens=CLARIFY_MAX_TOKENS, name="consult.clarify")
    except LLMError as exc:
        # 원인만 기록 — compose 가 같은 LLM 장애 안내로 종료
        observability.step("clarify", error=f"{type(exc).__name__}: {exc}",
                           level=logging.WARNING)
        return {"llm_error": f"{type(exc).__name__}: {exc}"}

    m = re.search(r"\{.*\}", raw, re.S)
    try:
        parsed = json.loads(m.group()) if m else {}
    except ValueError:
        parsed = {}
    if not isinstance(parsed, dict):
        observability.step("clarify", error="판정 응답을 JSON 으로 읽지 못함")
        return {}

    verdict = _verdict_of(parsed)
    # 등급은 판정이 실제로 돈 턴에만 기록(계측에서 미실행 턴과 구분)
    graded: dict[str, Any] = {"judge_verdict": verdict}

    if verdict == ASK:
        ask = parsed.get("ask")
        options = [o for o in (parsed.get("options") or []) if isinstance(o, str) and o.strip()]
        if not isinstance(ask, str) or not ask.strip() or len(options) < MIN_OPTIONS:
            # 선택지 부족 시 되묻지 않음. 등급은 계측용으로 유지
            observability.step("clarify", verdict=verdict, options=len(options), discarded=False)
            return graded
        observability.step("clarify", verdict=verdict, options=len(options), discarded=True)
        asked = {"question": ask.strip(), "options": options}
        # 선택지 출처로 근거 카드 첨부 — 비우면 화면에 「근거: 없음」
        return {**graded, "clarify": asked, "answer": _render(ask.strip(), options),
                "sources": [{**s, "role": tools.GROUND} for s in tools.ledger_sources(evidence)]}

    observability.step("clarify", verdict=verdict)
    if verdict == ASSUME:
        premise = str(parsed.get("premise") or "").strip()
        # 정할 재료(고객·이전 대화) 없으면 assume 무시 — LLM 이 전제를 지어냄
        if premise and _can_settle(state) and _quotable(premise, evidence):
            return {**graded, "judge_note": COMPOSE_PREMISE_BLOCK.format(premise=premise)}
        return graded

    if verdict == NONE:
        missing = str(parsed.get("missing") or "").strip()
        if missing and _quotable(missing, evidence):
            return {**graded, "judge_note": COMPOSE_MISSING_BLOCK.format(missing=missing)}
        return graded

    return graded
