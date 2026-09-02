"""대화형 REPL — 고객 화면이 열린 상태를 흉내 낸다.

    python -m pension_agent.consult_agent -c 198734-1205842
    python -m pension_agent.consult_agent -c 198734-1205842 "질문1" "질문2"    # 멀티턴 시나리오

-c/--customer 를 넘기지 않으면 브리핑질의·LMS발송·수정 세 의도가 "고객 화면을 먼저
열어주세요"로 답한다. 고객 id(KB-PIN)는 strategy_agent/customer.py 의 PERSONAS 참고.
"""

from __future__ import annotations

import sys

from pension_agent import clock
from pension_agent.consult_agent.graph import ask
from pension_agent.consult_agent.tools import source_lines

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# «오늘»이 실제 날짜가 아니면 시작할 때 말한다.
#
# 미리 만들어 둔 브리핑이 있으면 오늘이 그 기준일로 맞춰진다(clock._prebuilt_today) —
# 화면과 CLI 가 같은 저장본을 읽게 하려는 것이다. 그런데 그 파일이 묵으면 프로세스 전체가
# 과거를 오늘로 믿게 되고, **아무 말도 안 하면 만기 D-day 가 이상한 것을 에이전트 탓으로
# 읽는다.** 날짜와 그 출처를 한 줄로 먼저 밝혀 둔다.
_today, _source = clock.resolve()
if _source != "앱을 켠 날":
    print(f"(오늘 {_today:%Y-%m-%d} — {_source})")

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

def _print_source(s: dict) -> None:
    # 표기는 공용 함수 하나가 정한다(tools.source_lines — 문서명·id·관련도·↗URL 규약이
    # 전부 거기 있다). 디버그 실행기(tests/debug)와 각자 복사해 갖고 있던 동안 출처에
    # URL 을 싣는 변경이 이쪽에만 적용되고 디버그 화면에는 빠졌다.
    for line in source_lines(s):
        print(line)


def _progress(text: str) -> None:
    # 진행 표시(graph.ask on_progress). 답변과 구분되게 들여서 찍는다.
    print(f"  ⋯ {text}")


def _print_answer(r: dict) -> None:
    print(r["answer"])
    # 답이 나온 재료(근거)와 표현을 제한한 재료(주의)를 갈라 보여준다 — 한 목록에 섞으면
    # 질문과 무관한 고객 상태 가드가 답의 근거처럼 보인다(plan._sources).
    sources = r["sources"]
    ground = [s for s in sources if s.get("role", "근거") == "근거"]
    caution = [s for s in sources if s.get("role") == "주의"]
    print("\n─ 근거" + ("" if ground else ": 없음"))
    for s in ground:
        _print_source(s)
    if caution:
        print("\n─ 이 고객 상담에서 지켜야 할 것 (근거 카드)")
        for s in caution:
            _print_source(s)

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
