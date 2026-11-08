
from __future__ import annotations

from langchain_core.messages import AIMessage

from data_analyze_agent.db import execute_write
from data_analyze_agent.graph.state import DataAnalyzeState

__all__ = ["execute_write_node"]


def execute_write_node(state: DataAnalyzeState) -> dict:
    if not state.get("approval_granted"):
        raise ValueError("execute_write_node 未获审批（approval_granted 非 True），图装配错误")
    sql = state.get("write_sql")
    if not sql:
        raise ValueError("execute_write_node 未取到 write_sql，图装配错误")

    result = execute_write(sql, target=state.get("write_target"),
                           estimated_rows=state.get("estimated_rows"))
    if not result["ok"]:
        err = result["data"]
        return {"write_result": {"ok": False, "error_code": "execute_failed",
                                 "error_message": err.message}}

    d = result["data"]
    audit_id = d.get("audit_id", "")
    actual = d.get("actual_rows", 0)
    mutation_status = d.get("mutation_status", "")
    if mutation_status and mutation_status != "done":
        text = (f"写操作已提交（审计编号 {audit_id}）。ClickHouse 异步变更状态："
                f"{mutation_status}——数据会在后台生效，可稍后查询确认。"
                f"预估影响 {d.get('estimated_rows', 0)} 行。")
    else:
        text = (f"写操作已执行完成（审计编号 {audit_id}），"
                f"影响 {actual} 行，耗时 {d.get('elapsed_ms', 0)}ms。")
    return {"write_result": {"ok": True, **d},
            "messages": [AIMessage(content=text)]}
