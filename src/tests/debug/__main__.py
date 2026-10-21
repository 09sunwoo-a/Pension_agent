"""디버그 CLI — 운영 CLI 출력 + 실행 트레이스. 인자 규약은 운영 CLI(`pension_agent.__main__`)와 동일.

    cd src
    python -m tests.debug --script tax_credit_asserts_wrong --debug --show-llm
    python -m tests.debug --debug "세액공제 한도가 얼마야?"          # 실제 LLM
    python -m tests.debug -c 198734-1205842 --debug "질문1" "질문2"  # 멀티턴 한 줄 재현
    python -m tests.debug -c 198734-1205842 --debug                  # REPL
    python -m tests.debug --list                                     # 시나리오 목록

`-c/--customer` 위치 무관 · 질문 여러 개면 한 턴씩 이어서 · 질문 없으면 REPL. 추가 옵션:

    --debug          답변 아래 실행 트레이스
    --show-llm       compose 가 받은 LLM 문장(폐기 포함) 표시
    --script N       캔드 LLM 시나리오로 실행(키 불필요)
    --any-customer   이 체크아웃에 없는 고객 id 도 진행(경고만)
    -e/--employee    상담 직원 사번 — 쪽지 받는 사람 기본값이자 보내는 주체. 없으면 WORKB_EMP_NO
"""

from __future__ import annotations

import sys

from tests.debug import script, trace as TR
from tests.debug.runner import session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _print_source(s: dict) -> None:
    # 운영 CLI 와 같은 공용 함수 사용 — 복사본은 출처 표기 변경을 놓침
    from pension_agent.tools import source_lines  # noqa: PLC0415 — graph 적재 뒤

    for line in source_lines(s):
        print(line)


def _print_answer(r: dict) -> None:
    print(r["answer"])
    # 단말 화면 딥링크 — 터미널은 목록으로 표시
    for item in r.get("links") or []:
        print(f"  [화면] {item['screen']} {item.get('label') or ''} → {item['url']}")
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


def _check_customer(customer_id: str | None, allow_any: bool) -> tuple[bool, str]:
    """`-c` id 실재 확인 — 오타와 재료 0건이 화면에서 구분 안 됨. 반환: (진행 여부, 경고)."""
    if not customer_id:
        return True, ""
    from pension_agent.tools.customer_solution import customer as SC  # noqa: PLC0415
    if SC.get_profile(customer_id):
        return True, ""

    known = " · ".join(f"{p.id} {p.nm}" for p in SC.PERSONAS)
    if allow_any:
        return True, (f"{customer_id} 는 이 체크아웃에 없는 고객입니다(--any-customer) — "
                      "고객 재료는 전부 0건이 됩니다.")
    print(f"-c {customer_id} : 그런 고객이 이 체크아웃에 없습니다.")
    print(f"   있는 고객: {known}")
    print("   (id 체계가 다르면 페르소나 정의가 이 브랜치에 아직 안 들어온 것입니다)")
    print("   그대로 넘기려면 --any-customer 를 붙이세요.")
    return False, ""


def _usage() -> None:
    print(__doc__)
    print("시나리오:")
    for name, scn in script.SCENARIOS.items():
        print(f"  {name:<24} {scn.expect}")


def _repl(ask, tr, customer_id: str | None, debug: bool, show_llm: bool) -> int:
    """대화형 REPL. 계측은 세션 내내, 트레이스는 방금 턴만 출력."""
    print("질문을 입력하세요 (빈 줄 입력 시 종료). 후속 질문은 이전 맥락을 이어서 물어보면 됩니다.")
    if customer_id:
        print(f"(고객 화면 열림: {customer_id})")
    if debug:
        print("(--debug: 답변 아래에 이번 턴의 트레이스를 붙입니다)")
    from pension_agent.common.session_store import scrub_text  # noqa: PLC0415 — graph 적재 뒤

    while True:
        try:
            # 비 UTF-8 터미널의 반쪽 바이트 제거 — 남으면 상담이력 저장에서 턴 실패
            question = scrub_text(input("\n> ")).strip()
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
    allow_any = "--any-customer" in argv
    argv = [a for a in argv if a not in ("--debug", "--show-llm", "--any-customer")]

    scenario = None
    customer_id = None
    employee_id = None
    for flag in ("-c", "--customer", "-e", "--employee", "--script"):
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
        elif flag in ("-e", "--employee"):
            employee_id = value      # 상담 직원 사번
        else:
            customer_id = value

    ok, warning = _check_customer(customer_id, allow_any)
    if not ok:
        return 2

    # 남은 인자 = 질문 — 오타 플래그가 조용히 질문이 되지 않도록 모르는 `--` 인자는 실패
    unknown = [a for a in argv if a.startswith("--")]
    if unknown:
        print(f"모르는 옵션입니다: {' '.join(unknown)}")
        print("  이 CLI 의 옵션: --debug · --show-llm · --script N · --any-customer · -c/--customer · -e/--employee")
        print("  시연 대본을 돌리려면 다른 모듈입니다: python -m tests.debug.reps demo --why")
        return 1

    questions = argv or ([scenario.question] if scenario else [])

    # 행내 MCP(쪽지 발송) 연결 — 캔드 시나리오는 스텁이라 제외. 미연결 시 승낙 턴이 «미연결»
    if scenario is None:
        from pension_agent.common import mcp, note  # noqa: PLC0415 — graph 적재 뒤
        if not mcp.install():
            print("(쪽지 발송: 미연결 — .env 의 MCP_* 가 없거나 행내 패키지가 없습니다. "
                  "확인: python -m pension_agent.common.mcp)", file=sys.stderr)
        elif note.employee_id(employee_id) is None:
            # 보내는 사번 없으면 승낙 턴 실패
            print(f"(쪽지 보내는 사번: 없음 — -e 사번 을 넘기거나 {note.EMP_NO_ENV} 를 채우세요)",
                  file=sys.stderr)

    with session(customer_id=customer_id, scenario=scenario,
                 employee_id=employee_id) as (ask, tr):
        tr.note(warning)
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
