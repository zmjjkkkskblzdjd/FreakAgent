import hashlib, os
from unified_agent.tools.mcp_client import McpServer, call_tool

def _server() -> McpServer | None:
    url = os.getenv("MCP_WEB_SEARCH_URL", "").strip()
    return McpServer("web_search", url, os.getenv("MCP_WEB_SEARCH_TOKEN", "")) if url else None

def mcp_web_search(query: str, top_k: int = 5) -> list[dict]:
    server = _server()
    if server is None:
        return []
    res = call_tool(server, "search", {"query": query, "top_k": top_k})
    if not res.get("ok"):
        return []
    rows = (res.get("data") or {}).get("results") or []
    out = []
    for row in rows:
        url, content = row.get("url") or "", row.get("content") or ""
        if not url and not content:
            continue
        cid = hashlib.md5((url or content).encode()).hexdigest()[:8]
        out.append({"chunk_id": cid, "content": content, "source": url,
                    "doc_type": "web", "chunk_index": 0, "score": 1.0, "match_type": "web"})
    return out