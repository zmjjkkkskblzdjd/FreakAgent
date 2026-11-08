from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
from datetime import datetime
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, delete, insert, select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import NullPool

from allfusion_auth.crypto import (
    SecretDecryptError, decrypt_secret, encrypt_secret,
)
from allfusion_auth.db import get_engine
from allfusion_auth.deps import AuthUser, require_db_admin, require_user
from allfusion_auth.models import (
    app_user, audit_log, datasource, datasource_mirror_table, datasource_table,
    datasource_write_grant,
)
from allfusion_auth.redact import redact_text

router = APIRouter()

_SUPPORTED = {"mysql", "postgresql"}
_DEFAULT_PORT = {"mysql": 3306, "postgresql": 5432}
_TIMEOUT_SEC = 5
_TEST_MIN_INTERVAL = 2.0
_MAX_TABLES = 80
_SAMPLE_ROWS = 3
_SAMPLE_LEN = 100
_SENSITIVE_COL = re.compile(
    r"(pass(word|wd)?|pwd|secret|token|api[_-]?key|salt|credential|cert|private[_-]?key|hash)",
    re.IGNORECASE)
_ALLOW_PRIVATE = os.getenv("AF_DS_ALLOW_PRIVATE", "1") != "0"

_last_test: dict[tuple[int, int, str], float] = {}


def _now() -> datetime:
    return datetime.now()



def _check_host(host: str) -> str:
    h = (host or "").strip()
    if not h:
        raise HTTPException(status_code=400, detail={"error": "invalid_host"})
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError:
        raise HTTPException(status_code=400, detail={
            "error": "host_unresolvable", "hint": "主机名无法解析"})
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise HTTPException(status_code=400, detail={
                "error": "ssrf_blocked", "hint": "不允许回环/链路本地/元数据地址"})
        if ip.is_private and not _ALLOW_PRIVATE:
            raise HTTPException(status_code=400, detail={
                "error": "ssrf_blocked", "hint": "内网地址需显式开启 AF_DS_ALLOW_PRIVATE"})
    return h


def _url(ds: dict, password: str) -> str:
    scheme = {"mysql": "mysql+pymysql", "postgresql": "postgresql+psycopg"}.get(ds["db_type"])
    if scheme is None:
        raise HTTPException(status_code=400, detail={
            "error": "unsupported_db_type", "hint": "暂只支持 mysql / postgresql"})
    host = ds["host"].strip()
    port = int(ds["port"] or _DEFAULT_PORT.get(ds["db_type"], 0))
    db = quote_plus(ds["database_name"].strip())
    user = quote_plus(ds["username"].strip())
    pwd = quote_plus(password or "")
    return f"{scheme}://{user}:{pwd}@{host}:{port}/{db}"


def _connect(ds: dict, password: str):
    ds = {**ds, "host": _check_host(ds["host"])}
    url = _url(ds, password)
    connect_args = {"connect_timeout": _TIMEOUT_SEC}
    engine = create_engine(url, poolclass=NullPool, connect_args=connect_args)
    return engine, engine.connect()


def _payload(row) -> dict:
    return {
        "id": int(row.id), "name": row.name, "db_type": row.db_type,
        "host": row.host, "port": int(row.port),
        "database_name": row.database_name, "schema_name": row.schema_name,
        "username": row.username, "has_password": bool(row.password_cipher),
        "cdc_username": row.cdc_username,
        "has_cdc_password": bool(row.cdc_password_cipher),
        "max_rows": int(row.max_rows), "query_timeout_sec": int(row.query_timeout_sec),
        "enabled": bool(row.enabled),
        "created_at": row.created_at.timestamp() if row.created_at else None,
    }


def _row(conn, ds_id: int, tenant_id: int):
    row = conn.execute(select(datasource).where(
        datasource.c.id == ds_id, datasource.c.tenant_id == tenant_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "datasource_not_found"})
    return row


def _password_of(row, provided: str | None) -> str:
    if provided:
        return provided
    if not row.password_cipher:
        return ""
    try:
        return decrypt_secret(bytes(row.password_cipher), row.key_version)
    except SecretDecryptError:
        raise HTTPException(status_code=400, detail={
            "error": "password_undecryptable", "hint": "密码密文无法解密，请重新录入"})
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"error": "password_unavailable"})


def _rate_limit(user: AuthUser, action: str) -> None:
    key = (user.tenant_id, user.user_id, action)
    now = time.monotonic()
    if now - _last_test.get(key, 0) < _TEST_MIN_INTERVAL:
        raise HTTPException(status_code=429, detail={
            "error": "too_many_tests", "hint": "请求过于频繁，请稍后再试"})
    _last_test[key] = now


def _hint(exc: Exception) -> str:
    return redact_text(str(exc))[:300]



@router.get("/datasources")
def list_datasources(user: AuthUser = Depends(require_user)) -> dict:
    if user.dev_mode:
        return {"datasources": []}
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(datasource).where(datasource.c.tenant_id == user.tenant_id)
            .order_by(datasource.c.id.asc())).all()
    return {"datasources": [_payload(r) for r in rows]}


class DataSourceIn(BaseModel):
    name: str
    db_type: str
    host: str
    port: int | None = None
    database_name: str
    schema_name: str | None = None
    username: str
    password: str | None = None
    cdc_username: str | None = None
    cdc_password: str | None = None
    max_rows: int = 1000
    query_timeout_sec: int = 30
    enabled: int = 1


def _validate(req: DataSourceIn) -> dict:
    name = (req.name or "").strip()[:64]
    if not name:
        raise HTTPException(status_code=400, detail={"error": "invalid_name"})
    db_type = (req.db_type or "").strip().lower()
    if db_type not in _SUPPORTED:
        raise HTTPException(status_code=400, detail={
            "error": "unsupported_db_type", "hint": "暂只支持 mysql / postgresql"})
    if not (req.host or "").strip() or not (req.database_name or "").strip():
        raise HTTPException(status_code=400, detail={"error": "invalid_target"})
    return {
        "name": name, "db_type": db_type,
        "host": req.host.strip()[:255],
        "port": int(req.port or _DEFAULT_PORT[db_type]),
        "database_name": req.database_name.strip()[:128],
        "schema_name": (req.schema_name or "").strip()[:128] or None,
        "username": (req.username or "").strip()[:128],
        "max_rows": max(1, min(int(req.max_rows or 1000), 10000)),
        "query_timeout_sec": max(1, min(int(req.query_timeout_sec or 30), 300)),
    }


@router.post("/datasources")
def create_datasource(req: DataSourceIn, user: AuthUser = Depends(require_db_admin)) -> dict:
    values = _validate(req)
    if not values["username"]:
        raise HTTPException(status_code=400, detail={"error": "invalid_username"})
    cipher, version = (None, 1)
    if req.password:
        cipher, version = encrypt_secret(req.password)
    cdc_user = (req.cdc_username or "").strip()[:128] or None
    cdc_cipher, cdc_version = (None, 1)
    if cdc_user and req.cdc_password:
        cdc_cipher, cdc_version = encrypt_secret(req.cdc_password)
    now = _now()
    try:
        with get_engine().begin() as conn:
            ds_id = conn.execute(insert(datasource).values(
                tenant_id=user.tenant_id, **values,
                password_cipher=cipher, key_version=version,
                cdc_username=cdc_user, cdc_password_cipher=cdc_cipher,
                cdc_key_version=cdc_version,
                options_json=None, enabled=1 if req.enabled else 0,
                created_by=user.user_id, created_at=now,
            )).inserted_primary_key[0]
    except IntegrityError:
        raise HTTPException(status_code=409, detail={
            "error": "datasource_exists", "hint": "同名数据源已存在"})
    with get_engine().connect() as conn:
        return {"ok": True, "datasource": _payload(_row(conn, int(ds_id), user.tenant_id))}


class DataSourcePatchIn(BaseModel):
    name: str | None = None
    db_type: str | None = None
    host: str | None = None
    port: int | None = None
    database_name: str | None = None
    schema_name: str | None = None
    username: str | None = None
    password: str | None = None        
    cdc_username: str | None = None
    cdc_password: str | None = None    
    max_rows: int | None = None
    query_timeout_sec: int | None = None
    enabled: int | None = None


@router.patch("/datasources/{ds_id}")
def patch_datasource(ds_id: int, req: DataSourcePatchIn,
                     user: AuthUser = Depends(require_db_admin)) -> dict:
    values: dict = {}
    _limits = {"name": 64, "host": 255, "database_name": 128,
               "schema_name": 128, "username": 128}
    for field, limit in _limits.items():
        value = getattr(req, field)
        if value is None:
            continue
        text_value = value.strip()[:limit]
        values[field] = text_value or None
    if "name" in values and not values["name"]:
        raise HTTPException(status_code=400, detail={"error": "invalid_name"})
    for required in ("host", "database_name", "username"):
        if required in values and not values[required]:
            raise HTTPException(status_code=400, detail={"error": "invalid_target"})
    if req.db_type is not None:
        db_type = req.db_type.strip().lower()
        if db_type not in _SUPPORTED:
            raise HTTPException(status_code=400, detail={"error": "unsupported_db_type"})
        values["db_type"] = db_type
    if req.port is not None:
        values["port"] = int(req.port)
    if req.max_rows is not None:
        values["max_rows"] = max(1, min(int(req.max_rows), 10000))
    if req.query_timeout_sec is not None:
        values["query_timeout_sec"] = max(1, min(int(req.query_timeout_sec), 300))
    if req.enabled is not None:
        values["enabled"] = 1 if req.enabled else 0
    if req.password:
        cipher, version = encrypt_secret(req.password)
        values["password_cipher"] = cipher
        values["key_version"] = version
    if req.cdc_username is not None:
        cdc_user = req.cdc_username.strip()[:128]
        values["cdc_username"] = cdc_user or None
        if not cdc_user:
            values["cdc_password_cipher"] = None
            values["cdc_key_version"] = 1
    if req.cdc_password:
        cdc_cipher, cdc_version = encrypt_secret(req.cdc_password)
        values["cdc_password_cipher"] = cdc_cipher
        values["cdc_key_version"] = cdc_version
    try:
        with get_engine().begin() as conn:
            hit = conn.execute(update(datasource).where(
                datasource.c.id == ds_id,
                datasource.c.tenant_id == user.tenant_id).values(**values)).rowcount
    except IntegrityError:
        raise HTTPException(status_code=409, detail={
            "error": "datasource_exists", "hint": "同名数据源已存在"})
    if not hit:
        raise HTTPException(status_code=404, detail={"error": "datasource_not_found"})
    with get_engine().connect() as conn:
        return {"ok": True, "datasource": _payload(_row(conn, ds_id, user.tenant_id))}


@router.delete("/datasources/{ds_id}")
def delete_datasource(ds_id: int, user: AuthUser = Depends(require_db_admin)) -> dict:
    with get_engine().begin() as conn:
        hit = conn.execute(delete(datasource).where(
            datasource.c.id == ds_id,
            datasource.c.tenant_id == user.tenant_id)).rowcount
        if hit:
            conn.execute(delete(datasource_table).where(
                datasource_table.c.datasource_id == ds_id,
                datasource_table.c.tenant_id == user.tenant_id))
    if not hit:
        raise HTTPException(status_code=404, detail={"error": "datasource_not_found"})
    return {"ok": True}



class TestIn(BaseModel):
    id: int | None = None                  
    db_type: str | None = None
    host: str | None = None
    port: int | None = None
    database_name: str | None = None
    schema_name: str | None = None
    username: str | None = None
    password: str | None = None


@router.post("/datasources/test")
def test_datasource(req: TestIn, user: AuthUser = Depends(require_db_admin)) -> dict:
    _rate_limit(user, "test")
    if req.id:
        with get_engine().connect() as conn:
            row = _row(conn, int(req.id), user.tenant_id)
        ds = {"db_type": row.db_type, "host": req.host or row.host,
              "port": req.port or int(row.port),
              "database_name": req.database_name or row.database_name,
              "schema_name": row.schema_name or req.schema_name,
              "username": req.username or row.username}
        password = _password_of(row, req.password)
    else:
        if not (req.db_type and req.host and req.database_name and req.username):
            raise HTTPException(status_code=400, detail={"error": "incomplete_target"})
        ds = {"db_type": req.db_type.strip().lower(), "host": req.host,
              "port": req.port, "database_name": req.database_name,
              "schema_name": req.schema_name, "username": req.username}
        password = req.password or ""
    started = time.monotonic()
    try:
        engine, conn = _connect(ds, password)
        try:
            conn.execute(text("SELECT 1"))
        finally:
            conn.close()
            engine.dispose()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 数据库错误按字符串脱敏后回传
        return {"ok": False, "error": "connect_failed", "hint": _hint(e)}
    return {"ok": True, "latency_ms": int((time.monotonic() - started) * 1000)}



def _q(db_type: str) -> str:
    return "`" if db_type == "mysql" else '"'


def _collect(ds: dict, password: str) -> list[dict]:
    engine, conn = _connect(ds, password)
    try:
        if ds["db_type"] == "mysql":
            schema = ds["database_name"]
            table_sql = text(
                "SELECT table_name AS name, table_comment AS comment "
                "FROM information_schema.tables WHERE table_schema=:s "
                "AND table_type='BASE TABLE' ORDER BY table_name LIMIT " + str(_MAX_TABLES))
            col_sql = text(
                "SELECT table_name AS t, column_name AS c, data_type AS d, "
                "column_comment AS cm FROM information_schema.columns "
                "WHERE table_schema=:s ORDER BY table_name, ordinal_position")
        else:
            schema = ds.get("schema_name") or "public"
            table_sql = text(
                "SELECT table_name AS name, '' AS comment FROM information_schema.tables "
                "WHERE table_schema=:s AND table_type='BASE TABLE' "
                "ORDER BY table_name LIMIT " + str(_MAX_TABLES))
            col_sql = text(
                "SELECT table_name AS t, column_name AS c, data_type AS d, '' AS cm "
                "FROM information_schema.columns WHERE table_schema=:s "
                "ORDER BY table_name, ordinal_position")
        tables = [dict(r._mapping) for r in conn.execute(table_sql, {"s": schema}).all()]
        cols: dict[str, list[dict]] = {}
        for r in conn.execute(col_sql, {"s": schema}).all():
            m = r._mapping
            cols.setdefault(m["t"], []).append(
                {"name": m["c"], "type": m["d"], "comment": m["cm"] or ""})
        for table in tables:
            name = table["name"]
            table["columns"] = cols.get(name, [])
            table["samples"] = {}
            try:
                rows = conn.execute(text(
                    f"SELECT * FROM {_q(ds['db_type'])}{schema}{_q(ds['db_type'])}."
                    f"{_q(ds['db_type'])}{name}{_q(ds['db_type'])} LIMIT {_SAMPLE_ROWS}")
                ).mappings().all()
            except Exception:  # noqa: BLE001 单表失败不影响其它表
                rows = []
            for row in rows:
                for key, value in row.items():
                    bucket = table["samples"].setdefault(key, [])
                    if _SENSITIVE_COL.search(str(key)):
                        if not bucket:
                            bucket.append("***")
                        continue
                    text_value = "" if value is None else str(value)[:_SAMPLE_LEN]
                    if text_value and text_value not in bucket and len(bucket) < 3:
                        bucket.append(text_value)
        return tables
    finally:
        conn.close()
        engine.dispose()


@router.post("/datasources/{ds_id}/init")
def init_datasource(ds_id: int, user: AuthUser = Depends(require_db_admin)) -> dict:
    _rate_limit(user, "init")
    with get_engine().connect() as conn:
        row = _row(conn, ds_id, user.tenant_id)
    password = _password_of(row, None)
    ds = {"db_type": row.db_type, "host": row.host, "port": int(row.port),
          "database_name": row.database_name, "schema_name": row.schema_name,
          "username": row.username}
    try:
        tables = _collect(ds, password)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "init_failed", "hint": _hint(e)}
    now = _now()
    with get_engine().begin() as conn:
        conn.execute(delete(datasource_table).where(
            datasource_table.c.datasource_id == ds_id,
            datasource_table.c.tenant_id == user.tenant_id))
        for table in tables:
            conn.execute(insert(datasource_table).values(
                datasource_id=ds_id, tenant_id=user.tenant_id,
                table_name=table["name"][:128],
                table_comment=((table.get("comment") or "").strip()[:255] or None),
                selected=0,
                columns_json=json.dumps({
                    "columns": table["columns"], "samples": table["samples"]},
                    ensure_ascii=False),
                updated_at=now,
            ))
    return {"ok": True, "table_count": len(tables),
            "tables": [t["name"] for t in tables]}


@router.get("/datasources/{ds_id}/tables")
def list_tables(ds_id: int, user: AuthUser = Depends(require_user)) -> dict:
    with get_engine().connect() as conn:
        _row(conn, ds_id, user.tenant_id)
        rows = conn.execute(
            select(datasource_table)
            .where(datasource_table.c.datasource_id == ds_id,
                   datasource_table.c.tenant_id == user.tenant_id)
            .order_by(datasource_table.c.table_name.asc())).all()
    return {"tables": [{
        "id": int(r.id), "table_name": r.table_name,
        "table_comment": r.table_comment, "selected": bool(r.selected),
        "columns_json": r.columns_json, "updated_at": r.updated_at.timestamp()
        if r.updated_at else None,
    } for r in rows]}



class SelectTablesIn(BaseModel):
    tables: list[str]
    selected: bool = True


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


@router.post("/datasources/{ds_id}/tables/select")
def select_tables(ds_id: int, req: SelectTablesIn, request: Request,
                  user: AuthUser = Depends(require_db_admin)) -> dict:
    names: list[str] = []
    for raw in req.tables:
        name = (raw or "").strip()[:128]
        if name and name not in names:
            names.append(name)
    if not names:
        raise HTTPException(status_code=400, detail={"error": "empty_tables"})
    if len(names) > _MAX_TABLES:
        raise HTTPException(status_code=400, detail={
            "error": "too_many_tables", "hint": f"单次最多 {_MAX_TABLES} 张表"})
    now = _now()
    flag = 1 if req.selected else 0
    with get_engine().begin() as conn:
        _row(conn, ds_id, user.tenant_id)
        existing = {r.table_name for r in conn.execute(
            select(datasource_table.c.table_name).where(
                datasource_table.c.datasource_id == ds_id,
                datasource_table.c.tenant_id == user.tenant_id,
                datasource_table.c.table_name.in_(names))).all()}
        missing = [n for n in names if n not in existing]
        if missing:
            raise HTTPException(status_code=400, detail={
                "error": "unknown_tables", "tables": missing,
                "hint": "先调用 /datasources/{id}/init 初始化表清单"})
        conn.execute(update(datasource_table).where(
            datasource_table.c.datasource_id == ds_id,
            datasource_table.c.tenant_id == user.tenant_id,
            datasource_table.c.table_name.in_(names),
        ).values(selected=flag, updated_at=now))
        mirror = "skipped"
        if flag:
            try:
                conn.execute(mysql_insert(datasource_mirror_table)
                             .prefix_with("IGNORE")
                             .values([{"datasource_id": ds_id, "table_name": n,
                                       "phase": "pending", "updated_at": now}
                                      for n in names]))
                mirror = "pending"
            except OperationalError as e:
                if getattr(e.orig, "args", [None])[0] != 1146:
                    raise
                mirror = "ddl_missing"
        conn.execute(insert(audit_log).values(
            tenant_id=user.tenant_id, user_id=user.user_id,
            action="datasource.tables.select", target=f"datasource:{ds_id}"[:128],
            ip=_client_ip(request),
            detail_json=json.dumps({"tables": names, "selected": bool(flag),
                                    "mirror": mirror}, ensure_ascii=False)[:4000],
            created_at=now))
    return {"ok": True, "datasource_id": ds_id, "selected": bool(flag),
            "tables": names, "mirror": mirror}



def _is_db_admin(user: AuthUser) -> bool:
    return user.is_db_admin


def _clean_tables(raw: list[str]) -> list[str]:
    names: list[str] = []
    for item in raw:
        name = (item or "").strip()[:128]
        if name and name not in names:
            names.append(name)
    return names


def _require_member(conn, tenant_id: int, user_id: int) -> str:
    row = conn.execute(select(app_user.c.username, app_user.c.status).where(
        app_user.c.id == user_id, app_user.c.tenant_id == tenant_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "user_not_found"})
    if int(row.status or 0) != 1:
        raise HTTPException(status_code=400, detail={"error": "user_disabled"})
    return row.username


@router.get("/datasources/{ds_id}/grants")
def list_grants(ds_id: int, user: AuthUser = Depends(require_user)) -> dict:
    with get_engine().connect() as conn:
        _row(conn, ds_id, user.tenant_id)
        stmt = select(datasource_write_grant).where(
            datasource_write_grant.c.datasource_id == ds_id)
        if not _is_db_admin(user):
            stmt = stmt.where(datasource_write_grant.c.user_id == user.user_id)
        rows = conn.execute(stmt.order_by(
            datasource_write_grant.c.user_id.asc(),
            datasource_write_grant.c.table_name.asc())).all()
    return {"datasource_id": ds_id,
            "owner": _is_db_admin(user),
            "grants": [{"user_id": int(r.user_id),
                        "table_name": r.table_name,
                        "granted_by": int(r.granted_by or 0),
                        "created_at": r.created_at.timestamp()
                        if r.created_at else None} for r in rows]}


class GrantIn(BaseModel):
    user_id: int
    tables: list[str]


@router.post("/datasources/{ds_id}/grants")
def add_grants(ds_id: int, req: GrantIn, request: Request,
               user: AuthUser = Depends(require_db_admin)) -> dict:
    names = _clean_tables(req.tables)
    if not names:
        raise HTTPException(status_code=400, detail={"error": "empty_tables"})
    if len(names) > _MAX_TABLES:
        raise HTTPException(status_code=400, detail={"error": "too_many_tables"})
    now = _now()
    with get_engine().begin() as conn:
        _row(conn, ds_id, user.tenant_id)
        member = _require_member(conn, user.tenant_id, req.user_id)
        selected = {r.table_name for r in conn.execute(
            select(datasource_table.c.table_name).where(
                datasource_table.c.datasource_id == ds_id,
                datasource_table.c.tenant_id == user.tenant_id,
                datasource_table.c.selected == 1,
                datasource_table.c.table_name.in_(names))).all()}
        unknown = [n for n in names if n not in selected]
        if unknown:
            raise HTTPException(status_code=400, detail={
                "error": "tables_not_selected", "tables": unknown,
                "hint": "只能给已选入镜像范围的表授权写权限"})
        conn.execute(mysql_insert(datasource_write_grant)
                     .prefix_with("IGNORE")
                     .values([{"datasource_id": ds_id, "user_id": req.user_id,
                               "table_name": n, "granted_by": user.user_id,
                               "created_at": now} for n in names]))
        conn.execute(insert(audit_log).values(
            tenant_id=user.tenant_id, user_id=user.user_id,
            action="datasource.grants.add", target=f"datasource:{ds_id}"[:128],
            ip=_client_ip(request),
            detail_json=json.dumps({"user_id": req.user_id, "username": member,
                                    "tables": names}, ensure_ascii=False)[:4000],
            created_at=now))
    return {"ok": True, "datasource_id": ds_id, "user_id": req.user_id,
            "tables": names}


@router.delete("/datasources/{ds_id}/grants")
def remove_grants(ds_id: int, req: GrantIn, request: Request,
                  user: AuthUser = Depends(require_db_admin)) -> dict:
    names = _clean_tables(req.tables)
    now = _now()
    with get_engine().begin() as conn:
        _row(conn, ds_id, user.tenant_id)
        stmt = delete(datasource_write_grant).where(
            datasource_write_grant.c.datasource_id == ds_id,
            datasource_write_grant.c.user_id == req.user_id)
        if names:
            stmt = stmt.where(datasource_write_grant.c.table_name.in_(names))
        removed = conn.execute(stmt).rowcount or 0
        conn.execute(insert(audit_log).values(
            tenant_id=user.tenant_id, user_id=user.user_id,
            action="datasource.grants.remove", target=f"datasource:{ds_id}"[:128],
            ip=_client_ip(request),
            detail_json=json.dumps({"user_id": req.user_id, "tables": names,
                                    "removed": removed}, ensure_ascii=False)[:4000],
            created_at=now))
    return {"ok": True, "datasource_id": ds_id, "user_id": req.user_id,
            "removed": removed}
