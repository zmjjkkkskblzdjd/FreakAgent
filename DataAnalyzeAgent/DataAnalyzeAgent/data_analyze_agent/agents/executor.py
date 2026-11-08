
from __future__ import annotations

from config import get_app_config
from data_analyze_agent.db import run_select
from data_analyze_agent.graph.state import DataAnalyzeState

__all__ = ["execute_sql_node", "route_after_execute"]


def execute_sql_node(state: DataAnalyzeState) -> dict:
    verdict = state.get("review_verdict")
    if verdict is None:
        raise ValueError("execute_sql_node 未取到 review_verdict，无法执行")
    if not verdict.passed:
        raise ValueError("execute_sql_node 收到未通过审查的 SQL，这是图装配错误")

    result = run_select(verdict.checked_sql)
    if result["ok"]:
        return {"query_result": result["data"], "db_error": None}
    return {"query_result": None, "db_error": result["data"]}


def route_after_execute(state: DataAnalyzeState) -> str:
    if state.get("node_error"):
        return "finalize"               
    if state.get("db_error") is None:
        return "analyze_result"
    if not get_app_config().sql_guard.use_auto_repair:
        return "finalize"
    if state.get("repair_count", 0) < get_app_config().sql_guard.max_sql_repairs:
        return "write_sql"
    return "finalize"
