"""대화형 REPL — 고객 화면이 열린 상태를 흉내 낸다.

    python -m pension_agent.consult_agent -c C3
    python -m pension_agent.consult_agent -c C3 "질문1" "질문2"    # 멀티턴 시나리오

-c/--customer 를 넘기지 않으면 브리핑질의·LMS발송·수정 세 의도가 "고객 화면을 먼저
열어주세요"로 답한다. 고객 id 는 strategy_agent/customer.py 의 PERSONAS 참고.
"""

from __future__ import annotations

import sys

from pension_agent.consult_agent.graph import ask

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

def _print_source(s: dict) -> None:
    # 근거는 **원문 문서명**으로 읽어준다. 카드 id 는 역추적용으로 뒤에 남긴다 —
    # id 만 찍으면 사내 json 안의 코드가 근거처럼 보인다.
    # 관련도는 **있을 때만** 찍는다. 검색으로 오지 않은 재료(고객 브리핑·상담 기록·
    # 고객 상태에 걸린 가드)에는 관련도라는 것이 없고, 그 자리에 None 을 찍으면
    # "관련도를 못 잰 재료"가 "관련도가 없는 재료"로 읽힌다.
    print(f"   · {s.get('doc') or '출처 미상 — 확인 필요'}")
    tail = f" · 관련도 {s['score']}" if s.get("score") is not None else ""
    print(f"     — {s.get('title') or ''} [{s['id']}{tail}]")


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
    # 예: python -m pension_agent.consult_agent -c C3 "이현우 고객 투자성향 뭐야?" "그럼 최근 3개월 수익률은?"
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
