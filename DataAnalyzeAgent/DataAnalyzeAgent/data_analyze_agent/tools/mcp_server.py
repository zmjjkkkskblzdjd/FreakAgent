
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys

from mcp.server import MCPServer

from config import get_app_config
from data_analyze_agent import ask
from data_analyze_agent.db import introspect

mcp = MCPServer("data-analyze-agent")

logger = logging.getLogger("data-analyze-agent-mcp")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    )
    logger.addHandler(_handler)
    logger.propagate = False


@contextlib.contextmanager
def _quiet_stdout():
    if sys.stdout is sys.stderr:  
        yield
        return
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)




@mcp.tool()
def ask_data(question: str = "", thread_id: str = "", reply: str = "") -> str:
    tid = (thread_id or "").strip()
    answer_text = (question or "").strip()
    resume_text = (reply or "").strip()

    logger.info(
        "ask_data 调用：mode=%s thread_id=%s question=%.40s",
        "resume" if resume_text else "new", tid or "(自动)", answer_text,
    )

    if not resume_text and not answer_text:
        return _dump({
            "status": "failed",
            "error": "question 与 reply 不能同时为空：新问题传 question，恢复挂起传 reply",
            "thread_id": tid,
        })
    if resume_text and not tid:
        return _dump({
            "status": "failed",
            "error": "恢复挂起必须带上上次返回的 thread_id",
        })

    with _quiet_stdout():
        result = ask(
            question=answer_text or None,
            thread_id=tid or None,
            reply=resume_text or None,
        )
    logger.info(
        "ask_data 收口：status=%s kind=%s thread_id=%s",
        result.get("status"), result.get("interrupt_kind", "-"), result.get("thread_id"),
    )
    return _dump(result)




@mcp.tool()
def describe_database() -> str:
    logger.info("describe_database 调用")
    try:
        with _quiet_stdout():
            tables = introspect()
        payload = {
            "database": get_app_config().mysql.database,
            "table_count": len(tables),
            "tables": [
                {
                    "table": t.table_name,
                    "comment": t.table_comment,
                    "columns": [
                        {
                            "name": c.name,
                            "type": c.data_type,
                            "key": c.column_key or "",
                            "nullable": bool(c.is_nullable),
                            "comment": c.column_comment,
                        }
                        for c in t.columns
                    ],
                }
                for t in tables
            ],
        }
    except Exception as exc:  # noqa: BLE001 —— 协议层不抛裸异常，统一结构化
        logger.exception("describe_database 失败")
        return _dump({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return _dump(payload)




def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="data_analyze_agent.tools.mcp_server",
        description="DataAnalyzeAgent 的 MCP server（stdio / Streamable HTTP 双模）",
    )
    parser.add_argument(
        "--http", action="store_true",
        help="使用 Streamable HTTP 传输（默认 stdio）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址（默认回环，勿改 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 监听端口（默认 8765）")
    parser.add_argument("--path", default="/mcp", help="MCP 端点路径（默认 /mcp）")
    parser.add_argument(
        "--stateful", action="store_true",
        help="启用有状态会话（默认无状态：会话状态只认我们自己的 thread_id，最简最稳）",
    )
    parser.add_argument(
        "--json-response", action="store_true",
        help="POST 响应直接用 JSON（默认 SSE 流式，兼容面更广）",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.http:
        logger.info("以 stdio 模式启动（客户端经标准输入输出通信）")
        mcp.run(transport="stdio")
        return

    logger.info(
        "以 Streamable HTTP 模式启动：http://%s:%s%s （stateless=%s）",
        args.host, args.port, args.path, not args.stateful,
    )
    mcp.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path=args.path,
        stateless_http=not args.stateful,
        json_response=args.json_response,
    )


if __name__ == "__main__":
    main()
