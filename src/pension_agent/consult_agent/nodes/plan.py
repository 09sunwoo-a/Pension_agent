"""계획 루프 — LLM 이 도구를 고르고, 결과를 보고, 부족하면 또 부르고, 충분해지면 답을 쓴다.

예전에는 `understand` 가 의도 하나를 고르면 그에 대응하는 노드 하나가 답을 만들고 끝났다
(의도 enum = 능력 표면). 한 턴에 재료 하나만 쓸 수 있어서 "이 고객 수수료 불만인데 우리 IRP
수수료가 얼마고 뭐라고 말해야 하나" 같은 질문은 값·고객·화법 중 하나만 답해졌다.

여기서는 도구 목록이 능력 표면이고(tools.py), 한 턴에 여러 도구를 부를 수 있다.

━━ 무엇을 LLM 이 정하고 무엇을 코드가 정하나 ━━
LLM   어떤 도구를 어떤 질의로 부를지, 이제 충분한지 (plan_step) · 답변 문장 (compose)
코드  몇 바퀴까지 돌 수 있는지(MAX_STEPS) · 같은 호출 반복 차단 · 미등록 도구 차단 ·
      도구 실패 처리 · 원장 밖 수치 차단 · 원문 스팬 집행
루프의 경계는 전부 코드가 정한다. LLM 이 "한 번 더"를 무한히 말해도 MAX_STEPS 에서 끊긴다.

━━ 도구 종류에 따라 답변 만드는 방식이 갈리지 않는다 ━━
compose 는 모든 근거를 한 번에 받아 답변 전체를 쓴다. 화법이든 수치든 절차든 같은 경로다.
갈리는 것은 도구가 선언한 원문 스팬(`atomic`·`notices`)뿐이고, 그 집행은 코드가 한다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pension_agent.consult_agent import guard, relations, tools
from pension_agent.consult_agent import kb as KBMOD
from pension_agent.consult_agent.nodes.pitch import situation_line
from pension_agent.consult_agent.prompts import (
    ANSWER_SHAPES, COMPOSE_PROMPT, COMPOSE_SYSTEM, MUST_BLOCK, PLAN_MISSES_BLOCK,
    PLAN_PROMPT, PLAN_RETRY_BLOCK, SHAPE_BLOCK,
)
from pension_agent.consult_agent.state import KB, AgentState, format_history
from pension_agent.llm import LLMError, generate
from pension_agent.verify import numbers, verify_texts

#: 한 턴에 부를 수 있는 도구 호출 수. 코드가 쥔 상한이다.
#: 4 는 "고객 + 수치 + 절차 + 화법"이 한 답변에 들어가는 가장 무거운 질문을 기준으로 잡았다.
MAX_STEPS = 4

#: 계획 응답의 토큰 상한. JSON 한 줄이지만 `query` 에 직원 질문이 한국어로 되받아 적히므로
#: 80 은 빠듯했다 — 조금만 길어져도 닫는 괄호 전에 잘리고, 잘린 JSON 은 통째로 버려져
#: "도구를 한 번도 안 부른" 것이 된다(증상은 '근거 없음'이라 원인이 안 보인다).
PLAN_MAX_TOKENS = 300

#: 근거를 하나도 못 모았을 때의 답. 지어내는 대신 없다고 말하고 무엇이 있는지 알려준다.
NO_EVIDENCE = (
    "그 질문에 쓸 근거를 지식베이스에서 찾지 못했습니다. "
    "제가 가진 재료는 화법·제도 수치·업무 절차·단말 화면번호·비대면 채널 경로·"
    "고객군 정의·관리 방법론·현장 관찰이고, 고객 개별 정보와 지난 상담 기록은 "
    "브리핑 화면이 열려 있을 때만 볼 수 있습니다."
)

#: '없다'에 덧붙이는 **무엇을 찾아봤는지**. §5 "못 찾았으면 무엇을 갖고 있는지 알려준다"의
#: 나머지 절반이다 — 어떤 재료를 어떤 말로 뒤졌는지 보이면 직원이 다시 물을 수 있다.
#: 이게 없으면 "분명 있는 지식인데 왜 못 찾지?"에 아무도 답할 수 없다(화면에서 끝나야 한다).
TRIED = "\n\n찾아본 곳: {calls}\n다른 말로 다시 물어보시면 찾을 수도 있어요."


def _no_evidence(state: AgentState) -> str:
    """근거 0건 답변. 무엇을 찾아봤는지 함께 말한다."""
    calls = [c for c in (state.get("plan_calls") or []) if c]
    if not calls:
        return NO_EVIDENCE
    return NO_EVIDENCE + TRIED.format(calls=" · ".join(calls))

#: 빠진 필수 표시를 채워 넣는 블록의 머리말. 근거 원문 전체가 아니라 표시만 붙는다.
MISSING_NOTICES = "── 빠뜨리면 안 되는 표시"

#: 재료 성격 표시 블록의 머리말(§7). 어느 자료에서 온 말인지 · 고객에게 그대로 옮겨도
#: 되는지. 답을 읽는 사람은 직원이고, 무엇을 옮길지는 직원이 거른다 — 그 판단에 필요한
#: 표시를 주는 데까지가 에이전트의 몫이다.
MATERIAL_MARKS = "── 이 답의 재료"

#: LLM 단계가 깨졌을 때의 답. **'근거가 없다'와 절대 같은 말을 하면 안 된다** —
#: 찾아보고 없는 것과 찾아보지도 못한 것은 다르고, 뒤를 앞으로 말하면 지식베이스에 있는
#: 자료를 없다고 답하는 셈이 된다. 원인을 함께 남겨 진단이 화면에서 끝나게 한다.
#: 어느 단계(슬롯 분해·계획·문장 작성)에서 실패했든 이 한 문장으로 답한다.
LLM_FAILED = (
    "지금은 답변을 만들 수 없어요 — LLM 호출이 실패했습니다. "
    "지식베이스에 자료가 없다는 뜻이 아니니, 잠시 후 다시 시도해주세요.\n({reason})"
)


def _json_obj(text: str) -> dict:
    """LLM 응답에서 JSON 객체만 꺼낸다. 못 찾으면 빈 dict(= 더 할 일 없음)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        val = json.loads(m.group())
    except ValueError:
        return {}
    return val if isinstance(val, dict) else {}


# ─────────────────────────────────────────────────────────────
# Node. plan_step — 다음 도구 하나를 고르고 실행해 원장에 쌓는다
# ─────────────────────────────────────────────────────────────

def _untried(state: AgentState, calls: list[str]) -> list[str]:
    """이 턴에 아직 안 불러본 도구 이름. 재계획 관문(_wrap_up)과 재계획 지시가 함께 쓴다."""
    used = {c.split(":", 1)[0] for c in calls}
    return [name for name in tools.usable(state) if name not in used]


def _wrap_up(state: AgentState, evidence: list, calls: list[str]) -> dict[str, Any]:
    """계획을 끝내기 전 마지막 관문 — **근거 0건이면 한 번은 다시 계획한다**(§5).

    LLM 이 done 을 말했든, 같은 호출을 반복했든, 없는 도구를 골랐든 끝내려는 사건은
    같다. 그런데 근거가 0건인 채 여기서 끝나면, 재료가 없는 것이 아니라 **도구·질의
    고르기를 실패한 것**이 '근거 없음'으로 답해진다 — 다른 도구를 써 볼 기회를 코드가
    한 번 만든다(재계획 프롬프트에는 빗나간 호출과 안 써 본 도구가 실린다). 두 번째
    끝내기는 존중한다: 정직한 '없음' 경로를 막지 않는다.
    """
    if evidence or state.get("plan_retry") or not _untried(state, calls):
        return {"plan_done": True}
    return {"plan_retry": True}


def _misses_block(state: AgentState, misses: list[str], calls: list[str]) -> str:
    """계획 프롬프트에 끼우는 '빗나간 호출' + (재계획 턴이면) '아직 안 써 본 도구' 블록.

    원장에는 성공한 재료만 실리므로, 이 블록이 없으면 계획은 자기가 뭘 불러봤는지 모르고
    같은 호출을 반복한다 — 반복은 코드가 끊고, 그러면 턴이 '근거 없음'으로 끝난다.
    """
    parts: list[str] = []
    if misses:
        parts.append(PLAN_MISSES_BLOCK.format(misses="\n".join(f"- {m}" for m in misses)))
    if state.get("plan_retry"):
        parts.append(PLAN_RETRY_BLOCK.format(untried=", ".join(_untried(state, calls))))
    return "".join(parts)


def plan_step(state: AgentState) -> dict[str, Any]:
    evidence = list(state.get("evidence") or [])
    calls = list(state.get("plan_calls") or [])
    misses = list(state.get("plan_misses") or [])

    if len(calls) >= MAX_STEPS:
        return {"plan_done": True}

    question = state["question"]
    try:
        raw = generate(
            PLAN_PROMPT.format(
                catalog=tools.catalog(state),
                ledger=tools.summarize(evidence),
                misses_block=_misses_block(state, misses, calls),
                # 후속 질문("그럼 안 된다고 하면요?")은 이전 턴을 이어받아야 무엇을 묻는지
                # 정해진다. 이 줄이 없으면 계획이 이번 질문 한 줄만 보고 재료를 고른다(§2-1).
                history_block=format_history(state.get("history")),
                question=question,
            ),
            max_tokens=PLAN_MAX_TOKENS,
        )
    except LLMError as exc:
        # LLM 이 없거나 죽으면 계획을 세울 수 없다. **왜 못 했는지를 남긴다** — 예전에는
        # 여기서 조용히 루프만 끝냈고, 그러면 401·타임아웃·모델명 오류가 전부 "근거가
        # 없습니다"로 둔갑해 원인이 화면에서 사라졌다. 계획이 못 돈 것과 재료가 없는 것은
        # 다른 사건이고, 다르게 말해야 한다.
        return {"plan_done": True, "llm_error": f"{type(exc).__name__}: {exc}"}

    # 이 호출이 됐다는 것은 LLM 이 살아 있다는 뜻이다. 앞 단계(슬롯 분해)가 일시적으로
    # 실패해 남긴 원인은 여기서 지운다 — 안 지우면 정상적으로 찾아보고 재료가 없었던 턴이
    # 'LLM 실패'로 답해진다(뒤집힌 방향의 같은 사고).
    alive: dict[str, Any] = {"llm_error": ""}

    action = _json_obj(raw)
    if not action:
        # 규격 밖 응답(설명문·잘린 JSON). 같은 이유로 조용히 넘기지 않는다.
        return {"plan_done": True,
                "llm_error": f"계획 응답을 JSON 으로 읽지 못함 — {raw.strip()[:120]!r}"}

    name = action.get("tool")
    if action.get("done") or not isinstance(name, str) or name not in tools.TOOLS:
        return {**alive, **_wrap_up(state, evidence, calls)}

    # 이 도구가 마지막이라고 말했으면 한 바퀴를 아낀다 — 재료 하나로 끝나는 질문
    # ("이 고객 예금 잔액 얼마지")도 계획에만 LLM 을 두 번 쓰던 자리다. 상한은 그대로
    # 코드가 정한다. **단, 그 도구가 실제로 재료를 내놨을 때만이다**(아래).
    last = bool(action.get("last"))

    query = action.get("query") or state.get("utterance") or question
    if not isinstance(query, str):
        query = question
    signature = f"{name}:{query}"
    if signature in calls:
        # 같은 호출을 반복하면 진전이 없다 — 도구를 다시 돌리지는 않되, 근거 0건이면
        # _wrap_up 이 한 번 되돌려 보낸다(빗나간 호출 목록을 보여주며).
        return {**alive, **_wrap_up(state, evidence, calls)}

    try:
        found = tools.run(name, state, query)
    except LLMError as exc:
        # 도구 안에서 LLM 이 죽었다(카드 선택·적합성 판정). 이걸 "근거를 못 찾았다"로
        # 접으면 있는 자료를 없다고 답하게 된다 — 계획 실패와 같은 사건으로 다룬다.
        return {"plan_done": True, "llm_error": f"{type(exc).__name__}: {exc}"}
    update: dict[str, Any] = {**alive, "plan_calls": calls + [signature]}
    if found is not None:
        update["evidence"] = evidence + [found]
    else:
        # 빗나간 호출로 기록한다 — 다음 계획 프롬프트가 이걸 보고 같은 호출을 반복하는
        # 대신 질의를 바꾸거나 다른 도구를 고른다(원장에는 성공한 재료만 실리므로,
        # 이 기록이 없으면 계획은 자기가 뭘 불러봤는지 모른다).
        update["plan_misses"] = misses + [signature]

    # `last` 는 **재료를 얻었을 때만** 존중한다. 근거를 못 찾았는데 루프를 끝내면 다른 도구를 써
    # 볼 기회가 없이 그 턴이 '근거 없음'으로 끝난다 — 계획이 고른 도구·질의가 빗나갔을
    # 뿐 지식베이스에는 답이 있는 경우가 그렇게 사라진다("포트폴리오 운용현황 조회 화면
    # 번호"가 [04-12-642] 카드를 두고 못 찾던 자리). 한 바퀴를 아끼는 것은 재료를 실제로
    # 얻었을 때의 이야기다.
    if last and found is not None:
        update["plan_done"] = True
    return update


# ─────────────────────────────────────────────────────────────
# Node. llm_down — LLM 이 죽어 분류조차 못 한 턴
# ─────────────────────────────────────────────────────────────

def llm_down(state: AgentState) -> dict[str, Any]:
    """§11. 규칙·검색만으로 대신 답을 만들지 않는다.

    이 노드가 따로 있는 이유는 **어느 단계에서 실패하든 같은 문장으로 끝나야** 하기
    때문이다. 의도 분류(understand)에서 죽으면 여기, 계획·작성에서 죽으면 compose 가
    같은 LLM_FAILED 를 낸다 — 직원이 받는 안내가 실패 지점에 따라 달라지면 그 자체가
    진단을 어렵게 한다.
    """
    return {"answer": LLM_FAILED.format(reason=state.get("llm_error") or "원인 미상"),
            "sources": []}


# ─────────────────────────────────────────────────────────────
# 원문 스팬 집행
# ─────────────────────────────────────────────────────────────

#: 스팬 위반 판정. 값을 잘못 짝지은 것과 표시를 빼먹은 것은 대응이 달라야 한다.
DISCARD, APPEND, OK = "discard", "append", "ok"


def _known_products() -> set[str]:
    """실재하는 상품 이름 전부 — 답변이 상품명을 지어냈는지 판정하는 **등록부**다.

    출처가 둘이고 둘 다 필요하다.

    · `strategy_agent` 의 상품 카탈로그 — 적합성 게이트가 타입드 필드로 비교하는 관계형
      데이터. 지금은 데모 12종이다.
    · **지식베이스가 선언한 상품명** — 행내 배포자료(05 시황·상품) 표의 상품명 칸.

    뒤쪽이 빠져 있던 동안 「KB 온국민 TDF 시리즈」처럼 원문 표에 그대로 적힌 상품을 말한
    답변이 '미등록'으로 버려졌다. 등록부가 좁은 것은 안전이 아니라 **오판**이다 — 맞는
    문장을 거부하면 그 자리에 근거 원문 덤프가 나간다.

    임포트 비용을 지연시킨다(strategy_agent 는 무겁다).
    """
    from pension_agent.strategy_agent import engine  # noqa: PLC0415
    return {r["name"] for r in engine.PRODUCTS} | KBMOD.product_names(KB)


def _span_verdict(found: tools.Evidence, answer: str) -> tuple[str, list[str]]:
    """이 근거의 원문 스팬이 답변에서 어떻게 어긋났는지 판정한다. 종류는 도구가 선언한다.

    · `atomic` — 값 + 조건이 붙은 한 덩이. 그 숫자를 쓰면서 원문을 안 실었다 → **DISCARD**.
      블록을 덧붙이는 복구로는 안 된다. 틀린 문장이 옳은 블록 옆에 그대로 남기 때문이다.
      "총급여 5,500만원 초과면 16.5%" 는 두 숫자가 다 원장에 있어 수치 집합 검사를
      통과하는데 뜻은 뒤집혀 있다. 그런 문장은 지우는 것만이 답이다.
    · `notices` — 빠지면 안 되는 표시. 누락 → **APPEND** 와 함께 **빠진 표시만** 돌려준다.
      답변이 틀린 게 아니라 덜 갖춰진 것이므로 모자란 것만 채운다.

    **답변이 쓰지 않은 근거의 표시는 붙이지 않는다.** 계획 루프가 여러 도구를 부르면
    답변이 안 쓴 카드도 원장에 남는데, 그 카드의 ⚠ 를 답 옆에 세우면 질문과 무관한 경고가
    붙어 정작 관계있는 표시가 묻힌다(화면번호 하나를 물었는데 다른 절차의 주의사항이
    따라 나오던 자리다). 판단은 값 스팬의 등장 여부로 하고, 걸 스팬이 없는 도구
    (화법·고객재료)는 판단할 수 없으므로 표시를 유지한다 — 잃는 쪽으로 기울지 않는다.
    """
    for span in found["atomic"]:
        if span not in answer and (numbers(span) & numbers(answer)):
            return DISCARD, []

    scopes = found.get("notice_scopes") or []
    keyed = [s for s in scopes if s.get("keys")]
    used = [s for s in keyed if any(k in answer for k in s["keys"])]
    # 카드를 골라 뺄 수 있는 건 **답변이 어느 카드를 썼는지 분간될 때뿐**이다. 하나도 못
    # 가리면(문장이 화면번호를 인용하지 않고 절차를 풀어 썼을 수 있다) 전부 유지한다 —
    # 잡음을 줄이자고 ⚠ 를 잃는 것은 바꾸지 않는다.
    selective = bool(used) and len(keyed) > 1

    gaps: list[tuple[str, list[str]]] = []
    for scope in scopes:
        if selective and scope.get("keys") and scope not in used:
            continue   # 답변이 안 쓴 카드 — 그 표시는 아무것도 한정하지 않는다
        missing = [s for s in scope.get("notices") or [] if s not in answer]
        if missing:
            gaps.append((scope.get("label") or found["tool"], missing))
    return (APPEND, gaps) if gaps else (OK, [])


#: 출처의 역할 어휘는 tools 가 갖는다(답을 내보내는 노드가 둘이다 — 여기와 clarify).
GROUND, CAUTION = tools.GROUND, tools.CAUTION


def _sources(evidence: list[tools.Evidence], guards: list, alts: list) -> list[dict]:
    """이번 답변에 영향을 준 재료 전부 — 원장(근거) + 「하지 말 것」 가드와 대안 화법(주의).

    가드·대안이 빠져 있었다. 둘 다 지식베이스 카드에서 나오고 프롬프트로 답변의 **내용을
    바꾸는데**(guard.prompt_note), 출처 목록에는 없어서 "지적보다 비교그룹 대조로 접근하라"
    같은 문장이 근거 없이 나온 것처럼 보였다 — 실제로는 pitch 카드가 근거인데도.
    답변에 영향을 준 재료는 전부 출처에 실린다(§3).

    그래서 실었더니 이번엔 반대쪽으로 틀렸다. 가드는 **고객 상태**에 걸리는 것이라 질문
    주제와 무관하게 매 턴 붙는데(§8 · gap 10 이 그렇게 만든 것이 맞다), 그것이 원장과
    한 목록에 섞이니 화면번호 하나를 물은 답변에 수익률 관리 방법론 카드가 '근거'로
    나란히 섰다. 재료를 지우는 것이 답이 아니다 — 프롬프트에 실제로 들어갔으므로 지우면
    §3 을 어긴다. **역할을 함께 싣고 화면이 갈라 보여준다.**

    `score` 는 검색 관련도라 검색으로 온 재료에만 있다. 없는 것은 없는 대로 두고, 화면이
    그 자리에 `None` 을 찍지 않는다 — 관련도 0 과 관련도를 잴 수 없는 재료는 다르다.
    """
    out = [{**s, "role": GROUND} for s in tools.ledger_sources(evidence)]
    seen = {s["id"] for s in out}
    for item in list(guards) + list(alts):
        card = item.get("card")
        if not card or card in seen:
            continue
        seen.add(card)
        out.append({"id": card, "title": item.get("title") or item.get("text", "")[:40],
                    "doc": item.get("doc"), "score": None, "page": None, "role": CAUTION})
    return out


def _shape_block(evidence: list[tools.Evidence]) -> str:
    """답에 무엇이 들어가야 하는지 — **원장에 실린 재료의 것만** (§5 표 · gap 4).

    질문 유형을 따로 분류하지 않는 이유는 계획 루프가 이미 정했기 때문이다. fact 를
    불렀으면 값을 묻는 질문이고, pitch 를 불렀으면 할 말을 묻는 질문이다. 한 답변에
    여러 유형이 섞이면 요구도 함께 실린다 — 그게 정상이다(§1).

    쓰지 않은 재료의 요구는 싣지 않는다. 무관한 지시가 늘수록 관계있는 지시가 묻힌다.
    """
    seen: list[str] = []
    for e in evidence:
        shape = ANSWER_SHAPES.get(e["tool"])
        if shape and shape not in seen:
            seen.append(shape)
    return SHAPE_BLOCK.format(shapes="\n".join(f"- {x}" for x in seen)) if seen else ""


# ─────────────────────────────────────────────────────────────
# Node. compose — 원장만으로 답을 만든다
# ─────────────────────────────────────────────────────────────

def compose(state: AgentState) -> dict[str, Any]:
    """모은 근거로 답을 만든다 — 도구 종류에 따라 방식이 갈리지 않는다.

    한 번의 생성으로 답변 전체를 쓰고, 그 뒤에 코드가 세 가지를 집행한다.
      ① 원장 밖 수치가 있으면 생성문을 버린다(지어낸 값이므로 복구 불가).
      ② **데이터가 선언한 관계**를 어겼으면 생성문을 버린다 — 조건과 값을 잘못 짝지었거나
         행원들이 적어둔 알려진 오답을 그대로 말한 경우다(relations.py).
      ③ 관계 선언이 없는 카드는 아직 값 스팬 강제로 지킨다. 어기면 역시 버린다.
      ④ 필수 표시가 빠졌으면 **그 표시만** 덧붙인다(덜 갖춰진 것이므로 모자란 것을 채운다).
    통과한 답변은 근거 안에서만 나온 것이고, 그 안에서 문장은 자유롭다.
    """
    evidence: list[tools.Evidence] = list(state.get("evidence") or [])
    if not evidence:
        # 재료가 없는 이유가 둘이다. 찾아봤는데 없는 것(NO_EVIDENCE)과 LLM 이 깨져 찾아보지도
        # 못한 것(LLM_FAILED). 둘을 같은 문장으로 답하면 있는 자료를 없다고 말하게 된다.
        failure = state.get("llm_error")
        return {"answer": LLM_FAILED.format(reason=failure) if failure else _no_evidence(state),
                "sources": []}

    # 「하지 말 것」 — 고객 화면이 열려 있으면 **코드가** 그 고객 상태를 읽어 붙인다.
    # LLM 이 customer 도구를 불렀는지에 의존하지 않는다(§8). 지식베이스에 금지 문장이
    # 없는 요건에는 여전히 아무것도 붙지 않는다(guard.py).
    conds = guard.conditions_of(state.get("customer_id"))
    guards = guard.cautions_for(KB, conds) if conds else []
    alts = guard.sensitive_cards(KB, conds) if conds else []

    spans = [a for e in evidence for a in (e["atomic"] + e["notices"])]
    prompt = COMPOSE_PROMPT.format(
        context="\n\n".join(e["text"] for e in evidence),
        must_block=MUST_BLOCK.format(spans="\n".join(f"- {a}" for a in spans)) if spans else "",
        shape_block=_shape_block(evidence),
        history_block=format_history(state.get("history")),
        question=state["question"],
        situation_line=situation_line(state.get("intent"), tools.ledger_slots(evidence)),
    )
    note = guard.prompt_note(guards, alts)
    if note:
        prompt = f"{prompt}\n\n{note}"

    try:
        answer = generate(prompt, max_tokens=1500, system=COMPOSE_SYSTEM).strip()
    except LLMError as exc:
        # 재료는 모았는데 문장을 못 쓴 것이다. 아래 폴백(근거 원문 그대로 싣기)으로 흘려보내면
        # 완성된 답변처럼 보이는 카드 덩어리가 나간다 — LLM 이 죽었을 때 다른 단계가 내는
        # 안내와 결과가 달라진다(§11). 여기서 끊고 같은 안내로 답한다.
        return {"answer": LLM_FAILED.format(reason=f"{type(exc).__name__}: {exc}"),
                "sources": _sources(evidence, [], [])}

    if answer:
        ok, _bad = verify_texts(answer, tools.ledger_texts(evidence),
                                known_products=_known_products())
        if not ok:
            answer = ""

    # 관계 위반 — 값–조건 오짝 · 알려진 오답. 원장 밖 수치 검사가 못 잡는 자리다.
    if answer:
        broken = relations.check(answer, tools.ledger_related(evidence))
        if broken:
            answer = ""

    appends: list[str] = []
    if answer:
        verdicts = [_span_verdict(e, answer) for e in evidence]
        if any(v == DISCARD for v, _ in verdicts):
            answer = ""
        else:
            # 채우는 것은 **빠진 표시**다. 예전에는 근거 블록을 통째로 덧붙여서, ⚠ 한 줄이
            # 모자란 답변 아래에 카드 전문 1,000자가 붙었다 — 정작 그 한 줄이 묻힌다.
            for _found, (verdict, gaps) in zip(evidence, verdicts):
                if verdict != APPEND:
                    continue
                appends += [f"· {label}\n" + "\n".join(f"  {m}" for m in missing)
                            for label, missing in gaps]

    if answer:
        parts = [answer] + ([MISSING_NOTICES, *appends] if appends else [])
    else:
        parts = [e["text"] for e in evidence]  # 생성문을 못 쓰면 근거 원문이 답이다

    # 재료 성격 표시 — 신뢰 등급 · 내부용 주의(§7). 답변이 이미 같은 말을 했으면 겹쳐
    # 세우지 않는다. 근거 원문을 그대로 내보낸 경우에도 붙는다 — 표시는 문장이 아니라
    # **재료**에 걸리는 것이라, 누가 문장을 썼는지와 무관하다.
    body = "\n\n".join(parts)
    seen = [m for m in tools.ledger_marks(evidence) if m not in body]
    if seen:
        parts.append(MATERIAL_MARKS + "\n" + "\n".join(f"· {m}" for m in seen))

    return {"answer": "\n\n".join(parts) or _no_evidence(state),
            "sources": _sources(evidence, guards, alts),
            "guards": guards, "guard_alternatives": alts}
