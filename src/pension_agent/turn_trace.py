"""대화 턴 절차 기록 — 응답 `trace` 이벤트(«답변 근거» 패널) 재료. stdout 미기록.

미포함: 폐기된 초안 전문(시도·판정·사유만) · LLM 이 단 출처(문장–근거 대응은 코드가 증명한 것만).
개인정보 가림은 응답 경계(main.py)에서.
"""

from __future__ import annotations

import re
from typing import Any

from pension_agent.evidence import verify

#: 근거 블록 원문 상한(글자). 초과 시 자르고 `truncated` 표시.
EVIDENCE_TEXT_MAX = 4000

#: 카드 선언 문구 대응의 최소 길이(`plan._cited` 와 동일) — 짧은 문구는 증거 불가.
KEY_MIN = 8

#: 문장 경계 — 마침표·물음표·느낌표 뒤 공백. 어미(«다 »)로는 자르지 않음(문장 중간에도 등장).
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")


def _sentences(answer: str) -> list[str]:
    """답변 → 문장 목록. 줄바꿈도 경계."""
    out: list[str] = []
    for line in (answer or "").splitlines():
        line = line.strip()
        if not line:
            continue
        out += [s.strip() for s in _SENTENCE_END.split(line) if s and s.strip()]
    return out


def _matches(sentence: str, evidence: list[dict], allowed: list[set[str]]) -> list[dict]:
    """문장의 근거 대응(코드가 증명 가능한 것만). `allowed` 는 블록별 인용 가능 수치."""
    found: list[dict] = []
    measures = verify.measures(sentence)
    for e, nums in zip(evidence, allowed):
        tool = e.get("tool")
        cards = [s.get("id") for s in (e.get("sources") or []) if s.get("id")]
        only = cards[0] if len(cards) == 1 else None
        for span in e.get("atomic") or []:
            if span.strip() and span.strip() in sentence:
                found.append({"tool": tool, "card": only, "by": "원문스팬", "span": span.strip()})
        for card, keys in (e.get("source_keys") or {}).items():
            hit = next((k.strip() for k in keys if len(k.strip()) >= KEY_MIN and k.strip() in sentence), None)
            if hit:
                found.append({"tool": tool, "card": card, "by": "카드문구", "span": hit})
        if measures:
            # 값의 형태 하나라도 이 근거에 있으면 이 근거의 값
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
            # `used` 거짓 = 딸려 왔지만 답변이 쓰지 않은 카드
            "cards": [{"id": s.get("id"), "title": s.get("title"), "doc": s.get("doc"),
                       "score": s.get("score"), "used": s.get("id") in used}
                      for s in (e.get("sources") or []) if s.get("id")],
            "atomic": list(e.get("atomic") or []),
            "notices": list(e.get("notices") or []),
        })
    return out


def build(out: dict[str, Any], journal: list[dict], *, started_at: str,
          finished_at: str) -> dict[str, Any]:
    """그래프 산출 + 단계 기록 → 절차 기록. 없는 칸은 빈 목록."""
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
        # 단계 순서(로그 줄과 같은 사건)
        "timeline": journal,
        # 도구 호출 — 질의·결과(found · miss · failed)·실패 사유
        "rounds": [{"n": i + 1, "tool": s.get("tool"), "query": s.get("query"),
                    "outcome": s.get("outcome"), "reason": s.get("reason") or None}
                   for i, s in enumerate(out.get("steps") or [])],
        "evidence": _evidence(evidence, used),
        "sources": sources,
        "sentences": [{"text": s, "matches": _matches(s, evidence, allowed)}
                      for s in _sentences(answer)] if evidence else [],
    }
