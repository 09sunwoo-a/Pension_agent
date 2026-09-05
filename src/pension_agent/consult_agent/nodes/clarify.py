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
  · 승낙 턴("네")에도 되묻지 않는다 — 판정할 질문이 없다. 무엇을 보여주기로 했는지는
    제안한 턴이 정했고, 이번 턴의 말에서 다시 추측하지 않는다(§10).
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
#: `playbook` 도 같다 — 이 고객의 문제상황에 걸린 카드를 코드가 골라 온 것이라, 카드끼리
#: 대상 고객 상태가 달라 보여도 그 축은 원장이 이미 정했다. 2026-09-04 gemma 실측: 만기
#: 임박 고객(원리금보장 32.4%)에게 「만기 임박+디폴트옵션 미등록 고객에게」와 「원리금보장
#: 100% 운용 고객에게」 화법 2장이 왔고, 판정이 그 둘을 갈래로 읽어 **직원에게 고객 상태를
#: 되물었다.** 카드 안에 다른 축의 갈래(절차 방향 등)가 있어도 여기서는 세지 않는다 —
#: 그 경우 작성이 전제를 밝히고 답한다(§5 ①).
_NO_BRANCH = frozenset({"customer", "history", "transcript", "date", "playbook"})

#: «이미 정해진 것» 블록에 싣는 계좌 상태 항목. `render._account_state` 의 키 중 되묻기가
#: 갈래로 오독할 수 있는 축만 고른다 — 전부 원장 값이거나 코드가 이미 계산한 것이다.
_SETTLED_STATE_KEYS = ("디폴트옵션", "연금개시", "연금개시요건", "세액공제_잔여한도",
                       "판매중단_보유상품", "ISA_만기자금")


def settled_block(state: AgentState) -> str:
    """판정 프롬프트의 «이미 정해진 것» — 열려 있는 고객에 대해 코드가 아는 값 (CLAUDE.md §5).

    되묻기는 «질문·대화 맥락·열린 고객 화면 어디에도 정할 근거가 없을 때»만이다(§5). 그런데
    판정 컨텍스트에는 `_NO_BRANCH` 재료가 빠져 있어 **열린 고객 화면을 아예 못 봤다**(§12
    gap 30). 갈래를 만들지 않는 재료가 갈래를 **정해 주는** 일은 한다 — 「수수료 얼마야?」의
    부담금 종류(이 고객은 퇴직급여 5.2억·개인부담금 0원), 「뭐라고 말하면 좋아?」의 고객 상태.

    두 재료를 싣는다. ① 원장에 이미 실린 `_NO_BRANCH` 재료의 본문(고객 브리핑·상담 기록·
    오늘 날짜) — 작성(compose)이 보는 것과 같은 텍스트다. ② 그 도구가 안 불린 턴을 위해
    코드가 프로파일에서 직접 계산한 상태(성립 요건·문제상황·계좌 상태) — 브리핑 산출과
    같은 함수를 부르므로 화면과 다른 값을 말할 수 없다(§3). 어느 쪽도 LLM 호출이 없다.

    갈래 후보(<근거>)와 **분리해** 싣는다. 근거에 섞으면 판정이 그것을 갈래 재료로 읽는다.
    고객이 열려 있지 않으면 빈 문자열이다.
    """
    lines: list[str] = []
    customer_id = state.get("customer_id")
    if customer_id:
        try:
            from pension_agent.strategy_agent import customer as SC  # noqa: PLC0415
            from pension_agent.strategy_agent.engine.render import _account_state  # noqa: PLC0415
            from pension_agent.strategy_agent.situations import problem_situations  # noqa: PLC0415
            profile = SC.get_profile(customer_id)
        except Exception:  # noqa: BLE001 — 프로파일이 없으면 블록이 비는 것이 맞다
            profile = None
        if profile is not None:
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
        if e["tool"] in _NO_BRANCH and e["tool"] != "playbook" and e.get("text"):
            lines.append(e["text"])
    if not lines:
        return ""
    return ("<이미 정해진 것>\n열려 있는 고객에 대해 코드가 원장에서 계산한 값이다. 갈래가 아니다 —\n"
            "이 축(어느 고객인가 · 고객 상태 · 계좌 상태 · 보유 금액)으로는 되묻지 않는다.\n"
            "근거가 고객 상태별로 갈리면 여기 적힌 상태에 해당하는 쪽이 이미 정해진 것이다.\n"
            + "\n".join(lines) + "\n</이미 정해진 것>\n")


def asked_last_turn(history: list[dict] | None) -> bool:
    """직전 턴이 되묻기였는가. 연속 되묻기를 막는 상한이자, 이 상한의 전부다.

    "최근 N턴에 몇 번"이 아니라 **직전 한 턴**만 보는 이유는, 되물은 다음 턴은 그 답이라
    그 자리에서 답이 나와야 하기 때문이다. 그 뒤에 새 질문이 오면 그건 새 갈래이고,
    거기서 다시 되묻는 것은 반복이 아니다.
    """
    return bool((history or []) and (history[-1] or {}).get("pending_clarify"))


def _render(ask: str, options: list[str]) -> str:
    return "\n".join([ask, "", *(f"· {o}" for o in options)])


def applicable(state: AgentState) -> bool:
    """이 턴에 되묻기 판정을 **돌릴 수 있나**. 위 코드 관문 중 LLM 없이 결정되는 부분이다.

    `clarify` 안에도 같은 판정이 남아 있다(직접 부르는 호출자를 위해). 밖으로 꺼낸 이유는
    호출부가 «판정을 부를 것인가»를 미리 알아야 하기 때문이다 — 답변 작성과 동시에
    돌릴 때, 애초에 판정이 없는 턴까지 스레드를 띄우면 아끼려던 것을 도로 쓴다.
    """
    # 승낙 턴에는 되묻지 않는다. 이번 턴의 입력은 "네" 한 글자라 **모호함을 판정할 질문
    # 자체가 없고**, 무엇을 보여주기로 했는지는 제안한 턴이 이미 정했다(§10 "이번 턴의
    # 말에서 다시 추측하지 않는다"). 여기서 되물으면 확인에 확인을 겹치는 턴이 된다(§5).
    if state.get("intent") == "confirm_action":
        return False
    evidence = [e for e in (state.get("evidence") or []) if e["tool"] not in _NO_BRANCH]
    return bool(evidence) and not asked_last_turn(state.get("history"))


def clarify(state: AgentState) -> dict[str, Any]:
    """되물을지 판정한다. 되묻지 않기로 하면 아무것도 바꾸지 않고 compose 로 흘려보낸다."""
    evidence = [e for e in (state.get("evidence") or []) if e["tool"] not in _NO_BRANCH]
    if not applicable(state):
        return {}

    prompt = CLARIFY_PROMPT.format(
        context="\n\n".join(e["text"] for e in evidence),
        settled_block=settled_block(state),
        history_block=format_history(state.get("history")),
        question=state["question"],
    )
    try:
        raw = generate(prompt, max_tokens=CLARIFY_MAX_TOKENS, name="consult.clarify")
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
