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

import logging
import re
from collections.abc import Iterable
from typing import Any

from pension_agent import observability
from pension_agent.consult_agent import progress, tools
from pension_agent.consult_agent.effects import screens
from pension_agent.consult_agent.evidence import guard, kb_index, relations
from pension_agent.consult_agent.evidence.marks import MATERIAL_MARKS
from pension_agent.consult_agent.evidence.pitch_slots import situation_line
from pension_agent.consult_agent.prompts import (
    ACCEPTED_BLOCK, ANSWER_SHAPES, COMPOSE_PROMPT, COMPOSE_RETRY_BLOCK, COMPOSE_SYSTEM,
    MUST_BLOCK,
    PLAN_BUDGET_BLOCK, PLAN_MISSES_BLOCK, PLAN_PROMPT, PLAN_RETRY_BLOCK, REPEAT_BLOCK,
    REWRITE_BLOCK, SHAPE_BLOCK,
)
from pension_agent.consult_agent.state import KB, AgentState, format_history
from pension_agent.llm import LLMError, generate, json_object
from pension_agent.verify import numbers, verify_texts

#: 한 턴에 부를 수 있는 도구 호출 수. 코드가 쥔 상한이다.
#: 4 는 "고객 + 수치 + 절차 + 화법"이 한 답변에 들어가는 가장 무거운 질문을 기준으로 잡았다.
MAX_STEPS = 4

#: 계획 응답의 토큰 상한. JSON 한 줄이지만 `query` 에 직원 질문이 한국어로 되받아 적히므로
#: 80 은 빠듯했다 — 조금만 길어져도 닫는 괄호 전에 잘리고, 잘린 JSON 은 통째로 버려져
#: "도구를 한 번도 안 부른" 것이 된다(증상은 '근거 없음'이라 원인이 안 보인다).
PLAN_MAX_TOKENS = 300

#: 근거를 하나도 못 모았을 때의 답. 지어내는 대신 없다고 말하고 무엇이 있는지 알려준다.
#:
#: **말투는 답변과 같은 공손한 해요체다**(2026-09-18 — 그 전에는 「찾지 못했습니다」였다).
#: 이 문장은 답변 자리에 그대로 나가므로, 평소 답변보다 딱딱하면 직원에게는 «못 찾은 턴에만
#: 에이전트가 무례해지는» 것으로 읽힌다 — 실제로 시험 질문(지식베이스에 없는 예측 질문)에
#: 「그 자료는 없어요」가 나가 그 지적을 받았다. 내용(무엇이 있는지 · 무엇이 화면에 걸려
#: 있는지)은 그대로다 — 공손하게 쓰는 것과 없는 것을 있다고 말하는 것은 다르다.
NO_EVIDENCE = (
    "죄송해요, 그 질문에 쓸 근거는 제가 가진 지식베이스에서 찾지 못했어요. "
    "제가 가진 자료는 화법·제도 수치·업무 절차·단말 화면번호·비대면 채널 경로·"
    "고객군 정의·관리 방법론·현장 관찰이고, 고객 개별 정보와 지난 상담 기록은 "
    "브리핑 화면이 열려 있을 때만 볼 수 있어요."
)

#: '없다'에 덧붙이는 **무엇을 찾아봤는지**. §5 "못 찾았으면 무엇을 갖고 있는지 알려준다"의
#: 나머지 절반이다 — 어떤 재료를 어떤 말로 뒤졌는지 보이면 직원이 다시 물을 수 있다.
#: 이게 없으면 "분명 있는 지식인데 왜 못 찾지?"에 아무도 답할 수 없다(화면에서 끝나야 한다).
TRIED = "\n\n찾아본 곳: {calls}\n다른 말로 다시 물어보시면 찾을 수도 있어요."


#: 재료를 **읽지 못한** 턴의 답. NO_EVIDENCE 와 절대 같은 말을 하면 안 된다 — 찾아보고
#: 없는 것과 도구가 죽어 확인하지 못한 것은 다른 사건이고, 뒤를 앞으로 말하면 지식베이스에
#: 있는 자료를 없다고 답하는 셈이 된다(LLM_FAILED 와 같은 자리 · §11). 문장 꼴을 LLM_FAILED
#: 에 맞춘 것도 같은 이유다 — 직원이 받는 안내가 실패 지점에 따라 달라지면 진단이 어렵다.
TOOL_FAILED = (
    "지금은 답변을 만들 수 없어요 — {what} 자료를 읽는 데 실패했습니다. "
    "지식베이스에 자료가 없다는 뜻이 아니니, 잠시 후 다시 시도해주세요.\n({reasons})"
)


# ─────────────────────────────────────────────────────────────
# 이번 턴에 해 본 것 — `state["steps"]` 를 읽는 자리는 전부 여기를 거친다
#
# 항목 하나가 호출 하나다: {"tool", "query", "outcome": found|miss|failed, "reason"}.
# 결과 종류를 `outcome` 한 칸에 둔 이유는 state.py 의 `steps` 주석에 적어 뒀다 — 종류마다
# 리스트를 하나씩 늘리면 «저 호출은 어떻게 됐나»가 서명 문자열을 잘라 맞추는 일이 된다.
# ─────────────────────────────────────────────────────────────

#: 결과 세 값의 원본은 tools 다 — 도구를 부르는 곳(`tools.run`)이 장부·로그·점수에 같은 값을 쓴다.
FOUND, MISS, FAILED = tools.FOUND, tools.MISS, tools.FAILED


def _steps(state: AgentState) -> list[dict]:
    return list(state.get("steps") or [])


def _step(tool: str, query: str, outcome: str, reason: str = "") -> dict:
    """장부 한 줄. `reason` 은 고장에만 있다 — 빈 값을 넣어 두면 «원인 미상»과 구분이 없어진다."""
    entry = {"tool": tool, "query": query, "outcome": outcome}
    if reason:
        entry["reason"] = reason
    return entry


def _of(steps: list[dict], outcome: str) -> list[dict]:
    return [s for s in steps if s.get("outcome") == outcome]


def _label(step: dict) -> str:
    """계획·답변에 보이는 호출 표기. 서명(`"도구:질의"`)과 같은 꼴이다 — 예전에는 이 문자열이
    **기록 그 자체**여서 다시 잘라 써야 했고, 지금은 표시할 때만 만든다."""
    return f"{step.get('tool')}:{step.get('query')}"


def _repeated(steps: list[dict], tool: str, query: str) -> bool:
    """같은 도구를 같은 말로 이미 불렀나. 문자열을 합쳐 비교하지 않는다 — 두 칸을 그대로 잰다."""
    return any(s.get("tool") == tool and s.get("query") == query for s in steps)


def _failed_label(name: str) -> str:
    """죽은 도구를 직원이 읽는 말로. 문구는 도구 선언에서 온다(progress.py ①)."""
    tool = tools.TOOLS.get(name)
    return (tool.progress if tool and tool.progress else name)


def _tried_label(step: dict) -> str:
    """«찾아본 곳» 한 칸 — 직원이 읽는 재료 이름 + 찾아본 말.

    **`_label` 과 갈라 둔다.** 그쪽은 계획 프롬프트에 실리는 서명이라 도구 **이름**이어야
    하고(LLM 이 같은 호출을 다시 고르지 않게 하는 좌표다), 이쪽은 화면에 나가는 문장이다.
    한 함수로 쓰던 동안 「찾아본 곳: fact:고유계정대 최근 한 달 증가액」이 직원 화면에
    그대로 떴다(2026-09-21 실측, 13번) — `fact` 는 코드 안의 이름이지 업무 표현이 아니고,
    §5 「재료에 개발 용어를 쓰지 않는다」가 막는 바로 그것이다. 죽은 도구를 `_failed_label`
    이 이미 같은 방식으로 옮기고 있었는데 이 줄만 빠져 있었다.

    재료 이름은 도구 선언에서 온다 — 여기서 표를 새로 만들지 않는다(진행 표시·고장 안내와
    같은 문구를 쓴다. 표가 둘이면 한쪽만 고쳐진다). 찾아본 말(`query`)은 계획 LLM 이 쓴
    한국어라 그대로 싣는다 — 그게 «어떤 말로 찾아봤는지»이고, 직원이 다시 물을 때의 단서다.
    """
    return f"{_failed_label(str(step.get('tool') or ''))} 「{step.get('query')}」"


def _tool_failed(state: AgentState) -> str:
    """도구가 죽어 재료를 못 읽은 턴의 답. 무엇이 왜 실패했는지를 함께 남긴다 —
    진단이 화면에서 끝나야 한다(§11 · LLM_FAILED 가 원인을 싣는 것과 같은 이유).

    **사유는 한 줄로 자른다**(`short_reason` — 아래에 있다). 사유는 `str(예외)` 그대로라
    길이도 줄 수도 예외가 정한다 — 파서·HTTP 응답을 실어 오는 예외 하나가 답변 칸을
    스택트레이스 자리로 만든다(LLM 장애 안내가 프로바이더 본문으로 그렇게 됐다). 여기는
    **도구마다 한 줄**로 자른다: 통째로 이어 붙인 뒤 자르면 뒤쪽 도구의 사유가 통째로
    사라져 «무엇이 왜»의 절반을 잃는다. 전문은 로그·트레이스에 남는다.
    """
    failed = [s for s in _of(_steps(state), FAILED) if s.get("tool")]
    return TOOL_FAILED.format(
        what=" · ".join(dict.fromkeys(_failed_label(s["tool"]) for s in failed)),
        reasons="; ".join(short_reason(s.get("reason")) for s in failed))


def _no_evidence(state: AgentState) -> str:
    """근거 0건 답변. 무엇을 찾아봤는지 함께 말한다.

    죽은 호출은 «찾아본 곳»에 세지 않는다 — 그 도구는 지식베이스를 보지도 못했으므로,
    거기 세우면 «그 재료로 찾아봤는데 없더라»는 거짓 진술이 된다. 장부가 하나가 된 뒤로는
    그 판정이 `outcome` 한 칸이다(예전에는 서명을 잘라 죽은 도구 목록과 맞춰 봤다).
    """
    calls = [_tried_label(s) for s in _steps(state) if s.get("outcome") != FAILED]
    if not calls:
        return NO_EVIDENCE
    return NO_EVIDENCE + TRIED.format(calls=" · ".join(calls))

#: 빠진 필수 표시를 채워 넣는 블록의 머리말. 근거 원문 전체가 아니라 표시만 붙는다.
MISSING_NOTICES = "── 빠뜨리면 안 되는 표시"

#: 재료 성격 표시 블록의 머리말(§7). 어느 자료에서 온 말인지 · 고객에게 그대로 옮겨도
#: 되는지. 답을 읽는 사람은 직원이고, 무엇을 옮길지는 직원이 거른다 — 그 판단에 필요한
#: 표시를 주는 데까지가 에이전트의 몫이다.

#: LLM 단계가 깨졌을 때의 답. **'근거가 없다'와 절대 같은 말을 하면 안 된다** —
#: 찾아보고 없는 것과 찾아보지도 못한 것은 다르고, 뒤를 앞으로 말하면 지식베이스에 있는
#: 자료를 없다고 답하는 셈이 된다. 원인을 함께 남겨 진단이 화면에서 끝나게 한다.
#: 어느 단계(슬롯 분해·계획·문장 작성)에서 실패했든 이 한 문장으로 답한다.
LLM_FAILED = (
    "지금은 답변을 만들 수 없어요 — LLM 호출이 실패했습니다. "
    "지식베이스에 자료가 없다는 뜻이 아니니, 잠시 후 다시 시도해주세요.\n({reason})"
)


#: 화면에 실을 원인의 길이 상한(자). 진단이 화면에서 끝나게 하려고 원인을 싣지만,
#: **프로바이더가 준 응답 본문을 통째로 싣는 것은 다른 일이다** — 429 한 번에 쿼터 지표
#: 이름·문서 URL·환경변수 이름(LLM_MAX_CONCURRENCY …)이 직원 화면에 그대로 떴다(실측
#: 2026-09-18). 상담 화면에 설정값 이름이 뜨면 읽는 사람이 할 수 있는 일이 없고, 답변
#: 칸이 스택트레이스 자리가 된다. 전문은 트레이스·로그에 그대로 남는다.
REASON_MAX = 120


def short_reason(reason: str | None) -> str:
    """화면에 실을 한 줄짜리 원인. 첫 줄만, 상한까지."""
    head = (reason or "원인 미상").strip().split("\n", 1)[0].strip()
    return head if len(head) <= REASON_MAX else head[:REASON_MAX].rstrip() + "…"


#: 생성문이 끝내 검증을 통과하지 못한 턴의 머리말. 그 뒤에 근거 원문이 그대로 붙는다.
#:
#: §6 이 재생성을 첫 수로 만든 이유가 「폴백이 화면에서는 **답변처럼 보인다**」였는데, 재생성
#: 뒤에도 남는 폴백에는 그 문제가 그대로였다 — 말투가 갑자기 카드 덤프로 바뀌고(■ 제목 줄 ·
#: 「· 기준시점 … · 출처 …」 메타 줄) 직원은 에이전트가 왜 다르게 말하는지 알 수 없다.
#: 2026-09-18 실측: 「키움키워드림적격TDF 자세히 설명」이 TDF 매트릭스 원문 1,700자로
#: 답해졌고, 무엇이 왜 걸렸는지는 화면에 한 글자도 없었다.
#:
#: 그래서 한 줄로 밝히고 걸린 사유를 함께 남긴다 — 진단이 화면에서 끝나야 한다(§11 과 같은
#: 규약이고, TOOL_FAILED·LLM_FAILED 가 원인을 싣는 것과 같은 자리다).
#:
#: **`_NOTICE_HEADS` 에는 넣지 않는다.** 이건 «답을 못 했다»가 아니라 «근거는 이것이다»이고,
#: 뒤에 붙는 원문은 다음 턴이 「방금 그거 줄여줘」로 가리킬 수 있는 재료다(`last_answer`).
#: 안내문으로 분류하면 그 재료가 턴 기록에서 사라진다.
RAW_EVIDENCE = (
    "답변 문장이 근거 검증을 통과하지 못해서, 찾은 근거를 원문 그대로 보여드려요 — "
    "아래는 제가 쓴 문장이 아니라 지식베이스 원문입니다.\n({reason})"
)


def fault_kinds(faults: list[str]) -> str:
    """직원 화면에 적을 «무엇에 걸렸나» — 걸린 **종류**만이고 값은 떼어낸다.

    폴백 머리말(아래)과 쪽지 미발송 안내(`effects/memo.py`)가 함께 쓴다. `screen()` 이
    두 곳에서 같은 검사를 하므로 그 결과를 사람이 읽는 말로 바꾸는 자리도 하나다.

    사유 문자열은 「종류: 걸린 자리」 꼴인데(`_screen`), 뒷부분에는 **버려진 값이 그대로
    들어 있다**("자료에 없는 수치·상품명: 수치 '1,234,567'"). 그것을 화면에 실으면 §6 이
    막으려던 것이 사유 줄로 우회해 나간다 — 지어낸 숫자가 직원 눈앞에 다시 서고, 화면은
    그 값이 «답변에서 빠진 값»인지 «맞는 값»인지 말해주지 않는다. 종류만으로도 진단은
    된다(근거 밖 수치인지 · 관계 오짝인지 · 원문 스팬인지). 자세한 것은 로그와 트레이스에
    남는다(위 observability.step 의 `reason`) — 그쪽은 직원이 읽는 화면이 아니다.

    위 `short_reason` 과 같은 자리의 규칙이고 자르는 축만 다르다 — 그쪽은 **길이**(프로바이더
    응답 본문), 이쪽은 **내용**(버려진 값)이다. 종류를 이어 붙인 것도 길어질 수 있어 상한은
    그 함수에 맡긴다.
    """
    kinds = dict.fromkeys(f.split(":", 1)[0].strip() for f in faults if f.strip())
    return short_reason(" · ".join(kinds)) if kinds else "사유 미상"


#: 답변 자리에 나가지만 **답변이 아닌** 안내문의 머리말 셋 — 찾아봤는데 없음(NO_EVIDENCE) ·
#: 도구 고장(TOOL_FAILED) · LLM 장애(LLM_FAILED). 서식 자리(`{what}`·`{reason}`) 앞까지가
#: 고정 문구라 그 앞부분으로 가린다.
_NOTICE_HEADS = tuple(t.split("{", 1)[0] for t in (NO_EVIDENCE, TOOL_FAILED, LLM_FAILED))


def is_failure_notice(answer: str | None) -> bool:
    """이 답변이 위 안내문 중 하나인가.

    진입점이 턴 기록의 `answer` 를 채울지 정할 때 쓴다(graph.ask). 되묻기·LLM 장애는 상태
    키(`clarify`·`llm_error`)로 갈리지만 **근거 0건 안내와 도구 고장 안내는 상태에 표지가
    없다** — 문장으로만 남는다. 그래서 그 둘이 «답변»으로 저장됐고, 다음 턴의 `last_answer`
    가 그것을 다시 쓰는 재료로 실었다(2026-09-15 리허설 케이스 12c — 「근거를 찾지 못했다」는
    안내문이 «이전 답변»이 되어, 지식베이스에 있는 ETF 화법 2장을 두고 "자료가 없어요"로
    답했다). 안내문은 재료가 아니다 — 다시 쓰면 실패 안내가 답변처럼 나간다.
    """
    text = (answer or "").lstrip()
    return bool(text) and text.startswith(_NOTICE_HEADS)


# ─────────────────────────────────────────────────────────────
# Node. plan_step — 다음 도구 하나를 고르고 실행해 원장에 쌓는다
# ─────────────────────────────────────────────────────────────

def _untried(state: AgentState, steps: list[dict]) -> list[str]:
    """이 턴에 아직 안 불러본 도구 이름. 재계획 관문(_wrap_up)과 재계획 지시가 함께 쓴다."""
    used = {s.get("tool") for s in steps}
    return [name for name in tools.usable(state) if name not in used]


def _wrap_up(state: AgentState, evidence: list, steps: list[dict]) -> dict[str, Any]:
    """계획을 끝내기 전 마지막 관문 — **근거 0건이면 한 번은 다시 계획한다**(§5).

    LLM 이 done 을 말했든, 같은 호출을 반복했든, 없는 도구를 골랐든 끝내려는 사건은
    같다. 그런데 근거가 0건인 채 여기서 끝나면, 재료가 없는 것이 아니라 **도구·질의
    고르기를 실패한 것**이 '근거 없음'으로 답해진다 — 다른 도구를 써 볼 기회를 코드가
    한 번 만든다(재계획 프롬프트에는 빗나간 호출과 안 써 본 도구가 실린다). 두 번째
    끝내기는 존중한다: 정직한 '없음' 경로를 막지 않는다.
    """
    if evidence or state.get("plan_retry") or not _untried(state, steps):
        observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}", done=True,
                           retry=False if state.get("plan_retry") else None)
        return {"plan_done": True}
    observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}", done=True, retry=True,
                       unused=len(_untried(state, steps)))
    return {"plan_retry": True}


def _misses_block(state: AgentState, steps: list[dict]) -> str:
    """계획 프롬프트에 끼우는 '빗나간 호출' + (재계획 턴이면) '아직 안 써 본 도구' 블록.

    원장에는 성공한 재료만 실리므로, 이 블록이 없으면 계획은 자기가 뭘 불러봤는지 모르고
    같은 호출을 반복한다 — 반복은 코드가 끊고, 그러면 턴이 '근거 없음'으로 끝난다.

    **성공한 호출은 여기 세우지 않는다** — 그건 원장(ledger)이 이미 말하고 있고, 두 번
    세우면 관계있는 지시가 묻힌다(§7 과 같은 이유). 고장 난 호출도 세우지 않는다: 그
    도구는 이미 카탈로그에서 빠져 있어(`tools.usable`) 계획이 고를 수 없다.
    """
    parts: list[str] = []
    misses = [_label(s) for s in _of(steps, MISS)]
    if misses:
        parts.append(PLAN_MISSES_BLOCK.format(misses="\n".join(f"- {m}" for m in misses)))
    if state.get("plan_retry"):
        parts.append(PLAN_RETRY_BLOCK.format(untried=", ".join(_untried(state, steps))))
    return "".join(parts)


def _budget_block(steps: list[dict]) -> str:
    """계획 프롬프트에 끼우는 '남은 호출 수'. 상한을 쥔 것은 코드인데 계획은 그 값을 못
    봤다 — `last: true` 로 한 바퀴를 아끼라고 시키면서 몇 바퀴가 남았는지는 안 알려주던
    자리다(PLAN_BUDGET_BLOCK 머리말). 계산은 장부 길이 하나다."""
    return PLAN_BUDGET_BLOCK.format(left=max(MAX_STEPS - len(steps), 0))


def plan_step(state: AgentState) -> dict[str, Any]:
    evidence = list(state.get("evidence") or [])
    steps = _steps(state)

    if len(steps) >= MAX_STEPS:
        return {"plan_done": True}

    # 진행 표시 — 실제로 계획 LLM 을 부르기 직전에만 찍는다(위의 상한 조기 종료는 계획이
    # 아니다). 재계획 바퀴에도 찍는다 — 다시 정하는 것도 정하는 일이다.
    progress.emit("무엇을 찾아볼지 정하고 있어요")

    question = state["question"]
    try:
        raw = generate(
            PLAN_PROMPT.format(
                catalog=tools.catalog(state),
                ledger=tools.summarize(evidence),
                misses_block=_misses_block(state, steps),
                budget_block=_budget_block(steps),
                # 후속 질문("그럼 안 된다고 하면요?")은 이전 턴을 이어받아야 무엇을 묻는지
                # 정해진다. 이 줄이 없으면 계획이 이번 질문 한 줄만 보고 재료를 고른다(§2-1).
                history_block=format_history(state.get("history")),
                question=question,
            ),
            max_tokens=PLAN_MAX_TOKENS,
            name="consult.plan",
        )
    except LLMError as exc:
        # LLM 이 없거나 죽으면 계획을 세울 수 없다. **왜 못 했는지를 남긴다** — 예전에는
        # 여기서 조용히 루프만 끝냈고, 그러면 401·타임아웃·모델명 오류가 전부 "근거가
        # 없습니다"로 둔갑해 원인이 화면에서 사라졌다. 계획이 못 돈 것과 재료가 없는 것은
        # 다른 사건이고, 다르게 말해야 한다.
        observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}",
                           error=f"{type(exc).__name__}: {exc}", level=logging.WARNING)
        return {"plan_done": True, "llm_error": f"{type(exc).__name__}: {exc}"}

    # 이 호출이 됐다는 것은 LLM 이 살아 있다는 뜻이다. 앞 단계(슬롯 분해)가 일시적으로
    # 실패해 남긴 원인은 여기서 지운다 — 안 지우면 정상적으로 찾아보고 재료가 없었던 턴이
    # 'LLM 실패'로 답해진다(뒤집힌 방향의 같은 사고).
    alive: dict[str, Any] = {"llm_error": ""}

    action = json_object(raw) or {}
    if not action:
        # 규격 밖 응답(설명문·잘린 JSON). 같은 이유로 조용히 넘기지 않는다.
        observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}",
                           error="계획 응답을 JSON 으로 읽지 못함", level=logging.WARNING)
        return {"plan_done": True,
                "llm_error": f"계획 응답을 JSON 으로 읽지 못함 — {raw.strip()[:120]!r}"}

    name = action.get("tool")
    # 판정은 **이번 턴 카탈로그**(tools.usable)로 한다 — 등록 여부(tools.TOOLS)가 아니다.
    # 카탈로그에서 뺀 도구는 이번 턴에 재료가 없는 도구다(고객 화면이 닫혀 있을 때의 고객
    # 도구 · 다시 쓸 답변이 없을 때의 last_answer · 이번 턴에 죽은 도구). 그런데 계획
    # 프롬프트 본문이 도구 이름을 규칙 안에 적고 있어(PLAN_PROMPT 「직원이 이전 답변을
    # 가리키면 last_answer 를 부른다」) LLM 은 카탈로그에 없는 이름도 고른다. 등록 여부로만
    # 거르면 그 호출이 실행돼 빈손으로 돌아오고, 같은 이름이 반복 차단에 걸릴 때까지 바퀴를
    # 버린 뒤 «찾아본 곳: last_answer:[1]» 이 직원 화면에 나갔다(2026-09-15 케이스 12b).
    # 카탈로그 밖 이름은 없는 도구와 같은 처분이다 — 실행하지 않고 재계획으로 넘긴다.
    if action.get("done") or not isinstance(name, str) or name not in tools.usable(state):
        return {**alive, **_wrap_up(state, evidence, steps)}

    # 이 도구가 마지막이라고 말했으면 한 바퀴를 아낀다 — 재료 하나로 끝나는 질문
    # ("이 고객 예금 잔액 얼마지")도 계획에만 LLM 을 두 번 쓰던 자리다. 상한은 그대로
    # 코드가 정한다. **단, 그 도구가 실제로 재료를 내놨을 때만이다**(아래).
    last = bool(action.get("last"))

    query = action.get("query") or state.get("utterance") or question
    if not isinstance(query, str):
        query = question
    # 계획이 정한 것을 실행 전에 남긴다 — 도구 줄(tools.record)은 결과만 말하므로, 이 줄이
    # 없으면 «무엇을 골랐나»가 로그에서 «무엇을 얻었나» 뒤에 숨는다.
    observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}", tool=name,
                       query=tools._preview(query), done=True if last else None)
    if _repeated(steps, name, query):
        # 같은 호출을 반복하면 진전이 없다 — 도구를 다시 돌리지는 않되, 근거 0건이면
        # _wrap_up 이 한 번 되돌려 보낸다(빗나간 호출 목록을 보여주며).
        return {**alive, **_wrap_up(state, evidence, steps)}

    try:
        found = tools.run(name, state, query)
    except LLMError as exc:
        # 도구 안에서 LLM 이 죽었다(카드 선택·적합성 판정). 이걸 "근거를 못 찾았다"로
        # 접으면 있는 자료를 없다고 답하게 된다 — 계획 실패와 같은 사건으로 다룬다.
        observability.step("tool", name, error=f"{type(exc).__name__}: {exc}",
                           level=logging.WARNING)
        return {"plan_done": True, "llm_error": f"{type(exc).__name__}: {exc}"}
    except tools.ToolFailure as exc:
        # 도구가 죽었다. **루프는 끊지 않는다** — LLM 이 죽은 것과 달리 나머지 도구로 답이
        # 나올 수 있고, 죽은 도구는 다음 바퀴의 카탈로그에서 빠진다(tools.usable). 빗나간
        # 호출로 접지 않는 이유는 그쪽의 처방이 «질의의 말을 바꿔라»여서다 — 고장에는
        # 그 말이 틀렸고, 원장이 끝내 비었을 때 답도 갈린다(compose). 로그·점수는
        # tools.run 이 이미 남겼다.
        return {**alive, "steps": steps + [_step(name, query, FAILED, exc.reason)]}

    # 무슨 일이 있었는지는 한 번만 적는다 — 예전에는 성공·빗나감·고장이 각자 리스트를
    # 갖고 있어서, 결과 종류가 늘 때마다 반환값의 키가 늘었다(state.py 의 `steps` 주석).
    outcome = FOUND if found is not None else MISS
    update: dict[str, Any] = {"steps": steps + [_step(name, query, outcome)], **alive}
    # 게이트가 이번 호출에서 표시한 갈래(tools.record_branches 가 state 에 쌓아 둔 것)를
    # **자기 반환값으로** 넘긴다. 그래프 상태 전파를 in-place 변경에 기대지 않는다 —
    # 노드가 돌려준 것만 다음 노드가 본다는 규약이 여기서도 지켜져야, 계획이 여러 바퀴
    # 도는 동안 갈래가 조용히 사라지거나 두 벌이 되는 일이 없다.
    branches = state.get("branches")
    if branches:
        update["branches"] = list(branches)
    if found is not None:
        update["evidence"] = evidence + [found]

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
    return {"answer": LLM_FAILED.format(reason=short_reason(state.get("llm_error"))),
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
    return {r["name"] for r in engine.PRODUCTS} | kb_index.product_names(KB)


#: 근거 카드의 화면번호 스팬 꼴(`[04-12-646]`). 다른 `atomic` 스팬과 갈라 판정하기 위한 것이라
#: 대괄호까지 포함해 본다 — 도구가 그 꼴로 선언한다(`tools._procedure_decls`).
#: 답변에서 찾는 꼴(`_SCREEN_IN_TEXT`)은 대괄호를 요구하지 않는다 — 직원이 읽는 문장에서는
#: 「04-12-646 지급/해지조회」처럼 괄호 없이 쓰는 것이 정상이고, 표기 차이로 옳은 답변을
#: 버리지 않는다(§6 「이름 표기도 같다」와 같은 자리).
#: **둘 다 `screens` 가 갖는다** — 같은 판정을 화면 링크(`screens.links_in`)도 하므로, 여기
#: 따로 적으면 「화면번호란 무엇인가」의 출처가 둘이 되고 한쪽만 고쳐지는 날이 온다.
_SCREEN_SPAN = screens.SPAN
_SCREEN_IN_TEXT = screens.IN_TEXT


def _ledger_screens(evidence: Iterable[tools.Evidence]) -> set[str]:
    """이번 턴 원장 **전체**가 아는 화면번호(정규형). 근거 한 건이 아니라 합집합이다.

    화면번호 판정은 «답변이 원장에 없는 화면을 가리키는가»인데, 근거 한 건씩 재면 다른
    근거가 아는 화면이 «없는 화면»이 된다. 실측(2026-09-07 — 오세훈 SH5 · 박정호 PJ5):
    원장에 절차 카드([06-12-622] → [02-12-221] 2단계)와 화면 카드([02-12-221] 한 장)가 함께
    있었고, 답변이 절차 본문의 [06-12-622] 를 인용하자 **화면 카드 근거를 재는 차례에서**
    «이 근거에 없는 화면»으로 답이 통째로 버려졌다. 절차 근거 차례에서는 통과한 번호다.
    """
    return screens.declared(evidence)


def _span_verdict(found: tools.Evidence, answer: str,
                  known_screens: set[str] | None = None) -> tuple[str, list[str]]:
    """이 근거의 원문 스팬이 답변에서 어떻게 어긋났는지 판정한다. 종류는 도구가 선언한다.

    `known_screens` — 화면번호 판정에 쓸 «원장이 아는 화면» 집합. 호출부(`_screen`)가
    원장 전체의 합집합(`_ledger_screens`)을 넘긴다. 넘기지 않으면 이 근거 것만 본다
    (근거 하나로 재는 검사용).

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
    # 화면번호는 **식별자**라 다른 스팬과 판정이 다르다(§12 gap 2). 이름을 정확히 부르거나
    # 아예 안 부르거나이고, 흩어진 토큰으로 재면 안 된다 — 번호끼리 앞 마디를 공유하기
    # 때문이다(`04-12-…`·`06-12-…`). 예전 규칙(스팬이 답변에 없는데 숫자가 겹치면 폐기)은
    # 그래서 **답변이 화면번호를 일부만 인용하면 걸렸다**: 안 쓴 번호의 04·12 가 쓴 번호와
    # 겹쳐 «숫자는 썼는데 원문을 안 실었다»로 오판됐다. 원장 화면이 일곱 개인 절차 답변이
    # 여섯 개를 정확히 인용하고도 폐기돼 카드 원문이 덤프됐다(2026-09-02 실측 — 박정호 P3).
    #
    # 지금 재는 것은 «답변이 이 턴 원장에 **없는** 화면을 가리키는가» 하나다. 빠뜨린 것은
    # 위반이 아니고(안 부른 것이다), 대괄호 유무는 같은 화면이다(`screens.normalize`).
    # 지어낸 번호는 여기서도 걸리고 수치 검사에도 걸린다 — 마지막 마디가 원장에 없다.
    # 아는 화면은 원장 전체로 본다(`_ledger_screens` 머리말) — 이 근거만 보면 다른 근거의
    # 화면이 «없는 화면»이 된다.
    if known_screens is None:
        known_screens = _ledger_screens([found])
    if known_screens:
        for m in _SCREEN_IN_TEXT.finditer(answer):
            said = screens.normalize(m.group())
            if said not in known_screens:
                return DISCARD, [(m.group(), [])]

    for span in found["atomic"]:
        if _SCREEN_SPAN.fullmatch(span.strip()):
            continue                      # 위에서 식별자 규칙으로 이미 판정했다
        if span not in answer and (numbers(span) & numbers(answer)):
            # 걸린 스팬을 함께 돌려준다 — DISCARD 처분에는 안 쓰이지만, 계측(trace)이 이걸
            # 실어야 리허설 로그가 «무엇을 그대로 안 실어서 잘렸나»를 말할 수 있다. 판정
            # 상수만 남기면 화면에 "discard" 한 단어가 떨어져 아무도 진단할 수 없다.
            return DISCARD, [(span, [])]

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


#: 답변이 이 카드를 썼다고 보기 위해 스팬이 답변에 남아야 하는 길이. 짧은 조각은 다른
#: 문장에도 우연히 들어 있어서(「디폴트옵션」 한 낱말) 판정이 되지 않는다.
_KEY_MIN = 8


def _cited(answer: str, keys: list[str]) -> bool:
    """답변이 이 출처의 스팬을 실제로 담고 있는가. 잴 수 있는 스팬이 없으면 참(늘 남긴다)."""
    long = [k for k in keys if len(k.strip()) >= _KEY_MIN]
    if not long:
        return True
    return any(k.strip() in answer for k in long)


def _cite_filter(evidence: list[tools.Evidence], answer: str) -> set[str]:
    """답변이 쓰지 않은 것이 **분간되는** 출처 id — 이것만 근거 목록에서 뺀다.

    ━━ 왜 빼는가 ━━
    「이 고객 디폴트옵션 등록됐어?」에 "네, 설정되어 있어요" 한 줄로 답하고도 근거가 여섯
    건 섰다(2026-09-17 실측, 박정호). `customer` 도구가 원장 값과 함께 화면 ⑥⑦⑧ 이 이
    고객에게 고른 화법·방법론 카드를 묶어 싣기 때문이다 — 그 다섯 장은 답에 한 글자도
    쓰이지 않았는데 「이 답의 근거」로 나란히 섰고, 그러면 근거 표시가 무엇을 뜻하는지
    직원이 읽지 않게 된다.

    ━━ 왜 검색 결과는 안 빼는가 ━━
    §3 은 「답변에 영향을 준 재료는 전부 출처에 싣는다」이고 그것을 무르지 않는다. 검색으로
    찾아온 재료는 **그 질문에 답하려고 부른 것**이라 전부 근거이고, 답변이 화법 카드를
    의역해 쓰는 것은 정상이라 글자 대조로는 «안 썼다»가 판정되지 않는다 — 거기까지 빼면
    실제로 쓴 근거가 사라진다. 빼는 것은 **묶음으로 따라온** 재료뿐이고, 그것은 도구가
    스팬을 선언한 출처(`source_keys`)로 한정된다.

    ━━ 좁히는 것은 «선언» 하나다 ━━
    ⚠ 표시를 카드 단위로 가릴 때는 「하나도 못 가리면 전부 유지」를 둔다(`_screen` 의
    `selective`) — 거기서는 가릴 수 있는 것이 **한 도구가 돌려준 한 블록 안의 카드들**이라
    그 예외가 유일한 안전장치다. 여기서는 그 예외를 두지 않는다. 한 번 뒀더니 정작 지적된
    턴에서 아무것도 빠지지 않았다 — 원장 한 줄로 답한 턴은 딸려 온 다섯 장을 **하나도** 안
    쓰는 것이 정상이고, 그때가 바로 빼야 하는 때다. 안전장치는 선언 쪽에 이미 있다:
    스팬을 선언한 출처만 후보이고, 선언하는 도구는 지금 `customer` 하나다.

    남는 위험은 하나다 — 딸려 온 화법을 답변이 **한 글자도 겹치지 않게** 의역해 쓰면 그
    카드가 근거에서 빠진다. 작성 규칙이 고객 대사를 지식베이스 원문으로 쓰게 하므로
    (`prompts.COMPOSE_SYSTEM` 6번) 화법을 쓴 답변에는 그 카드의 문구가 남는다.
    """
    declared = {sid: keys for e in evidence
                for sid, keys in (e.get("source_keys") or {}).items()}
    return {sid for sid, keys in declared.items() if not _cited(answer, keys)}


def _sources(evidence: list[tools.Evidence], guards: list, alts: list,
             answer: str = "") -> list[dict]:
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
    dropped = _cite_filter(evidence, answer) if answer else set()
    out = [{**s, "role": GROUND} for s in tools.ledger_sources(evidence)
           if s["id"] not in dropped]
    seen = {s["id"] for s in out}
    for item in list(guards) + list(alts):
        card = item.get("card")
        if not card or card in seen:
            continue
        seen.add(card)
        # 제목이 없어 본문 발췌를 세울 때는 말줄임을 붙인다 — 뚝 끊긴 문장("…\"운용지시가
        # 되지 않는")이 출처 줄에 그대로 서면 잘린 것인지 원문이 그런 것인지 분간이 안 된다.
        excerpt = " ".join(item.get("text", "").split())
        if len(excerpt) > 60:
            excerpt = excerpt[:60] + "…"
        out.append({"id": card, "title": item.get("title") or excerpt,
                    "doc": item.get("doc"), "url": item.get("url"),
                    "score": None, "page": None, "role": CAUTION})
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


def _repeated_materials(state: AgentState, evidence: list[tools.Evidence]) -> list[str]:
    """직전 턴이 이번 턴과 같은 재료로 답했는가 — 겹치는 도구 이름.

    「그 중에 ~」처럼 좁히는 후속 질문에서, LLM 은 앞 답을 통째로 다시 세웠다(실측 T11 —
    적합성 목록 8종 + 제외 4종을 그대로 반복, 1,021자). 지시로 못 막는 이유는 **볼 수가
    없어서**다: 프롬프트에 실리는 이전 대화는 직원 질문만이고 답변 원문은 없다.

    답변 원문을 실어 해결하지 않는다 — LLM 이 그 수치를 되받으면 이번 턴 원장 밖이라
    `verify` 가 답을 통째로 버린다(§6). 도구 **이름**만 보면 그 위험이 없다.

    직전 한 턴만 본다. 두세 턴 전이면 직원이 다시 보고 싶어 물었을 수 있고, 그때는
    반복이 아니라 답이다.

    **다시 쓰는 턴에는 붙지 않는다.** 원장에 `last_answer` 가 있으면 직원이 직전 답변을
    다시 정리해 달라고 한 것이라, 「같은 목록을 다시 세우지 마라」가 요구와 정반대다
    (REPEAT_BLOCK 자체가 「직원이 다시 정리해 달라고 한 것이 아니면」이라고 단서를 단다 —
    그 단서를 코드가 확인할 수 있는 자리가 여기다).
    """
    history = state.get("history") or []
    if not history or any(e["tool"] == "last_answer" for e in evidence):
        return []
    prev = set(history[-1].get("tools") or [])
    return sorted(prev & {e["tool"] for e in evidence})


# ─────────────────────────────────────────────────────────────
# 생성문 점검 — 걸리면 한 번 다시 쓰게 한다
#
# 점검 자체는 §6 그대로다(수치·관계·스팬). 바뀐 것은 **걸렸을 때의 처분**이다. 예전에는
# 처분이 하나였다 — 근거 원문을 통째로 내보내기. 안전하지만 화면에서는 답변처럼 보이고,
# 말투가 갑자기 카드 덤프로 바뀐다(■ 제목 줄 · 「· 출처 …」 메타 줄). 걸린 이유가 한 문장인
# 경우가 대부분이라, 그 한 문장만 고치면 되는 답이 통째로 버려지고 있었다.
#
# §6 은 "걸린 생성문이 화면에 나가지 않는다"만 요구하고 그 뒤의 처분은 «재생성 · 근거 원문
# 제시» 중 구현이 정한다고 적어 뒀다. 재생성을 **한 번** 붙인다. 상한이 코드에 있는 것은
# 계획 루프의 MAX_STEPS 와 같은 이유다 — 통과할 때까지 돌면 한 턴의 비용이 열리게 된다.
# 두 번째도 걸리면 예전 그대로 근거 원문이 나간다(틀린 문장이 나가는 선택지는 없다).
# ─────────────────────────────────────────────────────────────

#: 재작성 시도 횟수. 0 이면 예전 동작(걸리면 바로 근거 원문 폴백)이다.
COMPOSE_RETRIES = 1

#: 재작성 프롬프트에 싣는 «걸린 자리» 최대 건수. 전부 실으면 지시가 길어져 정작 고쳐야 할
#: 자리가 묻힌다(§7 과 같은 이유 — 무관한 지시가 늘수록 관계있는 지시가 묻힌다).
FAULTS_SHOWN = 8


def _screen(answer: str, evidence: list[tools.Evidence],
            question: str, known: set[str],
            prompt_texts: Iterable[str] = ()) -> tuple[list[str], list[str]]:
    """생성문을 §6 의 세 검사에 건다. 반환: (걸린 자리, 덧붙일 표시).

    걸린 자리가 비어 있으면 통과다. 검사 순서는 예전과 같고(수치 → 관계 → 스팬), 앞에서
    걸리면 뒤는 돌리지 않는다 — 계측(trace)이 「앞에서 끊김」을 그대로 말할 수 있어야 한다.

    ━━ 프롬프트에 들어간 것은 인용도 허용된다 (§6) ━━
    `prompt_texts` 는 원장 밖이면서 **코드가 이번 턴 프롬프트에 실어 보낸** 텍스트다 —
    「하지 말 것」 가드와 승낙 턴의 제안 문구. 둘 다 코드가 LLM 에게 읽히기로 정한 것인데
    원장에는 없어서, 시킨 대로 인용하면 «자료 밖 수치»로 답이 통째로 버려졌다(실측:
    가드의 「…→ 6번」 → `수치 '6'`, 승낙 문구의 「화법 2건」 → `수치 '2'`). 가드는
    `_sources()` 가 이미 **출처로도 싣는다** — 「이게 근거다」라고 세워 놓고 인용은 막는
    상태였다.

    넓히는 것은 **수치뿐**이다(`echoable` 규약 그대로). 상품명은 넓히지 않는다 — 이름만
    대서 적합성 게이트를 뚫는 길을 열지 않기 위해서다(verify.verify_texts 머리말).
    그리고 이번 턴 프롬프트에 **실제로 들어간 것**만 넣는다: `_POOL_KEYS` 가 경고한
    «답변이 쓰지도 않을 후보 더미»와 다른 점이 그것이다.
    """
    # 질문은 «되받아 말해도 되는 값»이다 — 직원이 방금 말한 수치를 옮겨 적은 것을 지어낸
    # 값으로 보면 맞는 답이 버려진다(verify.verify_texts 의 echoable 머리말).
    ok, bad = verify_texts(answer, tools.ledger_texts(evidence), known_products=known,
                           echoable=[question, *(t for t in prompt_texts if t)])
    if not ok:
        return [f"자료에 없는 수치·상품명: {b}" for b in (bad or [])] or ["자료 밖 수치"], []

    # 관계 위반 — 값–조건 오짝 · 알려진 오답. 원장 밖 수치 검사가 못 잡는 자리다.
    broken = relations.check(answer, tools.ledger_related(evidence))
    if broken:
        return [f"자료가 「틀린 표현」으로 적어둔 것을 그대로 말함: {b}" for b in broken], []

    ledger_screens = _ledger_screens(evidence)
    verdicts = [_span_verdict(e, answer, ledger_screens) for e in evidence]
    if any(v == DISCARD for v, _ in verdicts):
        spans = [span for e, (v, detail) in zip(evidence, verdicts) if v == DISCARD
                 for span, _ in detail]
        return [f"그대로 옮겨야 하는 문장을 풀어 씀: {s}" for s in spans] or ["원문 스팬 누락"], []

    # 채우는 것은 **빠진 표시**다. 예전에는 근거 블록을 통째로 덧붙여서, ⚠ 한 줄이 모자란
    # 답변 아래에 카드 전문 1,000자가 붙었다 — 정작 그 한 줄이 묻힌다.
    appends: list[str] = []
    for _found, (verdict, gaps) in zip(evidence, verdicts):
        if verdict != APPEND:
            continue
        appends += [f"· {label}\n" + "\n".join(f"  {m}" for m in missing)
                    for label, missing in gaps]
    return [], appends


def screen(answer: str, evidence: list[tools.Evidence], question: str,
           *, prompt_texts: Iterable[str] = ()) -> list[str]:
    """§6 검사 한 벌 — 걸린 자리 목록(비어 있으면 통과). 화면 답변 밖에서 쓰는 공개 이름.

    **쪽지 본문도 같은 검사를 받는다**(§10 · `consult_agent/effects/memo.py`). 쪽지는 화면 답변과
    달리 되돌릴 수 없고, 검사를 따로 구현하면 두 벌이 곧 갈린다 — 한쪽만 관계 선언을 보고
    한쪽만 상품 등록부를 보는 식으로. 그래서 검사는 여기 하나이고, 갈리는 것은 **걸렸을 때의
    처분**뿐이다: 화면은 다시 쓰게 하고(compose), 쪽지는 보내지 않는다(폴백 없음).

    덧붙일 표시(`appends`)는 돌려주지 않는다 — 그것은 답변을 «채우는» 처분이고, 쪽지에는
    채우는 처분이 없다.
    """
    faults, _appends = _screen(answer, evidence, question, _known_products(),
                               prompt_texts=prompt_texts)
    return faults


# ─────────────────────────────────────────────────────────────
# Node. compose — 원장만으로 답을 만든다
# ─────────────────────────────────────────────────────────────

def compose(state: AgentState) -> dict[str, Any]:
    """모은 근거로 답을 만든다 — 도구 종류에 따라 방식이 갈리지 않는다.

    한 번의 생성으로 답변 전체를 쓰고, 그 뒤에 코드가 세 가지를 집행한다(`_screen`).
      ① 원장 밖 수치가 있으면 생성문을 버린다(지어낸 값이므로 복구 불가).
      ② **데이터가 선언한 관계**를 어겼으면 생성문을 버린다 — 조건과 값을 잘못 짝지었거나
         행원들이 적어둔 알려진 오답을 그대로 말한 경우다(relations.py).
      ③ 관계 선언이 없는 카드는 아직 값 스팬 강제로 지킨다. 어기면 역시 버린다.
      ④ 필수 표시가 빠졌으면 **그 표시만** 덧붙인다(덜 갖춰진 것이므로 모자란 것을 채운다).
    통과한 답변은 근거 안에서만 나온 것이고, 그 안에서 문장은 자유롭다.

    ①~③ 에 걸리면 **무엇이 걸렸는지를 실어 한 번 다시 쓰게 한다**(COMPOSE_RETRIES). 그래도
    걸리면 근거 원문이 답이다 — 틀린 문장이 나가는 선택지는 없다(§6).
    """
    evidence: list[tools.Evidence] = list(state.get("evidence") or [])
    if not evidence:
        # 재료가 없는 이유가 셋이다. 찾아봤는데 없는 것(NO_EVIDENCE) · LLM 이 깨져 찾아보지도
        # 못한 것(LLM_FAILED) · **도구가 죽어 읽지 못한 것**(TOOL_FAILED). 뒤의 둘을 앞으로
        # 말하면 있는 자료를 없다고 답하게 된다 — 셋을 갈라 답한다.
        failure = state.get("llm_error")
        if failure:
            answer = LLM_FAILED.format(reason=short_reason(failure))
            observability.step("compose", evidence="0건", answer="LLM실패안내", error=failure,
                               level=logging.WARNING)
        elif _of(_steps(state), FAILED):
            answer = _tool_failed(state)
            observability.step("compose", evidence="0건", answer="도구실패안내",
                               failed=len(_of(_steps(state), FAILED)), level=logging.WARNING)
        else:
            answer = _no_evidence(state)
            observability.step("compose", evidence="0건", answer="자료없음",
                               searched=len([s for s in _steps(state) if s.get("outcome") != FAILED]))
        return {"answer": answer, "sources": []}

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
    # 직전 턴이 같은 재료로 답했으면 그 사실을 알린다 — **판정은 코드가 한다.** history 에는
    # 답변 원문이 없어서(state.Turn) LLM 은 자기가 방금 무엇을 나열했는지 볼 수 없다.
    if _repeated_materials(state, evidence):
        prompt = f"{prompt}\n{REPEAT_BLOCK}"
    # 다시 쓰는 턴 — 원장에 이 에이전트가 이번 상담에서 한 답변이 실려 있다(`last_answer` 도구).
    # 이번 턴의 질문("좀 더 짧게 줄여줘")에는 주제가 없어서, 알려주지 않으면 작성 LLM 은
    # <자료>를 그 말에 대고 재고 「그 자료는 없어요」로 답한다(승낙 턴과 같은 모양). 판정은
    # 코드가 아는 값(원장의 도구 이름)으로 한다.
    if any(e["tool"] == "last_answer" for e in evidence):
        prompt = f"{prompt}\n{REWRITE_BLOCK}"
    # 승낙 턴 — 이번 턴의 질문은 "네" 한 마디다. 그 말에는 무엇을 쓰라는 것인지가 없어서,
    # 알려주지 않으면 LLM 은 <자료> 를 직전 턴의 질문에 대고 재고 「그 자료는 없어요」로
    # 답한다(ACCEPTED_BLOCK 주석의 실측). 무엇을 보여주기로 했는지는 제안한 턴이 정했고,
    # 그것을 아는 것은 코드다 — 이번 턴의 말에서 다시 추측하지 않는다(§10).
    if state.get("accepted"):
        prompt = f"{prompt}\n{ACCEPTED_BLOCK.format(label=state['accepted'])}"
    # 판정이 «전제를 밝히고 답하라»(assume)·«핵심 대상이 자료에 없다»(none)로 끝났으면 그
    # 블록을 얹는다(§5 · nodes/clarify.py). 판정과 작성은 동시에 도므로 이 값이 붙는 것은
    # **다시 쓰는 호출**뿐이다(nodes/answer.py) — 첫 호출은 판정 결과를 볼 수 없다.
    # 블록 안의 문장은 판정 LLM 이 썼지만 **원장 밖 수치가 없음이 이미 확인된 것**이라
    # (clarify._quotable) 인용 허용을 넓히지 않아도 된다.
    if state.get("judge_note"):
        prompt = f"{prompt}\n{state['judge_note']}"
    note = guard.prompt_note(guards, alts)
    if note:
        prompt = f"{prompt}\n\n{note}"
    # 위에서 프롬프트에 실어 보낸 것 중 **원장 밖인 것**. 인용해도 되는 값이어야 한다
    # (`_screen` 머리말). 원장에서 온 블록(재료·필수 스팬)은 이미 원장이라 넣지 않는다.
    injected = [note, state.get("accepted") or ""]

    progress.emit("모은 근거로 답변을 작성하고 있어요")
    known = _known_products()
    appends: list[str] = []
    try:
        # 관측 span — 작성과 게이트를 한 묶음으로 세운다. 「몇 번 다시 썼나 · 무엇에
        # 걸렸나」는 점수로도 나가 대시보드가 집계한다(observability.score).
        with observability.span("compose", metadata={"evidence": len(evidence)}) as sp:
            answer = generate(prompt, max_tokens=1500, system=COMPOSE_SYSTEM,
                              name="consult.compose").strip()
            observability.step("compose", evidence=f"{len(evidence)}건", draft=f"{len(answer)}자")
            faults: list[str] = []
            for attempt in range(COMPOSE_RETRIES + 1):
                if not answer:
                    break
                # 여기부터가 이 에이전트가 느린 이유의 절반이다 — 그 사실을 화면이 말하게 한다.
                # 지연이 «생각이 느린 것»이 아니라 «검증을 하는 것»으로 보여야 신뢰의 근거가 된다.
                progress.emit("답변이 근거를 벗어나지 않았는지 검증하고 있어요")
                faults, appends = _screen(answer, evidence, state["question"], known,
                                          prompt_texts=injected)
                if not faults:
                    observability.step("verify", passed=True,
                                       attempt=f"{attempt + 1}/{COMPOSE_RETRIES + 1}")
                    break
                answer = ""
                if attempt >= COMPOSE_RETRIES:
                    # 재작성 상한 — 근거 원문이 답이 된다(아래 폴백). 말투가 바뀌는 사건이라 WARNING.
                    observability.step("verify", passed=False,
                                       attempt=f"{attempt + 1}/{COMPOSE_RETRIES + 1}",
                                       fallback="raw_evidence", reason="; ".join(faults[:2]),
                                       level=logging.WARNING)
                    break
                observability.step("verify", passed=False,
                                   attempt=f"{attempt + 1}/{COMPOSE_RETRIES + 1}",
                                   reason="; ".join(faults[:2]), level=logging.WARNING)
                # 걸린 자리를 실어 한 번 더. 폐기 사유를 안 주면 같은 문장이 다시 나온다.
                progress.emit("근거와 어긋난 부분을 고쳐 다시 쓰고 있어요")
                retry = prompt + COMPOSE_RETRY_BLOCK.format(
                    faults="\n".join(f"- {f}" for f in faults[:FAULTS_SHOWN]))
                answer = generate(retry, max_tokens=1500, system=COMPOSE_SYSTEM,
                                  name="consult.compose.retry").strip()
            sp.update(output=answer or None, retries=attempt, faults=faults[:FAULTS_SHOWN])
            # 「지난 30번 중 몇 번이 게이트에 걸렸나」는 트레이스를 한 건씩 열어서는 못
            # 센다. 값은 전부 코드가 아는 사실이다 — LLM 이 자기 답을 채점하지 않는다.
            observability.score("compose_passed", bool(answer),
                                comment="; ".join(faults[:FAULTS_SHOWN]) or None)
            observability.score("compose_retries", attempt)
    except LLMError as exc:
        # 재료는 모았는데 문장을 못 쓴 것이다. 아래 폴백(근거 원문 그대로 싣기)으로 흘려보내면
        # 완성된 답변처럼 보이는 카드 덩어리가 나간다 — LLM 이 죽었을 때 다른 단계가 내는
        # 안내와 결과가 달라진다(§11). 여기서 끊고 같은 안내로 답한다.
        # 실패 원인을 상태에도 남긴다 — plan_step 이 앞 단계의 원인을 지우고 들어오므로
        # (`alive`), 여기서 안 남기면 «LLM 이 죽은 턴»이 상태만 보면 정상 턴과 구별되지
        # 않는다. 뒤에 붙는 것들(추천질문 등)이 실패 안내를 정상 답변으로 오인한다.
        observability.step("compose", evidence=f"{len(evidence)}건", answer="LLM실패안내",
                           error=f"{type(exc).__name__}: {exc}", level=logging.WARNING)
        return {"answer": LLM_FAILED.format(reason=short_reason(f"{type(exc).__name__}: {exc}")),
                "llm_error": f"{type(exc).__name__}: {exc}",
                "sources": _sources(evidence, [], [])}

    if answer:
        parts = [answer] + ([MISSING_NOTICES, *appends] if appends else [])
    else:
        # 생성문을 못 쓰면 근거 원문이 답이다 — 다만 그것이 **답변이 아님을 밝힌다**.
        parts = [RAW_EVIDENCE.format(reason=fault_kinds(faults))]
        parts += [e["text"] for e in evidence]

    # 재료 성격 표시 — 신뢰 등급 · 내부용 주의(§7). 답변이 이미 같은 말을 했으면 겹쳐
    # 세우지 않는다. 근거 원문을 그대로 내보낸 경우에도 붙는다 — 표시는 문장이 아니라
    # **재료**에 걸리는 것이라, 누가 문장을 썼는지와 무관하다.
    body = "\n\n".join(parts)
    seen = [m for m in tools.ledger_marks(evidence) if m not in body]
    if seen:
        parts.append(MATERIAL_MARKS + "\n" + "\n".join(f"· {m}" for m in seen))

    body_out = "\n\n".join(parts)
    return {"answer": body_out or _no_evidence(state),
            # 답변을 함께 넘긴다 — 묶음으로 따라온 카드 중 답변이 쓰지 않은 것을 가리려면
            # 답변이 있어야 한다. 생성문을 못 써 근거 원문을 그대로 내보낸 턴(`fallback`)은
            # 그 원문이 곧 답변이라 전부 «쓴 것»으로 잡힌다 — 그것이 맞다.
            "sources": _sources(evidence, guards, alts, body_out),
            # **생성문이 나갔는지 근거 원문이 나갔는지**를 상태에 남긴다. 폴백도 `answer` 가
            # 채워져 나가므로 호출부가 답의 유무만 보면 둘을 구분할 수 없고, 실제로
            # `nodes/answer.py` 가 그래서 **검증을 통과한 답을 손에 쥐고도 원문 덤프를
            # 내보냈다**(2026-09-17 실측 — 판정=전제로 다시 쓴 것이 게이트에 걸린 턴).
            # 그 파일 머리말은 「게이트에 걸려 폐기되면 처음 것을 낸다」고 적어 두고 있었다.
            "fallback": "" if answer else "raw_evidence",
            "guards": guards, "guard_alternatives": alts}
