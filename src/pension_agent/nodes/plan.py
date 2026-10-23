"""계획 루프 — LLM 이 도구를 고르고 결과를 보고 반복, 충분하면 답변 작성.

LLM: 도구·질의 선택과 종료(plan_step) · 답변 문장(compose).
코드: 바퀴 상한(MAX_STEPS) · 반복·미등록 차단 · 도구 실패 처리 · 원장 밖 수치 차단 · 스팬 집행.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from pension_agent.common import observability
from pension_agent.common import progress
from pension_agent import tools
from pension_agent.actions import screens
from pension_agent.tools.customer_solution import guard
from pension_agent.tools.kb_search import kb_index
from pension_agent.evidence import relations
from pension_agent.evidence.marks import MATERIAL_MARKS
from pension_agent.tools.kb_search.pitch_slots import situation_line
from pension_agent.prompts import (
    ACCEPTED_BLOCK, ANSWER_SHAPES, COMPOSE_PROMPT, COMPOSE_RETRY_BLOCK, COMPOSE_SYSTEM,
    MUST_BLOCK,
    PLAN_BUDGET_BLOCK, PLAN_MISSES_BLOCK, PLAN_PROMPT, PLAN_RETRY_BLOCK, REPEAT_BLOCK,
    REWRITE_BLOCK, SHAPE_BLOCK,
)
from pension_agent.state import KB, AgentState, format_history
from pension_agent.common.llm import LLMError, generate, json_object
from pension_agent.evidence.verify import numbers, verify_texts

#: 한 턴 도구 호출 상한 — 고객+수치+절차+화법 질문 기준.
MAX_STEPS = 4

#: 계획 응답 토큰 상한. 너무 작으면 JSON 이 잘려 도구 미호출(근거 없음)로 보임.
PLAN_MAX_TOKENS = 300

#: 근거 0건 답 — 공손한 해요체 한 문장. 찾아본 질의·재료 종류 나열 미포함(진단은 트레이스).
NO_EVIDENCE = "죄송해요, 그 질문은 제가 가진 자료로는 답을 드리기 어려워요."


#: 도구 고장으로 재료를 못 읽은 턴의 답. NO_EVIDENCE 와 같은 문장 금지(있는 자료를 없다고 답하게 됨).
TOOL_FAILED = (
    "지금은 답변을 만들 수 없어요 — {what} 자료를 읽는 데 실패했습니다. "
    "자료가 없다는 뜻이 아니니, 잠시 후 다시 시도해주세요.\n({reasons})"
)


# state["steps"] 항목 = 호출 하나: {"tool", "query", "outcome": found|miss|failed, "reason"}

#: 호출 결과 값(원본은 tools).
FOUND, MISS, FAILED = tools.FOUND, tools.MISS, tools.FAILED


def _steps(state: AgentState) -> list[dict]:
    return list(state.get("steps") or [])


def _step(tool: str, query: str, outcome: str, reason: str = "") -> dict:
    """장부 한 줄. reason 은 고장에만 기록."""
    entry = {"tool": tool, "query": query, "outcome": outcome}
    if reason:
        entry["reason"] = reason
    return entry


def _of(steps: list[dict], outcome: str) -> list[dict]:
    return [s for s in steps if s.get("outcome") == outcome]


def _label(step: dict) -> str:
    """호출 표기 `"도구:질의"`."""
    return f"{step.get('tool')}:{step.get('query')}"


def _repeated(steps: list[dict], tool: str, query: str) -> bool:
    """같은 도구·같은 질의 호출 이력 여부."""
    return any(s.get("tool") == tool and s.get("query") == query for s in steps)


def _failed_label(name: str) -> str:
    """죽은 도구의 직원 표시 이름."""
    tool = tools.TOOLS.get(name)
    return (tool.progress if tool and tool.progress else name)


def _tool_failed(state: AgentState) -> str:
    """도구 고장 턴의 답 — 무엇이 왜 실패했는지 포함.

    사유는 도구마다 한 줄로 자른 뒤 연결 — 이어 붙인 뒤 자르면 뒤쪽 도구 사유 소실.
    """
    failed = [s for s in _of(_steps(state), FAILED) if s.get("tool")]
    return TOOL_FAILED.format(
        what=" · ".join(dict.fromkeys(_failed_label(s["tool"]) for s in failed)),
        reasons="; ".join(short_reason(s.get("reason")) for s in failed))


def _no_evidence(state: AgentState) -> str:
    """근거 0건 답변(NO_EVIDENCE). 안내문 셋을 같은 꼴로 부르기 위한 함수."""
    return NO_EVIDENCE

#: 표시 문장 머리 기호(※ 시효 · ⚠ 주의 · ⚖ 고지).
_MARK_HEADS = ("※", "⚠", "⚖")


def _as_mark(text: str) -> str:
    """코드가 덧붙이는 표시 한 줄. 기호 없으면 ※ 추가, 문장 자체는 불변(_span_verdict 가 원문 대조)."""
    text = text.strip()
    return text if text.startswith(_MARK_HEADS) else f"※ {text}"


def _already_said(mark: str, body: str) -> bool:
    """답변이 이 표시를 이미 말했는지 — 기준시점 꼬리(« — … 기준 표기») 뗀 본체로 대조.

    LLM 이 꼬리를 조금 바꿔 옮기면 전체 문자열 대조로는 중복 미검출.
    """
    core = mark.split(" — ", 1)[0].strip()
    return bool(core) and (core in body or mark in body)


#: 앞 문장 뒤에 이어 붙은 표시(※·⚠·⚖) — 문장 일부로 읽히는 문제.
_MARK_INLINE = re.compile(r"(?<=\S)[ \t]+(?=[※⚠⚖]\s)")


def _break_marks(text: str) -> str:
    """이어 붙은 표시를 제 줄로 분리. 공백→줄바꿈만이라 검증 결과 불변."""
    return _MARK_INLINE.sub("\n", text)

#: LLM 실패 턴의 답 — 어느 단계든 동일. NO_EVIDENCE 와 같은 문장 금지.
LLM_FAILED = (
    "지금은 답변을 만들 수 없어요 — LLM 호출이 실패했습니다. "
    "자료가 없다는 뜻이 아니니, 잠시 후 다시 시도해주세요.\n({reason})"
)


#: 화면에 싣는 실패 원인 길이 상한(자) — 프로바이더 응답 본문 노출 방지. 전문은 로그.
REASON_MAX = 120


def short_reason(reason: str | None) -> str:
    """화면에 실을 한 줄짜리 원인. 첫 줄만, 상한까지."""
    head = (reason or "원인 미상").strip().split("\n", 1)[0].strip()
    return head if len(head) <= REASON_MAX else head[:REASON_MAX].rstrip() + "…"


#: 검증 끝내 실패 시 근거 원문 앞 머리말 — 폴백임과 걸린 종류를 밝힘.
#: _NOTICE_HEADS 미포함 — 뒤의 원문은 last_answer 재료로 남아야 함.
RAW_EVIDENCE = (
    "답변 문장이 근거 검증을 통과하지 못해서, 찾은 근거를 원문 그대로 보여드려요 — "
    "아래는 제가 쓴 문장이 아니라 자료 원문입니다.\n({reason})"
)


def fault_kinds(faults: list[str]) -> str:
    """화면용 «무엇에 걸렸나» — 종류만, 값은 제거(지어낸 값이 사유 줄로 노출 방지).

    폴백 머리말·쪽지 미발송 안내(actions/memo.py) 공용.
    """
    kinds = dict.fromkeys(f.split(":", 1)[0].strip() for f in faults if f.strip())
    return short_reason(" · ".join(kinds)) if kinds else "사유 미상"


#: 답변이 아닌 안내문 셋의 고정 머리말(서식 자리 앞까지).
_NOTICE_HEADS = tuple(t.split("{", 1)[0] for t in (NO_EVIDENCE, TOOL_FAILED, LLM_FAILED))


def is_failure_notice(answer: str | None) -> bool:
    """안내문(NO_EVIDENCE·TOOL_FAILED·LLM_FAILED) 여부. graph.ask 의 턴 기록용.

    두 안내문은 상태 표지가 없어 문장으로 판별 — 안 거르면 last_answer 재료로 재사용됨.
    """
    text = (answer or "").lstrip()
    return bool(text) and text.startswith(_NOTICE_HEADS)


def _untried(state: AgentState, steps: list[dict]) -> list[str]:
    """이 턴에 아직 안 불러본 도구 이름."""
    used = {s.get("tool") for s in steps}
    return [name for name in tools.usable(state) if name not in used]


def _wrap_up(state: AgentState, evidence: list, steps: list[dict]) -> dict[str, Any]:
    """계획 종료 관문 — 근거 0건이고 안 써 본 도구가 있으면 한 번 재계획. 두 번째 종료는 존중."""
    if evidence or state.get("plan_retry") or not _untried(state, steps):
        observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}", done=True,
                           retry=False if state.get("plan_retry") else None)
        return {"plan_done": True}
    observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}", done=True, retry=True,
                       unused=len(_untried(state, steps)))
    return {"plan_retry": True}


def _misses_block(state: AgentState, steps: list[dict]) -> str:
    """계획 프롬프트용 '빗나간 호출' + (재계획 시) '안 써 본 도구' 블록. 성공·고장 호출 미포함."""
    parts: list[str] = []
    misses = [_label(s) for s in _of(steps, MISS)]
    if misses:
        parts.append(PLAN_MISSES_BLOCK.format(misses="\n".join(f"- {m}" for m in misses)))
    if state.get("plan_retry"):
        parts.append(PLAN_RETRY_BLOCK.format(untried=", ".join(_untried(state, steps))))
    return "".join(parts)


def _budget_block(steps: list[dict]) -> str:
    """계획 프롬프트용 '남은 호출 수' 블록."""
    return PLAN_BUDGET_BLOCK.format(left=max(MAX_STEPS - len(steps), 0))


def plan_step(state: AgentState) -> dict[str, Any]:
    evidence = list(state.get("evidence") or [])
    steps = _steps(state)

    if len(steps) >= MAX_STEPS:
        return {"plan_done": True}

    progress.emit("무엇을 찾아볼지 정하고 있어요")

    question = state["question"]
    try:
        raw = generate(
            PLAN_PROMPT.format(
                catalog=tools.catalog(state),
                ledger=tools.summarize(evidence),
                misses_block=_misses_block(state, steps),
                budget_block=_budget_block(steps),
                # 후속 질문 해석용 이전 대화
                history_block=format_history(state.get("history")),
                question=question,
            ),
            max_tokens=PLAN_MAX_TOKENS,
            name="consult.plan",
        )
    except LLMError as exc:
        # 원인 기록 필수 — 없으면 LLM 장애가 «근거 없음»으로 둔갑
        observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}",
                           error=f"{type(exc).__name__}: {exc}", level=logging.WARNING)
        return {"plan_done": True, "llm_error": f"{type(exc).__name__}: {exc}"}

    # LLM 정상 — 앞 단계의 일시 실패 원인 제거(안 지우면 정상 턴이 LLM 실패로 답해짐)
    alive: dict[str, Any] = {"llm_error": ""}

    action = json_object(raw) or {}
    if not action:
        # 규격 밖 응답(설명문·잘린 JSON)도 원인 기록
        observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}",
                           error="계획 응답을 JSON 으로 읽지 못함", level=logging.WARNING)
        return {"plan_done": True,
                "llm_error": f"계획 응답을 JSON 으로 읽지 못함 — {raw.strip()[:120]!r}"}

    name = action.get("tool")
    # 등록 여부(TOOLS)가 아니라 이번 턴 카탈로그(usable)로 판정 — 프롬프트 본문의 도구 이름을
    # LLM 이 골라도 실행하지 않고 재계획으로
    if action.get("done") or not isinstance(name, str) or name not in tools.usable(state):
        return {**alive, **_wrap_up(state, evidence, steps)}

    # last — 재료를 얻었을 때만 한 바퀴 절약(아래)
    last = bool(action.get("last"))

    query = action.get("query") or state.get("utterance") or question
    if not isinstance(query, str):
        query = question
    observability.step("plan", step=f"{len(steps) + 1}/{MAX_STEPS}", tool=name,
                       query=tools._preview(query), done=True if last else None)
    if _repeated(steps, name, query):
        # 반복 호출 미실행 — 근거 0건이면 _wrap_up 이 재계획
        return {**alive, **_wrap_up(state, evidence, steps)}

    try:
        found = tools.run(name, state, query)
    except LLMError as exc:
        # 도구 내부 LLM 장애 — «근거 없음»이 아니라 계획 실패로 처리
        observability.step("tool", name, error=f"{type(exc).__name__}: {exc}",
                           level=logging.WARNING)
        return {"plan_done": True, "llm_error": f"{type(exc).__name__}: {exc}"}
    except tools.ToolFailure as exc:
        # 도구 고장 — 루프 유지(죽은 도구는 다음 카탈로그에서 제외). miss 로 접지 않음
        return {**alive, "steps": steps + [_step(name, query, FAILED, exc.reason)]}

    outcome = FOUND if found is not None else MISS
    update: dict[str, Any] = {"steps": steps + [_step(name, query, outcome)], **alive}
    # 게이트 갈래 표시는 반환값으로 전달 — in-place 변경에 의존 금지
    branches = state.get("branches")
    if branches:
        update["branches"] = list(branches)
    if found is not None:
        update["evidence"] = evidence + [found]

    # last 는 재료를 얻었을 때만 존중 — 빗나간 호출로 끝내면 다른 도구 기회 소실
    if last and found is not None:
        update["plan_done"] = True
    # 완성 문서(전체 브리핑)면 수집 종료 — 그대로 답(document_answer)
    if found is not None and found["meta"].get("document"):
        update["plan_done"] = True
    return update


def llm_down(state: AgentState) -> dict[str, Any]:
    """분류 단계 LLM 장애 턴 — compose 와 같은 LLM_FAILED 안내. 규칙 응답 대체 금지."""
    return {"answer": LLM_FAILED.format(reason=short_reason(state.get("llm_error"))),
            "sources": []}


#: 스팬 판정 — 폐기(값 오짝) · 표시 덧붙임(누락) · 통과.
DISCARD, APPEND, OK = "discard", "append", "ok"


def _known_products() -> set[str]:
    """상품명 등록부 — 상품 카탈로그 ∪ 지식베이스 선언 상품명. 무거운 임포트 지연."""
    from pension_agent.tools.customer_solution import engine  # noqa: PLC0415
    return {r["name"] for r in engine.PRODUCTS} | kb_index.product_names(KB)


#: 화면번호 꼴 — 스팬은 대괄호 포함, 답변 속은 대괄호 무관. 정의는 screens 단일 출처.
_SCREEN_SPAN = screens.SPAN
_SCREEN_IN_TEXT = screens.IN_TEXT


def _ledger_screens(evidence: Iterable[tools.Evidence]) -> set[str]:
    """원장 전체가 아는 화면번호(정규형) 합집합 — 근거별로 재면 다른 근거의 화면이 오판됨."""
    return screens.declared(evidence)


#: 답변·원문 스팬 속 링크. 인용부호·괄호·공백에서 끊고, 문장부호 꼬리는 뒤에서 뗀다.
_URL = re.compile(r"https?://[^\s\"'“”‘’«»<>()\[\]]+")


def _urls_in(text: str) -> set[str]:
    return {m.group().rstrip(".,;:!?…") for m in _URL.finditer(text or "")}


def _ledger_urls(evidence: Iterable[tools.Evidence]) -> set[str]:
    """원장 전체가 원문 스팬으로 선언한 링크 합집합."""
    return {u for e in evidence for span in e["atomic"] for u in _urls_in(span)
            if _URL.fullmatch(span.strip())}


def _span_verdict(found: tools.Evidence, answer: str,
                  known_screens: set[str] | None = None,
                  known_urls: set[str] | None = None) -> tuple[str, list[str]]:
    """근거 하나의 원문 스팬 판정. known_screens·known_urls 미지정 시 이 근거 것만 사용.

    · atomic — 숫자를 쓰고 원문 미포함 → DISCARD(덧붙이기로 복구 불가)
    · notices — 누락 → APPEND + 빠진 표시만. 답변이 안 쓴 카드의 표시는 제외(가릴 수 없으면 유지)
    """
    # 화면번호는 식별자 — 원장에 없는 화면을 가리키는지만 판정. 흩어진 숫자 토큰 대조 금지
    # (번호끼리 앞 마디 공유로 일부 인용이 오판됨)
    if known_screens is None:
        known_screens = _ledger_screens([found])
    if known_screens:
        for m in _SCREEN_IN_TEXT.finditer(answer):
            said = screens.normalize(m.group())
            if said not in known_screens:
                return DISCARD, [(m.group(), [])]

    # 링크도 식별자 — 같은 규칙(링크끼리 꼬리 번호 공유). 원장 선언 링크 없으면 미판정
    if known_urls is None:
        known_urls = _ledger_urls([found])
    if known_urls:
        for said in sorted(_urls_in(answer)):
            if said not in known_urls:
                return DISCARD, [(said, [])]

    for span in found["atomic"]:
        if _SCREEN_SPAN.fullmatch(span.strip()) or _URL.fullmatch(span.strip()):
            continue                      # 식별자 규칙으로 판정 완료
        if span not in answer and (numbers(span) & numbers(answer)):
            # 걸린 스팬 반환 — 계측 진단용
            return DISCARD, [(span, [])]

    scopes = found.get("notice_scopes") or []
    keyed = [s for s in scopes if s.get("keys")]
    used = [s for s in keyed if any(k in answer for k in s["keys"])]
    # 쓴 카드가 분간될 때만 선별, 아니면 전부 유지(⚠ 손실 방지)
    selective = bool(used) and len(keyed) > 1

    gaps: list[tuple[str, list[str]]] = []
    for scope in scopes:
        if selective and scope.get("keys") and scope not in used:
            continue   # 답변이 안 쓴 카드
        missing = [s for s in scope.get("notices") or [] if s not in answer]
        if missing:
            gaps.append((scope.get("label") or found["tool"], missing))
    return (APPEND, gaps) if gaps else (OK, [])


#: 출처 역할 어휘(원본은 tools — clarify 와 공용).
GROUND, CAUTION = tools.GROUND, tools.CAUTION


#: 인용 판정용 스팬 최소 길이 — 짧은 조각은 우연 일치.
_KEY_MIN = 8


def _cited(answer: str, keys: list[str]) -> bool:
    """답변이 출처 스팬을 담는지. 잴 스팬 없으면 참."""
    long = [k for k in keys if len(k.strip()) >= _KEY_MIN]
    if not long:
        return True
    return any(k.strip() in answer for k in long)


def _cite_filter(evidence: list[tools.Evidence], answer: str) -> set[str]:
    """답변이 안 쓴 것으로 분간되는 출처 id — 근거 목록에서 제외 대상.

    후보는 source_keys 를 선언한 출처(묶음으로 따라온 재료)뿐 — 검색 결과는 의역 인용이 정상이라 제외 금지.
    «하나도 못 가리면 전부 유지» 예외 없음 — 딸려 온 카드를 하나도 안 쓴 턴이 바로 뺄 때.
    """
    declared = {sid: keys for e in evidence
                for sid, keys in (e.get("source_keys") or {}).items()}
    return {sid for sid, keys in declared.items() if not _cited(answer, keys)}


def _sources(evidence: list[tools.Evidence], guards: list, alts: list,
             answer: str = "") -> list[dict]:
    """답변에 영향을 준 재료 전부 — 원장(근거 역할) + 가드·대안 화법(주의 역할).

    score 는 검색 재료에만 — 가드·대안은 None(관련도 0 과 구분).
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
        # 제목 없으면 발췌 + 말줄임(잘림 표시)
        excerpt = " ".join(item.get("text", "").split())
        if len(excerpt) > 60:
            excerpt = excerpt[:60] + "…"
        out.append({"id": card, "title": item.get("title") or excerpt,
                    "doc": item.get("doc"), "url": item.get("url"),
                    "score": None, "page": None, "role": CAUTION})
    return out


def _shape_block(evidence: list[tools.Evidence]) -> str:
    """답변 형태 요구 블록 — 원장에 실린 재료 종류의 것만(질문 유형 = 부른 도구)."""
    seen: list[str] = []
    for e in evidence:
        shape = ANSWER_SHAPES.get(e["tool"])
        if shape and shape not in seen:
            seen.append(shape)
    return SHAPE_BLOCK.format(shapes="\n".join(f"- {x}" for x in seen)) if seen else ""


def _repeated_materials(state: AgentState, evidence: list[tools.Evidence]) -> list[str]:
    """직전 한 턴과 겹치는 재료 도구 이름 — 반복 나열 방지 지시(REPEAT_BLOCK) 판정용.

    답변 원문 대신 도구 이름으로 판정(원문을 실으면 원장 밖 수치 되받기로 폐기).
    last_answer 턴(다시 정리 요청)은 제외.
    """
    history = state.get("history") or []
    if not history or any(e["tool"] == "last_answer" for e in evidence):
        return []
    prev = set(history[-1].get("tools") or [])
    return sorted(prev & {e["tool"] for e in evidence})


#: 검증 실패 시 재작성 횟수(코드 상한). 0 이면 바로 근거 원문 폴백.
COMPOSE_RETRIES = 1

#: 재작성 프롬프트에 싣는 «걸린 자리» 최대 건수.
FAULTS_SHOWN = 8


def _screen(answer: str, evidence: list[tools.Evidence],
            question: str, known: set[str],
            prompt_texts: Iterable[str] = ()) -> tuple[list[str], list[str]]:
    """생성문 검사(수치 → 관계 → 스팬, 앞에서 걸리면 중단). 반환: (걸린 자리, 덧붙일 표시).

    prompt_texts — 코드가 이번 턴 프롬프트에 실은 원장 밖 텍스트(가드·승낙 문구). 수치만 인용 허용,
    상품명은 미허용(이름으로 적합성 게이트 우회 방지).
    """
    # 질문 수치는 되받기 허용
    ok, bad = verify_texts(answer, tools.ledger_texts(evidence), known_products=known,
                           echoable=[question, *(t for t in prompt_texts if t)])
    if not ok:
        return [f"자료에 없는 수치·상품명: {b}" for b in (bad or [])] or ["자료 밖 수치"], []

    # 관계 위반 — 값–조건 오짝 · 알려진 오답
    broken = relations.check(answer, tools.ledger_related(evidence))
    if broken:
        return [f"자료가 「틀린 표현」으로 적어둔 것을 그대로 말함: {b}" for b in broken], []

    ledger_screens = _ledger_screens(evidence)
    ledger_urls = _ledger_urls(evidence)
    verdicts = [_span_verdict(e, answer, ledger_screens, ledger_urls) for e in evidence]
    if any(v == DISCARD for v, _ in verdicts):
        spans = [span for e, (v, detail) in zip(evidence, verdicts) if v == DISCARD
                 for span, _ in detail]
        return [f"그대로 옮겨야 하는 문장을 풀어 씀: {s}" for s in spans] or ["원문 스팬 누락"], []

    # 빠진 표시만 채움(카드 전문 미첨부)
    appends: list[str] = []
    for _found, (verdict, gaps) in zip(evidence, verdicts):
        if verdict != APPEND:
            continue
        # 표시 문장만 — 카드 제목 줄 미포함
        appends += [m for _label, missing in gaps for m in missing if m not in appends]
    return [], appends


def screen(answer: str, evidence: list[tools.Evidence], question: str,
           *, prompt_texts: Iterable[str] = ()) -> list[str]:
    """근거 검사 공개 진입점 — 걸린 자리 목록(빈 목록이면 통과). 쪽지(actions/memo.py) 공용.

    덧붙일 표시는 미반환(쪽지에는 채우는 처분 없음).
    """
    faults, _appends = _screen(answer, evidence, question, _known_products(),
                               prompt_texts=prompt_texts)
    return faults


def document_answer(state: AgentState) -> dict[str, Any] | None:
    """원장의 완성 문서(meta["document"], 전체 브리핑)를 그대로 답으로. 없으면 None.

    도구 안에서 검사 완료 — compose 재작성 시 양식·표 값 훼손.
    """
    evidence: list[tools.Evidence] = list(state.get("evidence") or [])
    doc = next((e["meta"].get("document") for e in evidence if e["meta"].get("document")), None)
    if not doc:
        return None
    conds = guard.conditions_of(state.get("customer_id"))
    guards = guard.cautions_for(KB, conds) if conds else []
    alts = guard.sensitive_cards(KB, conds) if conds else []
    observability.step("compose", evidence=f"{len(evidence)}건", answer="문서", draft=f"{len(doc)}자")
    return {"answer": doc, "sources": _sources(evidence, guards, alts, doc), "fallback": ""}


def compose(state: AgentState) -> dict[str, Any]:
    """원장만으로 답변 작성 후 코드 검사(_screen).

    ① 원장 밖 수치 ② 관계 위반 ③ 값 스팬 미준수 → 폐기, ④ 빠진 표시 → 덧붙임.
    ①~③ 은 걸린 자리를 실어 COMPOSE_RETRIES 회 재작성, 그래도 실패 시 근거 원문 폴백.
    """
    evidence: list[tools.Evidence] = list(state.get("evidence") or [])
    if not evidence:
        # 근거 0건 사유 셋 구분 — LLM 장애 · 도구 고장 · 없음
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

    # 「하지 말 것」 가드 — LLM 의 customer 도구 호출과 무관하게 코드가 고객 상태로 부착
    conds = guard.conditions_of(state.get("customer_id"))
    guards = guard.cautions_for(KB, conds) if conds else []
    alts = guard.sensitive_cards(KB, conds) if conds else []

    # 필수 인용은 atomic 만, notices 미전달 — LLM 이 부분 복사해 표시가 중복됨(표시는 appends 가 부착)
    spans = [a for e in evidence for a in e["atomic"]]
    prompt = COMPOSE_PROMPT.format(
        context="\n\n".join(e["text"] for e in evidence),
        must_block=MUST_BLOCK.format(spans="\n".join(f"- {a}" for a in spans)) if spans else "",
        shape_block=_shape_block(evidence),
        history_block=format_history(state.get("history")),
        question=state["question"],
        situation_line=situation_line(state.get("intent"), tools.ledger_slots(evidence)),
    )
    # 직전 턴과 재료 겹침 — 코드 판정(LLM 은 직전 답변 원문을 못 봄)
    if _repeated_materials(state, evidence):
        prompt = f"{prompt}\n{REPEAT_BLOCK}"
    # 다시 쓰는 턴(last_answer) — 알리지 않으면 주제 없는 질문에 «자료 없음»으로 답함
    if any(e["tool"] == "last_answer" for e in evidence):
        prompt = f"{prompt}\n{REWRITE_BLOCK}"
    # 승낙 턴 — 질문이 «네»뿐이라 제안 내용(코드가 앎)을 알려야 함
    if state.get("accepted"):
        prompt = f"{prompt}\n{ACCEPTED_BLOCK.format(label=state['accepted'])}"
    # 판정 블록(assume·none) — 재작성 호출에만 존재. 원장 밖 수치 없음은 clarify._quotable 이 확인
    if state.get("judge_note"):
        prompt = f"{prompt}\n{state['judge_note']}"
    note = guard.prompt_note(guards, alts)
    if note:
        prompt = f"{prompt}\n\n{note}"
    # 프롬프트에 실은 원장 밖 텍스트 — 수치 인용 허용 대상
    injected = [note, state.get("accepted") or ""]

    progress.emit("모은 근거로 답변을 작성하고 있어요")
    known = _known_products()
    appends: list[str] = []
    try:
        # 작성 + 게이트를 한 관측 span 으로
        with observability.span("compose", metadata={"evidence": len(evidence)}) as sp:
            answer = generate(prompt, max_tokens=1500, system=COMPOSE_SYSTEM,
                              name="consult.compose").strip()
            observability.step("compose", evidence=f"{len(evidence)}건", draft=f"{len(answer)}자")
            faults: list[str] = []
            for attempt in range(COMPOSE_RETRIES + 1):
                if not answer:
                    break
                progress.emit("답변이 근거를 벗어나지 않았는지 검증하고 있어요")
                faults, appends = _screen(answer, evidence, state["question"], known,
                                          prompt_texts=injected)
                if not faults:
                    observability.step("verify", passed=True,
                                       attempt=f"{attempt + 1}/{COMPOSE_RETRIES + 1}")
                    break
                answer = ""
                if attempt >= COMPOSE_RETRIES:
                    # 재작성 상한 — 근거 원문 폴백
                    observability.step("verify", passed=False,
                                       attempt=f"{attempt + 1}/{COMPOSE_RETRIES + 1}",
                                       fallback="raw_evidence", reason="; ".join(faults[:2]),
                                       detail={"faults": faults[:FAULTS_SHOWN]},
                                       level=logging.WARNING)
                    break
                observability.step("verify", passed=False,
                                   attempt=f"{attempt + 1}/{COMPOSE_RETRIES + 1}",
                                   reason="; ".join(faults[:2]),
                                   detail={"faults": faults[:FAULTS_SHOWN]},
                                   level=logging.WARNING)
                # 걸린 자리 미전달 시 같은 문장 반복
                progress.emit("근거와 어긋난 부분을 고쳐 다시 쓰고 있어요")
                retry = prompt + COMPOSE_RETRY_BLOCK.format(
                    faults="\n".join(f"- {f}" for f in faults[:FAULTS_SHOWN]))
                answer = generate(retry, max_tokens=1500, system=COMPOSE_SYSTEM,
                                  name="consult.compose.retry").strip()
            sp.update(output=answer or None, retries=attempt, faults=faults[:FAULTS_SHOWN])
            # 대시보드 집계용 점수
            observability.score("compose_passed", bool(answer),
                                comment="; ".join(faults[:FAULTS_SHOWN]) or None)
            observability.score("compose_retries", attempt)
    except LLMError as exc:
        # 근거 원문 폴백 금지 — 다른 단계와 같은 LLM 장애 안내. llm_error 도 기록(후속 처리 오인 방지)
        observability.step("compose", evidence=f"{len(evidence)}건", answer="LLM실패안내",
                           error=f"{type(exc).__name__}: {exc}", level=logging.WARNING)
        return {"answer": LLM_FAILED.format(reason=short_reason(f"{type(exc).__name__}: {exc}")),
                "llm_error": f"{type(exc).__name__}: {exc}",
                "sources": _sources(evidence, [], [])}

    # 답변 틀(부점 브리핑 — 코드가 만든 제목·표). 검사를 통과한 문장에만 씌운다
    frame = next((e["meta"].get("frame") for e in evidence if (e.get("meta") or {}).get("frame")), None)
    if answer and frame:
        answer = tools.framed(answer, frame)
    if answer:
        # 빠진 표시는 본문 아래 제 줄로
        parts = [_break_marks(answer)] + ([ "\n".join(_as_mark(a) for a in appends)] if appends else [])
    else:
        # 근거 원문 폴백 — 폴백임을 머리말로 밝힘
        parts = [RAW_EVIDENCE.format(reason=fault_kinds(faults))]
        parts += [e["text"] for e in evidence]

    # 재료 성격 표시(신뢰 등급·내부용) — 폴백에도 부착, 답변이 이미 말했으면 생략
    body = "\n\n".join(parts)
    seen = [m for m in tools.ledger_marks(evidence) if not _already_said(m, body)]
    if seen:
        parts.append(MATERIAL_MARKS + "\n" + "\n".join(f"· {m}" for m in seen))

    body_out = "\n\n".join(parts)
    return {"answer": body_out or _no_evidence(state),
            # 답변 전달 — 안 쓴 묶음 카드 제외용
            "sources": _sources(evidence, guards, alts, body_out),
            # 폴백 여부 — answer 유무로는 구분 불가(answer.py 가 사용)
            "fallback": "" if answer else "raw_evidence",
            "guards": guards, "guard_alternatives": alts}
