
from __future__ import annotations

import re

from langgraph.types import interrupt

from config import get_app_config
from data_analyze_agent.db import execute_write
from data_analyze_agent.graph.state import DataAnalyzeState

__all__ = ["approve_write_node", "route_after_approval"]


def approve_write_node(state: DataAnalyzeState) -> dict:
    verdict = state.get("review_verdict")
    if verdict is None or not verdict.passed:
        raise ValueError("approve_write_node 收到未通过审查的 SQL，这是图装配错误")
    sql = verdict.checked_sql
    cfg = get_app_config()

    asks = state.get("approval_asks", 0)
    if asks >= cfg.graph.max_approval_asks:
        return {"approval_granted": False, "write_result": {
            "ok": False, "error_code": "approval_exhausted",
            "error_message": f"本问题已提请审批 {asks} 次（上限 {cfg.graph.max_approval_asks}），"
                             "为避免循环不再审批，本次未执行任何写入。请换个问法或新开话题重试。"}}

    pre = execute_write(sql, estimated_rows=None, dry_run=True)
    if not pre["ok"]:
        m = re.match(r"\[(\w+)\]", pre["data"].message)
        error_code = "too_many_rows" if m and m.group(1) == "too_many_rows" \
            else "precheck_failed"
        return {"approval_granted": False, "write_result": {
            "ok": False, "error_code": error_code, "error_message": pre["data"].message}}

    est = int(pre["data"].get("estimated_rows", 0))
    target = pre["data"].get("target", "")
    if est > cfg.sql_guard.max_write_rows:
        return {"approval_granted": False, "write_result": {
            "ok": False, "error_code": "too_many_rows",
            "error_message": f"预估影响 {est} 行，超过单次上限 {cfg.sql_guard.max_write_rows}，"
                             "已拒绝执行。建议：缩小 WHERE 范围分批操作，或由管理员手工执行。",
            "estimated_rows": est}}

    card = (
        "【写操作待确认】\n"
        f"目标：{target}（{pre['data'].get('database', '')}.{pre['data'].get('table', '')}）\n"
        f"操作：{pre['data'].get('operation', '')}，预估影响 {est} 行\n"
        f"SQL 全文：\n{sql}\n\n"
        "确认执行请输入 y；输入其他任意内容取消（不会执行任何写入）。"
    )
    reply = interrupt(card)      
    granted = str(reply).strip().lower() == "y"
    patch: dict = {
        "write_sql": sql, "write_target": target, "estimated_rows": est,
        "approval_granted": granted, "approval_asks": asks + 1,
    }
    if not granted:
        patch["write_result"] = {"ok": False, "error_code": "user_cancelled"}
    return patch


def route_after_approval(state: DataAnalyzeState) -> str:
    if state.get("node_error"):
        return "finalize"
    return "execute_write" if state.get("approval_granted") else "finalize"
