"""생성물·배선 정합 — 아키텍처 다이어그램 · 노드 라벨 충돌 · 리허설 기대값.

`tests/test_consult_agent.py::main` 이 정해진 순서로 부른다 — 여기 함수를 직접 돌리지 않는다
(스텁 설치와 정리가 거기 있다).
"""

from __future__ import annotations

from pension_agent.consult_agent import graph as G

from tests.consult._common import print  # noqa: A001 — 집계용 print


def check_architecture_doc() -> int:
    """README 의 아키텍처 다이어그램은 생성물이다 — 코드(그래프 노드·도구·게이트)와
    어긋난 채 남으면 손그림 시절의 사고(lms_send 개명 뒤에도 옛 이름이 그려져 있던 것)가
    재발한다. 재생성 결과와 README 의 마커 구간이 같은지 대조한다."""
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
    """그래프 노드 라벨이 상태 키와 겹치지 않는가.

    행내 환경의 langgraph(구버전)는 add_node 에서 라벨이 상태 키와 같으면 거부한다
    ("'answer' is already being used as a state key"). 개발 환경(1.x)은 그 검사가 없어
    여기서만 통과하고 행내에서 임포트가 죽었다 — answer 노드를 compose 로 개명한
    이유다. 같은 충돌이 다시 들어오면 행내에 가서야 터지므로 여기서 잡는다."""
    import typing

    from pension_agent.consult_agent.state import AgentState
    nodes = set(G.build_agent().get_graph().nodes) - {"__start__", "__end__"}
    overlap = nodes & set(typing.get_type_hints(AgentState))
    hit = not overlap
    print(f"{'✓' if hit else '✗'} 노드 라벨이 상태 키와 겹치지 않는다 (구버전 langgraph 호환)"
          + ("" if hit else f" — {sorted(overlap)}"))
    return hit


def check_rehearsal_expectations() -> int:
    """리허설 기대값 — `sees` 중 기계가 판정할 수 있는 부분 (tests/debug/scenarios.EXPECT).

    실 LLM 없이 재는 것은 **판정 장치 자체**다: 기대가 실재하는 턴을 가리키는가 · 무엇을
    어긋남으로 보는가 · 실행 사실을 어떻게 읽는가. 실제 대본을 도는 것은 `reps` 의 일이고
    그건 LLM 이 있어야 한다.
    """
    from tests.debug import reps as REPS, scenarios as SCEN, trace as TR
    ok = 0
    print("\n[리허설 기대값 — sees 를 기계가 판정한다]")

    # 기대는 실재하는 턴을 가리켜야 한다. 없는 라벨을 적으면 그 기대는 영원히 판정되지
    # 않으면서 통과처럼 보인다 — 검사 표가 거짓말하는 가장 나쁜 형태다.
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

    # ── diff 의 규약 ──────────────────────────────────────
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

    # ── 실행 사실을 어떻게 읽나(_observed) ────────────────
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

    # LLM 이 죽은 턴은 «되묻지도 답하지도 않은» 세 번째 결말이다(§11). 답변으로 세면
    # 장애가 난 실행이 통과로 보고된다 — 실제로 리허설 11턴 중 10턴이 그랬던 날이 있다.
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

    # 기대가 없는 턴은 아무것도 찍지 않는다 — 대부분의 턴이 그렇다.
    line, misses = REPS._expect_line("cases", "9", {}, answered)
    hit = line == "" and misses == []
    print(f"{'✓' if hit else '✗'} 기대가 없는 턴은 판정하지 않는다")
    ok += hit

    return ok
