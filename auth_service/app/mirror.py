from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import insert, select, text, update

from allfusion_auth.crypto import SecretDecryptError, decrypt_secret
from allfusion_auth.db import get_engine
from allfusion_auth.deps import AuthUser, require_db_admin, require_user
from allfusion_auth.models import (
    audit_log, datasource, datasource_mirror, datasource_mirror_table,
    datasource_table,
)
from allfusion_auth.redact import redact_text
from auth_service.app import datasources as ds_mod

router = APIRouter()

_CH_URL = (os.getenv("AF_CH_HTTP") or os.getenv("AF_CH_URL")
           or os.getenv("QG_CH_HTTP")
           or "http://192.168.1.136:8123").rstrip("/")
_CH_USER = os.getenv("AF_CH_USER") or os.getenv("QG_CH_USER") or ""
_CH_PASSWORD = os.getenv("AF_CH_PASSWORD") or os.getenv("QG_CH_PASSWORD") or ""
_CH_READER = (os.getenv("AF_CH_READER") or os.getenv("QG_CH_USER")
              or "da_readonly")
_CONNECT_URL = (os.getenv("AF_KAFKA_CONNECT")
                or "http://192.168.1.135:8083").rstrip("/")
_BROKERS = os.getenv("AF_KAFKA_BROKERS") or "192.168.1.135:9092"
_SOURCE_ID_BASE = 861000           
_DRIFT_INTERVAL_SEC = 300          
_MAX_TABLES = 80
_IDENT_RE = re.compile(r"^[A-Za-z0-9_$]{1,64}$")
_TIME_RE = re.compile(r"^time\((\d*)\)", re.IGNORECASE)
_DEC_RE = re.compile(r"^decimal\((\d+),(\d+)\)", re.IGNORECASE)

_drift_checked_at: dict[int, float] = {}


class MirrorError(RuntimeError):

    def __init__(self, code: str, status: int = 502, message: str = "",
                 http_status: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.status = status
        self.message = message or code
        self.http_status = http_status


def _now() -> datetime:
    return datetime.now()


def _hint(exc: Exception) -> str:
    return redact_text(str(exc))[:300]


def _fail(exc: MirrorError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={
        "error": exc.code, "hint": _hint(exc)})


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]



def ch_database(ds_id: int) -> str:
    return f"af_ds_{int(ds_id)}"


def connector_name(ds_id: int) -> str:
    return f"ds-{int(ds_id)}"


def server_name(ds_id: int, generation: int) -> str:
    return f"af_ds_{int(ds_id)}" if int(generation or 1) <= 1 \
        else f"af_ds_{int(ds_id)}_g{int(generation)}"



def _request(url: str, *, data: bytes | None = None, method: str = "GET",
             headers: dict | None = None, timeout: float = 30) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def _ch(query: str, timeout: float = 30) -> str:
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if _CH_USER:
        token = base64.b64encode(f"{_CH_USER}:{_CH_PASSWORD}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    try:
        status, body = _request(_CH_URL + "/", data=query.encode("utf-8"),
                                method="POST", headers=headers, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise MirrorError("clickhouse_error", 502, detail)
    except (urllib.error.URLError, OSError) as e:
        raise MirrorError("clickhouse_unreachable", 503, str(e))
    if status >= 400 or body.lstrip().startswith("Code:"):
        raise MirrorError("clickhouse_error", 502, body)
    return body


def _ch_json(query: str) -> list[dict]:
    body = _ch(query + "\nFORMAT JSONEachRow")
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def _connect(method: str, path: str, payload: dict | None = None,
             timeout: float = 30) -> tuple[int, str]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        return _request(_CONNECT_URL + path, data=data, method=method,
                        headers=headers, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise MirrorError("kafka_connect_error", 502, detail, http_status=e.code)
    except (urllib.error.URLError, OSError) as e:
        raise MirrorError("kafka_connect_unreachable", 503, str(e))



def _mapping(column_type: str, data_type: str, nullable: bool) -> tuple[str, str]:
    ct = (column_type or "").lower()
    dt = (data_type or "").lower()
    unsigned = "unsigned" in ct
    if dt in ("tinyint", "smallint", "mediumint", "int", "integer", "bigint"):
        bits = {"tinyint": 8, "smallint": 16, "mediumint": 32,
                "int": 32, "integer": 32, "bigint": 64}[dt]
        ch_type = ("UInt" if unsigned else "Int") + str(bits)
        return ch_type, f"JSONExtract(payload, '@@C@@', '{ch_type}')"
    if dt in ("decimal", "numeric"):
        m = _DEC_RE.match(ct)
        scale = int(m.group(2)) if m else 0
        precision = int(m.group(1)) if m else 18
        if precision <= 18:
            cast = "toDecimal64"
        elif precision <= 38:
            cast = "toDecimal128"
        else:
            cast = "toDecimal256"
        return (f"Decimal(38,{scale})",
                f"{cast}(JSONExtract(payload, '@@C@@', 'String'), {scale})")
    if dt in ("float",):
        return "Float32", "toFloat32(JSONExtract(payload, '@@C@@', 'Float64'))"
    if dt in ("double", "real"):
        return "Float64", "JSONExtract(payload, '@@C@@', 'Float64')"
    if dt == "date":
        return "Date", "toDate(JSONExtract(payload, '@@C@@', 'Int32'))"
    if dt in ("datetime", "timestamp"):
        return "DateTime", ("toDateTime(intDiv(JSONExtract(payload, '@@C@@', "
                            "'Int64'), 1000) - 28800)")
    if dt == "time":
        return "Int32", "JSONExtract(payload, '@@C@@', 'Int32')"
    if dt == "year":
        return "UInt16", "toUInt16(JSONExtract(payload, '@@C@@', 'Int32'))"
    if dt == "bit":
        return "UInt64", "JSONExtract(payload, '@@C@@', 'UInt64')"
    return "String", "JSONExtract(payload, '@@C@@', 'String')"


def _column_ddl(col: dict) -> tuple[str, str, str]:
    name = col["name"]
    ch_type, expr = _mapping(col["column_type"], col["data_type"], col["nullable"])
    expr = expr.replace("@@C@@", name)
    if col["nullable"]:
        ch_type = f"Nullable({ch_type})"
        expr = (f"if(JSONExtractRaw(payload, '{name}') = 'null', "
                f"NULL, {expr})")
    return f"`{name}` {ch_type}", f"{expr} AS `{name}`", ch_type


def build_ch_ddl(ds_id: int, table: str, columns: list[dict],
                 pk: list[str], topic: str, generation: int) -> list[str]:
    db = ch_database(ds_id)
    cols, exprs, view_types = [], [], []
    for col in columns:
        ddl, expr, vtype = _column_ddl(col)
        cols.append(ddl)
        exprs.append(expr)
        view_types.append(vtype)
    pks = [c for c in pk if any(c == col["name"] for col in columns)]
    if not pks:
        raise MirrorError("no_primary_key", 400, f"{table} 没有可用主键")
    group = f"ch_{db}_{table}_g{int(generation or 1)}"
    order_by = ", ".join(f"`{c}`" for c in pks)
    all_cols = ",\n    ".join(cols)
    select_list = ",\n    ".join(exprs)
    view_cols = ",\n    ".join(f"`{col['name']}` {vtype}"
                               for col, vtype in zip(columns, view_types))
    arg_list = ",\n    ".join(
        (f"`{col['name']}`" if col["name"] in pks
         else f"argMax(`{col['name']}`, _af_version) AS `{col['name']}`")
        for col in columns)
    group_by = ", ".join(f"`{c}`" for c in pks)
    return [
        f"CREATE DATABASE IF NOT EXISTS `{db}`",
        f"""CREATE TABLE IF NOT EXISTS `{db}`.`kafka_{table}`
(
    `raw` String
)
ENGINE = Kafka
SETTINGS kafka_broker_list = '{_BROKERS}', kafka_topic_list = '{topic}',
    kafka_group_name = '{group}', kafka_format = 'JSONAsString',
    kafka_num_consumers = 1""",
        f"""CREATE TABLE IF NOT EXISTS `{db}`.`{table}`
(
    {all_cols},
    `_af_version` UInt64,
    `_af_deleted` UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(_af_version)
ORDER BY ({order_by})""",
        f"""CREATE MATERIALIZED VIEW IF NOT EXISTS `{db}`.`mv_{table}` TO `{db}`.`{table}`
(
    {all_cols},
    `_af_version` UInt64,
    `_af_deleted` UInt8
)
AS WITH multiIf(JSONExtract(raw, 'op', 'String') = 'd',
                JSONExtractRaw(raw, 'before'), JSONExtractRaw(raw, 'after')) AS payload
SELECT
    {select_list},
    JSONExtract(raw, 'ts_ms', 'UInt64') AS `_af_version`,
    multiIf(JSONExtract(raw, 'op', 'String') = 'd', toUInt8(1), toUInt8(0)) AS `_af_deleted`
FROM `{db}`.`kafka_{table}`
WHERE JSONExtract(raw, 'op', 'String') IN ('c', 'u', 'r', 'd')""",
        f"""CREATE VIEW IF NOT EXISTS `{db}`.`v_{table}`
(
    {view_cols}
)
AS SELECT
    {arg_list}
FROM `{db}`.`{table}`
GROUP BY {group_by}
HAVING argMax(_af_deleted, _af_version) = 0""",
    ]


def drop_ch_ddl(ds_id: int, table: str) -> list[str]:
    db = ch_database(ds_id)
    return [
        f"DROP TABLE IF EXISTS `{db}`.`mv_{table}`",
        f"DROP VIEW IF EXISTS `{db}`.`v_{table}`",
        f"DROP TABLE IF EXISTS `{db}`.`{table}`",
        f"DROP TABLE IF EXISTS `{db}`.`kafka_{table}`",
    ]


def _grant_reader(ds_id: int) -> None:
    reader = _CH_READER.strip()
    if not reader or reader == _CH_USER.strip():
        return
    if not _IDENT_RE.match(reader):
        raise MirrorError("invalid_ch_reader", 500, "AF_CH_READER 含非法字符")
    try:
        _ch(f"GRANT SELECT ON `{ch_database(ds_id)}`.* TO `{reader}`")
    except MirrorError as e:
        raise MirrorError("clickhouse_grant_failed", 502, e.message,
                          http_status=e.http_status)


def build_connector_config(ds_row, cdc_user: str, cdc_password: str,
                           tables: list[str], generation: int) -> dict:
    sname = server_name(ds_row.id, generation)
    dbname = ds_row.database_name
    return {
        "connector.class": "io.debezium.connector.mysql.MySqlConnector",
        "database.hostname": ds_row.host,
        "database.port": str(int(ds_row.port)),
        "database.user": cdc_user,
        "database.password": cdc_password,
        "database.server.id": str(_SOURCE_ID_BASE + int(ds_row.id)),
        "database.server.name": sname,
        "database.include.list": dbname,
        "table.include.list": ",".join(f"{dbname}.{t}" for t in tables),
        "database.history.kafka.bootstrap.servers": _BROKERS,
        "database.history.kafka.topic": f"schema-changes.{sname}",
        "include.schema.changes": "false",
        "tombstones.on.delete": "false",
        "snapshot.mode": "initial",
        "snapshot.fetch.size": "2000",
        "decimal.handling.mode": "string",
        "database.serverTimezone": "Asia/Shanghai",
        "value.converter": "org.apache.kafka.connect.json.JsonConverter",
        "key.converter": "org.apache.kafka.connect.json.JsonConverter",
        "value.converter.schemas.enable": "false",
        "key.converter.schemas.enable": "false",
    }



def _ds_row(conn, ds_id: int, tenant_id: int):
    row = conn.execute(select(datasource).where(
        datasource.c.id == ds_id, datasource.c.tenant_id == tenant_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "datasource_not_found"})
    return row


def _selected_tables(conn, ds_id: int, tenant_id: int) -> list[str]:
    rows = conn.execute(select(datasource_table.c.table_name).where(
        datasource_table.c.datasource_id == ds_id,
        datasource_table.c.tenant_id == tenant_id,
        datasource_table.c.selected == 1).order_by(
            datasource_table.c.table_name.asc())).all()
    return [r.table_name for r in rows]


def _cdc_credentials(row) -> tuple[str, str]:
    if not row.cdc_username or not row.cdc_password_cipher:
        raise HTTPException(status_code=400, detail={
            "error": "cdc_credentials_required",
            "hint": "请先为数据源配置 CDC 复制账号（cdc_username / cdc_password）"})
    try:
        password = decrypt_secret(bytes(row.cdc_password_cipher), row.cdc_key_version)
    except SecretDecryptError:
        raise HTTPException(status_code=400, detail={
            "error": "cdc_password_undecryptable",
            "hint": "CDC 口令密文无法解密，请重新录入"})
    return row.cdc_username, password


def _source_engine(row, user: str, password: str):
    ds = {"db_type": row.db_type, "host": row.host, "port": int(row.port),
          "database_name": row.database_name, "schema_name": row.schema_name,
          "username": user}
    return ds_mod._connect(ds, password)


def _source_columns(conn, schema: str, tables: list[str]) -> dict[str, list[dict]]:
    stmt = text(
        "SELECT table_name AS t, column_name AS c, column_type AS ct, "
        "data_type AS dt, is_nullable AS n, column_key AS k, ordinal_position AS o "
        "FROM information_schema.columns WHERE table_schema = :s "
        "AND table_name IN :names ORDER BY table_name, ordinal_position"
    ).bindparams(sa.bindparam("names", expanding=True))
    out: dict[str, list[dict]] = {}
    for r in conn.execute(stmt, {"s": schema, "names": tables}).all():
        m = r._mapping
        out.setdefault(m["t"], []).append({
            "name": m["c"], "column_type": m["ct"], "data_type": m["dt"],
            "nullable": (m["n"] or "").upper() == "YES",
            "pk": (m["k"] or "").upper() == "PRI",
        })
    missing = [t for t in tables if t not in out]
    if missing:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_tables", "tables": missing,
            "hint": "源库中找不到这些表，请重新初始化表清单"})
    return out


def _preflight(conn) -> list[str]:
    problems: list[str] = []
    wanted = ("log_bin", "binlog_format", "binlog_row_image")
    values: dict[str, str] = {}
    for name in wanted:
        row = conn.execute(text(f"SHOW VARIABLES LIKE '{name}'")).first()
        if row is not None:
            values[name] = str(row[1]).upper()
    if values.get("log_bin") != "ON":
        problems.append("log_bin 未开启")
    if values.get("binlog_format") != "ROW":
        problems.append("binlog_format 需为 ROW")
    if values.get("binlog_row_image") not in (None, "FULL"):
        problems.append("binlog_row_image 需为 FULL")
    grants = " ".join(str(r[0]).upper() for r in conn.execute(text("SHOW GRANTS")).all())
    if "ALL PRIVILEGES" not in grants:
        if not ("REPLICATION SLAVE" in grants or "REPLICATION CLIENT" in grants):
            problems.append("复制账号缺少 REPLICATION SLAVE/CLIENT 权限")
        if "LOCK TABLES" not in grants:
            problems.append("复制账号缺少 LOCK TABLES 权限（快照期一致读）")
    return problems



def _upsert_mirror(conn, ds_id: int, values: dict) -> None:
    values = {**values, "updated_at": _now()}
    hit = conn.execute(update(datasource_mirror).where(
        datasource_mirror.c.datasource_id == ds_id).values(**values)).rowcount
    if not hit:
        conn.execute(insert(datasource_mirror).values(
            datasource_id=ds_id, **values))


def _set_phases(conn, ds_id: int, tables: list[str], phase: str,
                error_msg: str | None = None) -> None:
    now = _now()
    for name in tables:
        hit = conn.execute(update(datasource_mirror_table).where(
            datasource_mirror_table.c.datasource_id == ds_id,
            datasource_mirror_table.c.table_name == name).values(
                phase=phase, error_msg=error_msg, updated_at=now)).rowcount
        if not hit:
            conn.execute(insert(datasource_mirror_table).values(
                datasource_id=ds_id, table_name=name, phase=phase,
                error_msg=error_msg, updated_at=now))



def _connector_state(ds_id: int) -> dict:
    try:
        _, body = _connect("GET", f"/connectors/{connector_name(ds_id)}/status")
    except MirrorError as e:
        if e.http_status == 404:
            return {"state": "missing"}
        raise
    payload = json.loads(body or "{}")
    connector = payload.get("connector") or {}
    tasks = payload.get("tasks") or []
    task_state = tasks[0].get("state") if tasks else None
    trace = tasks[0].get("trace") if tasks else None
    state = connector.get("state") or task_state or "UNKNOWN"
    if task_state == "FAILED":
        state = "FAILED"
    return {"state": str(state).upper(), "trace": trace}


def _ch_counts_and_versions(ds_id: int, tables: list[str]) -> dict[str, dict]:
    db = ch_database(ds_id)
    parts = []
    for name in tables:
        parts.append(
            f"SELECT '{name}' AS t, count() AS n, max(_af_version) AS v "
            f"FROM `{db}`.`{name}`")
    if not parts:
        return {}
    rows = _ch_json("\nUNION ALL\n".join(parts))
    return {r["t"]: {"rows": int(r.get("n") or 0),
                     "ts_ms": int(r.get("v") or 0)} for r in rows}


def _ch_view_columns(ds_id: int, table: str) -> list[str]:
    rows = _ch_json(
        f"SELECT name FROM system.columns WHERE database = '{ch_database(ds_id)}' "
        f"AND table = 'v_{table}' ORDER BY position")
    return [r["name"] for r in rows]


def _drift(ds_id: int, table: str, source_cols: list[dict]) -> bool:
    source_names = [c["name"] for c in source_cols]
    return source_names != _ch_view_columns(ds_id, table)


def _start_connector(ds_id: int, config: dict) -> None:
    name = connector_name(ds_id)
    try:
        state = _connector_state(ds_id)
    except MirrorError:
        state = {"state": "missing"}
    if state["state"] != "missing":
        _connect("DELETE", f"/connectors/{name}")
    _connect("POST", "/connectors", {"name": name, "config": config}, timeout=90)


def refresh_status(ds_id: int, tenant_id: int) -> dict:
    with get_engine().connect() as conn:
        row = _ds_row(conn, ds_id, tenant_id)
        mirror = conn.execute(select(datasource_mirror).where(
            datasource_mirror.c.datasource_id == ds_id)).first()
        tables = _selected_tables(conn, ds_id, tenant_id)
    if not tables or mirror is None:
        return {"exists": mirror is not None, "status": "stopped" if mirror else "none",
                "tables": []}
    generation = int(mirror.generation or 1)
    state = _connector_state(ds_id)
    now_ms = int(time.time() * 1000)
    lag_ms = None
    newest_ts = 0
    status = "running"
    error_msg = None
    phases: dict[str, str] = {}

    if state["state"] == "missing":
        status = "stopped"
    elif state["state"] == "FAILED":
        status = "failed"
        error_msg = redact_text(str(state.get("trace") or "connector failed"))[:500]
    elif state["state"] in ("PAUSED", "UNASSIGNED"):
        status = "stopped"

    try:
        stats = _ch_counts_and_versions(ds_id, tables)
    except MirrorError:
        time.sleep(0.3)                     
        try:
            stats = _ch_counts_and_versions(ds_id, tables)
        except MirrorError as e:
            stats = {}
            if status == "running":
                status = "failed"
                error_msg = ("ClickHouse 不可达，镜像状态未知"
                             if e.code == "clickhouse_unreachable"
                             else f"ClickHouse 查询失败：{_hint(e)}")[:500]

    source_cols = None
    if status == "running":
        try:
            with get_engine().connect() as conn:
                ds = _ds_row(conn, ds_id, tenant_id)
            user, password = _cdc_credentials(ds)
            engine, sconn = _source_engine(ds, user, password)
            try:
                source_cols = _source_columns(sconn, ds.database_name, tables)
            finally:
                sconn.close()
                engine.dispose()
        except Exception:  # noqa: BLE001 源库不可达不阻断状态刷新
            source_cols = None

    for name in tables:
        stat = stats.get(name) or {}
        rows = stat.get("rows") or 0
        ts_ms = stat.get("ts_ms") or 0
        phase = "pending"
        if ts_ms:
            newest_ts = max(newest_ts, ts_ms)
            lag_ms = max(lag_ms or 0, max(0, now_ms - ts_ms))
        if rows > 0:
            phase = "cdc"
        if status == "failed":
            phase = "failed"
        elif source_cols is not None:
            throttle = _drift_checked_at.get(ds_id, 0)
            if time.monotonic() - throttle >= _DRIFT_INTERVAL_SEC:
                try:
                    if _drift(ds_id, name, source_cols.get(name, [])):
                        phase = "drift"
                except MirrorError:
                    pass
        phases[name] = phase

    if source_cols is not None:
        _drift_checked_at[ds_id] = time.monotonic()

    last_event_at = (datetime.fromtimestamp(newest_ts / 1000)
                     if newest_ts else None)
    with get_engine().begin() as conn:
        values = {"status": status, "lag_ms": lag_ms, "error_msg": error_msg,
                  "generation": generation, "last_event_at": last_event_at}
        if status == "running" and all(p == "cdc" for p in phases.values()):
            if mirror.snapshot_at is None:
                values["snapshot_at"] = _now()
        _upsert_mirror(conn, ds_id, values)
        for name in tables:
            _set_phases(conn, ds_id, [name], phases[name],
                        error_msg if phases[name] in ("failed", "drift") else None)

    return {"exists": True, "status": status, "lag_ms": lag_ms,
            "connector": connector_name(ds_id), "generation": generation,
            "snapshot_at": mirror.snapshot_at.timestamp() if mirror.snapshot_at else None,
            "last_event_at": last_event_at.timestamp() if last_event_at else None,
            "error": error_msg,
            "tables": [{"name": n, "phase": phases[n],
                        "rows": (stats.get(n) or {}).get("rows"),
                        "last_event_at": (datetime.fromtimestamp(
                            stats[n]["ts_ms"] / 1000).timestamp()
                            if (stats.get(n) or {}).get("ts_ms") else None)}
                       for n in tables]}



def _validate_table_names(tables: list[str]) -> None:
    bad = [t for t in tables if not _IDENT_RE.match(t)]
    if bad:
        raise HTTPException(status_code=400, detail={
            "error": "unsupported_table_name", "tables": bad,
            "hint": "表名含非字母数字字符，无法建立 ClickHouse 镜像对象"})


def _audit(conn, user: AuthUser, request: Request, action: str, ds_id: int,
           detail: dict) -> None:
    conn.execute(insert(audit_log).values(
        tenant_id=user.tenant_id, user_id=user.user_id, action=action,
        target=f"datasource:{ds_id}"[:128], ip=_client_ip(request),
        detail_json=json.dumps(detail, ensure_ascii=False)[:4000],
        created_at=_now()))


@router.post("/datasources/{ds_id}/mirror/start")
def start_mirror(ds_id: int, request: Request,
                 user: AuthUser = Depends(require_db_admin)) -> dict:
    with get_engine().connect() as conn:
        ds = _ds_row(conn, ds_id, user.tenant_id)
        tables = _selected_tables(conn, ds_id, user.tenant_id)
        mirror = conn.execute(select(datasource_mirror).where(
            datasource_mirror.c.datasource_id == ds_id)).first()
    if (ds.db_type or "").lower() != "mysql":
        raise HTTPException(status_code=400, detail={
            "error": "cdc_unsupported_db_type", "hint": "本版 CDC 只支持 MySQL 数据源"})
    if not tables:
        raise HTTPException(status_code=400, detail={
            "error": "no_selected_tables",
            "hint": "先调用 /datasources/{id}/tables/select 选择要镜像的表"})
    if len(tables) > _MAX_TABLES:
        raise HTTPException(status_code=400, detail={"error": "too_many_tables"})
    _validate_table_names(tables)
    cdc_user, cdc_password = _cdc_credentials(ds)
    if mirror is not None:
        state = _connector_state(ds_id)
        if state["state"] in ("RUNNING", "PAUSED"):
            raise HTTPException(status_code=409, detail={
                "error": "mirror_already_running",
                "hint": "镜像已在运行；如需重建请调用 /mirror/resync"})

    engine, sconn = _source_engine(ds, cdc_user, cdc_password)
    try:
        problems = _preflight(sconn)
        columns = _source_columns(sconn, ds.database_name, tables)
    finally:
        sconn.close()
        engine.dispose()
    if problems:
        raise HTTPException(status_code=409, detail={
            "error": "cdc_preflight_failed", "problems": problems,
            "hint": "源库未满足 CDC 前置条件，请先开启 binlog/ROW/FULL 并授权"})
    no_pk = [t for t in tables
             if not [c["name"] for c in columns[t] if c["pk"]]]
    if no_pk:
        raise HTTPException(status_code=400, detail={
            "error": "no_primary_key", "tables": no_pk,
            "hint": "无主键表无法稳定去重，禁止镜像"})

    generation = int(mirror.generation or 1) if mirror else 1
    sname = server_name(ds_id, generation)
    for table in tables:
        for ddl in build_ch_ddl(ds_id, table, columns[table],
                                [c["name"] for c in columns[table] if c["pk"]],
                                f"{sname}.{ds.database_name}.{table}", generation):
            _ch(ddl)
    _grant_reader(ds_id)

    config = build_connector_config(ds, cdc_user, cdc_password, tables, generation)
    _start_connector(ds_id, config)

    with get_engine().begin() as conn:
        _upsert_mirror(conn, ds_id, {
            "ch_database": ch_database(ds_id),
            "connector_name": connector_name(ds_id),
            "status": "snapshot", "generation": generation,
            "error_msg": None, "snapshot_at": None, "lag_ms": None,
        })
        _set_phases(conn, ds_id, tables, "snapshot")
        _audit(conn, user, request, "datasource.mirror.start", ds_id,
               {"tables": tables, "generation": generation})
    _drift_checked_at.pop(ds_id, None)
    return {"ok": True, "connector": connector_name(ds_id),
            "generation": generation, "tables": tables}


@router.post("/datasources/{ds_id}/mirror/stop")
def stop_mirror(ds_id: int, request: Request,
                user: AuthUser = Depends(require_db_admin)) -> dict:
    with get_engine().connect() as conn:
        _ds_row(conn, ds_id, user.tenant_id)
    state = _connector_state(ds_id)
    if state["state"] != "missing":
        _connect("DELETE", f"/connectors/{connector_name(ds_id)}")
    with get_engine().begin() as conn:
        _upsert_mirror(conn, ds_id, {
            "ch_database": ch_database(ds_id),
            "connector_name": connector_name(ds_id),
            "status": "stopped", "lag_ms": None})
        _audit(conn, user, request, "datasource.mirror.stop", ds_id, {})
    return {"ok": True, "connector": connector_name(ds_id), "status": "stopped"}


@router.post("/datasources/{ds_id}/mirror/resync")
def resync_mirror(ds_id: int, request: Request,
                  user: AuthUser = Depends(require_db_admin)) -> dict:
    with get_engine().connect() as conn:
        ds = _ds_row(conn, ds_id, user.tenant_id)
        tables = _selected_tables(conn, ds_id, user.tenant_id)
        mirror = conn.execute(select(datasource_mirror).where(
            datasource_mirror.c.datasource_id == ds_id)).first()
    if not tables:
        raise HTTPException(status_code=400, detail={"error": "no_selected_tables"})
    _validate_table_names(tables)
    cdc_user, cdc_password = _cdc_credentials(ds)
    generation = (int(mirror.generation or 1) if mirror else 0) + 1
    sname = server_name(ds_id, generation)

    engine, sconn = _source_engine(ds, cdc_user, cdc_password)
    try:
        columns = _source_columns(sconn, ds.database_name, tables)
        problems = _preflight(sconn)
    finally:
        sconn.close()
        engine.dispose()
    if problems:
        raise HTTPException(status_code=409, detail={
            "error": "cdc_preflight_failed", "problems": problems})

    state = _connector_state(ds_id)
    if state["state"] != "missing":
        _connect("DELETE", f"/connectors/{connector_name(ds_id)}")
    for table in tables:
        for ddl in drop_ch_ddl(ds_id, table):
            _ch(ddl)
        for ddl in build_ch_ddl(ds_id, table, columns[table],
                                [c["name"] for c in columns[table] if c["pk"]],
                                f"{sname}.{ds.database_name}.{table}", generation):
            _ch(ddl)
    _grant_reader(ds_id)
    config = build_connector_config(ds, cdc_user, cdc_password, tables, generation)
    _start_connector(ds_id, config)

    with get_engine().begin() as conn:
        _upsert_mirror(conn, ds_id, {
            "ch_database": ch_database(ds_id),
            "connector_name": connector_name(ds_id),
            "status": "snapshot", "generation": generation,
            "error_msg": None, "snapshot_at": None, "lag_ms": None,
        })
        _set_phases(conn, ds_id, tables, "snapshot")
        _audit(conn, user, request, "datasource.mirror.resync", ds_id,
               {"tables": tables, "generation": generation})
    _drift_checked_at.pop(ds_id, None)
    return {"ok": True, "connector": connector_name(ds_id),
            "generation": generation, "tables": tables}


@router.get("/datasources/{ds_id}/mirror")
def mirror_status(ds_id: int, user: AuthUser = Depends(require_user)) -> dict:
    with get_engine().connect() as conn:
        _ds_row(conn, ds_id, user.tenant_id)
        mirror = conn.execute(select(datasource_mirror).where(
            datasource_mirror.c.datasource_id == ds_id)).first()
        tables = _selected_tables(conn, ds_id, user.tenant_id)
    if mirror is None:
        return {"exists": False, "status": "none", "selected_tables": tables,
                "tables": []}
    try:
        data = refresh_status(ds_id, user.tenant_id)
    except MirrorError as e:
        raise _fail(e)
    data["selected_tables"] = tables
    return data
