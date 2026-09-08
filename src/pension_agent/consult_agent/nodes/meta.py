"""메타 질문("뭘 도와줄 수 있어?") 응답 노드 `agent_help` — 지식베이스 메타데이터만으로 답한다(LLM 없음).

이름이 `capabilities` 가 아닌 이유는 routing.INTENTS 의 주석 참고 — 07_에이전트_기능정의/01 ② "가능 여부
즉시 확인"(고객 계좌 상태 조회)과 구분하기 위해서다. 이 노드는 '에이전트 자신'이 무엇을 돕는지만 답한다.
"""

from __future__ import annotations

from typing import Any

from pension_agent.consult_agent.state import KB, AgentState


def agent_help(state: AgentState) -> dict[str, Any]:
    """지식베이스 메타데이터만으로 생성한다. 새 장(chapter)이 추가돼도
    코드를 고치지 않아도 자동으로 최신 내용이 반영된다."""
    by_type: dict[str, list[dict]] = {}
    for p in KB.pitches:
        by_type.setdefault(p["type"], []).append(p)

    lines = [
        "■ 제가 도와드릴 수 있는 것",
        "IRP(개인형 퇴직연금) 상담 화법을 안내해 드려요. 예를 들면:",
        "",
    ]
    if by_type.get("proposal"):
        who = ", ".join(KB.customer_types) or "다양한"
        lines.append(f"· 신규 제안 — {who} 고객에게 IRP를 처음 권할 때")
    if by_type.get("objection") and KB.objection_types:
        lines.append(f"· 거절 대응 — {', '.join(KB.objection_types)} 같은 반응이 나왔을 때")
    guides_by_stage: dict[str, list[dict]] = {}
    for g in by_type.get("guide", []):
        guides_by_stage.setdefault(g["tags"].get("stage", "공통"), []).append(g)
    for stage, guides in guides_by_stage.items():
        label = "상품 비교·설명 자료" if stage == "공통" else f"{stage} 업무 가이드"
        lines.append(f"· {label} — 예: {guides[0]['title']}")

    # 화법 밖의 지식 종류 — 적재된 것만 안내한다. 새 종류가 붙어도 라벨 한 줄만 추가하면 된다.
    counts = {kind: sum(1 for c in KB.cards if c["_kind"] == kind)
              for kind in {c["_kind"] for c in KB.cards}}
    for kind, label in (("segment", "관리 대상 정의 — 어떤 고객군을 왜 관리하는지"),
                        ("method", "관리 방법론 — 이런 상황이면 무엇을 제안하는지"),
                        ("procedure", "업무 처리 절차 — 처리 순서·주의"),
                        ("screen", "단말 화면번호 — 무슨 업무가 몇 번 화면인지"),
                        ("channel", "비대면 채널 경로 — 고객이 앱·웹에서 직접 하는 메뉴"),
                        ("fieldtip", "현장의 목소리 — 영업점이 실제로 하는 방식")):
        if counts.get(kind):
            lines.append(f"· {label} ({counts[kind]}건)")
    if KB.facts:
        lines.append(f"· 제도·상품 팩트 — 한도·요건·수수료 같은 확정 수치 ({len(KB.facts)}건)")
    # 지식 카드가 아니라 운영 기록이라 위 집계에 안 잡힌다. 고객 화면이 열려 있을 때만
    # 성립하는 재료이므로 그때만 안내한다 — 못 쓰는 능력을 목록에 세우지 않는다(§3).
    if state.get("customer_id"):
        lines.append("· 지난 상담 기록 — 이 고객과 언제 무슨 얘기를 했는지")
        lines.append("· 이번 상담 요약 — 지금까지 나눈 대화를 정리하고, 원하시면 쪽지로 보내기")

    lines += [
        "",
        "■ 활용 팁",
        '"사업자 고객인데 수수료 부담된다고 하시네요" 처럼 실제 상황을 그대로 말씀해 주시면 바로 화법을 찾아드려요.',
        '고객이 한 말을 그대로 옮겨 주셔도 됩니다("증권사가 수수료 무료라던데요") — 그 말에 어떻게 답하면 되는지 짚어드려요.',
        '"세액공제 한도 얼마야?"·"디폴트옵션 변경 화면번호"처럼 값이나 절차만 물어보셔도 됩니다.',
    ]
    return {"answer": "\n".join(lines), "sources": []}
