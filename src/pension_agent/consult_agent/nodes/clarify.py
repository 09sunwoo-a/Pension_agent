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

━━ 왜 계획 루프 뒤에 있나 ━━
갈래가 **실제로 존재하는지**는 지식베이스를 봐야 안다. 질문만 보고 "모호하다"고 판정하면
근거에 갈래가 하나뿐인 질문에도 되묻게 되고, 그건 §5 가 금지하는 확인차 되묻기다.
그래서 재료를 다 모은 뒤(plan) 답을 쓰기 전(compose)에 한 번 판정한다.

━━ 갈래를 «만드는» 재료와 «정해 주는» 재료 ━━
둘은 다른데 오래도록 한 필터(`_NO_BRANCH`)로 묶여 있었다. 고객 원장·지난 상담·오늘 날짜는
갈래를 만들지 않으므로 **관문**에서 빠지는 것이 맞지만(어느 고객인지가 이미 정해져 있다),
갈래를 **정해 주는** 일은 한다 — 「수수료 얼마야?」의 부담금 종류 갈래를 이 고객의 원장이
이미 답하고 있는데도 되물었다(§12 gap 30 · 대본 T8). 지금은 관문에서만 빼고 판정
컨텍스트에는 싣는다.

━━ 무엇을 코드가 쥐나 ━━
등급은 LLM 이 정하지만, **판정이 일어날 수 있는 자리**와 **판정이 만들 수 있는 것의 경계**는
코드가 정한다.

  · 근거가 0건이면 되묻지 않는다 — 못 찾은 것은 모호한 것이 아니다.
  · 갈래를 만드는 재료가 하나도 없으면 판정을 돌리지 않는다. "이 고객 예금 잔액 얼마지"에
    판정을 돌리는 것은 답이 갈릴 수 없는 질문에 LLM 호출을 한 번 쓰는 것이다.
  · 직전 턴이 되묻기였으면 되묻지 않는다(§5 "연속으로 되묻지 않는다").
  · 승낙 턴("네")에도 판정하지 않는다 — 판정할 질문이 없다(§10).
  · 선택지가 2개 미만이면 되묻지 않는다.
  · **판정이 낸 문장은 수치를 새로 만들 수 없다**(`_quotable`). premise·missing 은 LLM 이
    쓴 문장인데 작성 프롬프트로 들어가므로, 원장 밖 숫자가 거기 있으면 작성자가 그것을
    되받고 답이 통째로 폐기된다(§6). 경계를 넓힐 수 있는 것은 코드뿐이다(루트 규칙 2).

되묻기 턴에는 화면 연계 제안을 붙이지 않는다(§5 마지막) — 그건 그래프 배선이 한다
(routing.route_answer: clarify 가 있으면 offer 를 거치지 않고 END 로 간다).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.consult_agent import tools
from pension_agent.consult_agent.prompts import (
    COMPOSE_MISSING_BLOCK, COMPOSE_PREMISE_BLOCK,
    JUDGE_BRANCHES_BLOCK, JUDGE_DECIDING_BLOCK, JUDGE_PROMPT,
)
from pension_agent.consult_agent.state import AgentState, format_history
from pension_agent.llm import LLMError, generate
from pension_agent.verify import numbers

#: 판정 응답의 토큰 상한. 등급 한 낱말 + (되물으면) 질문 한 문장과 선택지 몇 개.
CLARIFY_MAX_TOKENS = 250

#: 선택지의 최소 개수. 갈래를 보여주지 못하면 되묻는 의미가 없다.
MIN_OPTIONS = 2

#: 갈래가 있을 수 없는 재료. 어느 고객인지가 이미 정해져 있어서다 — 브리핑도 상담 기록도
#: 열려 있는 고객 하나의 것이라, 되물어서 좁힐 갈래가 지식베이스에 없다.
#: 오늘 날짜(date)도 같다. 갈래가 아니라 하나뿐인 값이라, 되묻는 것은 좁히는 게 아니라
#: 「오늘이 며칠인지」를 직원에게 되묻는 꼴이 된다.
#:
#: **이 목록은 «관문»에만 쓴다.** 판정 컨텍스트에서까지 빼면 갈래를 정해 줄 값을 못 보고
#: 되묻게 된다(gap 30) — 그 둘을 갈라 놓는 것이 이 상수의 지금 역할이다.
_NO_BRANCH = frozenset({"customer", "history", "transcript", "date"})

#: 갈래를 정해 주는 재료에서 **빼는** 줄. 고객 재료에는 ⑥⑦⑧ 이 고른 화법·반론·참고자료와
#: AI 브리핑 산문이 함께 실리는데(tools._customer), 그것들은 갈래를 정하지 못하면서 블록만
#: 몇 배로 불린다 — 판정 프롬프트가 길어질수록 정작 갈래를 정해 줄 값이 묻힌다(§7 과 같은
#: 이유). 갈래를 정하는 것은 라벨–값 줄이다.
_NOT_DECIDING = ("이렇게 말해보세요", "예상 반론", "상담 참고",
                 "AI브리핑 문장", "AI브리핑 근거해설")

#: 갈래를 정해 주는 재료 한 건에서 판정 프롬프트로 넘기는 최대 길이. 상한이 없으면 상담
#: 기록이 긴 고객에서 판정 프롬프트가 작성 프롬프트보다 커진다.
_DECIDING_MAX_CHARS = 1500

#: 판정 등급. 뒤의 둘은 «작성 프롬프트에 블록을 얹어 다시 쓴다»로 이어진다.
ANSWER, ASSUME, ASK, NONE = "answer", "assume", "ask", "none"
_VERDICTS = (ANSWER, ASSUME, ASK, NONE)


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
    """이 턴에 판정을 **돌릴 수 있나**. 위 코드 관문 중 LLM 없이 결정되는 부분이다.

    `clarify` 안에도 같은 판정이 남아 있다(직접 부르는 호출자를 위해). 밖으로 꺼낸 이유는
    호출부가 «판정을 부를 것인가»를 미리 알아야 하기 때문이다 — 답변 작성과 동시에
    돌릴 때, 애초에 판정이 없는 턴까지 스레드를 띄우면 아끼려던 것을 도로 쓴다.
    """
    # 승낙 턴에는 판정하지 않는다. 이번 턴의 입력은 "네" 한 글자라 **모호함을 판정할 질문
    # 자체가 없고**, 무엇을 보여주기로 했는지는 제안한 턴이 이미 정했다(§10 "이번 턴의
    # 말에서 다시 추측하지 않는다"). 여기서 되물으면 확인에 확인을 겹치는 턴이 된다(§5).
    if state.get("intent") == "confirm_action":
        return False
    return bool(_branchable(state)) and not asked_last_turn(state.get("history"))


def _branchable(state: AgentState) -> list:
    """갈래를 만들 수 있는 재료. 관문도 판정 프롬프트의 <근거> 도 이것으로 선다."""
    return [e for e in (state.get("evidence") or []) if e["tool"] not in _NO_BRANCH]


def _trim(text: str) -> str:
    """판정 프롬프트에 실을 꼴로 자른다 — 갈래를 정하지 못하는 줄을 빼고 길이를 자른다."""
    lines = [ln for ln in text.splitlines()
             if not any(f"· {label}:" in ln for label in _NOT_DECIDING)]
    return "\n".join(lines)[:_DECIDING_MAX_CHARS].strip()


def _deciding_block(state: AgentState) -> str:
    """갈래를 **정해 주는** 재료 블록. 없으면 빈 문자열(블록 자체가 안 붙는다).

    원장에 실린 것(계획이 그 도구를 부른 경우)에 더해, **고객 화면이 열려 있으면 코드가
    그 고객의 재료를 읽어 붙인다.** 원장만 보면 계획 LLM 이 `customer` 를 골랐을 때만
    갈래가 정해지는데, 「수수료 얼마야?」는 `fact` 하나만 부르고도 이 고객의 원장이
    부담금 종류를 이미 답한다 — 실제로 그래서 되물었다(대본 T8 · 2026-09-07 실측).
    판정이 무엇을 볼 수 있는지가 LLM 의 도구 선택에 달려 있으면 안 된다는 것은
    지워진 gap 10 이 「하지 말 것」 가드에서 이미 정한 규약이다.
    """
    parts: list[str] = []
    for e in state.get("evidence") or []:
        if e["tool"] not in _NO_BRANCH:
            continue
        text = _trim(e["text"])
        if text:
            parts.append(text)
    if not any(e["tool"] == "customer" for e in state.get("evidence") or []):
        # 원장에 없을 때만 읽는다 — 있으면 같은 재료가 두 번 실린다.
        extra = tools.customer_material(state)
        if extra and _trim(extra):
            parts.append(_trim(extra))
    return JUDGE_DECIDING_BLOCK.format(deciding="\n\n".join(parts)) if parts else ""


def _branches_block(state: AgentState) -> str:
    """게이트가 표시한 갈래 축. 없으면 빈 문자열.

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


def _parse(raw: str) -> dict:
    """판정 응답을 dict 로. 규격 밖이면 빈 dict(= 판정 없음)."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        val = json.loads(m.group())
    except ValueError:
        return {}
    return val if isinstance(val, dict) else {}


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
    """답의 형태를 판정한다. `answer` 면 아무것도 바꾸지 않고 compose 결과가 그대로 나간다."""
    if not applicable(state):
        return {}
    evidence = _branchable(state)

    prompt = JUDGE_PROMPT.format(
        context="\n\n".join(e["text"] for e in evidence),
        branches_block=_branches_block(state),
        deciding_block=_deciding_block(state),
        history_block=format_history(state.get("history")),
        question=state["question"],
    )
    try:
        raw = generate(prompt, max_tokens=CLARIFY_MAX_TOKENS, name="consult.clarify")
    except LLMError as exc:
        # 판정을 못 돌린 것과 되묻지 않기로 한 것은 다르지만, 결과는 같아야 한다 —
        # 여기서 LLM 이 죽었으면 답을 쓸 LLM 도 죽었다. compose 가 같은 안내로 끝낸다(§11).
        return {"llm_error": f"{type(exc).__name__}: {exc}"}

    parsed = _parse(raw)
    verdict = _verdict_of(parsed)
    # 등급은 판정이 **실제로 돈** 턴에만 남긴다 — 관문에서 걸러 판정을 안 돌린 턴과
    # 「answer」로 판정한 턴은 다른 사건이고, 계측이 그 둘을 갈라 세야 한다(graph.ask).
    graded: dict[str, Any] = {"judge_verdict": verdict}

    if verdict == ASK:
        ask = parsed.get("ask")
        options = [o for o in (parsed.get("options") or []) if isinstance(o, str) and o.strip()]
        if not isinstance(ask, str) or not ask.strip() or len(options) < MIN_OPTIONS:
            # 되묻기로 판정했지만 선택지를 못 세웠다 — 갈래를 보여주지 못하는 되묻기는
            # "무엇을 원하세요?"와 같아서 하지 않는다(§5). 등급은 남겨 계측에 잡히게 한다.
            return graded
        asked = {"question": ask.strip(), "options": options}
        # 선택지는 근거 카드에서 나온 것이므로 그 카드를 출처로 싣는다(§3 "모든 답에 출처를
        # 밝힌다"). 비워 두면 화면이 "근거: 없음"이라고 말하는데, 직원 입장에서는 어디서 나온
        # 갈래인지 모른 채 고르라는 말이 된다 — 되묻기도 재료에서 나온 답이다.
        return {**graded, "clarify": asked, "answer": _render(ask.strip(), options),
                "sources": [{**s, "role": tools.GROUND} for s in tools.ledger_sources(evidence)]}

    if verdict == ASSUME:
        premise = str(parsed.get("premise") or "").strip()
        if premise and _quotable(premise, evidence):
            return {**graded, "judge_note": COMPOSE_PREMISE_BLOCK.format(premise=premise)}
        return graded

    if verdict == NONE:
        missing = str(parsed.get("missing") or "").strip()
        if missing and _quotable(missing, evidence):
            return {**graded, "judge_note": COMPOSE_MISSING_BLOCK.format(missing=missing)}
        return graded

    return graded
