"""아키텍처 다이어그램 생성기 — consult_agent README 의 생성 구간을 코드에서 다시 그린다.

`get_graph()` 는 노드·엣지만 그린다 — 도구 루프는 `plan` 노드 **안**이라(도구 목록·호출
상한은 코드 소유, 루트 CLAUDE.md 규칙 2) 그래프에 안 찍히고, 검증 게이트도 `answer` 한 칸
뒤에 숨는다. 그렇다고 손으로 그린 다이어그램은 코드와 어긋나기 시작한다(lms_send → lms_link
개명 때 실측). 그래서 이 스크립트가 세 소스를 **코드에서** 읽어 mermaid 로 그린다:

  ① 컴파일된 그래프의 노드·엣지 (`graph.build_agent().get_graph()`)
  ② 도구 레지스트리 (`tools.TOOLS` — 이름·개수를 그대로 옮긴다)
  ③ 답변 점검 게이트 순서 (`plan` 의 검사 셋 — 속성이 사라지면 크게 실패한다)

산출물은 README 의 마커 구간 **하나**다 — 아키텍처 문서를 둘로 늘리지 않는다(생성물은
생성기가 만든다는 저장소 규약 — DEMO_STATUS·kb_*.json 과 같다).

    cd src
    python -m scripts.render_architecture           # README 구간 갱신 (멱등)
    python -m scripts.render_architecture --check   # 갱신될 것이 있으면 실패 (회귀 테스트용)
"""

from __future__ import annotations

import sys

from pension_agent import config

MARK_START = "<!-- generated:architecture:start — python -m scripts.render_architecture 가 갱신한다. 손으로 고치지 않는다 -->"
MARK_END = "<!-- generated:architecture:end -->"

README = config.PACKAGE_ROOT / "consult_agent" / "README.md"

#: 그래프 노드의 화면 라벨. 노드 이름은 그래프에서 오고, 설명 한 줄만 여기서 단다 —
#: 없는 노드에 라벨을 달아 두면 생성 시점에 그대로 드러난다(라벨만 있고 노드가 없으면 무시).
_NODE_LABELS = {
    "understand": "understand<br/>질문 → intent·utterance",
    "plan": "plan<br/>계획 루프 — 도구를 골라 원장에 쌓는다",
    "answer": "answer<br/>되묻기 판정 ∥ 답변 작성 → 게이트",
    "agent_help": "agent_help<br/>에이전트 능력 안내",
    "lms_link": "lms_link<br/>LMS 발송 화면 연계 제안 (보내지 않는다)",
    "correction": "correction<br/>브리핑 산문 수정",
    "llm_down": "llm_down<br/>LLM 장애 안내 (§11)",
    "confirm_action": "confirm_action<br/>직전 제안의 네/아니오",
    "offer": "offer<br/>화면 연계·화법 제안 (규칙이 정한다)",
}


def _graph_lines() -> list[str]:
    """컴파일된 그래프의 노드·엣지 → mermaid 줄. 조건 분기는 점선이다."""
    from pension_agent.consult_agent import graph as G  # noqa: PLC0415 — langgraph 지연
    drawable = G.build_agent().get_graph()

    lines: list[str] = []
    for name in drawable.nodes:
        if name == "__start__":
            lines.append('    __start__([START])')
        elif name == "__end__":
            lines.append('    __end__([END])')
        else:
            lines.append(f'    {name}["{_NODE_LABELS.get(name, name)}"]')
    for edge in drawable.edges:
        arrow = "-.->" if edge.conditional else "-->"
        label = f'|"{edge.data}"|' if getattr(edge, "data", None) else ""
        lines.append(f"    {edge.source} {arrow}{label} {edge.target}")
    return lines


def _tool_lines() -> list[str]:
    """도구 레지스트리 → plan 옆의 주석 노드. 이름은 레지스트리에서 그대로 옮긴다."""
    from pension_agent.consult_agent import tools as T  # noqa: PLC0415
    names = list(T.TOOLS)
    rows = ["&nbsp;·&nbsp;".join(names[i:i + 4]) for i in range(0, len(names), 4)]
    return [
        f'    tools[["tools.TOOLS — 도구 {len(names)}종 (능력 표면 · 코드 소유)'
        f'<br/>{"<br/>".join(rows)}"]]',
        '    plan -. "무엇을 부를지는 LLM ·<br/>목록·상한·반복 차단은 코드" .-> tools',
    ]


def _gate_lines() -> list[str]:
    """답변 점검 게이트 → answer 옆의 주석 노드. 검사 함수가 사라지면 크게 실패한다 —
    이름만 남아 있는 다이어그램은 없는 것만 못하다(tests/debug/trace 와 같은 원칙)."""
    from pension_agent.consult_agent.nodes import plan as P  # noqa: PLC0415
    missing = [a for a in ("verify_texts", "relations", "_span_verdict") if not hasattr(P, a)]
    if missing:
        raise AttributeError(f"게이트 검사가 사라졌습니다 — 다이어그램이 거짓이 됩니다: {missing}")
    return [
        '    gates[["답변 점검 게이트 (걸리면 생성문 폐기·보완)'
        '<br/>① 원장 밖 수치·미등록 상품 (verify_texts)'
        '<br/>② 값–조건 오짝·알려진 오답 (relations)'
        '<br/>③ 원문 스팬·필수 표시 (span)"]]',
        '    answer -. "원장만 보고 대조" .-> gates',
    ]


def render_block() -> str:
    """마커 사이에 들어갈 본문 전체."""
    body = "\n".join([
        "```mermaid",
        "flowchart TD",
        *_graph_lines(),
        *_tool_lines(),
        *_gate_lines(),
        "```",
        "",
        "실선은 고정 엣지, 점선은 분기(`routing.py`)·주석이다. `tools`·`gates` 상자는",
        "LangGraph 노드가 아니라 `plan`·`answer` **안**에서 도는 것을 꺼내 보인 것이다 —",
        "`get_graph()` 출력에 도구가 안 보이는 이유가 그것이고, 설계 그대로다(도구 선택은",
        "LLM, 경계는 코드 — 루트 CLAUDE.md 규칙 2).",
    ])
    return f"{MARK_START}\n{body}\n{MARK_END}"


def main(argv: list[str]) -> int:
    text = README.read_text(encoding="utf-8")
    if MARK_START not in text or MARK_END not in text:
        print(f"README 에 마커가 없습니다: {README}")
        return 1
    head, _, rest = text.partition(MARK_START)
    _, _, tail = rest.partition(MARK_END)
    updated = head + render_block() + tail
    if "--check" in argv:
        if updated != text:
            print("아키텍처 다이어그램이 코드와 다릅니다 — "
                  "cd src && python -m scripts.render_architecture 로 갱신하세요.")
            return 1
        print("아키텍처 다이어그램 최신 상태")
        return 0
    if updated == text:
        print(f"[render_architecture] 변경 없음 — {README}")
    else:
        README.write_text(updated, encoding="utf-8")
        print(f"[render_architecture] 갱신 — {README}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
