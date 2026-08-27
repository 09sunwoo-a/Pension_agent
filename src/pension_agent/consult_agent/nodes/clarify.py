"""판별 질문 — 답의 갈래가 갈리는데 정할 근거가 없으면 답변 대신 되묻는다 (CLAUDE.md §5).

되묻기는 답을 미루는 것이 아니라 **틀린 답을 막는 것**이다. "실물이전"이 타행→당행인지
당행→타행인지에 따라 절차가 다른데, 한쪽을 골라 답하면 직원은 그게 다른 절차인 줄도 모른
채 잘못된 순서로 처리한다.

━━ 왜 계획 루프 뒤에 있나 ━━
갈래가 **실제로 존재하는지**는 지식베이스를 봐야 안다. 질문만 보고 "모호하다"고 판정하면
근거에 갈래가 하나뿐인 질문에도 되묻게 되고, 그건 §5 가 금지하는 확인차 되묻기다.
그래서 재료를 다 모은 뒤(plan) 답을 쓰기 전(compose)에 한 번 판정한다.

━━ 무엇을 코드가 쥐나 ━━
되물을지는 LLM 이 판정하지만, **되물을 수 있는 자리**는 코드가 정한다.

  · 근거가 0건이면 되묻지 않는다 — 못 찾은 것은 모호한 것이 아니다.
  · 지식베이스 재료가 하나도 없으면 되묻지 않는다. 되묻는 근거는 "지식베이스에 갈래가
    실제로 존재한다"인데(§5), 열려 있는 고객의 재료(브리핑·상담 기록)에는 갈래가 없다 —
    어느 고객인지가 이미 정해져 있기 때문이다. "이 고객 예금 잔액 얼마지"에 판정을 돌리는
    것은 답이 갈릴 수 없는 질문에 LLM 호출을 한 번 쓰는 것이다.
  · 직전 턴이 되묻기였으면 되묻지 않는다(§5 "연속으로 되묻지 않는다"). 확인만 반복하는
    턴이 이어지면 직원은 에이전트를 쓰지 않게 된다.
  · 선택지가 2개 미만이면 되묻지 않는다 — 갈래를 보여주지 못하는 되묻기는 "무엇을
    원하세요?"와 같고, 직원이 무엇을 답해야 할지 다시 생각해야 한다.

되묻기 턴에는 화면 연계 제안을 붙이지 않는다(§5 마지막) — 그건 그래프 배선이 한다
(graph.py: clarify 는 offer 를 거치지 않고 END 로 간다).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.consult_agent import tools
from pension_agent.consult_agent.prompts import CLARIFY_PROMPT
from pension_agent.consult_agent.state import AgentState, format_history
from pension_agent.llm import LLMError, generate

#: 판별 질문 응답의 토큰 상한. 질문 한 문장 + 선택지 몇 개 분량.
CLARIFY_MAX_TOKENS = 200

#: 선택지의 최소 개수. 갈래를 보여주지 못하면 되묻는 의미가 없다.
MIN_OPTIONS = 2

#: 갈래가 있을 수 없는 재료. 어느 고객인지가 이미 정해져 있어서다 — 브리핑도 상담 기록도
#: 열려 있는 고객 하나의 것이라, 되물어서 좁힐 갈래가 지식베이스에 없다.
#: 오늘 날짜(date)도 같다. 갈래가 아니라 하나뿐인 값이라, 되묻는 것은 좁히는 게 아니라
#: 「오늘이 며칠인지」를 직원에게 되묻는 꼴이 된다.
_NO_BRANCH = frozenset({"customer", "history", "date"})


def asked_last_turn(history: list[dict] | None) -> bool:
    """직전 턴이 되묻기였는가. 연속 되묻기를 막는 상한이자, 이 상한의 전부다.

    "최근 N턴에 몇 번"이 아니라 **직전 한 턴**만 보는 이유는, 되물은 다음 턴은 그 답이라
    그 자리에서 답이 나와야 하기 때문이다. 그 뒤에 새 질문이 오면 그건 새 갈래이고,
    거기서 다시 되묻는 것은 반복이 아니다.
    """
    return bool((history or []) and (history[-1] or {}).get("pending_clarify"))


def _render(ask: str, options: list[str]) -> str:
    return "\n".join([ask, "", *(f"· {o}" for o in options)])


def clarify(state: AgentState) -> dict[str, Any]:
    """되물을지 판정한다. 되묻지 않기로 하면 아무것도 바꾸지 않고 compose 로 흘려보낸다."""
    evidence = [e for e in (state.get("evidence") or []) if e["tool"] not in _NO_BRANCH]
    if not evidence or asked_last_turn(state.get("history")):
        return {}

    prompt = CLARIFY_PROMPT.format(
        context="\n\n".join(e["text"] for e in evidence),
        history_block=format_history(state.get("history")),
        question=state["question"],
    )
    try:
        raw = generate(prompt, max_tokens=CLARIFY_MAX_TOKENS)
    except LLMError as exc:
        # 판정을 못 돌린 것과 되묻지 않기로 한 것은 다르지만, 결과는 같아야 한다 —
        # 여기서 LLM 이 죽었으면 답을 쓸 LLM 도 죽었다. compose 가 같은 안내로 끝낸다(§11).
        return {"llm_error": f"{type(exc).__name__}: {exc}"}

    m = re.search(r"\{.*\}", raw, re.S)
    try:
        verdict = json.loads(m.group()) if m else {}
    except ValueError:
        verdict = {}
    if not isinstance(verdict, dict):
        return {}

    ask = verdict.get("ask")
    options = [o for o in (verdict.get("options") or []) if isinstance(o, str) and o.strip()]
    if not isinstance(ask, str) or not ask.strip() or len(options) < MIN_OPTIONS:
        return {}

    asked = {"question": ask.strip(), "options": options}
    # 선택지는 근거 카드에서 나온 것이므로 그 카드를 출처로 싣는다(§3 "모든 답에 출처를
    # 밝힌다"). 비워 두면 화면이 "근거: 없음"이라고 말하는데, 직원 입장에서는 어디서 나온
    # 갈래인지 모른 채 고르라는 말이 된다 — 되묻기도 재료에서 나온 답이다.
    return {"clarify": asked, "answer": _render(ask.strip(), options),
            "sources": [{**s, "role": tools.GROUND} for s in tools.ledger_sources(evidence)]}
