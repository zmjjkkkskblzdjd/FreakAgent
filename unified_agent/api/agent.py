from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pydantic import BaseModel

from allfusion_auth.deps import AuthUser, require_user
from allfusion_auth.logging_redact import install_exception_handler, install_logging_redact
from allfusion_auth.redact import redact_text
from kb_service.kb_owner import filter_visible as filter_visible_kb_ids
from kb_service.kb_registry import DEFAULT_KB_ID
from unified_agent.agents.recall_catalog import prewarm_snapshot
from unified_agent.api import sessions
from unified_agent.core.contracts import ResumeCommand, UnifiedFinalReport
from unified_agent.core.llm import _THINK_SINK, _TOKEN_SINK, bind_tenant, bind_user
from unified_agent.core.trace_sink import bind_trace, new_trace_id
from unified_agent.core.state import SCHEMA_VERSION
from unified_agent.graph import checkpoint
from unified_agent.graph.builder import build_graph
from unified_agent.graph.checkpoint import SchemaVersionError


_checkpointer = None
_graph = None
_runtime_lock = threading.Lock()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    from concurrent.futures import ThreadPoolExecutor
    loop = asyncio.get_running_loop()
    max_workers = int(os.getenv("AF_THREAD_POOL", "32"))
    loop.set_default_executor(ThreadPoolExecutor(max_workers=max_workers))
    print(f"[unified_agent] default ThreadPoolExecutor = {max_workers}")

    global _checkpointer, _graph
    try:
        stale = sessions.mark_stale_streaming()
        if stale:
            print(f"[unified_agent] 已将 {stale} 条中断的流式消息标记为 error")
    except Exception as e:  # noqa: BLE001 — 数据库暂不可用不应阻塞服务启动
        print(f"[unified_agent] 清理 streaming 残留失败：{e}")
    try:
        _checkpointer = checkpoint.get_checkpointer()
        _graph = build_graph(checkpointer=_checkpointer)
        print("[unified_agent] PostgreSQL checkpointer 就绪")
    except Exception as e:  # noqa: BLE001 — 启动失败时 /health 报 degraded，端点返回 503
        print(f"[unified_agent] checkpointer 初始化失败：{e}")
    try:
        yield
    finally:
        _graph = None
        _checkpointer = None
        checkpoint.dispose_checkpointer()


app = FastAPI(title="unified_agent", version="1.3.0", lifespan=_lifespan)

install_logging_redact()
install_exception_handler(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)
app.include_router(sessions.router)


@app.get("/health")
async def health():
    ready = _graph is not None and _checkpointer is not None
    body = {"status": "ok" if ready else "degraded", "ready": ready,
            "checkpointer": "postgres", "version": app.version}
    return JSONResponse(body, status_code=200 if ready else 503)


@app.get("/ready")
async def ready():
    ok = await asyncio.to_thread(checkpoint.pg_ping, 1.0)
    body = {"status": "ok" if ok else "degraded", "ready": ok,
            "checkpointer": "postgres", "version": app.version}
    return JSONResponse(body, status_code=200 if ok else 503)


class AgentRequest(BaseModel):
    session_id: str
    thread_id: str
    message: str
    stream: bool = False
    kb_ids: list[str] | None = None     
    kb_fallback: bool = True            
    datasource_id: int | None = None    
    datasource_enabled: bool = True     
    web_enabled: bool = True            


_MAX_KB_IDS = 10
log = logging.getLogger(__name__)


def _kb_ids(req: AgentRequest, user: AuthUser) -> list[str]:
    ids: list[str] = []
    for raw in req.kb_ids or []:
        kb_id = str(raw).strip()
        if kb_id and kb_id not in ids:
            ids.append(kb_id)
    ids = ids[:_MAX_KB_IDS]
    if not ids:
        return [DEFAULT_KB_ID] if getattr(req, "kb_fallback", True) else []
    try:
        kept, dropped = filter_visible_kb_ids(
            ids, user.tenant_id, user.user_id, user.is_kb_admin)
    except Exception as e:  # noqa: BLE001 — 归属查询失败不得阻断对话
        log.warning("kb visibility lookup failed: %s", redact_text(str(e)))
        kept, dropped = [], ids
    if dropped:
        log.warning("drop kb_ids not visible to user %s: %s", user.user_id, dropped)
    return (kept or [DEFAULT_KB_ID])[:_MAX_KB_IDS]


class ResumeRequest(BaseModel):
    thread_id: str
    command: dict          


class CancelRequest(BaseModel):
    thread_id: str


def _pick_node(state):
    return {}                          


def _pick_router(state):
    return state.get("t", "fan")


def _kb_empty_names(state: dict | None) -> list[str]:
    scope = (state or {}).get("kb_scope")
    if not isinstance(scope, dict):
        return []
    return [str(n) for n in (scope.get("empty_names") or []) if str(n).strip()]


def _report_metadata(report: dict | None, state: dict | None = None) -> dict:
    report = report or {}
    return {
        "citations": report.get("citations") or [],
        "sql": report.get("sql"),
        "validation_errors": report.get("validation_errors") or [],
        "mode": report.get("mode") or "",
        "channel": report.get("channel") or None,
        "writes": report.get("writes") or [],
        "refusal_reason": report.get("refusal_reason") or "",
        "kb_empty_names": _kb_empty_names(state),
    }


def _turn_context(req: AgentRequest, user: AuthUser) -> dict:
    return {
        "tenant_id": str(user.tenant_id),
        "user_id": str(user.user_id),
        "role": user.role or "member",
        "datasource_id": req.datasource_id,
        "sql_enabled": getattr(req, "datasource_enabled", True),
        "web_enabled": getattr(req, "web_enabled", True),
    }


_TURN_LOCKS: dict[str, threading.Lock] = {}
_TURN_LOCKS_GUARD = threading.Lock()


def _turn_lock(thread_id: str) -> threading.Lock:
    with _TURN_LOCKS_GUARD:
        lock = _TURN_LOCKS.get(thread_id)
        if lock is None:
            lock = _TURN_LOCKS[thread_id] = threading.Lock()
        return lock


_MAX_INFLIGHT = int(os.getenv("AF_MAX_INFLIGHT", "8"))
_INFLIGHT_GUARD = asyncio.Lock()
_global_inflight = 0


async def _try_acquire() -> bool:
    global _global_inflight
    async with _INFLIGHT_GUARD:
        if _global_inflight >= _MAX_INFLIGHT:
            return False
        _global_inflight += 1
    return True


async def _release_slot() -> None:
    global _global_inflight
    async with _INFLIGHT_GUARD:
        if _global_inflight > 0:
            _global_inflight -= 1


def _raise_too_many_inflight() -> None:
    raise HTTPException(status_code=429, detail={
        "ok": False, "error": "too_many_inflight",
        "hint": f"当前有 {_global_inflight}/{_MAX_INFLIGHT} 个会话正在分析，请终止一个后再发送",
        "inflight": _global_inflight, "limit": _MAX_INFLIGHT,
    })


_CANCEL_GUARD = threading.Lock()
_CANCEL_EVENTS: dict[str, threading.Event] = {}
_INFLIGHT_TURNS: dict[str, dict] = {}


def _begin_inflight(thread_id: str, turn: dict) -> threading.Event:
    event = threading.Event()
    with _CANCEL_GUARD:
        _CANCEL_EVENTS[thread_id] = event
        _INFLIGHT_TURNS[thread_id] = turn
    return event


def _end_inflight(thread_id: str, event: threading.Event) -> None:
    with _CANCEL_GUARD:
        if _CANCEL_EVENTS.get(thread_id) is event:
            _CANCEL_EVENTS.pop(thread_id, None)
            _INFLIGHT_TURNS.pop(thread_id, None)


def _is_cancelled(thread_id: str, event: threading.Event) -> bool:
    with _CANCEL_GUARD:
        if _CANCEL_EVENTS.get(thread_id) is not event:
            return True
        return event.is_set()


def _turn_inflight(thread_id: str) -> bool:
    with _CANCEL_GUARD:
        return thread_id in _INFLIGHT_TURNS


def _finalize_turn(turn: dict, answer: str, *, status: str = "complete",
                   metadata: dict | None = None,
                   trace_id: str | None = None) -> bool:
    with _CANCEL_GUARD:
        if turn.get("finalized"):
            return False
        turn["finalized"] = True
    sessions.finalize_assistant(turn, answer, status=status,
                                metadata=metadata, trace_id=trace_id)
    return True


def _cancel_turn(thread_id: str) -> bool:
    with _CANCEL_GUARD:
        event = _CANCEL_EVENTS.get(thread_id)
        turn = _INFLIGHT_TURNS.get(thread_id)
        claim = turn is not None and not turn.get("finalized")
        if claim:
            turn["finalized"] = True
    if event is None:
        return False
    event.set()
    if claim:
        try:
            sessions.finalize_assistant(turn, turn.get("partial") or "",
                                        status="cancelled",
                                        metadata={"cancelled": True})
        except Exception as e:  # noqa: BLE001 — 落库失败也要回「已取消」，由前端本地兜底
            log.warning("cancel finalize failed: %s", redact_text(str(e)))
    q = turn.get("cancel_queue") if turn else None
    if q is not None:
        try:
            q.put(("cancelled", {"cancelled": True}))
        except Exception:  # noqa: BLE001 — 队列已关也无需报错
            pass
    return True


def _ensure_runtime():
    global _graph, _checkpointer
    try:
        ckpt = checkpoint.ensure_checkpointer()
    except Exception as e:  # noqa: BLE001 — 掉库/重建失败统一 503，不泄露细节
        log.warning("checkpointer unavailable: %s", redact_text(str(e)))
        raise HTTPException(status_code=503,
                            detail={"ok": False, "error": "checkpointer_unavailable"})
    with _runtime_lock:
        if _graph is None or _checkpointer is not ckpt:
            _graph = build_graph(checkpointer=ckpt)
            _checkpointer = ckpt
        return _graph, _checkpointer


def _require_graph():
    return _ensure_runtime()[0]


def _reject_expired_thread(user: AuthUser, thread_id: str) -> None:
    session = sessions.assert_owner(thread_id, user)
    if int(session.get("message_count") or 0) == 0:
        return
    if not checkpoint.has_snapshot(_checkpointer, thread_id):
        raise HTTPException(status_code=409, detail={
            "ok": False, "error": "thread_expired",
            "hint": "历史已归档，无法继续该会话，请新开会话",
        })


def _invoke_with_version_gate(graph_input, cfg):
    graph = _require_graph()
    thread_id = cfg["configurable"]["thread_id"]
    with _turn_lock(thread_id):
        checkpoint.load_state(_checkpointer, thread_id)
        return graph.invoke(graph_input, cfg)


@app.post("/agent")
async def agent(req: AgentRequest, user: AuthUser = Depends(require_user)):
    if not await _try_acquire():
        _raise_too_many_inflight()
    try:
        _require_graph()
        bind_tenant(user.tenant_id)          
        bind_user(user.user_id)              
        _reject_expired_thread(user, req.thread_id)
        turn = sessions.begin_turn(user, req.thread_id, req.message)
        trace_id = new_trace_id()
        bind_trace(trace_id=trace_id, tenant_id=user.tenant_id, user_id=user.user_id,
                   session_id=req.session_id, thread_id=req.thread_id,
                   request_id=turn.get("request_id"))
        cfg = {"configurable": {"thread_id": req.thread_id}}
        try:
            result = await asyncio.to_thread(
                _invoke_with_version_gate,
                {"session_id": req.session_id, "message": req.message,
                 "question": req.message, "schema_version": SCHEMA_VERSION,
                 "kb_ids": _kb_ids(req, user), **_turn_context(req, user)}, cfg)
        except SchemaVersionError as e:
            sessions.finish_turn(turn, "", status="error", trace_id=trace_id,
                                 metadata={"error": "history_reset", "hint": str(e)})
            raise HTTPException(status_code=409,
                                detail={"ok": False, "error": "history_reset", "hint": str(e)})
        except HTTPException:
            raise                       
        except Exception as e:  
            hint = redact_text(str(e))[:500]
            sessions.finish_turn(turn, "", status="error", metadata={"error": hint},
                                 trace_id=trace_id)
            raise HTTPException(status_code=500, detail={"ok": False, "error": hint})

        intr = result.get("__interrupt__")
        if intr:
            payload = intr[0].value
            sessions.finish_turn(turn, "", status="interrupted", trace_id=trace_id,
                                 metadata={"interrupt": payload})
            return {"event": "interrupt", "data": payload}
        report = result.get("final_report")
        if report is None:
            sessions.finish_turn(turn, "", metadata=_report_metadata(None, result), trace_id=trace_id)
            return {"event": "final", "data": None,
                    "task_status": result.get("task_status", "done")}
        sessions.finish_turn(turn, report.get("answer_md") or "",
                             metadata=_report_metadata(report, result), trace_id=trace_id)
        return UnifiedFinalReport(**report).model_dump()
    finally:
        await _release_slot()


@app.post("/agent/resume")
async def resume(req: ResumeRequest, user: AuthUser = Depends(require_user)):
    if not await _try_acquire():
        _raise_too_many_inflight()
    try:
        _require_graph()
        bind_tenant(user.tenant_id)
        bind_user(user.user_id)
        _reject_expired_thread(user, req.thread_id)
        turn = sessions.resume_turn(user, req.thread_id)
        cancel_event = _begin_inflight(req.thread_id, turn)
        trace_id = new_trace_id()
        bind_trace(trace_id=trace_id, tenant_id=user.tenant_id, user_id=user.user_id,
                   session_id=turn["session_id"], thread_id=req.thread_id,
                   request_id=turn.get("request_id"))
        cmd = ResumeCommand(**req.command)
        cfg = {"configurable": {"thread_id": req.thread_id}}
        cancelled = False
        try:
            result = await asyncio.to_thread(
                _invoke_with_version_gate, Command(resume=cmd.model_dump()), cfg)
            cancelled = cancel_event.is_set()
        except SchemaVersionError as e:
            _finalize_turn(turn, "", status="error", trace_id=trace_id,
                           metadata={"error": "history_reset", "hint": str(e)})
            raise HTTPException(status_code=409,
                                detail={"ok": False, "error": "history_reset", "hint": str(e)})
        except HTTPException:
            raise                       
        except Exception as e:
            hint = redact_text(str(e))[:500]
            _finalize_turn(turn, "", status="error",
                           metadata={"error": hint}, trace_id=trace_id)
            raise HTTPException(status_code=500, detail={"ok": False, "error": hint})
        finally:
            _end_inflight(req.thread_id, cancel_event)
        if cancelled:
            return {"event": "cancelled", "data": None}
        intr = result.get("__interrupt__")
        if intr:
            payload = intr[0].value
            _finalize_turn(turn, "", status="interrupted",
                       metadata={"interrupt": payload}, trace_id=trace_id)
            return {"event": "interrupt", "data": payload}
        report = result.get("final_report")
        if report is None:
            _finalize_turn(turn, "", metadata=_report_metadata(None, result), trace_id=trace_id)
            return {"event": "final", "data": None,
                    "task_status": result.get("task_status", "done")}
        _finalize_turn(turn, report.get("answer_md") or "",
                       metadata=_report_metadata(report, result), trace_id=trace_id)
        return UnifiedFinalReport(**report).model_dump()
    finally:
        await _release_slot()


class UndoRequest(BaseModel):
    thread_id: str


@app.post("/agent/undo")
def undo_write_op(req: UndoRequest, user: AuthUser = Depends(require_user)) -> dict:
    sessions.assert_owner(req.thread_id, user)
    if _turn_inflight(req.thread_id):
        raise HTTPException(status_code=409, detail={
            "ok": False, "error": "turn_in_progress", "hint": "本轮生成中，请先终止"})
    graph = _require_graph()
    cfg = {"configurable": {"thread_id": req.thread_id}}
    snap = graph.get_state(cfg)
    state = (snap.values or {}) if snap else {}
    if not state.get("sql_ctx"):
        raise HTTPException(status_code=404, detail={
            "ok": False, "error": "no_write_context", "hint": "该会话没有可撤回的写操作"})
    from unified_agent.agents.approve import undo_write as undo_write_node

    out = undo_write_node(state, cfg)
    sql_ctx = out.get("sql_ctx")
    if sql_ctx is not None:
        graph.update_state(cfg, {"sql_ctx": sql_ctx})
    result = out.get("undo_result") or {"ok": False, "error": "undo_failed"}
    return {"ok": bool(result.get("ok")), **result,
            "answer_md": sql_ctx.analysis_md if sql_ctx else ""}


@app.post("/agent/cancel")
def cancel(req: CancelRequest, user: AuthUser = Depends(require_user)) -> dict:
    sessions.assert_owner(req.thread_id, user)
    if _cancel_turn(req.thread_id):
        return {"ok": True, "cancelled": True}
    return {"ok": True, "cancelled": False, "hint": "本轮已结束"}


@app.delete("/sessions/{thread_id}/messages/{message_id}")
def delete_message(thread_id: str, message_id: str,
                   user: AuthUser = Depends(require_user)) -> dict:
    sessions.assert_owner(thread_id, user)
    if _turn_inflight(thread_id):
        raise HTTPException(status_code=409, detail={
            "error": "turn_in_progress", "hint": "本轮生成中，请先终止再删除",
        })
    lock = _turn_lock(thread_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail={
            "error": "turn_in_progress", "hint": "本轮生成中，请先终止再删除",
        })
    try:
        if _turn_inflight(thread_id):
            raise HTTPException(status_code=409, detail={
                "error": "turn_in_progress", "hint": "本轮生成中，请先终止再删除",
            })
        session = sessions.assert_owner(thread_id, user)
        row = sessions.get_message(session["id"], message_id, user.tenant_id)
        if row is None:
            raise HTTPException(status_code=404, detail={
                "error": "message_not_found", "hint": "消息不存在或已删除",
            })
        graph, saver = _ensure_runtime()
        has_snapshot = checkpoint.has_snapshot(saver, thread_id)
        sessions.delete_message(session["id"], message_id)
        sessions.refresh_preview(session["id"])
        history = sessions.remaining_history(session["id"], user.tenant_id)
        if has_snapshot:
            messages = [HumanMessage(content=m["content"]) if m["role"] == "user"
                        else AIMessage(content=m["content"]) for m in history]
            try:
                saver.delete_thread(thread_id)
                graph.update_state(
                    {"configurable": {"thread_id": thread_id}},
                    {"history": messages, "schema_version": SCHEMA_VERSION,
                     "route_clarify_count": 0})
            except Exception as e:  # noqa: BLE001 — 行已删但记忆没重建，必须显式报错
                hint = redact_text(str(e))[:500]
                log.error("history rebuild failed: %s", hint)
                raise HTTPException(status_code=500, detail={
                    "error": "memory_rebuild_failed",
                    "hint": "消息已删除，但短期记忆重建失败，请重试或新开会话",
                })
        return {"ok": True, "thread_id": thread_id, "message_id": message_id,
                "history_size": len(history), "memory_rebuilt": has_snapshot}
    finally:
        lock.release()


_STREAM_NODES = {
    "recall_memory", "compress_history", "intake", "recall_catalog",
    "preflight", "rag", "sql", "approve_write",
    "execute_write", "merge", "web_attribution", "finalize", "chitchat",
}

_FLUSH_INTERVAL_SEC = 1.0
_FLUSH_BYTES = 2048


def _sse(event: str, data) -> str:
    payload = json.dumps(data if data is not None else {}, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@app.post("/agent/stream")
async def agent_stream(req: AgentRequest, user: AuthUser = Depends(require_user)):
    if not await _try_acquire():
        _raise_too_many_inflight()

    graph = _require_graph()
    bind_tenant(user.tenant_id)
    bind_user(user.user_id)
    _reject_expired_thread(user, req.thread_id)
    turn = sessions.begin_turn(user, req.thread_id, req.message)
    assistant_id = sessions.start_assistant(turn, user)
    trace_id = new_trace_id()
    bind_trace(trace_id=trace_id, tenant_id=user.tenant_id, user_id=user.user_id,
               session_id=req.session_id, thread_id=req.thread_id,
               request_id=turn.get("request_id"))
    cancel_event = _begin_inflight(req.thread_id, turn)

    def _run_stream(q: "queue.Queue") -> None:
        bind_tenant(user.tenant_id)
        bind_user(user.user_id)
        bind_trace(trace_id=trace_id, tenant_id=user.tenant_id, user_id=user.user_id,
                   session_id=req.session_id, thread_id=req.thread_id,
                   request_id=turn.get("request_id"))
        cfg = {"configurable": {"thread_id": req.thread_id}}
        if _is_cancelled(req.thread_id, cancel_event):
            _finalize_turn(turn, turn.get("partial") or "", status="cancelled",
                           metadata={"cancelled": True}, trace_id=trace_id)
            q.put(("cancelled", {"cancelled": True}))
            return
        try:
            checkpoint.load_state(_checkpointer, req.thread_id)
        except SchemaVersionError as e:
            _finalize_turn(turn, "", status="error", trace_id=trace_id,
                           metadata={"error": "history_reset", "hint": str(e)})
            q.put(("error", {"code": "history_reset", "message": str(e)}))
            return
        except Exception as e:  # noqa: BLE001
            hint = redact_text(str(e))[:500]
            _finalize_turn(turn, "", status="error",
                           metadata={"error": hint}, trace_id=trace_id)
            q.put(("error", {"code": "internal", "message": hint}))
            return

        parts: list[str] = []
        state = {"chars": 0, "flushed_at": time.monotonic()}

        def _flush(text: str, force: bool = False) -> bool:
            now = time.monotonic()
            if not force and (now - state["flushed_at"] < _FLUSH_INTERVAL_SEC
                              and len(text) - state["chars"] < _FLUSH_BYTES):
                return False
            turn["partial"] = text
            try:
                sessions.update_assistant(assistant_id, text, status="streaming")
            except Exception:  # noqa: BLE001 — 落库失败不应打断流
                return False
            state["chars"] = len(text)
            state["flushed_at"] = now
            return True

        def _sink(delta: str) -> None:
            parts.append(delta)
            q.put(("token", {"delta": delta}))
            _flush("".join(parts))

        _TOKEN_SINK.set(_sink)
        _THINK_SINK.set(lambda delta: q.put(("thinking", {"delta": delta})))
        interrupted = False
        cancelled = False
        try:
            init_state = {"session_id": req.session_id, "message": req.message,
                          "question": req.message, "schema_version": SCHEMA_VERSION,
                          "kb_ids": _kb_ids(req, user), **_turn_context(req, user)}

            def _prewarm() -> None:
                bind_tenant(user.tenant_id)
                bind_user(user.user_id)
                prewarm_snapshot(dict(init_state))

            threading.Thread(target=_prewarm, daemon=True).start()

            for chunk in graph.stream(init_state, cfg, stream_mode=["updates", "custom"]):
                if _is_cancelled(req.thread_id, cancel_event):
                    cancelled = True
                    break
                mode, payload = chunk if isinstance(chunk, tuple) else ("updates", chunk)
                if mode == "custom":
                    name = payload.get("node") if isinstance(payload, dict) else None
                    if name in _STREAM_NODES:
                        q.put(("node", {"node": name, "state": "start"}))
                    continue
                if not isinstance(payload, dict):
                    continue
                if "__interrupt__" in payload:
                    interrupted = True
                    interrupt_payload = payload["__interrupt__"][0].value
                    _finalize_turn(turn, "".join(parts),
                                   status="interrupted",
                                   metadata={"interrupt": interrupt_payload},
                                   trace_id=trace_id)
                    q.put(("interrupt", interrupt_payload))
                    continue
                for node in payload:
                    if node in _STREAM_NODES:
                        q.put(("node", {"node": node, "state": "done"}))
            if cancelled:
                turn["partial"] = "".join(parts)
                _finalize_turn(turn, turn["partial"], status="cancelled",
                               metadata={"cancelled": True}, trace_id=trace_id)
                q.put(("cancelled", {"cancelled": True}))
                return
            if interrupted:
                return
            _flush("".join(parts), force=True)
        except Exception as e:  # noqa: BLE001
            hint = redact_text(str(e))[:500]
            _finalize_turn(turn, "".join(parts), status="error",
                           metadata={"error": hint}, trace_id=trace_id)
            q.put(("error", {"code": "internal", "message": hint}))
            return

        try:
            values = graph.get_state(cfg).values or {}
        except Exception:  # noqa: BLE001
            values = {}
        report = values.get("final_report")
        answer = (report or {}).get("answer_md") or "".join(parts)
        if not _finalize_turn(turn, answer,
                              metadata=_report_metadata(report, values),
                              trace_id=trace_id):
            q.put(("cancelled", {"cancelled": True}))
            return
        if report:
            try:
                payload = UnifiedFinalReport(**report).model_dump()
                payload["kb_empty_names"] = _kb_empty_names(values)
                q.put(("final", payload))
            except Exception:  # noqa: BLE001
                q.put(("final", report))
        else:
            q.put(("final", {"event": "final", "data": None,
                             "task_status": values.get("task_status", "done")}))

    def _worker(q: "queue.Queue") -> None:
        try:
            with _turn_lock(req.thread_id):
                _run_stream(q)
        finally:
            _end_inflight(req.thread_id, cancel_event)

    async def _gen():
        q: "queue.Queue" = queue.Queue()
        turn["cancel_queue"] = q
        finished = False          
        try:
            yield _sse("meta", {"session_id": req.session_id,
                                "thread_id": req.thread_id,
                                "request_id": turn["request_id"],
                                "user_message_id": turn["user_message_id"],
                                "assistant_message_id": assistant_id})
            threading.Thread(target=_worker, args=(q,), daemon=True).start()
            while True:
                try:
                    event, data = await asyncio.to_thread(q.get, True, 15)
                except queue.Empty:
                    yield ": ping\n\n"        
                    continue
                yield _sse(event, data)
                if event in ("final", "error", "interrupt", "cancelled"):
                    finished = True
                    break
        finally:
            if not finished:
                _cancel_turn(req.thread_id)
            await _release_slot()

    return StreamingResponse(
        _gen(), media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("unified_agent.api.agent:app",
                host=os.getenv("UA_HOST", "127.0.0.1"),
                port=int(os.getenv("UA_PORT", "8000")),
                workers=int(os.getenv("UA_WORKERS", "1")))
