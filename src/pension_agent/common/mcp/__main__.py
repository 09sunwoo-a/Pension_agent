"""행내 MCP 연결 진단 — 설정·패키지·접속·도구 목록 확인.

    python -m pension_agent.common.mcp            # 설정·패키지만(접속 안 함)
    python -m pension_agent.common.mcp --connect  # 접속 후 도구 목록
    python -m pension_agent.common.mcp --send     # 본인(WORKB_EMP_NO)에게 시험 쪽지 실제 발송

키·토큰·서명 출력 금지.
"""

from __future__ import annotations

import asyncio
import sys

from pension_agent.common import note
from pension_agent.common.mcp import client, servers

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TEST_TITLE = "MCP 연결 확인"
TEST_BODY = "퇴직연금 사후관리 에이전트에서 보낸 연결 확인 쪽지입니다."


def _packages() -> str:
    reason = client.unavailable()
    return f"없음 · {reason}" if reason else "있음"


async def _connect(emp_no: str) -> None:
    conn = client.client_for(emp_no)
    await conn.connect()
    names = conn.tool_names()
    print(f"도구            {len(names)}개 · {', '.join(names) or '없음'}")


async def _send(emp_no: str) -> None:
    msg = note.Note(title=TEST_TITLE, body=TEST_BODY)
    from pension_agent.common.mcp import workb as adapter  # noqa: PLC0415 — 보낼 때만 필요

    result = await note.send_note([emp_no], msg, send=adapter.send_memo)
    print(f"발송            {result.get('status')} · {result.get('detail')}")


def main(argv: list[str]) -> int:
    cfg = client.settings()
    emp_no = note.employee_id()
    print(f"게이트웨이      {cfg.base_url or '(비어 있음)'}")
    print(f"설정            {'갖춰짐' if cfg.configured else '모자람 — ' + ', '.join(cfg.missing())}")
    print(f"행내 패키지     {_packages()}")
    print(f"보내는 사번     {emp_no or f'(비어 있음 — {note.EMP_NO_ENV})'}")
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
