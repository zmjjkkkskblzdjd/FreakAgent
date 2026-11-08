from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

from unified_agent.core.state import SCHEMA_VERSION, UnifiedState

_ALLFUSION_ROOT = Path(__file__).resolve().parents[2]

_pg_server = None          
_pool: ConnectionPool | None = None
_saver: PostgresSaver | None = None
_runtime_lock = threading.Lock()


class PgUnavailable(RuntimeError):
    pass



def _pgdata() -> Path:
    raw = (os.getenv("AF_PGDATA") or "").strip()
    path = Path(raw) if raw else Path("data") / "pgdata"
    if not path.is_absolute():
        path = _ALLFUSION_ROOT / path
    return path.resolve()


def _prune_stale_handles(pgdata: Path) -> None:
    path = pgdata / ".handle_pids.json"
    if not path.exists():
        return
    try:
        import json

        import psutil

        pids = json.loads(path.read_text() or "[]")
        alive = [p for p in pids if isinstance(p, int) and psutil.pid_exists(p)]
        if alive != pids:
            path.write_text(json.dumps(alive))
    except Exception:  # noqa: BLE001 — 清理失败不影响启动
        pass


def _start_pg(pgdata: Path):
    import time

    import pgserver
    from pgserver.postgres_server import PostgresServer

    deadline = time.monotonic() + float(os.getenv("AF_PG_START_TIMEOUT", "90"))
    wait = float(os.getenv("AF_PG_START_WAIT", "3"))
    attempt = 0
    last: Exception | None = None
    while True:
        attempt += 1
        try:
            return pgserver.get_server(pgdata, cleanup_mode="stop")
        except Exception as e:  # noqa: BLE001
            last = e
            PostgresServer._instances.pop(pgdata, None)
            print(f"[checkpoint] 内嵌 PG 启动第 {attempt} 次未就绪：{e!r}")
        if time.monotonic() + wait > deadline:
            raise last  # type: ignore[misc]
        time.sleep(wait)


def _resolve_dsn() -> str:
    global _pg_server
    dsn = (os.getenv("AF_PG_DSN") or "").strip()
    if dsn:
        return dsn
    pgdata = _pgdata()
    pgdata.parent.mkdir(parents=True, exist_ok=True)
    _prune_stale_handles(pgdata)
    _pg_server = _start_pg(pgdata)
    return _pg_server.get_uri(database="postgres")


def get_checkpointer() -> PostgresSaver:
    global _pool, _saver
    if _saver is not None:
        return _saver
    pool = ConnectionPool(
        conninfo=_resolve_dsn(),
        max_size=int(os.getenv("AF_PG_POOL", "8")),
        open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        check=ConnectionPool.check_connection,
        timeout=float(os.getenv("AF_PG_POOL_TIMEOUT", "5")),
    )
    saver = PostgresSaver(pool)
    if (os.getenv("AF_PG_SETUP", "1") or "1").strip().lower() in ("1", "true", "yes"):
        saver.setup()          
    _pool, _saver = pool, saver
    return saver


def _endpoint() -> tuple[str, int] | None:
    if _pool is None:
        return None
    try:
        from psycopg.conninfo import conninfo_to_dict

        info = conninfo_to_dict(_pool.conninfo)
        return str(info.get("host") or "127.0.0.1"), int(info.get("port") or 5432)
    except Exception:  # noqa: BLE001 — 解析失败按不可达处理
        return None


def pg_reachable(timeout: float = 0.5) -> bool:
    endpoint = _endpoint()
    if endpoint is None:
        return False
    try:
        with socket.create_connection(endpoint, timeout=timeout):
            return True
    except OSError:
        return False


def pg_ping(timeout: float = 1.0) -> bool:
    if _pool is None:
        return False
    try:
        with _pool.connection(timeout=timeout) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 — 池超时/连接断开都视为不可用
        return False


def ensure_checkpointer() -> PostgresSaver:
    global _saver
    if _saver is not None and pg_ping():
        return _saver
    with _runtime_lock:
        if _saver is not None and pg_ping():
            return _saver
        if _saver is not None and not pg_reachable():
            raise PgUnavailable("postgres port unreachable")
        dispose_checkpointer()
        return get_checkpointer()


def dispose_checkpointer() -> None:
    global _pool, _saver
    _saver = None
    if _pool is not None:
        try:
            _pool.close()
        finally:
            _pool = None


class SchemaVersionError(RuntimeError):
    pass


def _check_version(state: dict) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"thread schema_version={state.get('schema_version')} != {SCHEMA_VERSION}；"
            "首版策略为清空旧 thread（见迁移 §5），禁止静默反序列化为 dict"
        )


def has_snapshot(checkpointer: PostgresSaver, thread_id: str) -> bool:
    snapshot = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
    return snapshot is not None and snapshot.checkpoint is not None


def load_state(checkpointer: PostgresSaver, thread_id: str) -> UnifiedState:
    snapshot = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
    if snapshot is None or snapshot.checkpoint is None:
        return {"schema_version": SCHEMA_VERSION, "route_clarify_count": 0}
    state = snapshot.checkpoint.get("channel_values", {})
    _check_version(state)
    return state
