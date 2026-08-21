"""LangGraph 기반 IRP 상담 화법 에이전트 — 그래프 조립 · 실행 진입점.

직원이 "이런 상황인데 뭐라고 답변하지?" 라고 자연어로 물으면
지식베이스에서 관련 화법을 찾아 핵심 포인트와 근거를 짚어주는 코칭 조언을 만들어 준다.
후속 질문(이전 대화 맥락 이어가기)도 지원한다.

    from graph import ask
    r = ask("사업자 고객인데 수수료 부담된다고 하시네요")
    print(r["answer"])
    r2 = ask("그럼 안 된다고 하면요?", history=r["history"])   # 후속 질문

그래프 구조(노드·분기 다이어그램)는 README.md 참고. 이 파일은 그래프를
조립하고(build_agent) 단발 호출 헬퍼(ask)와 CLI 진입점만 담당한다.
노드 함수는 기능별로 나뉘어 있다 — 의도분류·상태정의·분기는 router.py, 화법 검색은
pitch.py, 메타 질문 응답은 meta.py, 브리핑질의는 briefing_qa.py, LMS발송은 lms.py,
브리핑수정은 correction.py. LLM 프롬프트는 prompts.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.session_store import append_turn  # noqa: E402

from briefing_qa import briefing_qa
from correction import correction
from lms import lms_send
from meta import agent_help
from pitch import broaden, fallback, llm_rerank, respond, retrieve_node, situation_slots, verify
from router import (
    HISTORY_LIMIT,
    AgentState,
    route,
    route_intent,
    route_rerank,
    route_verify,
    understand,
)


def build_agent():
    g = StateGraph(AgentState)
    g.add_node("understand", understand)
    g.add_node("agent_help", agent_help)
    g.add_node("situation_slots", situation_slots)
    g.add_node("retrieve", retrieve_node)
    g.add_node("broaden", broaden)
    g.add_node("llm_rerank", llm_rerank)
    g.add_node("verify", verify)
    g.add_node("respond", respond)
    g.add_node("fallback", fallback)
    g.add_node("briefing_qa", briefing_qa)
    g.add_node("lms_send", lms_send)
    g.add_node("correction", correction)

    g.add_edge(START, "understand")
    g.add_conditional_edges(
        "understand", route_intent,
        ["agent_help", "situation_slots", "briefing_qa", "lms_send", "correction"],
    )
    g.add_edge("situation_slots", "retrieve")
    g.add_conditional_edges("retrieve", route, ["verify", "broaden", "llm_rerank"])
    g.add_conditional_edges("llm_rerank", route_rerank, ["verify", "fallback"])
    g.add_conditional_edges("verify", route_verify, ["respond", "fallback"])
    g.add_edge("broaden", "retrieve")
    g.add_edge("agent_help", END)
    g.add_edge("respond", END)
    g.add_edge("fallback", END)
    g.add_edge("briefing_qa", END)
    g.add_edge("lms_send", END)
    g.add_edge("correction", END)
    return g.compile()


_AGENT = None


def ask(
    question: str, history: list[dict] | None = None,
    *, customer_id: str | None = None, session_id: str = "default",
) -> dict[str, Any]:
    """단발 호출용 헬퍼. FastAPI 핸들러에서 이것만 부르면 된다.

    후속 질문에 맥락을 이어가려면 이번 호출이 돌려준 "history"를 다음
    호출에 그대로 넘긴다. 세션(대화 묶음) 유지는 호출자 책임 — 이 함수
    자체는 상태를 들고 있지 않는다.

    customer_id: 현재 열려 있는 브리핑 화면의 고객 id. briefing_qa/lms_send/correction
    노드가 필요로 한다 — 넘기지 않으면 그 세 의도는 "고객을 찾을 수 없다"고 답한다.
    session_id: 상담 세션 구분자(REQUIREMENTS.md §14 상담이력 단위). 넘기지 않으면 "default"
    세션으로 기록된다 — 모든 턴은 intent 와 무관하게 이 진입점 한 곳에서 기록되므로, 새
    intent 가 추가돼도 상담이력 기록을 빠뜨릴 일이 없다.
    """
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()
    out = _AGENT.invoke({"question": question, "history": history or [], "customer_id": customer_id})
    turn = {
        "question": question,
        "customer_type": out.get("customer_type"),
        "objection_type": out.get("objection_type"),
        "stage": out.get("stage"),
        "utterance": out.get("utterance"),
    }
    new_history = [*(history or []), turn][-HISTORY_LIMIT:]

    if customer_id:
        append_turn(customer_id, session_id, {
            "role": "user", "text": question, "intent": out.get("intent"),
        })
        append_turn(customer_id, session_id, {
            "role": "agent", "text": out.get("answer", ""), "intent": out.get("intent"),
        })

    return {
        "answer": out["answer"], "sources": out.get("sources", []), "history": new_history,
        "intent": out.get("intent"),  # 프론트가 lms_send/correction 처럼 미완성 기능을 표시할 때 씀
    }


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    argv = sys.argv[1:]
    customer_id = None
    for flag in ("-c", "--customer"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                print(f"{flag} 뒤에 customer_id 를 지정하세요 (예: -c C3)")
                sys.exit(1)
            customer_id = argv[i + 1]
            del argv[i:i + 2]

    def _print_answer(r: dict) -> None:
        print(r["answer"])
        print("\n─ 근거:", ", ".join(f"{s['id']}({s['score']})" for s in r["sources"]) or "없음")

    if len(argv) > 1:
        # 인자를 여러 개 주면 순서대로 한 턴씩 실행 — 멀티턴 시나리오를 한 줄로 재현할 때 씀.
        # 예: python graph.py -c C3 "이현우 고객 투자성향 뭐야?" "그럼 최근 3개월 수익률은?"
        history: list[dict] = []
        for q in argv:
            print(f"\n> {q}")
            r = ask(q, history=history, customer_id=customer_id)
            history = r["history"]
            _print_answer(r)
    elif argv:
        r = ask(argv[0], customer_id=customer_id)
        _print_answer(r)
    else:
        print("질문을 입력하세요 (빈 줄 입력 시 종료). 후속 질문은 이전 맥락을 이어서 물어보면 됩니다.")
        if customer_id:
            print(f"(고객 화면 열림: {customer_id})")
        history = []
        while True:
            try:
                q = input("\n> ").strip()
            except EOFError:
                break
            if not q:
                break
            r = ask(q, history=history, customer_id=customer_id)
            history = r["history"]
            _print_answer(r)
