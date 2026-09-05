"""원장 helper — 근거 목록에서 슬롯·관계·표시·출처를 꺼낸다 · 출처 한 줄 표기(source_lines).

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from pension_agent.consult_agent.tools.base import Evidence


def ledger_slots(evidence: list[Evidence]) -> dict:
    """화법 도구가 뽑아둔 상담 상황 슬롯. 화법을 안 부른 턴에는 빈 dict."""
    for e in evidence:
        slots = e["meta"].get("slots")
        if slots:
            return slots
    return {}


def ledger_related(evidence: list[Evidence]) -> list[dict]:
    """원장에 실린, 관계를 선언한 카드 전부. compose 가 답변을 이것과 대조한다."""
    out: list[dict] = []
    seen: set[str] = set()
    for e in evidence:
        for card in e.get("related") or []:
            if card.get("id") not in seen:
                seen.add(card.get("id"))
                out.append(card)
    return out


def ledger_marks(evidence: list[Evidence]) -> list[str]:
    """원장에 실린 재료 성격 표시 전부(중복 제거, 등장 순서 유지). compose 가 답변에 붙인다."""
    out: list[str] = []
    for e in evidence:
        for m in e.get("marks") or []:
            if m not in out:
                out.append(m)
    return out


def ledger_texts(evidence: list[Evidence]) -> list[str]:
    """원장의 검증 허용 텍스트 전부. verify_texts 가 이걸 재료로 본다."""
    return [t for e in evidence for t in e["allow"]]


#: 출처의 역할. 답이 **그 재료에서 나온 것**인지, 표현을 **제한만** 한 것인지는 다른
#: 사건이고, 직원에게도 다르게 보여야 한다(§3 · §8). 답을 내보내는 노드가 둘(compose ·
#: clarify)이라 어휘는 한 곳에 둔다 — 갈리면 화면이 한쪽만 갈라 보여준다.
GROUND, CAUTION = "근거", "주의"


def source_lines(s: dict, *, compact: bool = False) -> list[str]:
    """출처 한 건의 터미널 표기. 운영 CLI(`consult_agent/__main__`)와 디버그 실행기
    (`tests/debug` — $CAD·$CADR)가 **같은 함수**를 쓴다. 각자 표기를 복사해 갖고 있던
    동안 출처에 URL 을 싣는 변경이 운영 CLI 에만 적용되고 디버그 화면에는 빠졌다 —
    한쪽만 고쳐지는 사고를 이 함수 하나로 막는다. (Streamlit 은 매체가 달라 별도 —
    마크다운 링크로 렌더한다. `app.py`)

    - 근거는 **원문 문서명**으로 읽어주고 카드 id 는 역추적용으로 뒤에 남긴다.
    - 관련도는 **있을 때만** 찍는다 — 검색으로 오지 않은 재료(고객 브리핑·상담 기록·
      가드)에는 관련도라는 것이 없고, 그 자리에 None 을 찍으면 "관련도를 못 잰 재료"가
      "관련도가 없는 재료"로 읽힌다.
    - 원천 문서에 게시글 URL(핫팁 등, doc.url)이 있으면 ↗ 줄로 함께 찍는다.
    - compact 는 대표 질문 묶음($CADR)의 한 줄 표기다 — 관련도 없이 문서명·제목·id 만.
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
    """원장의 근거 목록(중복 id 제거, 등장 순서 유지)."""
    out: list[dict] = []
    seen: set[str] = set()
    for e in evidence:
        for s in e["sources"]:
            if s["id"] not in seen:
                seen.add(s["id"])
                out.append(s)
    return out


def summarize(evidence: list[Evidence]) -> str:
    """계획 프롬프트에 넣는 '지금까지 모은 것' — 본문이 아니라 무엇을 이미 봤는지만."""
    if not evidence:
        return "(아직 없음)"
    return "\n".join(
        f"- {e['tool']}(\"{e['query']}\") → " +
        (", ".join(s["title"] or s["id"] for s in e["sources"][:3]) or "재료 확보")
        for e in evidence
    )
