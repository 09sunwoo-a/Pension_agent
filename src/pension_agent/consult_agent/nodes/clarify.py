"""답을 어떤 형태로 낼지 판정한다 — 답한다 · 전제를 밝히고 답한다 · 되묻는다 · 없다 (§5).

되묻기는 답을 미루는 것이 아니라 **틀린 답을 막는 것**이다. "실물이전"이 타행→당행인지
당행→타행인지에 따라 절차가 다른데, 한쪽을 골라 답하면 직원은 그게 다른 절차인 줄도 모른
채 잘못된 순서로 처리한다.

━━ 왜 예/아니오가 아니라 등급인가 ━━
§5 는 결론을 넷으로 정의하는데 이 판정의 출력은 오래도록 「되물을까/말까」 둘이었다.
그래서 나머지 둘이 **출력을 갖지 못했다**:

  전제를 밝히고 답한다   판정자는 어느 갈래인지 알아내고도 `{"ask": null}` 을 돌려줬다 —
                         그 사실이 작성자에게 한 글자도 건너가지 않아, 작성자는 전제가
                         필요한 줄도 몰랐다.
  없다고 답한다          작성 지시(COMPOSE_SYSTEM 9번)로만 걸려 있어서, 타행 수수료를
                         물었는데 당행 수수료표를 펴 놓는 답이 실제로 나갔다(대본 T12).

지금은 넷이 다 판정값이고, 뒤의 둘은 작성 프롬프트에 끼울 블록(`judge_note`)으로 나간다.
작성과 판정은 동시에 도므로(nodes/answer.py) 그 둘일 때만 한 번 다시 쓴다.

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
  · **판정이 낸 문장은 수치를 새로 만들 수 없다**(`_quotable`). premise·missing 은 LLM 이
    쓴 문장인데 작성 프롬프트로 들어가므로, 원장 밖 숫자가 거기 있으면 작성자가 그것을
    되받고 답이 통째로 폐기된다(§6). 경계를 넓힐 수 있는 것은 코드뿐이다(루트 규칙 2).

되묻기 턴에는 화면 연계 제안을 붙이지 않는다(§5 마지막) — 그건 그래프 배선이 한다
(graph.py: clarify 는 offer 를 거치지 않고 END 로 간다).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.consult_agent import tools
from pension_agent.consult_agent.prompts import (
    CLARIFY_PROMPT, COMPOSE_MISSING_BLOCK, COMPOSE_PREMISE_BLOCK, JUDGE_BRANCHES_BLOCK,
)
from pension_agent.consult_agent.state import AgentState, format_history
from pension_agent.llm import LLMError, generate
from pension_agent.verify import numbers

#: 판정 응답의 토큰 상한. 등급 한 낱말 + (되물으면) 질문 한 문장과 선택지 몇 개.
CLARIFY_MAX_TOKENS = 250

#: 판정 등급. 뒤의 둘은 «작성 프롬프트에 블록을 얹어 다시 쓴다»로 이어진다.
ANSWER, ASSUME, ASK, NONE = "answer", "assume", "ask", "none"
_VERDICTS = (ANSWER, ASSUME, ASK, NONE)

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
                       "판매중단_보유상품", "ISA_만기자금", "부담금별_구성")


def settled_block(state: AgentState) -> str:
    """판정 프롬프트의 «이미 정해진 것» — 열려 있는 고객에 대해 코드가 아는 값 (CLAUDE.md §5).

    되묻기는 «질문·대화 맥락·열린 고객 화면 어디에도 정할 근거가 없을 때»만이다(§5). 그런데
    판정 컨텍스트에는 `_NO_BRANCH` 재료가 빠져 있어 **열린 고객 화면을 아예 못 봤다**(§12
    gap 30). 갈래를 만들지 않는 재료가 갈래를 **정해 주는** 일은 한다 — 「수수료 얼마야?」의
    거래채널(대면/비대면)과 부담금 종류(사용자/가입자), 「뭐라고 말하면 좋아?」의 고객 상태.
    셋 다 원장 값이다. 원장이 모르는 축은 그대로 갈래다 — 소득구간은 비어 있으면
    **블록에 넣지 않는다**(「미확인」을 넣으면 판정이 정해진 것으로 읽는다).

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
            from pension_agent.strategy_agent.engine.render import _customer_header, won  # noqa: PLC0415
            # 화면 상단과 같은 식별 항목 — 거래채널(대면/비대면)·투자성향·평가금액. **모르는 값은
            # 싣지 않는다** — 소득구간이 비어 있으면(`income_bracket` None → 「구간 미확인」) 이 블록에
            # 넣지 않는다. 「이미 정해진 것」 안에 「미확인」이 있으면 판정이 그 축까지 정해진 것으로
            # 읽어 되묻지 않는다(2026-09-05 리허설 K3: 총급여 구간을 되물어야 하는 자리에서 두 구간을
            # 다 적고 답했다). 여기 없는 축은 갈래로 남는다.
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
        if e["tool"] in _NO_BRANCH and e["tool"] != "playbook" and e.get("text"):
            lines.append(e["text"])
    if not lines:
        return ""
    return ("<이미 정해진 것>\n열려 있는 고객에 대해 코드가 원장에서 계산한 값이다. 갈래가 아니다 —\n"
            "여기 적힌 축(어느 고객인가 · 고객 상태 · 계좌 상태 · 보유 금액 · 거래채널)으로는 되묻지 않는다.\n"
            "근거가 고객 상태별로 갈리면 여기 적힌 상태에 해당하는 쪽이 이미 정해진 것이다.\n"
            "여기 없는 값(예: 총급여 구간)은 모르는 값이다 — 그 값으로 답이 갈리면 되묻는다.\n"
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


def _branches_block(state: AgentState) -> str:
    """게이트가 표시한 갈래 축(tools.record_branches). 없으면 빈 문자열.

    없는데도 블록을 세우면(«갈래: 없음») 판정 LLM 이 없는 갈래를 만든다 — 조건부 표시가
    관련 있을 때만 붙어야 하는 것과 같은 자리다(§7).
    """
    axes = [b for b in (state.get("branches") or [])
            if isinstance(b, dict) and b.get("axis") and b.get("options")]
    if not axes:
        return ""
    lines = [f"- {b['axis']}: " + " / ".join(b["options"]) for b in axes]
    return JUDGE_BRANCHES_BLOCK.format(branches="\n".join(lines))


def _quotable(text: str, evidence: list) -> bool:
    """판정이 쓴 문장을 작성 프롬프트에 실어도 되나 — **원장 밖 수치가 없어야 한다.**

    premise·missing 은 LLM 이 지어낸 문장이라 그 자체가 근거가 아니다. 그런데 작성
    프롬프트에 들어가면 작성자가 그 말을 되받아 적고, 되받은 수치가 원장 밖이면 §6 의
    검사가 답을 통째로 버린다 — 판정을 도우려던 장치가 답을 죽인다.

    지워진 gap 31 이 «코드가 실어 보낸 문구»의 수치를 인용 허용에 더한 것과 방향이 반대다.
    그건 코드가 정한 문구였고 이건 LLM 이 만든 문장이라, 허용을 넓히는 대신 **넓힐 필요가
    없는 문장만 통과**시킨다. 걸리면 그 등급을 버리고 평범한 답으로 되돌아간다 — 잃는 것은
    전제 한 줄이고, 그건 이 변경 이전의 동작이다.
    """
    said = numbers(text)
    if not said:
        return True
    return said <= numbers("\n".join(tools.ledger_texts(evidence)))


def _can_settle(state: AgentState) -> bool:
    """갈래를 **정해 줄 수 있는 재료**가 이 턴에 있나 — 열린 고객이거나 이전 대화다.

    `settled_block` 이 서는 조건(고객 화면)과 §5 가 말하는 «맥락»(이전 대화) 둘이다.
    판정 프롬프트에 그 둘이 하나도 안 실린 턴에서 「정해졌다」는 판정이 나오면, 그것은
    무엇을 읽고 정한 것이 아니라 **지어낸 전제**다.
    """
    return bool(state.get("customer_id") or state.get("history"))


def _verdict_of(parsed: dict) -> str:
    """등급을 읽는다. 등급 칸이 없으면 **옛 규격**(`{"ask": …}`)으로 읽는다.

    작은 모델이 규격을 못 맞추는 일이 있는데, 그때 판정을 통째로 버리면 되묻기가 사라진다 —
    등급을 늘린 변경이 있던 기능을 없애는 쪽으로 작동하면 안 된다.
    """
    said = str(parsed.get("verdict") or "").strip().lower()
    if said in _VERDICTS:
        return said
    return ASK if parsed.get("ask") else ANSWER


def clarify(state: AgentState) -> dict[str, Any]:
    """되물을지 판정한다. 되묻지 않기로 하면 아무것도 바꾸지 않고 compose 로 흘려보낸다."""
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
        # 판정을 못 돌린 것과 되묻지 않기로 한 것은 다르지만, 결과는 같아야 한다 —
        # 여기서 LLM 이 죽었으면 답을 쓸 LLM 도 죽었다. compose 가 같은 안내로 끝낸다(§11).
        return {"llm_error": f"{type(exc).__name__}: {exc}"}

    m = re.search(r"\{.*\}", raw, re.S)
    try:
        parsed = json.loads(m.group()) if m else {}
    except ValueError:
        parsed = {}
    if not isinstance(parsed, dict):
        return {}

    verdict = _verdict_of(parsed)
    # 등급은 판정이 **실제로 돈** 턴에만 남긴다 — 관문에서 걸러 판정을 안 돌린 턴과
    # 「answer」로 판정한 턴은 다른 사건이고, 계측이 그 둘을 갈라 세야 한다(graph.ask).
    graded: dict[str, Any] = {"judge_verdict": verdict}

    if verdict == ASK:
        ask = parsed.get("ask")
        options = [o for o in (parsed.get("options") or []) if isinstance(o, str) and o.strip()]
        if not isinstance(ask, str) or not ask.strip() or len(options) < MIN_OPTIONS:
            # 되묻기로 판정했지만 선택지를 못 세웠다 — 갈래를 보여주지 못하는 되묻기는
            # "무엇을 원하세요?" 와 같아서 하지 않는다(§5). 등급은 남겨 계측에 잡히게 한다.
            return graded
        asked = {"question": ask.strip(), "options": options}
        # 선택지는 근거 카드에서 나온 것이므로 그 카드를 출처로 싣는다(§3 "모든 답에 출처를
        # 밝힌다"). 비워 두면 화면이 "근거: 없음"이라고 말하는데, 직원 입장에서는 어디서 나온
        # 갈래인지 모른 채 고르라는 말이 된다 — 되묻기도 재료에서 나온 답이다.
        return {**graded, "clarify": asked, "answer": _render(ask.strip(), options),
                "sources": [{**s, "role": tools.GROUND} for s in tools.ledger_sources(evidence)]}

    if verdict == ASSUME:
        premise = str(parsed.get("premise") or "").strip()
        # **정해 줄 것이 없으면 «정해졌다»고 말할 수 없다.** assume 은 «대화 맥락이나 열려
        # 있는 고객 값이 갈래를 정해 준다»는 판정인데, 그 둘이 다 없는 턴에서도 LLM 이
        # 전제를 만들어 냈다 — 2026-09-07 리허설 케이스 1(고객 화면 없음 · 첫 턴):
        # 「IRP 세액공제 한도가 얼마야?」에 전제를 세워 답을 ISA 전환 쪽으로 밀었고, 그
        # 답이 카드가 못박은 오답(「전환금 전액이 공제 대상」)에 걸려 폐기됐다.
        # 갈래를 **만드는** 것은 LLM 이 해도 되지만, 갈래가 **정해졌는지**는 코드가 아는
        # 사실이다(루트 규칙 2). 정할 재료가 없으면 그냥 답한다 — 이 등급 이전의 동작이다.
        if premise and _can_settle(state) and _quotable(premise, evidence):
            return {**graded, "judge_note": COMPOSE_PREMISE_BLOCK.format(premise=premise)}
        return graded

    if verdict == NONE:
        missing = str(parsed.get("missing") or "").strip()
        if missing and _quotable(missing, evidence):
            return {**graded, "judge_note": COMPOSE_MISSING_BLOCK.format(missing=missing)}
        return graded

    return graded
