"""행내 Sybase IQ 조회 — MCP `sybase` 서버의 `sybaseiq_query` 도구 어댑터.

    query(sql, emp_no)  →  list[dict]   # 검사 끝난 SELECT 만 받음(SQL 검사는 호출부 몫)

- 설정 부족 시 `MCPUnavailable`. 조회라 재시도 허용(`idempotent=True`)
- 인자 이름·응답 꼴 미실측 — 도구가 밝힌 인자 이름에 맞춤, 못 읽으면 오류(빈 결과로 접지 않음)
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
    """응답을 행 목록으로 읽지 못함."""


def _rows(raw: Any) -> list[dict]:
    """응답 → 행 목록. 문자열(JSON) · {data|rows|result: [...]} · [...] 지원."""
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
