"""행내 MCP 연결 진단 — `python -m pension_agent.mcp`

「설정을 넣었는데 왜 쪽지가 안 나가나」를 한 화면에서 끝내려고 둔다. 갈리는 자리가
넷이고 처방이 전부 다르기 때문이다 — 설정이 비었나 · 행내 패키지가 없나 · 게이트웨이에
못 붙나 · 붙었는데 도구가 없나.

    python -m pension_agent.mcp            # 설정·패키지까지만 본다(접속하지 않는다)
    python -m pension_agent.mcp --connect  # 실제로 붙어서 도구 목록을 읽는다
    python -m pension_agent.mcp --send     # 본인에게 시험 쪽지를 **실제로 보낸다**

`--send` 는 되돌릴 수 없다(루트 CLAUDE.md 규칙 5). 그래서 기본 동작이 아니고, 받는 사람도
`WORKB_EMP_NO`(본인)로 고정한다 — 다른 사번을 인자로 받지 않는다.

키·토큰·서명은 찍지 않는다. 설정 «여부»와 주소까지다(`/health` 와 같은 규약).
"""

from __future__ import annotations

import asyncio
import sys

from pension_agent import workb as notes
from pension_agent.mcp import client, servers

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TEST_TITLE = "MCP 연결 확인"
TEST_BODY = "퇴직연금 사후관리 에이전트에서 보낸 연결 확인 쪽지입니다."


def _packages() -> str:
    try:
        client.backend()
    except client.MCPUnavailable as exc:
        return f"없음 · {exc}"
    return "있음"


async def _connect(emp_no: str) -> None:
    conn = client.client_for(emp_no)
    await conn.connect()
    names = conn.tool_names()
    print(f"도구            {len(names)}개 · {', '.join(names) or '없음'}")


async def _send(emp_no: str) -> None:
    note = notes.Note(title=TEST_TITLE, body=TEST_BODY)
    from pension_agent.mcp import workb as adapter  # noqa: PLC0415 — 보낼 때만 필요하다

    result = await notes.send_note([emp_no], note, send=adapter.send_memo)
    print(f"발송            {result.get('status')} · {result.get('detail')}")


def main(argv: list[str]) -> int:
    cfg = client.settings()
    emp_no = notes.employee_id()
    print(f"게이트웨이      {cfg.base_url or '(비어 있음)'}")
    print(f"설정            {'갖춰짐' if cfg.configured else '모자람 — ' + ', '.join(cfg.missing())}")
    print(f"행내 패키지     {_packages()}")
    print(f"보내는 사번     {emp_no or f'(비어 있음 — {notes.EMP_NO_ENV})'}")
    for label, spec in servers.endpoints(
            cfg.servers, base_url=cfg.base_url or "(주소 없음)",
            client_id=cfg.client_id or "(id 없음)", conn_id=cfg.conn_id).items():
        print(f"서버            {label} · {spec['url']}")

    if not (cfg.configured and emp_no):
        print("\n→ 지금은 쪽지를 보내지 않고 «미연결»로 답합니다(본문은 그대로 만듭니다).")
        print("  .env 의 MCP_* 와 WORKB_EMP_NO 를 채우세요(src/.env.example).")
        return 0
    if "--connect" not in argv and "--send" not in argv:
        print("\n→ 실제로 붙어 보려면 --connect, 시험 쪽지를 보내려면 --send 를 붙이세요.")
        return 0

    try:
        asyncio.run(_connect(emp_no))
        if "--send" in argv:
            asyncio.run(_send(emp_no))
    except client.MCPError as exc:
        print(f"\n❌ {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
