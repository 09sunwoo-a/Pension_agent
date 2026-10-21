"""행내 Sybase IQ 조회 — MCP `sybase` 서버의 `sybaseiq_query` 도구 어댑터.

메인 브리핑(tools/branch_briefing)이 검사를 마친 SELECT 문 하나를 넘기면 행 목록을 돌려준다.
SQL 을 고치거나 검사하지 않는다 — 그것은 부르는 쪽의 일이다.

    query(sql, emp_no)  →  list[dict]

설정이 모자라면(`MCP_*` · `MCP_CONN_ID` · `MCP_SERVERS` 에 sybase) `MCPUnavailable` 을 낸다 —
부르는 쪽은 «DB 미연결»로 답한다. 조회는 부수효과가 없으므로 붙는 단계의 실패는 다시 시도한다
(`idempotent=True`).

━━ 확인되지 않은 것 ━━
도구의 인자 이름과 응답 꼴은 행내에서 아직 실측하지 않았다. 인자 이름은 도구가 스스로 밝힌
이름에 맞추고(workb 와 같은 방식), 응답은 흔한 꼴 몇 가지를 읽는다 — 못 읽으면 그 사실을
오류로 낸다(빈 결과로 접지 않는다: «0건»과 «못 읽음»은 다르다).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from pension_agent.common.mcp import client as mcp_client

TOOL = os.getenv("MCP_SYBASE_TOOL", "sybaseiq_query")
ARG_QUERY = os.getenv("MCP_SYBASE_QUERY_ARG", "query")


class UnreadableResult(RuntimeError):
    """응답을 행 목록으로 읽지 못했다."""


def _rows(raw: Any) -> list[dict]:
    """응답 → 행 목록. 문자열(JSON) · {data|rows|result: [...]} · [...] 를 읽는다."""
    for _ in range(3):
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise UnreadableResult(f"응답이 JSON 이 아닙니다: {raw[:120]}") from exc
            continue
        if isinstance(raw, dict):
            if raw.get("success") is False or raw.get("error"):
                raise UnreadableResult(f"조회가 거부됐습니다: {raw.get('error') or raw}")
            for key in ("data", "rows", "result", "results"):
                if key in raw:
                    raw = raw[key]
                    break
            else:
                raise UnreadableResult(f"행 목록이 없는 응답입니다: {list(raw)}")
            continue
        break
    if isinstance(raw, list) and all(isinstance(r, dict) for r in raw):
        return raw
    raise UnreadableResult(f"행 목록이 아닌 응답입니다: {type(raw).__name__}")


async def _query(sql: str, emp_no: str) -> list[dict]:
    client = mcp_client.client_for(emp_no)
    try:
        found = await client.tool(TOOL)
        declared = {k.lower(): k for k in (getattr(found, "args", None) or {})}
    except Exception:                       # noqa: BLE001 — 이름 맞추기가 조회를 막지 않는다
        declared = {}
    arg = declared.get(ARG_QUERY.lower(), ARG_QUERY)
    return _rows(await client.call(TOOL, {arg: sql}, idempotent=True))


def query(sql: str, emp_no: str) -> list[dict]:
    """동기 진입점 — 호출마다 새 이벤트 루프(쪽지 발송과 같은 방식)."""
    if not mcp_client.configured():
        raise mcp_client.MCPUnavailable(mcp_client.unavailable())
    return asyncio.run(_query(sql, emp_no))
