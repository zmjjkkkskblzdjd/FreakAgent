from __future__ import annotations

import sys
from pathlib import Path

from langgraph.types import interrupt

from langchain_core.runnables import RunnableConfig

from unified_agent.core.contracts import PendingInterrupt
from unified_agent.core.state import SQLContext, UnifiedState
from unified_agent.observability.tracer import emit_event  

_DA_ROOT = Path(__file__).resolve().parents[2] / "DataAnalyzeAgent" / "DataAnalyzeAgent"
if str(_DA_ROOT) not in sys.path:
    sys.path.insert(0, str(_DA_ROOT))

from config import get_app_config  # noqa: E402
from data_analyze_agent.db import execute_write as da_execute_write  # noqa: E402
from data_analyze_agent.db import undo_write as da_undo_write  # noqa: E402
from data_analyze_agent.db import use_datasource  # noqa: E402


def _ds_scope(state: UnifiedState):
    return use_datasource(state.get("datasource_id"), state.get("tenant_id"),
                          state.get("user_id"), state.get("role") or "member")


def _precheck_write(state: UnifiedState, sql: str) -> dict:
    try:
        with _ds_scope(state):
            r = da_execute_write(sql, estimated_rows=None, dry_run=True,
                                 transactional_preview=bool(state.get("datasource_id")))
    except Exception as e:               # noqa: BLE001 GatewayUnavailable / 网络异常
        return {"status": "unavailable", "error": f"预检不可用：网关不可达（{e}）"}
    if not r.get("ok"):
        err = r.get("data")
        msg = getattr(err, "message", None) or str(err)
        return {"status": "blocked", "error": msg}
    d = r.get("data") or {}
    pre = {
        "status": "ok",
        "target": d.get("target", ""),
        "database": d.get("database", ""),
        "table": d.get("table", ""),
        "operation": d.get("operation", ""),
        "estimated_rows": int(d.get("estimated_rows", 0) or 0),
        "max_write_rows": int(get_app_config().sql_guard.max_write_rows),
        "audit_id": d.get("audit_id", ""),
    }
    if d.get("preview_token"):
        pre["preview_token"] = str(d["preview_token"])
    if d.get("preview_hash"):
        pre["preview_hash"] = str(d["preview_hash"])
        pre["preview_rows"] = list(d.get("preview_rows") or [])[:5]
        pre["before_rows"] = list(d.get("before_rows") or [])[:5]
        pre["affected_rows"] = int(d.get("affected_rows", 0) or 0)
        pre["auto_pk"] = bool(d.get("auto_pk"))
    return pre


def approve_write(state: UnifiedState, config: RunnableConfig) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    thread_id = (config.get("configurable") or {}).get("thread_id", "unknown")
    sql = c.executed_sql or c.sql or ""
    pre = _precheck_write(state, sql)
    c.write_precheck = pre
    if pre["status"] != "ok":
        c.outcome = "refused"
        c.error_code = "write_precheck_failed"
        c.analysis_md = f"写操作未执行：{pre['error']}。未做任何变更。"
        return {"sql_ctx": c, "task_status": "running"}
    transactional = bool(pre.get("preview_hash"))
    payload = PendingInterrupt(
        interrupt_id=f"approve-{thread_id}", kind="approve_write", lane="sql",
        schema={
            "sql": sql,
            "preview": "transactional" if transactional else "dry_run",
            "target": pre["target"],
            "database": pre["database"],
            "table": pre["table"],
            "operation": pre["operation"],
            "est_rows": pre["estimated_rows"],
            "max_write_rows": pre["max_write_rows"],
            "audit_id": pre["audit_id"],
            "preview_rows": pre.get("preview_rows", []),
            "before_rows": pre.get("before_rows", []),
            "affected_rows": pre.get("affected_rows", 0),
            "auto_pk": bool(pre.get("auto_pk")),
            "preview_note": (
                "预览在事务内真实执行后已回滚（未落库）；触发器/外键的外部副作用"
                "不随回滚消失，撤回也会在 CDC/数仓留下“写入+撤回”两条变更。"
                + ("本表主键为自增列：预览中的主键值仅供示意，执行后以实际生成的值为准。"
                   if pre.get("auto_pk") else "")
                if transactional else ""
            ),
        },
    ).model_dump()
    answer = interrupt(payload)          
    from unified_agent.core.contracts import ResumeCommand
    answer = dict(answer) if isinstance(answer, dict) else {}
    answer.setdefault("type", "approve_write")   
    cmd = ResumeCommand(**answer)
    emit_event("approval_resume", "approve_write",
               "granted" if cmd.granted else "refused",
               {"granted": bool(cmd.granted)},
               thread_id, state.get("session_id", "") or "")
    if cmd.granted:
        return {"sql_ctx": c, "task_status": "running"}
    c.outcome = "refused"
    c.error_code = "write_not_approved"
    c.analysis_md = "您已拒绝该写操作，未执行任何变更。"
    return {"sql_ctx": c, "task_status": "running"}


def execute_write(state: UnifiedState, config: RunnableConfig = None) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    thread_id = ((config or {}).get("configurable") or {}).get("thread_id", "")
    session_id = state.get("session_id", "") or ""
    c.executed_sql = c.executed_sql or c.sql
    sql = c.executed_sql
    token = (c.write_precheck or {}).get("preview_token") or None
    try:
        with _ds_scope(state):
            result = da_execute_write(sql, preview_token=token)
    except Exception as e:                   # noqa: BLE001 GatewayUnavailable 等
        emit_event("tool_call", "execute_write", "error",
                   {"error": f"{type(e).__name__}: {e}"}, thread_id, session_id)
        c.analysis_md = f"写操作执行失败：网关不可达（{e}）"
        c.error_code = "write_execute_failed"
        return {"sql_ctx": c, "task_status": "running"}
    if not result.get("ok"):
        err = result.get("data")
        msg = getattr(err, "message", None) or str(err)
        emit_event("tool_call", "execute_write", "error",
                   {"error_code": "execute_failed", "message": msg}, thread_id, session_id)
        c.analysis_md = f"写操作执行失败：{msg}"
        c.error_code = "write_execute_failed"
        return {"sql_ctx": c, "task_status": "running"}
    d = result.get("data") or {}
    audit_id = d.get("audit_id", "")
    actual = d.get("actual_rows", 0)
    mutation = d.get("mutation_status", "")
    c.write_precheck = {
        **(c.write_precheck or {}),
        "audit_id": audit_id,
        "undo_deadline": d.get("undo_deadline", ""),
        "undo_available": bool(d.get("undo_available")),
        "actual_rows": actual,
        "committed": True,
    }
    undo_tail = ""
    if d.get("undo_available"):
        undo_tail = (f"\n\n如需撤回，可在 1 小时内点击「撤回」"
                     f"（截止 {d.get('undo_deadline', '')}）。")
    if mutation and mutation != "done":
        c.analysis_md = (f"写操作已提交（审计编号 {audit_id}）。异步变更状态：{mutation}，"
                         f"数据将在后台生效，可稍后查询确认。{undo_tail}")
    else:
        c.analysis_md = (f"写操作已执行完成（审计编号 {audit_id}），"
                         f"影响 {actual} 行，耗时 {d.get('elapsed_ms', 0)}ms。{undo_tail}")
    emit_event("tool_call", "execute_write", "ok",
               {"audit_id": audit_id, "actual_rows": actual, "mutation_status": mutation},
               thread_id, session_id)
    return {"sql_ctx": c, "task_status": "running"}


def undo_write(state: UnifiedState, config: RunnableConfig = None) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    thread_id = ((config or {}).get("configurable") or {}).get("thread_id", "")
    session_id = state.get("session_id", "") or ""
    pre = c.write_precheck or {}
    audit_id = str(pre.get("audit_id") or "")
    if not audit_id:
        c.analysis_md = "撤回失败：没有找到本次写操作的审计编号。"
        return {"sql_ctx": c, "undo_result": {"ok": False, "error": "audit_id_missing"}}
    try:
        r = da_undo_write(audit_id,
                          tenant_id=int(state.get("tenant_id") or 0),
                          user_id=int(state.get("user_id") or 0),
                          datasource_id=state.get("datasource_id"))
    except Exception as e:                       # noqa: BLE001
        c.analysis_md = f"撤回失败：网关不可达（{e}）。数据仍保持已写入状态。"
        emit_event("tool_call", "undo_write", "error",
                   {"audit_id": audit_id, "error": str(e)}, thread_id, session_id)
        return {"sql_ctx": c, "undo_result": {"ok": False, "error": "gateway_unavailable"}}
    if not r.get("ok"):
        code = str(r.get("error_code") or "undo_failed")
        c.analysis_md = f"撤回失败：{r.get('message') or code}。数据仍保持已写入状态。"
        emit_event("tool_call", "undo_write", "error",
                   {"audit_id": audit_id, "error_code": code}, thread_id, session_id)
        return {"sql_ctx": c, "undo_result": {"ok": False, "error": code}}
    rows = int((r.get("data") or {}).get("inverse_rows", 0) or 0)
    c.write_precheck = {**pre, "undo_available": False, "undone": True}
    c.analysis_md = f"已撤回上次写操作（审计编号 {audit_id}），反向恢复 {rows} 行。"
    emit_event("tool_call", "undo_write", "ok",
               {"audit_id": audit_id, "inverse_rows": rows}, thread_id, session_id)
    return {"sql_ctx": c, "undo_result": {"ok": True, "audit_id": audit_id,
                                          "inverse_rows": rows}}