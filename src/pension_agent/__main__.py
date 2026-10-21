"""대화형 REPL — 고객 화면이 열린 상태 재현.

    python -m pension_agent -c 198734-1205842
    python -m pension_agent -c 198734-1205842 "질문1" "질문2"    # 멀티턴 시나리오
    python -m pension_agent -c 198734-1205842 -e 3902172 "…쪽지 보내줘" "네"

-c/--customer  고객 id(KB-PIN). 없으면 고객 관련 의도는 «고객 화면을 먼저 열어주세요»
-e/--employee  상담 직원 사번 — 쪽지 수신·발송 주체. 없으면 WORKB_EMP_NO, 그것도 없으면 쪽지 승낙 턴 실패
"""

from __future__ import annotations

import logging
import sys

from pension_agent.common import mcp, note
from pension_agent.actions import render
from pension_agent.graph import ask
from pension_agent.common.session_store import scrub_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 단계 로그 → stderr(답변 stdout 과 분리). 형식은 main.py 와 동일
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s:     [%(name)s] %(message)s", stream=sys.stderr)
mcp.quiet_loggers()

# 행내 MCP 연결 — 누락 시 MCP_* 설정이 있어도 쪽지 승낙 턴이 항상 «미연결»
if not mcp.install():
    print("(쪽지 발송: 미연결 — .env 의 MCP_* 가 없거나 행내 패키지가 없습니다. 승낙 턴은 보내지 않고 "
          "초안만 만듭니다. 확인: python -m pension_agent.common.mcp)", file=sys.stderr)

argv = sys.argv[1:]
customer_id = None
employee_id = None
for flag, what in (("-c", "customer_id"), ("--customer", "customer_id"),
                   ("-e", "사번"), ("--employee", "사번")):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 >= len(argv):
            print(f"{flag} 뒤에 {what} 를 지정하세요")
            sys.exit(1)
        if what == "customer_id":
            customer_id = argv[i + 1]
        else:
            employee_id = argv[i + 1]
        del argv[i:i + 2]

# 발송 사번 없으면 승낙 턴 실패 — 시작 시 안내
if note.employee_id(employee_id) is None:
    print(f"(쪽지 보내는 사번: 없음 — -e 사번 을 넘기거나 {note.EMP_NO_ENV} 를 채우세요. "
          "없으면 쪽지 승낙 턴이 실패합니다)", file=sys.stderr)

def _progress(text: str) -> None:
    print(f"  ⋯ {text}")


def _print_answer(r: dict) -> None:
    print(r["answer"])
    # 단말 화면 딥링크 — 터미널에는 감쌀 자리가 없어 목록 출력
    for item in r.get("links") or []:
        print(f"  [화면] {item['screen']} {item.get('label') or ''} → {item['url']}")
    print(render.sources_block(r["sources"]))

if len(argv) > 1:
    # 인자 여러 개 → 순서대로 한 턴씩(멀티턴 재현)
    history: list[dict] = []
    for q in argv:
        print(f"\n> {q}")
        r = ask(q, history=history, customer_id=customer_id, employee_id=employee_id,
                on_progress=_progress)
        history = r["history"]
        _print_answer(r)
elif argv:
    r = ask(argv[0], customer_id=customer_id, employee_id=employee_id,
                on_progress=_progress)
    _print_answer(r)
else:
    print("질문을 입력하세요 (빈 줄 입력 시 종료). 후속 질문은 이전 맥락을 이어서 물어보면 됩니다.")
    if customer_id:
        print(f"(고객 화면 열림: {customer_id})")
    history = []
    while True:
        try:
            # 비UTF-8 터미널의 반쪽 바이트 제거 — 미제거 시 상담이력 저장에서 실패
            q = scrub_text(input("\n> ")).strip()
        except EOFError:
            break
        if not q:
            break
        r = ask(q, history=history, customer_id=customer_id, employee_id=employee_id,
                on_progress=_progress)
        history = r["history"]
        _print_answer(r)
