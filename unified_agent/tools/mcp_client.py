from __future__ import annotations
import asyncio, json
from dataclasses import dataclass
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

@dataclass(frozen=True)
class McpServer:
    name: str
    url: str
    token: str = ""

async def _acall(server: McpServer, tool: str, args: dict, timeout: float) -> dict:
    headers = {"Authorization": f"Bearer {server.token}"} if server.token else {}
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http:
        async with streamable_http_client(server.url, http_client=http) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool(tool, args, read_timeout_seconds=timeout)
    texts = [getattr(c, "text", "") for c in (res.content or [])]
    text = "\n".join(t for t in texts if t)
    try:
        return {"ok": not res.is_error, "data": json.loads(text)}
    except ValueError:
        return {"ok": not res.is_error, "text": text}

def call_tool(server: McpServer, tool: str, args: dict, timeout: float = 30.0) -> dict:
    try:
        return asyncio.run(_acall(server, tool, args, timeout))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}