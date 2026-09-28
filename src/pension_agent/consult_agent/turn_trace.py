"""대화 턴 하나의 절차 기록 — 응답의 `trace` 이벤트가 싣는 것.

━━ 왜 있나 ━━
발표용 «답변 근거» 패널이 이것을 그린다. 질문을 어떻게 읽었고, 어떤 도구를 어떤 질의로 불러
어떤 근거 카드를 얻었고, 답변이 검증을 어떻게 지났는지를 **근거 카드 단위로** 보여준다.
로그(`[agent]` 줄)와 같은 사건이지만 로그는 상한이 있는 요약이고, 이것은 구조 그대로다.
이 기록은 응답으로만 나간다 — stdout(Grafana)에는 싣지 않는다(main.py 머리말 «로그 이벤트»).

━━ 무엇을 싣지 않는가 ━━
  · **폐기된 초안 전문.** 검증에 걸린 초안은 근거 밖 수치를 담고 있다. 화면에 그대로 뜨면
    답으로 읽히고, 캡처되면 틀린 안내 문구가 된다. 시도 번호·판정·폐기 사유만 싣는다
    (`timeline` 의 verify 항목 · `detail.faults`).
  · **LLM 이 단 문장별 출처.** 문장과 근거의 대응(`sentences`)은 **코드가 대조해 증명한
    것만** 싣는다 — 원문 스팬이 문장에 그대로 있거나, 카드가 선언한 문구가 문장에 있거나,
    문장의 수치가 그 근거 블록에 있을 때다. 의역한 문장은 대응이 빈 목록으로 온다.
    출처 표시가 틀리면 신뢰 표시 전체가 거짓말이 된다(루트 CLAUDE.md 절대 규칙 1).

개인정보 가림은 여기서 하지 않는다 — 응답 경계(main.py)가 이벤트 전체에 한 번 건다.
"""

from __future__ import annotations

import re
from typing import Any

from pension_agent import verify

#: 근거 블록 원문을 싣는 상한(글자). 고객 원장 블록이 가장 길다 — 패널 한 칸에 다 보이고도
#: 남는 길이다. 넘으면 자르고 `truncated` 를 참으로 둔다.
EVIDENCE_TEXT_MAX = 4000

#: 카드 선언 문구로 대응을 세울 때의 최소 길이 — `plan._cited` 와 같은 기준이다. 짧은 문구
#: («만기»)는 아무 문장에나 들어 있어 대응의 증거가 못 된다.
KEY_MIN = 8

#: 문장 경계 — 마침표·물음표·느낌표 뒤의 공백. «다 »·«요 » 같은 어미로는 자르지 않는다 —
#: «다 채우면»의 «다»처럼 문장 중간에도 나온다.
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")


def _sentences(answer: str) -> list[str]:
    """답변을 문장으로 나눈다. 줄바꿈도 경계다(목록 줄 · 블록 머리)."""
    out: list[str] = []
    for line in (answer or "").splitlines():
        line = line.strip()
        if not line:
            continue
        out += [s.strip() for s in _SENTENCE_END.split(line) if s and s.strip()]
    return out


def _matches(sentence: str, evidence: list[dict], allowed: list[set[str]]) -> list[dict]:
    """이 문장이 어느 근거에서 나왔는지 — 코드가 증명할 수 있는 것만.

    `allowed` 는 근거 블록마다의 인용 가능 수치(`evidence` 와 같은 순서). 문장마다 다시 걷지
    않도록 호출부가 한 번만 만든다.
    """
    found: list[dict] = []
    measures = verify.measures(sentence)
    for e, nums in zip(evidence, allowed):
        tool = e.get("tool")
        cards = [s.get("id") for s in (e.get("sources") or []) if s.get("id")]
        # 블록에 카드가 한 장뿐이면 블록의 대응이 곧 그 카드의 대응이다.
        only = cards[0] if len(cards) == 1 else None
        for span in e.get("atomic") or []:
            if span.strip() and span.strip() in sentence:
                found.append({"tool": tool, "card": only, "by": "원문스팬", "span": span.strip()})
        for card, keys in (e.get("source_keys") or {}).items():
            hit = next((k.strip() for k in keys if len(k.strip()) >= KEY_MIN and k.strip() in sentence), None)
            if hit:
                found.append({"tool": tool, "card": card, "by": "카드문구", "span": hit})
        if measures:
            # 문장에 적힌 표기 그대로 — 그 값의 형태 하나라도 이 근거에 있으면 이 근거의 값이다.
            shared = list(dict.fromkeys(raw for raw, forms in measures if forms & nums))
            if shared:
                found.append({"tool": tool, "card": only, "by": "수치", "values": shared})
    return found


def _evidence(evidence: list[dict], used: set[str]) -> list[dict]:
    out = []
    for e in evidence:
        text = e.get("text") or ""
        out.append({
            "tool": e.get("tool"),
            "query": e.get("query"),
            "text": text[:EVIDENCE_TEXT_MAX],
            "truncated": len(text) > EVIDENCE_TEXT_MAX,
            # 이 블록에 묶여 온 카드. `used` 가 거짓이면 «딸려 왔지만 답변이 쓰지 않은» 카드다
            # (plan._cite_filter — 최종 출처 목록에서 빠진 것).
            "cards": [{"id": s.get("id"), "title": s.get("title"), "doc": s.get("doc"),
                       "score": s.get("score"), "used": s.get("id") in used}
                      for s in (e.get("sources") or []) if s.get("id")],
            # 답변이 그 수치를 쓰면 원문 그대로여야 하는 스팬 · 빠지면 안 되는 표시.
            "atomic": list(e.get("atomic") or []),
            "notices": list(e.get("notices") or []),
        })
    return out


def build(out: dict[str, Any], journal: list[dict], *, started_at: str,
          finished_at: str) -> dict[str, Any]:
    """그래프 산출(`out`)과 단계 기록(`observability.journal()`)으로 절차 기록을 만든다.

    되묻기·승낙·LLM 장애 턴은 도구나 검증 단계가 없다 — 있는 것만 싣고 없는 칸은 빈 목록이다
    (`sources`·`followups` 가 0건에도 빈 목록으로 오는 것과 같은 규약).
    """
    evidence = list(out.get("evidence") or [])
    sources = list(out.get("sources") or [])
    used = {s.get("id") for s in sources}
    answer = out.get("answer") or ""
    allowed = [verify.allowed_from_texts(e.get("allow") or [e.get("text") or ""])[0]
               for e in evidence]
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "intent": out.get("intent"),
        # 단계 순서대로 — 로그 줄과 같은 사건(understand · plan · tool · compose · verify · turn …).
        "timeline": journal,
        # 계획이 부른 도구 호출 — 질의 전문과 결과(found · miss · failed)·실패 사유.
        "rounds": [{"n": i + 1, "tool": s.get("tool"), "query": s.get("query"),
                    "outcome": s.get("outcome"), "reason": s.get("reason") or None}
                   for i, s in enumerate(out.get("steps") or [])],
        "evidence": _evidence(evidence, used),
        "sources": sources,
        "sentences": [{"text": s, "matches": _matches(s, evidence, allowed)}
                      for s in _sentences(answer)] if evidence else [],
    }
