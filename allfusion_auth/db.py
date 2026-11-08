from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

ALLFUSION_ROOT = Path(__file__).resolve().parents[1]

try:  
    from dotenv import load_dotenv

    load_dotenv(ALLFUSION_ROOT / ".env")
except ImportError:  # pragma: no cover - 依赖缺失时保持可用
    pass

_engine: Engine | None = None


def mysql_dsn() -> str:
    dsn = (os.getenv("AF_MYSQL_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("AF_MYSQL_DSN 未配置；请参考 .env.example 生成 .env")
    return dsn


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            mysql_dsn(),
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=int(os.getenv("AF_MYSQL_POOL", "5")),
            max_overflow=int(os.getenv("AF_MYSQL_POOL_OVERFLOW", "5")),
            future=True,
        )
    return _engine


def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def ping() -> bool:
    from sqlalchemy import text

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — 探活只回布尔
        return False
