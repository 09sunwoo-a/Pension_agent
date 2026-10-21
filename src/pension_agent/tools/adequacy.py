"""적합성 게이트 — 고른 근거가 질문에 답이 되는지 카드별 판정. 모든 검색 도구의 채택 직전 단계."""

from __future__ import annotations

import json
import re
from pension_agent.common import progress
from pension_agent.prompts import ADEQUACY_PROMPT
from pension_agent.state import AgentState, format_history
from pension_agent import tools as _T  # noqa: PLC0415 — 후크는 패키지 경유


# 판정은 카드 하나씩 — 묶음 YES/NO 는 빗나간 후보 하나 때문에 맞는 카드까지 버림

#: 후보 한 줄의 tiers 조건 수·길이 상한(조건만, 값 미포함).
_TIER_WHENS = 4
_TIER_CHARS = 90

#: 후보 한 줄의 대사 길이 상한.
_SPEECH_CHARS = 70


def _tiers_line(card: dict) -> str:
    """카드 tiers 의 조건만 한 줄로(카드 한 장 안의 갈래 표시용). 선언 없으면 빈 문자열."""
    whens: list[str] = []
    for t in card.get("tiers") or []:
        when = " ".join(str((t or {}).get("when") or "").split())
        if when and when not in whens:
            whens.append(when)
    if not whens:
        return ""
    line = " / ".join(whens[:_TIER_WHENS])
    if len(whens) > _TIER_WHENS:
        line += " / …"
    return line[:_TIER_CHARS]


def _speech_line(card: dict) -> str:
    """카드의 첫 행원 대사 한 줄. 없으면 빈 문자열.

    summary 가 저작 메모인 화법 카드가 있어 대사 미포함 시 게이트가 맞는 카드를 버림.
    고객 발화는 잡음이라 미포함.
    """
    lines = [d for d in (card.get("dialogue") or []) if isinstance(d, dict)]
    staff = [str(d.get("text") or "").strip() for d in lines if d.get("speaker") == "행원"]
    pick = next((t for t in staff if t), "")
    return " ".join(pick.split())[:_SPEECH_CHARS]


def _headline(card: dict) -> str:
    """게이트용 후보 한 줄 — 제목·요약 + 같은 말·대사·조건별 값."""
    title = card.get("title") or card.get("label") or card.get("id")
    detail = next((str(card[k]) for k in
                   ("value", "condition_text", "summary", "situation", "action", "content")
                   if card.get(k)), "")
    points = "; ".join(card.get("key_points") or [])[:80]
    tail = (detail or points).replace("\n", " ")[:80]
    speech = _speech_line(card)
    tiers = _tiers_line(card)
    # 별칭 미포함 시 게이트가 「디폴트옵션」↔「사전지정운용제도」를 무관 후보로 오판
    aliases = " · ".join(card.get("aliases") or [])
    return (f"- [{card.get('id')}] {title}" + (f" · {tail}" if tail else "")
            + (f" · 같은 말: {aliases}" if aliases else "")
            + (f" · 대사: {speech}" if speech else "")
            + (f" · 조건별 값: {tiers}" if tiers else ""))


#: 적합성 판정 응답 토큰 상한 — 부족하면 JSON 이 잘려 후보 전멸.
ADEQUACY_MAX_TOKENS = 400

#: 갈래 한 축의 최소 선택지 수(되묻기 MIN_OPTIONS 와 동일).
MIN_BRANCH_OPTIONS = 2


def _branches(raw: object) -> list[dict]:
    """게이트 응답의 branches 정규화 — 축 이름·선택지 라벨만(카드 id 미포함)."""
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        axis = str(item.get("axis") or "").strip()
        options = [str(o).strip() for o in (item.get("options") or [])
                   if isinstance(o, str) and str(o).strip()]
        if axis and len(options) >= MIN_BRANCH_OPTIONS:
            out.append({"axis": axis, "options": options})
    return out


def record_branches(state: AgentState, found: list[dict]) -> None:
    """게이트가 표시한 갈래를 state["branches"] 에 축 이름 기준 중복 없이 추가.

    원장 미포함 — 근거로 실으면 compose 가 인용함. 노드 밖 전달은 plan_step 반환값으로.
    """
    if not found:
        return
    bucket = state.setdefault("branches", [])
    seen = {b["axis"] for b in bucket}
    for b in found:
        if b["axis"] not in seen:
            seen.add(b["axis"])
            bucket.append(b)


def _adequacy_verdict(raw: str) -> tuple[set[str], list[dict]]:
    """게이트 응답 → (남길 id, 갈래). 객체·옛 배열 형식 모두 허용 — 형식 불일치로 후보 전멸 방지."""
    obj = re.search(r"\{.*\}", raw, re.S)
    if obj:
        try:
            val = json.loads(obj.group())
        except ValueError:
            val = None
        if isinstance(val, dict):
            keep = val.get("keep")
            return ({x for x in keep if isinstance(x, str)} if isinstance(keep, list) else set(),
                    _branches(val.get("branches")))
    arr = re.search(r"\[.*\]", raw, re.S)
    if arr:
        try:
            val = json.loads(arr.group())
        except ValueError:
            val = None
        if isinstance(val, list):
            return {x for x in val if isinstance(x, str)}, []
    return set(), []


def fits_question(question: str, hits: list[tuple[float, dict]],
                  kind: str = "지식", history: list[dict] | None = None,
                  query: str | None = None,
                  sink: list[dict] | None = None) -> list[tuple[float, dict]]:
    """질문 의도에 맞는 후보만 반환(순서·점수 유지). 지어낸 id 는 실재 후보와 대조해 제거.

    - 이전 대화 필수 — 없으면 후속 질문(«1번꺼»)에서 맞는 카드까지 탈락
    - query: 계획이 찾는 대상. 없으면 질문 사용
    - sink: 게이트가 표시한 갈래 수집처. 갈래 후보는 제거하지 않음
    - LLM 장애는 그대로 전파
    """
    progress.emit("찾은 자료가 질문에 맞는지 확인하고 있어요")
    cards = "\n".join(_headline(c) for _, c in hits)
    raw = _T.generate(ADEQUACY_PROMPT.format(question=question, cards=cards, kind=kind,
                                          query=query or question,
                                          history_block=format_history(history)),
                   max_tokens=ADEQUACY_MAX_TOKENS, name="consult.adequacy")
    keep, found = _adequacy_verdict(raw)
    if sink is not None:
        sink.extend(found)
    return [(score, card) for score, card in hits if card.get("id") in keep]


def _adopt(state: AgentState, query: str, hits: list[tuple[float, dict]],
           kind: str) -> list[tuple[float, dict]]:
    """게이트 통과 후보 반환(0건이면 게이트 생략). 직원 질문·이번 질의 둘 다 전달, 갈래는 상태에 기록."""
    if not hits:
        return []
    sink: list[dict] = []
    kept = _T.fits_question(state.get("question") or query, hits, kind,
                            history=state.get("history"), query=query, sink=sink)
    record_branches(state, sink)
    # tools.run 이 로그에 싣고 바로 지움. 원장 미포함
    state["_gate"] = {"candidates": len(hits), "picked": len(kept), "branches": len(sink)}
    return kept
