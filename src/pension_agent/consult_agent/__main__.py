"""대화형 REPL — 고객 화면이 열린 상태를 흉내 낸다.

    python -m pension_agent.consult_agent -c 198734-1205842
    python -m pension_agent.consult_agent -c 198734-1205842 "질문1" "질문2"    # 멀티턴 시나리오

-c/--customer 를 넘기지 않으면 브리핑질의·LMS발송·수정 세 의도가 "고객 화면을 먼저
열어주세요"로 답한다. 고객 id(KB-PIN)는 strategy_agent/customer.py 의 PERSONAS 참고.
"""

from __future__ import annotations

import sys

from pension_agent.consult_agent import render
from pension_agent.consult_agent.graph import ask

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

argv = sys.argv[1:]
customer_id = None
for flag in ("-c", "--customer"):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 >= len(argv):
            print(f"{flag} 뒤에 customer_id 를 지정하세요")
            sys.exit(1)
        customer_id = argv[i + 1]
        del argv[i:i + 2]

def _progress(text: str) -> None:
    # 진행 표시(graph.ask on_progress). 답변과 구분되게 들여서 찍는다.
    print(f"  ⋯ {text}")


def _print_answer(r: dict) -> None:
    # 출처 표기는 render 가 정한다 — CLI 와 행내 API 가 같은 글자를 내야 한다(render 주석).
    print(r["answer"])
    print(render.sources_block(r["sources"]))

if len(argv) > 1:
    # 인자를 여러 개 주면 순서대로 한 턴씩 실행 — 멀티턴 시나리오를 한 줄로 재현할 때 씀.
    # 예: python -m pension_agent.consult_agent -c 198734-1205842 "이 고객 투자성향 뭐야?" "그럼 최근 3개월 수익률은?"
    history: list[dict] = []
    for q in argv:
        print(f"\n> {q}")
        r = ask(q, history=history, customer_id=customer_id, on_progress=_progress)
        history = r["history"]
        _print_answer(r)
elif argv:
    r = ask(argv[0], customer_id=customer_id, on_progress=_progress)
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
        r = ask(q, history=history, customer_id=customer_id, on_progress=_progress)
        history = r["history"]
        _print_answer(r)
