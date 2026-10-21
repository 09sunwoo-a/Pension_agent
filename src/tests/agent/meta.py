"""생성물·배선 정합 — 아키텍처 다이어그램 · 노드 라벨 충돌 · 리허설 기대값.

`test_agent.py::main` 이 호출(스텁 설치·정리 포함). 직접 실행 금지.
"""

from __future__ import annotations

from pension_agent import graph as G

from tests.agent._common import print  # noqa: A001 — 집계용 print


def check_architecture_doc() -> int:
    """README 아키텍처 다이어그램(생성물)이 재생성 결과와 일치하는지."""
    from scripts import render_architecture as RA

    ok = 0
    text = RA.README.read_text(encoding="utf-8")
    hit = RA.MARK_START in text and RA.MARK_END in text
    print(f"{'✓' if hit else '✗'} README 에 생성 구간 마커가 있다")
    ok += hit

    block = text.partition(RA.MARK_START)[2].partition(RA.MARK_END)[0] if hit else ""
    hit = hit and (RA.MARK_START + block + RA.MARK_END) == RA.render_block()
    print(f"{'✓' if hit else '✗'} 다이어그램이 코드와 일치한다"
          + ("" if hit else " — python -m scripts.render_architecture 로 갱신"))
    ok += hit
    return ok


def check_node_label_collision() -> int:
    """노드 라벨·상태 키 충돌 없음 — 행내 구버전 langgraph 는 add_node 에서 거부(1.x 는 미검사)."""
    import typing

    from pension_agent.state import AgentState
    nodes = set(G.build_agent().get_graph().nodes) - {"__start__", "__end__"}
    overlap = nodes & set(typing.get_type_hints(AgentState))
    hit = not overlap
    print(f"{'✓' if hit else '✗'} 노드 라벨이 상태 키와 겹치지 않는다 (구버전 langgraph 호환)"
          + ("" if hit else f" — {sorted(overlap)}"))
    return hit


def check_rehearsal_expectations() -> int:
    """리허설 기대값(scenarios.EXPECT) 판정 장치 검사 — 실 LLM 없이."""
    from tests.debug import reps as REPS, scenarios as SCEN, trace as TR
    ok = 0
    print("\n[리허설 기대값 — sees 를 기계가 판정한다]")

    # 없는 턴 라벨은 영원히 미판정인데 통과처럼 보임
    hit = bool(SCEN.EXPECT) and all(
        label in SCEN._labels_of(script) for script, label in SCEN.EXPECT)
    print(f"{'✓' if hit else '✗'} 모든 기대가 실재하는 턴을 가리킨다 ({len(SCEN.EXPECT)}건)")
    ok += hit

    saved = dict(SCEN.EXPECT)
    try:
        SCEN.EXPECT[("cases", "없는턴")] = SCEN.Expect(outcome="answer")
        try:
            SCEN._validate_expectations()
            raised = False
        except ValueError:
            raised = True
    finally:
        SCEN.EXPECT.clear()
        SCEN.EXPECT.update(saved)
    print(f"{'✓' if raised else '✗'} 없는 턴을 가리키면 임포트가 실패한다(조용히 지나가지 않는다)")
    ok += raised

    # diff 규약
    want = SCEN.Expect(tools=("fact",), outcome="answer", verdict="assume", sources=True)
    base = {"tools": ["fact", "customer"], "outcome": "answer", "verdict": "assume",
            "sources": True}
    hit = want.diff(base) == []
    print(f"{'✓' if hit else '✗'} tools 는 부분집합 판정 — 여분의 도구는 어긋남이 아니다")
    ok += hit

    hit = len(SCEN.Expect(tools=("screen",)).diff(base)) == 1
    print(f"{'✓' if hit else '✗'} 기대한 도구가 안 불리면 어긋남")
    ok += hit

    hit = SCEN.Expect().diff({}) == [] and SCEN.Expect(outcome="").diff({"outcome": "clarify"}) == []
    print(f"{'✓' if hit else '✗'} 비워 둔 항목은 판정하지 않는다(확신 없는 기대를 강요하지 않는다)")
    ok += hit

    hit = SCEN.Expect(sources=False).diff({"sources": True}) != [] \
        and SCEN.Expect(sources=False).diff({}) == []
    print(f"{'✓' if hit else '✗'} 참/거짓 항목은 False 도 기대로 판정한다")
    ok += hit

    # 실행 사실 읽기(_observed)
    def turn(nodes):
        t = TR.Turn(question="q")
        t.nodes.extend(nodes)
        return t

    answered = turn([TR.Node(name="answer", delta={"judge_verdict": "assume"},
                             gates=[TR.Gate("verify_texts", True), TR.Gate("span", True)])])
    got = REPS._observed({"history": [{"tools": ["fact"]}], "sources": [{"id": "x"}],
                          "pending_action": {"label": "화면"}}, answered)
    hit = (got["outcome"] == "answer" and got["verdict"] == "assume"
           and got["tools"] == ["fact"] and got["gates_passed"] and got["offered"]
           and got["sources"])
    print(f"{'✓' if hit else '✗'} 답변 턴 — 도구·등급·게이트·출처·연계를 읽는다")
    ok += hit

    asked = turn([TR.Node(name="answer", delta={"judge_verdict": "ask", "clarify": {"question": "?"}})])
    got = REPS._observed({"history": [{"tools": ["fact"]}], "clarify": {"question": "?"},
                          "sources": [{"id": "x"}]}, asked)
    hit = got["outcome"] == "clarify" and got["verdict"] == "ask"
    print(f"{'✓' if hit else '✗'} 되묻기 턴 — 답변으로 세지 않는다")
    ok += hit

    # LLM 실패 턴은 세 번째 결말 — 답변으로 세면 장애 실행이 통과로 보고됨
    dead = turn([TR.Node(name="plan_step", delta={"llm_error": "HTTPError"}),
                 TR.Node(name="answer", delta={})])
    got = REPS._observed({"history": [{"tools": []}], "sources": []}, dead)
    hit = got["outcome"] == "llm_down" and not got["gates_passed"]
    print(f"{'✓' if hit else '✗'} LLM 이 죽은 턴 — 답변으로도 «게이트 통과»로도 세지 않는다")
    ok += hit

    stopped = turn([TR.Node(name="answer", delta={},
                            gates=[TR.Gate("verify_texts", True), TR.Gate("span", False)])])
    got = REPS._observed({"history": [{"tools": []}], "sources": []}, stopped)
    hit = not got["gates_passed"]
    print(f"{'✓' if hit else '✗'} 게이트가 생성문을 버렸으면 통과로 세지 않는다")
    ok += hit

    replanned = turn([TR.Node(name="plan_step", delta={"plan_retry": True}),
                      TR.Node(name="answer", delta={})])
    got = REPS._observed({"history": [{"tools": []}], "sources": []}, replanned)
    hit = got["replanned"]
    print(f"{'✓' if hit else '✗'} 재계획이 돌았는지 읽는다 (gap 23 이 만든 경로)")
    ok += hit

    line, misses = REPS._expect_line("cases", "9", {}, answered)
    hit = line == "" and misses == []
    print(f"{'✓' if hit else '✗'} 기대가 없는 턴은 판정하지 않는다")
    ok += hit

    return ok
