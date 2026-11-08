from __future__ import annotations

from unified_agent.tools.mcp_web_search import mcp_web_search
from unified_agent.tools.web_search import web_search as _web_search

_DEFAULT_TOP_K = 6


def search_web(query: str, top_k: int = _DEFAULT_TOP_K) -> list[dict]:
    primary = _web_search(query, top_k=5)
    secondary = mcp_web_search(query, top_k=3)
    seen: set[str] = set()
    merged: list[dict] = []
    for e in primary + secondary:
        key = e.get("source") or (e.get("content") or "")[:80]
        if key and key not in seen:
            seen.add(key)
            merged.append(e)
    return merged[:top_k]
