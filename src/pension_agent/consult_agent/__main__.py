"""대화형 REPL — 고객 화면이 열린 상태를 흉내 낸다.

    python -m pension_agent.consult_agent -c 198734-1205842
    python -m pension_agent.consult_agent -c 198734-1205842 "질문1" "질문2"    # 멀티턴 시나리오
    python -m pension_agent.consult_agent -c 198734-1205842 -e 3902172 "…쪽지 보내줘" "네"

-c/--customer 를 넘기지 않으면 브리핑질의·LMS발송·수정 세 의도가 "고객 화면을 먼저
열어주세요"로 답한다. 고객 id(KB-PIN)는 strategy_agent/customer.py 의 PERSONAS 참고.

-e/--employee 는 **이 상담을 하는 직원의 사번**이다 — 행내 API 가 요청의 x_client_user 에서
읽는 값의 자리다(main.py). 쪽지의 기본 받는 사람이자 **보내는 주체**(MCP 인증에 들어간다)라,
없으면 WORKB_EMP_NO 로 떨어지고 그것도 없으면 쪽지 승낙 턴이 «보낼 직원 사번이 없다»로
끝난다(2026-09-22 실측 — MCP 를 붙인 뒤에 드러났다).
"""

from __future__ import annotations

import logging
import sys

from pension_agent import mcp, note
from pension_agent.consult_agent.effects import render
from pension_agent.consult_agent.graph import ask
from pension_agent.session_store import scrub_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 단계 로그(`[agent]` — observability.step)를 여기서도 보이게 한다. 형식은 main.py 와 같고,
# 답변(stdout)과 섞이지 않게 stderr 로 보낸다. 바깥이 이미 잡아 두었으면 손대지 않는다.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s:     [%(name)s] %(message)s", stream=sys.stderr)
mcp.quiet_loggers()

# 행내 MCP(쪽지 발송)를 붙인다 — main.py·app.py 와 같은 자리. 여기서 안 붙이면 이 CLI 의
# 쪽지 승낙 턴은 .env 에 MCP_* 를 채워도 **언제나** «미연결»로 끝난다(2026-09-22 실측 —
# 「…3902172한테 쪽지 보내줘」→「네」가 그렇게 끝나 발송 경로를 이 CLI 로 확인할 수 없었다).
# 설정이 없으면 아무것도 하지 않고, 그 사실을 한 줄 알린다.
if not mcp.install():
    print("(쪽지 발송: 미연결 — .env 의 MCP_* 가 없거나 행내 패키지가 없습니다. 승낙 턴은 보내지 않고 "
          "초안만 만듭니다. 확인: python -m pension_agent.mcp)", file=sys.stderr)

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

# 쪽지를 «누구 이름으로» 보내는가 — 행내에서는 로그인 사번이고 여기서는 -e 또는 환경변수다.
# 없으면 승낙 턴이 실패하므로 시작할 때 말한다(붙었는데 못 보내는 것과 안 붙은 것은 다르다).
if note.employee_id(employee_id) is None:
    print(f"(쪽지 보내는 사번: 없음 — -e 사번 을 넘기거나 {note.EMP_NO_ENV} 를 채우세요. "
          "없으면 쪽지 승낙 턴이 실패합니다)", file=sys.stderr)

def _progress(text: str) -> None:
    # 진행 표시(graph.ask on_progress). 답변과 구분되게 들여서 찍는다.
    print(f"  ⋯ {text}")


def _print_answer(r: dict) -> None:
    # 출처 표기는 render 가 정한다 — CLI 와 행내 API 가 같은 글자를 내야 한다(render 주석).
    print(r["answer"])
    # 답변이 가리킨 단말 화면의 딥링크(§10). 화면은 본문의 번호를 이 url 로 감싸는데
    # 터미널에는 감쌀 자리가 없으므로 목록으로 세운다 — 안 찍으면 여기서는 «링크가 붙었나»를
    # 확인할 방법이 아예 없다(수동 확인 경로가 이것이다 — bin/cli.sh).
    for item in r.get("links") or []:
        print(f"  [화면] {item['screen']} {item.get('label') or ''} → {item['url']}")
    print(render.sources_block(r["sources"]))

if len(argv) > 1:
    # 인자를 여러 개 주면 순서대로 한 턴씩 실행 — 멀티턴 시나리오를 한 줄로 재현할 때 씀.
    # 예: python -m pension_agent.consult_agent -c 198734-1205842 "이 고객 투자성향 뭐야?" "그럼 최근 3개월 수익률은?"
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
            # 로케일이 UTF-8 이 아닌 터미널에서 백스페이스가 남긴 반쪽 바이트를 지운다
            # (session_store.scrub_text 주석 — 그대로 두면 상담이력 저장에서 턴이 죽는다).
            q = scrub_text(input("\n> ")).strip()
        except EOFError:
            break
        if not q:
            break
        r = ask(q, history=history, customer_id=customer_id, employee_id=employee_id,
                on_progress=_progress)
        history = r["history"]
        _print_answer(r)
