from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextvars import ContextVar
from datetime import datetime

from allfusion_auth.redact import redact_text

__all__ = ["bind_trace", "current_trace", "new_trace_id", "record",
           "record_event", "flush", "dropped"]

_LOG = logging.getLogger("unified_agent.trace")

_ENABLED = os.getenv("AF_TRACE_ENABLED", "1") != "0"
_CH_URL = os.getenv("AF_CH_URL", "http://192.168.1.136:8123").rstrip("/")
_CH_DB = os.getenv("AF_CH_DB", "allfusion_trace")
_CH_USER = os.getenv("AF_CH_USER") or ""
_CH_PASSWORD = os.getenv("AF_CH_PASSWORD") or ""
_SAMPLE = float(os.getenv("AF_TRACE_SAMPLE", "1.0") or "1.0")
_STORE_PAYLOAD = os.getenv("AF_TRACE_STORE_PAYLOAD", "0") != "0"
_SERVICE = "unified_agent"

_QUEUE_MAX = int(os.getenv("AF_TRACE_QUEUE_MAX", "2000"))
_BATCH_MAX = 500
_FLUSH_INTERVAL = 1.0
_WARN_INTERVAL = 60.0

_KIND_MAP = {
    "node_enter": "node", "node_exit": "node", "finalize": "node",
    "clarify_suspend": "node", "approval_suspend": "node",
    "approval_resume": "node", "llm_call": "llm", "tool_call": "tool",
    "web_fallback": "http", "escalation": "node", "repair": "node",
    "review_block": "node", "runtime_error": "node", "resolve": "node",
}
_PAYLOAD_KEYS = ("prompt", "answer", "messages", "content", "question", "sql")

_CTX: ContextVar[dict] = ContextVar("unified_trace_ctx", default={})

_q: "queue.Queue[dict]" = queue.Queue(maxsize=_QUEUE_MAX)
_lock = threading.Lock()
_worker: threading.Thread | None = None
_span_seq = 0
_dropped = 0
_last_warn = 0.0


def new_trace_id() -> str:
    return uuid.uuid4().hex


def bind_trace(**fields) -> None:
    ctx = dict(_CTX.get())
    ctx.update({k: v for k, v in fields.items() if v is not None})
    _CTX.set(ctx)


def current_trace() -> dict:
    return dict(_CTX.get())


def dropped() -> int:
    return _dropped


def _user_hash(user_id) -> int:
    raw = str(user_id or "").encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _sanitize(attrs) -> str:
    data = dict(attrs or {})
    if not _STORE_PAYLOAD:
        for key in _PAYLOAD_KEYS:
            if key in data:
                value = str(data.pop(key) or "")
                data[f"{key}_len"] = len(value)
                data[f"{key}_sha8"] = hashlib.sha256(
                    value.encode("utf-8")).hexdigest()[:8]
    text = json.dumps(data, ensure_ascii=False, default=str)
    return redact_text(text)[:8192]


def _next_span() -> int:
    global _span_seq
    with _lock:
        _span_seq = (_span_seq + 1) & 0x7FFFFFFFFFFFFFFF
        return _span_seq


def _warn(msg: str) -> None:
    global _last_warn
    now = time.monotonic()
    if now - _last_warn < _WARN_INTERVAL:
        return
    _last_warn = now
    _LOG.warning("trace sink: %s (ch=%s)", msg, _CH_URL)


def _post(rows: list[dict]) -> None:
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows).encode("utf-8")
    url = (f"{_CH_URL}/?database={urllib.parse.quote(_CH_DB)}"
           "&query=" + urllib.parse.quote("INSERT INTO trace_span FORMAT JSONEachRow"))
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-ndjson; charset=utf-8"})
    if _CH_USER:
        req.add_header("X-ClickHouse-User", _CH_USER)
    if _CH_PASSWORD:
        req.add_header("X-ClickHouse-Key", _CH_PASSWORD)
    urllib.request.urlopen(req, timeout=10).read()


def _loop() -> None:
    while True:
        batch: list[dict] = []
        try:
            batch.append(_q.get(timeout=_FLUSH_INTERVAL))
            deadline = time.monotonic() + 0.2
            while len(batch) < _BATCH_MAX and time.monotonic() < deadline:
                try:
                    batch.append(_q.get_nowait())
                except queue.Empty:
                    break
            if batch:
                _post(batch)
        except Exception as e:  # noqa: BLE001 旁路：CH 不可达不打断任何事
            _warn(f"{type(e).__name__}: {e}")


def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, name="trace-sink", daemon=True)
        _worker.start()


def _enqueue(row: dict) -> None:
    if not _ENABLED:
        return
    if _SAMPLE < 1.0 and random.random() > _SAMPLE:
        return
    _ensure_worker()
    try:
        _q.put_nowait(row)
    except queue.Full:
        global _dropped
        with _lock:
            _dropped += 1


def record(node: str, kind: str = "node", status: str = "ok", *,
           error_code: str = "", model: str = "", prompt_tokens: int = 0,
           completion_tokens: int = 0, duration_ms: int = 0,
           attrs: dict | None = None, trace_id: str | None = None,
           parent_span_id: int = 0) -> None:
    try:
        ctx = _CTX.get()
        tid = trace_id or ctx.get("trace_id") or new_trace_id()
        row = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "trace_id": tid,
            "span_id": _next_span(),
            "parent_span_id": int(parent_span_id or 0),
            "tenant_id": int(ctx.get("tenant_id") or 0),
            "user_hash": _user_hash(ctx.get("user_id")),
            "session_id": str(ctx.get("session_id") or ""),
            "thread_id": str(ctx.get("thread_id") or ""),
            "request_id": str(ctx.get("request_id") or ""),
            "service": _SERVICE,
            "node": str(node or ""),
            "kind": kind,
            "status": status,
            "error_code": str(error_code or "")[:200],
            "model": str(model or ""),
            "prompt_tokens": max(0, int(prompt_tokens or 0)),
            "completion_tokens": max(0, int(completion_tokens or 0)),
            "cost": 0,
            "duration_ms": max(0, min(int(duration_ms or 0), 0xFFFFFFFF)),
            "attrs": _sanitize(attrs),
        }
        _enqueue(row)
    except Exception:  # noqa: BLE001 旁路纪律：记账失败不影响业务
        pass


def record_event(event: dict) -> None:
    try:
        kind_raw = event.get("kind") or ""
        if kind_raw == "node_enter":
            return
        detail = event.get("detail") or {}
        error = str(detail.get("error") or "")
        status = str(event.get("status") or "ok")
        if kind_raw in ("clarify_suspend", "approval_suspend"):
            status = "interrupted"
        record(
            node=event.get("node") or "",
            kind=_KIND_MAP.get(kind_raw, "event"),
            status=status,
            error_code=(error.split(":", 1)[0] if error else ""),
            prompt_tokens=int(event.get("tokens_in") or 0),
            completion_tokens=int(event.get("tokens_out") or 0),
            duration_ms=int(event.get("duration_ms") or 0),
            attrs=detail,
        )
    except Exception:  # noqa: BLE001
        pass


def flush(timeout: float = 3.0) -> int:
    deadline = time.monotonic() + timeout
    while not _q.empty() and time.monotonic() < deadline:
        time.sleep(0.05)
    return _q.qsize()
