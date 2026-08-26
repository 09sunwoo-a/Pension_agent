"""디버그 CLI — 운영 CLI 와 같은 출력에 트레이스를 얹는다.

    cd src
    python -m tests.debug --script tax_credit_known_wrong --debug --show-llm
    python -m tests.debug --debug "세액공제 한도가 얼마야?"          # 실제 LLM
    python -m tests.debug -c 198734-1205842 --debug "질문1" "질문2"  # 멀티턴 한 줄 재현
    python -m tests.debug -c 181245-3097614 --debug                  # REPL
    python -m tests.debug --list                                     # 시나리오 목록

**인자 규약은 운영 CLI(`pension_agent.consult_agent.__main__`)와 같다** — 그래야 평소 쓰던
줄에 `--debug` 만 붙여 쓸 수 있다. `-c/--customer` 는 앞뒤 어디에 와도 되고, 질문이 여러
개면 순서대로 한 턴씩(맥락 이어서), 질문이 없으면 REPL 이다. 여기에 셋이 더 있다:

    --debug      답변 아래에 실행 트레이스를 붙인다
    --show-llm   compose 가 LLM 에게 받은 문장(폐기됐어도)을 그대로 보여준다
    --script N   캔드 LLM 시나리오로 실행한다(키 없이 돈다)

**운영 `__main__.py` 를 import 하지 않는다.** 그 파일은 import 즉시 `sys.argv` 를 파싱하고
REPL 로 들어가는 스크립트라, 불러오는 순간 이 CLI 가 아니라 그쪽이 돈다. 그래서 답변·근거
출력은 형식을 그대로 옮겨 적었다 — 중복이고, 운영 코드를 고치지 않는다는 제약의 대가다.
두 출력이 글자까지 같은지는 이 스위트가 재지 못한다(알고 남긴다).
"""

from __future__ import annotations

import sys

from tests.debug import script, trace as TR
from tests.debug.runner import session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _print_source(s: dict) -> None:
    print(f"   · {s.get('doc') or '출처 미상 — 확인 필요'}")
    tail = f" · 관련도 {s['score']}" if s.get("score") is not None else ""
    print(f"     — {s.get('title') or ''} [{s['id']}{tail}]")


def _print_answer(r: dict) -> None:
    print(r["answer"])
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


def _usage() -> None:
    print(__doc__)
    print("시나리오:")
    for name, scn in script.SCENARIOS.items():
        print(f"  {name:<24} {scn.expect}")


def _repl(ask, tr, customer_id: str | None, debug: bool, show_llm: bool) -> int:
    """대화형. 답변 보고 다음 질문을 그때그때 입력하는 실제 사용 형태 그대로 —
    계측은 세션 내내 걸려 있고 트레이스는 **방금 턴만** 찍는다."""
    print("질문을 입력하세요 (빈 줄 입력 시 종료). 후속 질문은 이전 맥락을 이어서 물어보면 됩니다.")
    if customer_id:
        print(f"(고객 화면 열림: {customer_id})")
    if debug:
        print("(--debug: 답변 아래에 이번 턴의 트레이스를 붙입니다)")
    while True:
        try:
            question = input("\n> ").strip()
        except EOFError:
            break
        if not question:
            break
        _print_answer(ask(question))
        if debug:
            print()
            print(TR.render(tr, show_llm=show_llm, last_only=True))
    return 0


def main(argv: list[str]) -> int:
    if "--list" in argv or "-h" in argv or "--help" in argv:
        _usage()
        return 0

    debug = "--debug" in argv
    show_llm = "--show-llm" in argv
    argv = [a for a in argv if a not in ("--debug", "--show-llm")]

    scenario = None
    customer_id = None
    for flag in ("-c", "--customer", "--script"):
        if flag not in argv:
            continue
        i = argv.index(flag)
        if i + 1 >= len(argv):
            print(f"{flag} 뒤에 값을 지정하세요")
            return 1
        value = argv[i + 1]
        del argv[i:i + 2]
        if flag == "--script":
            scenario = script.SCENARIOS.get(value)
            if scenario is None:
                print(f"그런 시나리오가 없습니다: {value}")
                _usage()
                return 1
        else:
            customer_id = value

    questions = argv or ([scenario.question] if scenario else [])

    with session(customer_id=customer_id, scenario=scenario) as (ask, tr):
        if questions:
            for question in questions:
                if len(questions) > 1:
                    print(f"\n> {question}")
                _print_answer(ask(question))
            if debug:
                print()
                print(TR.render(tr, show_llm=show_llm))
            return 0
        return _repl(ask, tr, customer_id, debug, show_llm)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
