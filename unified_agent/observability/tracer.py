from __future__ import annotations

import inspect
import itertools
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from langgraph.errors import GraphBubbleUp  

__all__ = ["TraceEvent", "Tracer", "get_tracer", "trace_node", "emit_event"]

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "trace.jsonl"


@dataclass
class TraceEvent:                
    event_id: str
    thread_id: str
    session_id: str
    seq: int
    node: str
    kind: Literal["node_enter", "node_exit", "llm_call", "tool_call",
                  "review_block", "runtime_error", "repair", "escalation",
                  "web_fallback", "clarify_suspend", "approval_suspend",
                  "approval_resume", "resolve", "finalize"]
    started_at: str
    duration_ms: int
    status: str
    detail: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0


class Tracer:

    def __init__(self, path: str | Path | None = None):
        env = os.getenv("UA_TRACE_PATH")
        p = Path(env) if env else (Path(path) if path else DEFAULT_PATH)
        self.path = p if p.is_absolute() else Path.cwd() / p
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq_counter = itertools.count(1)   

    def next_seq(self) -> int:
        return next(self._seq_counter)

    def emit(self, event: TraceEvent) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 旁路纪律：写不进就放弃这一条
            pass
        try:
            from unified_agent.core.trace_sink import record_event
            record_event(asdict(event))
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def now() -> str:
        return datetime.now().isoformat(timespec="seconds")


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer


def emit_event(kind: str, node: str, status: str = "ok", detail: dict | None = None,
               thread_id: str = "", session_id: str = "", duration_ms: int = 0) -> None:
    t = get_tracer()
    t.emit(TraceEvent(
        event_id=uuid.uuid4().hex, thread_id=thread_id, session_id=session_id,
        seq=t.next_seq(), node=node, kind=kind,
        started_at=Tracer.now(), duration_ms=duration_ms,
        status=status, detail=detail or {}))


def _identity(fn) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return True
    return any(p.kind is p.VAR_KEYWORD or p.name == "config" for p in params.values())


def _emit_step_start(name: str) -> None:
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()({"type": "node_start", "node": name})
    except Exception:  # noqa: BLE001 — UI 遥测失败不影响节点执行
        pass


def trace_node(name: str, fn):
    takes_config = _identity(fn)

    def wrapper(state, config=None, *args, **kwargs):
        thread = ((config or {}).get("configurable") or {}).get("thread_id", "") \
            if isinstance(config, dict) else ""
        session = state.get("session_id", "")
        t0 = time.perf_counter()
        emit_event("node_enter", name, "running", {}, thread, session)
        _emit_step_start(name)
        try:
            if hasattr(fn, "invoke"):
                patch = fn.invoke(state, config)
            elif takes_config:
                patch = fn(state, config, *args, **kwargs)
            else:
                patch = fn(state, *args, **kwargs)
        except GraphBubbleUp:
            kind = "approval_suspend" if name == "approve_write" else "clarify_suspend"
            emit_event(kind, name, "suspended", {}, thread, session)
            raise
        except Exception as e:  # noqa: BLE001 —— 记账后原样上抛，降级职责在调用方 wrapper
            dur = int((time.perf_counter() - t0) * 1000)
            emit_event("node_exit", name, "error",
                       {"error": f"{type(e).__name__}: {e}"}, thread, session, dur)
            raise
        dur = int((time.perf_counter() - t0) * 1000)
        if name == "finalize" and isinstance(patch, dict):
            errs = patch.get("validation_errors") or []
            detail = {"validation_errors": errs}
            if patch.get("validation_exempted"):
                detail["validation_exempted"] = patch["validation_exempted"]
            emit_event("finalize", name,
                       "validation_failed" if errs else "ok",
                       detail, thread, session, dur)
        emit_event("node_exit", name, "ok", {}, thread, session, dur)
        return patch

    wrapper.__name__ = name             
    wrapper._trace_node = name          
    return wrapper
