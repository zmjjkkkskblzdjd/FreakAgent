# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

_ALLFUSION_ROOT = Path(__file__).resolve().parents[2]
if str(_ALLFUSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_ALLFUSION_ROOT))

from allfusion_auth.crypto import (  # noqa: E402
    SecretDecryptError, decrypt_secret,
)
from allfusion_auth.db import get_engine  # noqa: E402
from allfusion_auth.models import (  # noqa: E402
    datasource, datasource_mirror, datasource_mirror_table, datasource_table,
    datasource_write_grant,
)

_CACHE_TTL = 5.0
_cache: dict[tuple, tuple[float, object]] = {}
_cache_lock = threading.Lock()


class DataSourceNotFound(Exception):
    pass


class PasswordUnavailable(Exception):
    pass


def _cached(key: tuple, loader):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def invalidate(datasource_id: int | None = None) -> None:
    with _cache_lock:
        for key in [k for k in _cache if datasource_id is None or k[0] == datasource_id]:
            _cache.pop(key, None)


def load_datasource(dsid: int, tenant_id: int | None = None) -> dict:
    with get_engine().connect() as conn:
        q = select(datasource).where(datasource.c.id == dsid)
        if tenant_id is not None:
            q = q.where(datasource.c.tenant_id == tenant_id)
        row = conn.execute(q).first()
    if row is None or not row.enabled:
        raise DataSourceNotFound(f"数据源 {dsid} 不存在或未启用")
    return {
        "id": int(row.id), "tenant_id": int(row.tenant_id), "name": row.name,
        "db_type": row.db_type, "host": row.host, "port": int(row.port),
        "database_name": row.database_name, "schema_name": row.schema_name,
        "username": row.username, "password_cipher": row.password_cipher,
        "key_version": row.key_version, "max_rows": int(row.max_rows),
        "query_timeout_sec": int(row.query_timeout_sec),
    }


def password_of(ds: dict) -> str:
    try:
        return decrypt_secret(bytes(ds.get("password_cipher") or b""),
                              ds.get("key_version"))
    except SecretDecryptError:
        raise PasswordUnavailable("数据源密码密文无法解密，请管理员重新录入") from None
    except Exception:  # noqa: BLE001 — 密钥未配置等，同样按不可用处理
        raise PasswordUnavailable("数据源密码不可用（密钥配置缺失）") from None


def selected_tables(dsid: int) -> set[str]:
    def _load() -> set[str]:
        with get_engine().connect() as conn:
            rows = conn.execute(select(datasource_table.c.table_name).where(
                datasource_table.c.datasource_id == dsid,
                datasource_table.c.selected == 1)).all()
        return {r.table_name for r in rows}

    try:
        return _cached(("selected", dsid), _load)
    except SQLAlchemyError:
        return set()


def known_tables(dsid: int) -> set[str]:
    def _load() -> set[str]:
        with get_engine().connect() as conn:
            rows = conn.execute(select(datasource_table.c.table_name).where(
                datasource_table.c.datasource_id == dsid)).all()
        return {r.table_name for r in rows}

    try:
        return _cached(("known", dsid), _load)
    except SQLAlchemyError:
        return set()


def mirror_state(dsid: int) -> dict:
    def _load() -> dict:
        with get_engine().connect() as conn:
            row = conn.execute(select(
                datasource_mirror.c.status, datasource_mirror.c.lag_ms,
                datasource_mirror.c.generation, datasource_mirror.c.snapshot_at,
                datasource_mirror.c.last_event_at, datasource_mirror.c.error_msg,
                datasource_mirror.c.ch_database,
            ).where(datasource_mirror.c.datasource_id == dsid)).first()
        if row is None:
            return {}
        return {
            "status": row.status or "",
            "lag_ms": int(row.lag_ms) if row.lag_ms is not None else None,
            "generation": int(row.generation or 1),
            "snapshot_at": row.snapshot_at.isoformat() if row.snapshot_at else None,
            "last_event_at": (row.last_event_at.isoformat()
                              if row.last_event_at else None),
            "error_msg": row.error_msg or "",
            "ch_database": row.ch_database or "",
        }

    try:
        return _cached(("mirror", dsid), _load)
    except OperationalError as e:
        if getattr(e.orig, "args", [None])[0] == 1146:
            return {}
        raise
    except SQLAlchemyError:
        return {}


def table_phases(dsid: int) -> dict[str, str]:
    def _load() -> dict[str, str]:
        with get_engine().connect() as conn:
            rows = conn.execute(select(
                datasource_mirror_table.c.table_name,
                datasource_mirror_table.c.phase).where(
                    datasource_mirror_table.c.datasource_id == dsid)).all()
        return {r.table_name: r.phase for r in rows}

    try:
        return _cached(("phases", dsid), _load)
    except OperationalError as e:
        if getattr(e.orig, "args", [None])[0] == 1146:
            return {}
        raise
    except SQLAlchemyError:
        return {}


def write_grants(dsid: int, user_id: int) -> set[str]:
    def _load() -> set[str]:
        with get_engine().connect() as conn:
            rows = conn.execute(select(datasource_write_grant.c.table_name).where(
                datasource_write_grant.c.datasource_id == dsid,
                datasource_write_grant.c.user_id == user_id)).all()
        return {r.table_name for r in rows}

    try:
        return _cached(("grants", dsid, user_id), _load)
    except SQLAlchemyError:
        return set()
