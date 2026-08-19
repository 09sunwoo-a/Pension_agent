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
노드 함수는 nodes.py, LLM 프롬프트는 prompts.py 에 있다.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from nodes import (
    HISTORY_LIMIT,
    AgentState,
    broaden,
    capabilities,
    fallback,
    llm_rerank,
    respond,
    retrieve_node,
    route,
    route_intent,
    route_rerank,
    route_verify,
    understand,
    verify,
)


def build_agent():
    g = StateGraph(AgentState)
    g.add_node("understand", understand)
    g.add_node("capabilities", capabilities)
    g.add_node("retrieve", retrieve_node)
    g.add_node("broaden", broaden)
    g.add_node("llm_rerank", llm_rerank)
    g.add_node("verify", verify)
    g.add_node("respond", respond)
    g.add_node("fallback", fallback)

    g.add_edge(START, "understand")
    g.add_conditional_edges("understand", route_intent, ["capabilities", "retrieve"])
    g.add_conditional_edges("retrieve", route, ["verify", "broaden", "llm_rerank"])
    g.add_conditional_edges("llm_rerank", route_rerank, ["verify", "fallback"])
    g.add_conditional_edges("verify", route_verify, ["respond", "fallback"])
    g.add_edge("broaden", "retrieve")
    g.add_edge("capabilities", END)
    g.add_edge("respond", END)
    g.add_edge("fallback", END)
    return g.compile()


_AGENT = None


def ask(question: str, history: list[dict] | None = None) -> dict[str, Any]:
    """단발 호출용 헬퍼. FastAPI 핸들러에서 이것만 부르면 된다.

    후속 질문에 맥락을 이어가려면 이번 호출이 돌려준 "history"를 다음
    호출에 그대로 넘긴다. 세션(대화 묶음) 유지는 호출자 책임 — 이 함수
    자체는 상태를 들고 있지 않는다.
    """
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()
    out = _AGENT.invoke({"question": question, "history": history or []})
    turn = {
        "question": question,
        "customer_type": out.get("customer_type"),
        "objection_type": out.get("objection_type"),
        "stage": out.get("stage"),
        "utterance": out.get("utterance"),
    }
    new_history = [*(history or []), turn][-HISTORY_LIMIT:]
    return {"answer": out["answer"], "sources": out.get("sources", []), "history": new_history}


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = " ".join(sys.argv[1:])
    if args:
        r = ask(args)
        print(r["answer"])
        print("\n─ 근거:", ", ".join(f"{s['id']}({s['score']})" for s in r["sources"]) or "없음")
    else:
        print("질문을 입력하세요 (빈 줄 입력 시 종료). 후속 질문은 이전 맥락을 이어서 물어보면 됩니다.")
        history: list[dict] = []
        while True:
            try:
                q = input("\n> ").strip()
            except EOFError:
                break
            if not q:
                break
            r = ask(q, history=history)
            history = r["history"]
            print(r["answer"])
            print("\n─ 근거:", ", ".join(f"{s['id']}({s['score']})" for s in r["sources"]) or "없음")
