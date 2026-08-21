"""메타 질문("뭘 도와줄 수 있어?") 응답 노드 `agent_help` — 지식베이스 메타데이터만으로 답한다(LLM 없음).

이름이 `capabilities` 가 아닌 이유는 router.INTENTS 의 주석 참고 — 06_에이전트_기능정의/01 ② "가능 여부
즉시 확인"(고객 계좌 상태 조회)과 구분하기 위해서다. 이 노드는 '에이전트 자신'이 무엇을 돕는지만 답한다.
"""

from __future__ import annotations

from typing import Any

from router import AgentState, _kb


def agent_help(state: AgentState) -> dict[str, Any]:
    """지식베이스 메타데이터만으로 생성한다. 새 장(chapter)이 추가돼도
    코드를 고치지 않아도 자동으로 최신 내용이 반영된다."""
    by_type: dict[str, list[dict]] = {}
    for p in _kb.pitches:
        by_type.setdefault(p["type"], []).append(p)

    lines = [
        "■ 제가 도와드릴 수 있는 것",
        "IRP(개인형 퇴직연금) 상담 화법을 안내해 드려요. 예를 들면:",
        "",
    ]
    if by_type.get("proposal"):
        who = ", ".join(_kb.customer_types) or "다양한"
        lines.append(f"· 신규 제안 — {who} 고객에게 IRP를 처음 권할 때")
    if by_type.get("objection") and _kb.objection_types:
        lines.append(f"· 거절 대응 — {', '.join(_kb.objection_types)} 같은 반응이 나왔을 때")
    guides_by_stage: dict[str, list[dict]] = {}
    for g in by_type.get("guide", []):
        guides_by_stage.setdefault(g["tags"].get("stage", "공통"), []).append(g)
    for stage, guides in guides_by_stage.items():
        label = "상품 비교·설명 자료" if stage == "공통" else f"{stage} 업무 가이드"
        lines.append(f"· {label} — 예: {guides[0]['title']}")

    lines += [
        "",
        "■ 활용 팁",
        '"사업자 고객인데 수수료 부담된다고 하시네요" 처럼 실제 상황을 그대로 말씀해 주시면 바로 화법을 찾아드려요.',
    ]
    return {"answer": "\n".join(lines), "sources": []}
