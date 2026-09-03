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

#: 그래프 노드의 화면 라벨 — «영문 노드명 — 기능명» 한 줄 + 하는 일 한 줄. 기능은 점검
#: 자리에서 읽는 사람 기준으로 쓰되, 코드로 찾아갈 수 있게 노드명은 남긴다. 그 밖의 개발
#: 용어는 다이어그램 아래 산문으로 내린다. 노드 이름 자체는 그래프에서 오고 여기서는
#: 설명만 단다(라벨만 있고 노드가 없으면 무시된다) — 첫 줄의 영문명은 dict 키에서 뽑으므로
#: 라벨과 실제 노드명이 어긋날 수 없다.
_NODE_DESCS = {
    "understand": ("질문 이해", "무엇을 원하는 질문인지 가려 보낸다"),
    "plan": ("근거 수집 루프", "질문에 필요한 자료를 도구로 찾아 모은다"),
    "compose": ("답변 작성", "모은 근거 안에서만 답을 쓰고,<br/>질문이 모호하면 선택지를 되묻는다"),
    "agent_help": ("기능 안내", "무엇을 도와줄 수 있는지 답한다"),
    "lms_link": ("LMS 발송 화면 연계", "요청받은 문구로 발송 화면 열기를 제안한다"),
    "correction": ("브리핑 수정", "화면의 AI 작성 문구를 고친다"),
    "llm_down": ("장애 안내", "LLM 연결이 안 되면 답 대신 상태를 알린다"),
    "confirm_action": ("제안 실행", "직전 턴에 제안한 화면 연계를 승낙받아 실행한다"),
    "offer": ("화면 연계 제안", "답변과 이어지는 업무 화면을 열지 묻는다"),
}

_NODE_LABELS = {name: f"{name} — {title}<br/>{desc}"
                for name, (title, desc) in _NODE_DESCS.items()}

#: 도구 상자의 갈래. 이름은 도구 선언의 진행 표시 라벨(`Tool.progress` — 코드 소유)에서
#: 오고, 여기는 묶음만 정한다. 레지스트리와 어긋나면(새 도구가 갈래에 없거나, 갈래에 적힌
#: 도구가 사라지면) 생성 시점에 크게 실패한다 — 낡은 다이어그램은 없는 것만 못하다.
_TOOL_GROUPS = (
    ("지식베이스", ("pitch", "fact", "procedure", "screen", "channel",
                    "segment", "method", "fieldtip", "market", "lineup")),
    ("현재 고객", ("customer", "suitable", "history", "transcript", "playbook", "outreach")),
    # `targets` 는 «고객 화면을 열기 전»의 재료라 현재 고객 갈래가 아니다 — 고객이 안
    # 열려 있을 때가 이 도구의 자리다(오늘의 타겟 목록이 서비스의 첫 화면이다).
    ("오늘의 목록", ("targets",)),
    ("계산", ("tax_credit", "date")),
)

#: 한 줄에 올리는 도구 수 상한. 지식베이스 10종을 한 줄에 다 쓰면 상자가 옆으로 늘어져
#: 다른 상자들이 그 폭에 끌려간다 — 넘치면 다음 줄로 내리고 들여쓴다.
_TOOLS_PER_LINE = 4


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
    """도구 레지스트리 → plan 옆의 주석 노드. 도구 이름은 선언의 진행 라벨에서 옮긴다."""
    from pension_agent.consult_agent import tools as T  # noqa: PLC0415
    grouped = {name for _label, names in _TOOL_GROUPS for name in names}
    if grouped != set(T.TOOLS):
        raise AssertionError(
            "도구 갈래가 레지스트리와 다릅니다 — _TOOL_GROUPS 를 고치세요: "
            f"갈래에만 {sorted(grouped - set(T.TOOLS))} · 레지스트리에만 {sorted(set(T.TOOLS) - grouped)}")
    rows = []
    for label, names in _TOOL_GROUPS:
        shown = [T.TOOLS[n].progress or n for n in names]
        chunks = [shown[i:i + _TOOLS_PER_LINE] for i in range(0, len(shown), _TOOLS_PER_LINE)]
        rows.append(f"{label}: {' · '.join(chunks[0])}")
        rows += ["&nbsp;&nbsp;&nbsp;&nbsp;" + " · ".join(c) for c in chunks[1:]]
    return [
        f'    tools[["자료 도구 {len(T.TOOLS)}종 — 답변의 근거는 모두 이 도구로 조회한다'
        f'<br/>{"<br/>".join(rows)}"]]',
        '    plan -. "필요한 자료를 골라 조회" .-> tools',
    ]


def _gate_lines() -> list[str]:
    """답변 점검 → compose(답변 작성) 옆의 주석 노드. 검사 함수가 사라지면 크게 실패한다 —
    이름만 남아 있는 다이어그램은 없는 것만 못하다(tests/debug/trace 와 같은 원칙)."""
    from pension_agent.consult_agent.nodes import plan as P  # noqa: PLC0415
    missing = [a for a in ("verify_texts", "relations", "_span_verdict") if not hasattr(P, a)]
    if missing:
        raise AttributeError(f"게이트 검사가 사라졌습니다 — 다이어그램이 거짓이 됩니다: {missing}")
    return [
        '    gates[["답변 점검 — 근거를 벗어난 답변은 화면에 내보내지 않는다'
        '<br/>① 근거에 없는 숫자·상품명 → 내보내지 않음'
        '<br/>② 값과 조건을 잘못 짝지은 문장 → 내보내지 않음'
        '<br/>③ 빠진 필수 안내 문구·원문 인용 → 보완해서 내보냄"]]',
        '    compose -. "내보내기 전 검사" .-> gates',
    ]


def render_block() -> str:
    """마커 사이에 들어갈 본문 전체."""
    body = "\n".join([
        "```mermaid",
        # 줄바꿈은 라벨의 <br/> 가 정한다 — 기본 wrapping(200px)이 도구 이름·설명을
        # 낱말 중간에서 다시 접어 「가/려 보낸다」 꼴이 되는 것을 막는다. 노드 6개가
        # 한 단에 늘어서는 그래프라 가로 간격은 조이고 세로 간격을 벌려 비율을 잡는다.
        '%%{init: {"flowchart": {"wrappingWidth": 800, "nodeSpacing": 35, "rankSpacing": 80}}}%%',
        "flowchart TD",
        *_graph_lines(),
        *_tool_lines(),
        *_gate_lines(),
        "```",
        "",
        "실선은 고정된 흐름, 점선은 질문에 따라 갈리는 분기다. 자료 도구와 답변 점검 상자는",
        "LangGraph 노드가 아니라 근거 수집·답변 작성 **안**에서 도는 것을 꺼내 그린 것이다 —",
        "`get_graph()` 출력에 도구가 보이지 않는 이유가 그것이다. 어떤 도구가 있는지와 답변을",
        "내보낼지는 코드가 정하고, 이번 질문에 무엇을 쓸지는 LLM 이 정한다(루트 CLAUDE.md 규칙 2).",
        "코드 대응: 도구 레지스트리 `tools.TOOLS` · 점검 `verify_texts`/`relations`/`span` ·",
        "분기 `routing.py`.",
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
