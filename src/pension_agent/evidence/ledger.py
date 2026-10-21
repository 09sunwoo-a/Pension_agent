"""원장 helper — 근거 목록에서 슬롯·관계·표시·출처 추출, 출처 한 줄 표기(source_lines)."""

from __future__ import annotations

from pension_agent.evidence.record import Evidence


def ledger_slots(evidence: list[Evidence]) -> dict:
    """화법 도구가 뽑은 상담 상황 슬롯. 없으면 빈 dict."""
    for e in evidence:
        slots = e["meta"].get("slots")
        if slots:
            return slots
    return {}


def ledger_related(evidence: list[Evidence]) -> list[dict]:
    """원장의 관계 선언 카드 전부(compose 대조용)."""
    out: list[dict] = []
    seen: set[str] = set()
    for e in evidence:
        for card in e.get("related") or []:
            if card.get("id") not in seen:
                seen.add(card.get("id"))
                out.append(card)
    return out


def ledger_marks(evidence: list[Evidence]) -> list[str]:
    """원장의 재료 성격 표시 전부(중복 제거, 순서 유지)."""
    out: list[str] = []
    for e in evidence:
        for m in e.get("marks") or []:
            if m not in out:
                out.append(m)
    return out


def ledger_texts(evidence: list[Evidence]) -> list[str]:
    """원장의 검증 허용 텍스트 전부(verify_texts 재료).

    notices 포함 — 코드가 카드 데이터로 만든 표시 문장(기준시점 등). 빠지면 그 값을 옮긴 답변이 폐기됨.
    """
    return [t for e in evidence for t in (*e["allow"], *(e.get("notices") or []))]


#: 출처 역할 — 답의 근거 vs 표현 제한(가드). compose·clarify 공용 어휘.
GROUND, CAUTION = "근거", "주의"


def source_lines(s: dict, *, compact: bool = False) -> list[str]:
    """출처 한 건의 터미널 표기 — 운영 CLI·디버그 실행기 공용.

    관련도는 있을 때만 표기, doc.url 은 ↗ 줄. compact 는 문서명·제목·id 한 줄.
    """
    doc = s.get("doc") or "출처 미상 — 확인 필요"
    title = s.get("title") or ""
    if compact:
        lines = [f"   · {doc} — {title} [{s['id']}]"]
    else:
        tail = f" · 관련도 {s['score']}" if s.get("score") is not None else ""
        lines = [f"   · {doc}", f"     — {title} [{s['id']}{tail}]"]
    if s.get("url"):
        lines.append(f"     ↗ {s['url']}")
    return lines


def ledger_sources(evidence: list[Evidence]) -> list[dict]:
    """원장의 근거 목록(중복 id 제거, 순서 유지)."""
    out: list[dict] = []
    seen: set[str] = set()
    for e in evidence:
        for s in e["sources"]:
            if s["id"] not in seen:
                seen.add(s["id"])
                out.append(s)
    return out


def summarize(evidence: list[Evidence]) -> str:
    """계획 프롬프트용 '지금까지 모은 것' — 본문 미포함."""
    if not evidence:
        return "(아직 없음)"
    return "\n".join(
        f"- {e['tool']}(\"{e['query']}\") → " +
        (", ".join(s["title"] or s["id"] for s in e["sources"][:3]) or "재료 확보")
        for e in evidence
    )
