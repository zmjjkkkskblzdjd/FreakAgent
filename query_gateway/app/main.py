# -*- coding: utf-8 -*-
import asyncio
import time
from datetime import datetime, timezone

import sqlglot
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlglot import exp

from . import chclient, config, mysql_client, platformdb, source_client
from .audit import audit
from .breaker import breaker
from .catalog import catalog
from .guard import GuardError, check_sql
from .internal_auth import verify_signature
from .models import (
    ColumnMeta, ColumnMetaOut, QueryRequest, QueryResponse, TableMeta, TableSchema,
    WriteRequest, WriteResponse, WriteUndo,
)
from .preview import PreviewError, previews, write_locks
from .write_audit import write_audit
from .write_guard import WriteForbidden, check_write_sql, over_write_limit
from .write_journal import journal

app = FastAPI(
    title="query_gateway",
    version="0.1.0",
    description="面向分析 Agent 的数据查询网关：SQL 安检 / LIMIT 保护 / 直读限额与熔断",
)
app.middleware("http")(verify_signature)
app.add_middleware(
    CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["*"], allow_headers=["*"],
)
_sem = asyncio.Semaphore(config.MAX_CONCURRENCY)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


if not config.GATEWAY_SECRET:
    print("[query_gateway] WARN: AF_GATEWAY_SECRET 未配置，内部签名校验已关闭（仅限本机开发）")


@app.get("/health")
def health():
    ok = chclient.ping()
    return {"ok": ok, "ch": config.CH_HTTP, "database": config.CH_DATABASE}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, request: Request):
    if req.datasource_id is not None:
        return await _query_datasource(req, request)

    t0 = time.perf_counter()
    raw_sql = req.sql
    try:
        checked = check_sql(raw_sql, caller_limit=req.max_limit)
    except GuardError as e:
        audit.log(event="query", client=_client_ip(request), ok=False,
                  rejected_by="guard", sql=raw_sql[:1000], error=str(e))
        raise HTTPException(status_code=400, detail={"ok": False, "error_message": str(e)})

    timeout_ms = min(req.timeout_ms, config.MAX_TIMEOUT_MS)

    def _run():
        return chclient.query_json(checked.sql, timeout_ms=timeout_ms)

    try:
        async with _sem:
            result = await asyncio.to_thread(_run)
    except chclient.ClickHouseError as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        audit.log(event="query", client=_client_ip(request), ok=False,
                  rejected_by="clickhouse", sql=checked.sql[:1000],
                  elapsed_ms=elapsed, ch_code=e.ch_code, error=str(e)[:500])
        return QueryResponse(ok=False, executed_sql=checked.sql, elapsed_ms=elapsed,
                             error_code=str(e.ch_code or ""), error_message=str(e),
                             source="clickhouse")

    elapsed = int((time.perf_counter() - t0) * 1000)
    cols = [ColumnMeta(name=m["name"], type=m["type"]) for m in result["meta"]]
    data = result.get("data", [])
    rows = [[r.get(c.name) for c in cols] for r in data]
    truncated = (checked.injected_limit is not None and len(rows) >= checked.injected_limit)

    audit.log(event="query", client=_client_ip(request), ok=True,
              sql=checked.sql[:1000], row_count=len(rows),
              elapsed_ms=elapsed, rewritten=checked.rewritten,
              ch_rows=result.get("rows"), ch_elapsed=result.get("statistics", {}).get("elapsed"))
    return QueryResponse(
        ok=True, columns=cols, rows=rows, row_count=len(rows),
        executed_sql=checked.sql, elapsed_ms=elapsed,
        truncated=truncated, rewritten=checked.rewritten, source="clickhouse",
    )



_ds_sems: dict[int, asyncio.Semaphore] = {}

_INFRA_ERRNOS = {2003, 2006, 2013, 2014, 1317}

_CH_PHASES = {"cdc"}


def _ds_sem(datasource_id: int) -> asyncio.Semaphore:
    sem = _ds_sems.get(datasource_id)
    if sem is None:
        sem = asyncio.Semaphore(config.SOURCE_MAX_CONCURRENCY)
        _ds_sems[datasource_id] = sem
    return sem


def _table_states(datasource_id: int) -> dict[str, str]:
    try:
        return platformdb.table_phases(datasource_id)
    except Exception:  # noqa: BLE001 — 进度表缺失/抖动不阻断直读
        return {}


def _base_table(name: str) -> str:
    return name[2:] if name.startswith("v_") else name


def _ch_database(dsid: int, mirror: dict) -> str:
    return mirror.get("ch_database") or f"af_ds_{dsid}"


def _parse_mysql_tables(raw_sql: str,
                        database: str) -> tuple[exp.Expression, list[str]]:
    sql = (raw_sql or "").strip()
    if not sql:
        raise GuardError("空 SQL")
    if len(sql) > config.MAX_SQL_CHARS:
        raise GuardError(f"SQL 超过最大长度 {config.MAX_SQL_CHARS} 字符")
    try:
        statements = [s for s in sqlglot.parse(sql, read="mysql") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardError(f"SQL 解析失败：{str(e).splitlines()[0][:200]}") from None
    if len(statements) != 1:
        raise GuardError("只允许单条 SELECT，禁止多语句")
    tree = statements[0]
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    names: list[str] = []
    for tbl in tree.find_all(exp.Table):
        tname = tbl.name
        if not tname:
            raise GuardError("禁止以表函数作为数据源")
        if tname.lower() in cte_names:
            continue
        db = (tbl.db or "").strip()
        if db and db.lower() != database.lower():
            raise GuardError(f"表 {tname} 只能引用当前数据源库（{database}）")
        if tname not in names:
            names.append(tname)
    return tree, names


def _rewrite_for_ch(tree: exp.Expression, ch_db: str,
                    mirrored: set[str]) -> str:
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for tbl in tree.find_all(exp.Table):
        tname = tbl.name
        if not tname or tname.lower() in cte_names:
            continue
        base = _base_table(tname)
        target = f"v_{base}" if base in mirrored else base
        tbl.set("this", exp.to_identifier(target))
        tbl.set("db", exp.to_identifier(ch_db))
        tbl.set("catalog", None)
    return tree.sql(dialect="clickhouse") + ";"


def _mirror_note(states: dict[str, str], tables: list[str]) -> str:
    parts = [f"{t}(phase={states.get(t) or 'pending'})" for t in tables]
    return "该表未镜像或镜像不可信：" + "、".join(parts) + "；已转源库限额直读（LIMIT 200/3s）"


async def _direct_query(req: QueryRequest, ds: dict, states: dict[str, str],
                        base: dict, note: str, *, fallback: bool):
    dsid = ds["id"]
    t0 = time.perf_counter()
    if not fallback:
        base["source"] = "mysql-direct"
        base["degraded"] = True
    try:
        password = platformdb.password_of(ds)
    except platformdb.PasswordUnavailable as e:
        audit.log(ok=False, rejected_by="datasource", error=str(e), **base)
        raise HTTPException(status_code=500, detail={
            "ok": False, "error_code": "password_unavailable", "error_message": str(e)})

    known = platformdb.known_tables(dsid)
    aliases = {f"v_{t}": t for t in known}
    try:
        checked = source_client.check_direct_sql(req.sql, ds["database_name"],
                                                 aliases=aliases)
    except GuardError as e:
        audit.log(ok=False, rejected_by="direct_guard", sql=req.sql[:1000],
                  error=str(e), **base)
        raise HTTPException(status_code=400, detail={
            "ok": False, "error_code": "degrade_query_not_allowed",
            "error_message": f"{e}（{note}）"})

    if known and checked.table not in known:
        audit.log(ok=False, rejected_by="direct_guard", sql=req.sql[:1000],
                  error=f"unknown_table: {checked.table}", **base)
        raise HTTPException(status_code=400, detail={
            "ok": False, "error_code": "unknown_table",
            "error_message": f"表 {checked.table} 不在该数据源的表清单中"})

    allowed, state = breaker.allow(dsid)
    if not allowed:
        audit.log(ok=False, rejected_by="breaker", state=state, sql=checked.sql[:1000], **base)
        return QueryResponse(ok=False, executed_sql=checked.sql, source="mysql-direct",
                             degraded=True, breaker_state=state,
                             error_code="source_unavailable",
                             error_message="分析服务繁忙（源库直读已熔断），请稍后再试",
                             source_table_states=states, mirror_note=note)

    sem = _ds_sem(dsid)
    timeout_ms = min(req.timeout_ms, config.SOURCE_TIMEOUT_MS)
    try:
        await asyncio.wait_for(sem.acquire(), timeout=config.SOURCE_QUEUE_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        audit.log(ok=False, rejected_by="source_busy", **base)
        return QueryResponse(ok=False, source="mysql-direct", degraded=True,
                             breaker_state=breaker.state_name(dsid),
                             error_code="source_busy",
                             error_message="源库直读排队超时，请稍后再试",
                             source_table_states=states, mirror_note=note)
    try:
        result = await asyncio.to_thread(
            source_client.query, ds, password, checked.sql, timeout_ms)
    except source_client.SourceTimeout as e:
        breaker.failure(dsid)
        elapsed = int((time.perf_counter() - t0) * 1000)
        audit.log(ok=False, rejected_by="source_timeout", sql=checked.sql[:1000],
                  elapsed_ms=elapsed, error=str(e)[:500], **base)
        if fallback:
            raise HTTPException(status_code=503, detail={
                "ok": False, "error_code": "mirror_table_unavailable",
                "error_message": f"镜像不可用且源库直读超时：{e}",
                "source_table_states": states, "mirror_note": note})
        return QueryResponse(ok=False, executed_sql=checked.sql, elapsed_ms=elapsed,
                             source="mysql-direct", degraded=True,
                             breaker_state=breaker.state_name(dsid),
                             error_code="source_timeout", error_message=str(e),
                             source_table_states=states, mirror_note=note)
    except source_client.SourceError as e:
        infra = e.errno in _INFRA_ERRNOS
        if infra:
            breaker.failure(dsid)
        elapsed = int((time.perf_counter() - t0) * 1000)
        audit.log(ok=False, rejected_by="source", errno=e.errno, sql=checked.sql[:1000],
                  elapsed_ms=elapsed, error=str(e)[:500], **base)
        if infra and fallback:
            raise HTTPException(status_code=503, detail={
                "ok": False, "error_code": "mirror_table_unavailable",
                "error_message": f"镜像不可用且源库不可达：{e}",
                "source_table_states": states, "mirror_note": note})
        if infra:
            return QueryResponse(ok=False, executed_sql=checked.sql, elapsed_ms=elapsed,
                                 source="mysql-direct", degraded=True,
                                 breaker_state=breaker.state_name(dsid),
                                 error_code="source_unavailable",
                                 error_message=str(e), source_table_states=states,
                                 mirror_note=note)
        raise HTTPException(status_code=400, detail={
            "ok": False, "error_code": "query_error", "error_message": str(e)[:500]})
    finally:
        sem.release()

    breaker.success(dsid)
    elapsed = int((time.perf_counter() - t0) * 1000)
    cols = [ColumnMeta(name=c["name"], type=c["type"]) for c in result["columns"]]
    audit.log(ok=True, sql=checked.sql[:1000], row_count=len(result["rows"]),
              elapsed_ms=elapsed, rewritten=checked.rewritten, **base)
    return QueryResponse(
        ok=True, columns=cols, rows=result["rows"], row_count=len(result["rows"]),
        executed_sql=checked.sql, elapsed_ms=elapsed,
        truncated=result["truncated"], rewritten=checked.rewritten,
        source="mysql-direct", degraded=True, breaker_state=breaker.state_name(dsid),
        source_table_states=states, mirror_note=note,
    )


async def _query_datasource(req: QueryRequest, request: Request):
    t0 = time.perf_counter()
    client = _client_ip(request)
    dsid = req.datasource_id
    base = {"event": "query", "client": client, "datasource_id": dsid,
            "tenant_id": req.tenant_id}
    try:
        ds = platformdb.load_datasource(dsid, req.tenant_id)
    except platformdb.DataSourceNotFound as e:
        audit.log(ok=False, rejected_by="datasource", error=str(e), **base)
        raise HTTPException(status_code=404, detail={
            "ok": False, "error_code": "datasource_not_found", "error_message": str(e)})

    states = _table_states(dsid)
    try:
        tree, names = _parse_mysql_tables(req.sql, ds["database_name"])
    except GuardError as e:
        audit.log(ok=False, rejected_by="guard", sql=req.sql[:1000], error=str(e), **base)
        raise HTTPException(status_code=400, detail={
            "ok": False, "error_code": "invalid_sql", "error_message": str(e)})

    known = platformdb.known_tables(dsid)
    unknown = [n for n in names if known and _base_table(n) not in known]
    if unknown:
        msg = f"表 {', '.join(unknown)} 不在该数据源的表清单中"
        audit.log(ok=False, rejected_by="guard", sql=req.sql[:1000],
                  error=f"unknown_table: {unknown}", **base)
        raise HTTPException(status_code=400, detail={
            "ok": False, "error_code": "unknown_table", "error_message": msg})

    selected = platformdb.selected_tables(dsid)
    mirrored = {_base_table(n) for n in names} & selected
    mirrored = {t for t in mirrored if states.get(t) in _CH_PHASES}
    all_mirrored = bool(names) and len(mirrored) == len(
        {_base_table(n) for n in names})

    if req.fresh:
        return await _direct_query(req, ds, states, base,
                                   "fresh=true：读取源库最新数据", fallback=False)

    if not all_mirrored:
        missing = sorted({_base_table(n) for n in names} - mirrored)
        return await _direct_query(req, ds, states, base,
                                   _mirror_note(states, missing), fallback=True)

    mirror = platformdb.mirror_state(dsid)
    status = mirror.get("status") or ""
    lag_ms = mirror.get("lag_ms")

    if status == "failed":
        return await _direct_query(
            req, ds, states, base,
            "镜像同步失败（status=failed）：禁止返回 CH 旧数据，转源库限额直读",
            fallback=True)
    if status != "running":
        return await _direct_query(
            req, ds, states, base,
            f"镜像未运行（status={status or 'none'}）：转源库限额直读", fallback=True)

    mirror_note = ""
    if lag_ms is not None and lag_ms > config.FRESH_LAG_MS:
        mirror_note = (f"镜像最近事件距今 {lag_ms}ms（表暂无新写入时该值会持续增长，"
                       f"仅供参考；阈值 {config.FRESH_LAG_MS}ms）")

    ch_db = _ch_database(dsid, mirror)
    ch_sql = _rewrite_for_ch(tree, ch_db, mirrored)
    try:
        checked = check_sql(ch_sql, caller_limit=req.max_limit,
                            allowed_databases={ch_db},
                            required_prefix=ds["database_name"])
    except GuardError as e:
        audit.log(ok=False, rejected_by="guard", sql=ch_sql[:1000], error=str(e), **base)
        raise HTTPException(status_code=400, detail={
            "ok": False, "error_code": "degrade_query_not_allowed", "error_message": str(e)})
    except chclient.ClickHouseError as e:
        audit.log(ok=False, rejected_by="clickhouse", sql=ch_sql[:1000],
                  error=str(e)[:500], **base)
        raise HTTPException(status_code=503, detail={
            "ok": False, "error_code": "analytics_unavailable",
            "error_message": ("分析库不可用，默认不降级到源库；"
                              "如需最新数据请显式传 fresh=true（受限直读）"),
            "lag_ms": lag_ms, "source_table_states": states}) from None

    timeout_ms = min(req.timeout_ms, config.MAX_TIMEOUT_MS)

    def _run():
        return chclient.query_json(checked.sql, timeout_ms=timeout_ms)

    try:
        async with _sem:
            result = await asyncio.to_thread(_run)
    except chclient.ClickHouseError as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        unreachable = str(e).startswith("无法连接 ClickHouse")
        audit.log(ok=False, rejected_by="clickhouse", sql=checked.sql[:1000],
                  elapsed_ms=elapsed, ch_code=e.ch_code, error=str(e)[:500], **base)
        if unreachable:
            raise HTTPException(status_code=503, detail={
                "ok": False, "error_code": "analytics_unavailable",
                "error_message": ("分析库不可用，默认不降级到源库；"
                                  "如需最新数据请显式传 fresh=true（受限直读）"),
                "lag_ms": lag_ms, "source_table_states": states})
        return QueryResponse(ok=False, executed_sql=checked.sql, elapsed_ms=elapsed,
                             error_code=str(e.ch_code or ""), error_message=str(e),
                             source="clickhouse", lag_ms=lag_ms,
                             source_table_states=states, mirror_note=mirror_note)

    elapsed = int((time.perf_counter() - t0) * 1000)
    cols = [ColumnMeta(name=m["name"], type=m["type"]) for m in result["meta"]]
    data = result.get("data", [])
    rows = [[r.get(c.name) for c in cols] for r in data]
    truncated = (checked.injected_limit is not None and len(rows) >= checked.injected_limit)

    audit.log(ok=True, sql=checked.sql[:1000], row_count=len(rows),
              elapsed_ms=elapsed, rewritten=checked.rewritten, lag_ms=lag_ms,
              ch_rows=result.get("rows"),
              ch_elapsed=result.get("statistics", {}).get("elapsed"), **base)
    return QueryResponse(
        ok=True, columns=cols, rows=rows, row_count=len(rows),
        executed_sql=checked.sql, elapsed_ms=elapsed,
        truncated=truncated, rewritten=checked.rewritten,
        source="clickhouse", lag_ms=lag_ms, degraded=False,
        source_table_states=states, mirror_note=mirror_note,
    )


def _recount(wc) -> int:
    if wc.insert_rows is not None:
        return wc.insert_rows
    return mysql_client.count(wc.count_sql(), wc.database)


@app.post("/write", response_model=WriteResponse)
async def write(req: WriteRequest, request: Request):
    if req.datasource_id is not None:
        return await _write_datasource(req, request)
    return await _write_legacy(req, request)


@app.post("/write/{audit_id}/undo")
async def undo_write_op(audit_id: str, req: WriteUndo, request: Request):
    client = _client_ip(request)

    def _fail(status: int, code: str, message: str):
        raise HTTPException(status_code=status, detail={
            "ok": False, "error_code": code, "error_message": message,
            "audit_id": audit_id})

    rec = journal.get(audit_id, tenant_id=req.tenant_id, user_id=req.user_id)
    if rec is None:
        _fail(404, "undo_not_found", "找不到可撤回的写操作（或不属于当前用户）")
    if rec["status"] != "committed":
        _fail(409, "undo_not_available", "该写操作已撤回或正在撤回")
    if time.time() > rec["undo_deadline"]:
        _fail(409, "undo_expired", "已超过撤回时限（确认后 1 小时）")
    if rec.get("datasource_id") is None:
        _fail(409, "undo_unsupported", "旧通道写操作不支持撤回")

    try:
        ds = platformdb.load_datasource(rec["datasource_id"], req.tenant_id)
        password = platformdb.password_of(ds)
    except platformdb.DataSourceNotFound as e:
        _fail(404, "datasource_not_found", str(e))
    except platformdb.PasswordUnavailable as e:
        _fail(500, "password_unavailable", str(e))

    try:
        done = await asyncio.to_thread(source_client.undo_write, ds, password, rec)
    except source_client.UndoConflict as e:
        write_audit.log(event="write_undo", client=client, sql=rec["sql"][:1000],
                        status="rejected", audit_id=audit_id,
                        tenant_id=req.tenant_id, user_id=req.user_id,
                        datasource_id=rec["datasource_id"], database=rec["database"],
                        table=rec["table_name"], operation=rec["operation"],
                        error=str(e))
        _fail(409, "undo_conflict", str(e))
    except (source_client.SourceError, source_client.SourceTimeout) as e:
        write_audit.log(event="write_undo", client=client, sql=rec["sql"][:1000],
                        status="error", audit_id=audit_id,
                        tenant_id=req.tenant_id, user_id=req.user_id,
                        datasource_id=rec["datasource_id"], database=rec["database"],
                        table=rec["table_name"], operation=rec["operation"],
                        error=str(e)[:300])
        _fail(503, "source_unavailable", f"撤回执行失败：{str(e)[:300]}")

    journal.mark_undone(audit_id)
    write_audit.log(event="write_undo", client=client, sql=rec["sql"][:1000],
                    status="ok", audit_id=audit_id, actual_rows=done,
                    tenant_id=req.tenant_id, user_id=req.user_id,
                    datasource_id=rec["datasource_id"], database=rec["database"],
                    table=rec["table_name"], operation=rec["operation"])
    return {"ok": True, "audit_id": audit_id, "inverse_rows": done,
            "undone_at": datetime.now(tz=timezone.utc).isoformat()}


async def _write_legacy(req: WriteRequest, request: Request):
    t0 = time.perf_counter()
    client = _client_ip(request)
    base = {"target": "", "database": "", "table": "", "operation": "", "estimated_rows": 0}
    wc = None                 
    mutation_status = ""      

    try:
        try:
            wc = check_write_sql(req.sql)
        except GuardError as e:
            aid = write_audit.log(event="write", client=client, sql=req.sql[:1000],
                                  status="rejected", error=str(e), **base)
            raise HTTPException(status_code=400, detail={
                "ok": False, "error_code": "guard_rejected",
                "error_message": str(e), "audit_id": aid})
        except Exception as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            aid = write_audit.log(event="write", client=client, sql=req.sql[:1000],
                                  status="error", error=f"guard 基础设施异常: {str(e)[:300]}",
                                  duration_ms=elapsed, **base)
            return WriteResponse(ok=False, audit_id=aid, error_code="precheck_failed",
                                 error_message=f"写前检查基础设施异常：{str(e)[:300]}",
                                 elapsed_ms=elapsed, **base)
        base.update(target=wc.target, database=wc.database, table=wc.table,
                    operation=wc.operation)

        try:
            estimated = await asyncio.to_thread(_recount, wc)
        except (chclient.ClickHouseError, mysql_client.MySQLWriteError) as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            aid = write_audit.log(event="write", client=client, sql=wc.normalized_sql[:1000],
                                  status="error", error=f"precheck: {e}", duration_ms=elapsed, **base)
            return WriteResponse(ok=False, audit_id=aid, error_code="precheck_failed",
                                 error_message=f"行数复核失败：{e}", elapsed_ms=elapsed, **base)
        base["estimated_rows"] = estimated

        if over_write_limit(estimated):
            msg = (f"预估影响 {estimated} 行，超过单次上限 {config.MAX_WRITE_ROWS}，已拒绝执行；"
                   "请缩小 WHERE 范围分批操作，或由管理员手工执行")
            aid = write_audit.log(event="write", client=client, sql=wc.normalized_sql[:1000],
                                  status="rejected", error=msg, **base)
            return WriteResponse(ok=False, audit_id=aid, error_code="too_many_rows",
                                 error_message=msg, **base)

        if req.dry_run:
            aid = write_audit.log(event="write", client=client, sql=wc.normalized_sql[:1000],
                                  status="dry_run", actual_rows=0, **base)
            return WriteResponse(ok=True, audit_id=aid, actual_rows=0, **base)

        try:
            actual = await asyncio.to_thread(mysql_client.execute, wc.normalized_sql, wc.database)
        except mysql_client.MySQLWriteError as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            aid = write_audit.log(event="write", client=client, sql=wc.normalized_sql[:1000],
                                  status="error", error=str(e)[:500],
                                  duration_ms=elapsed, **base)
            return WriteResponse(ok=False, audit_id=aid, error_code="mysql_error",
                                 error_message=str(e)[:500], elapsed_ms=elapsed, **base)

        elapsed = int((time.perf_counter() - t0) * 1000)
        aid = write_audit.log(event="write", client=client, sql=wc.normalized_sql[:1000],
                              status="ok", actual_rows=actual, mutation_status=mutation_status,
                              duration_ms=elapsed, **base)
        return WriteResponse(ok=True, audit_id=aid, actual_rows=actual,
                             mutation_status=mutation_status, elapsed_ms=elapsed, **base)
    except HTTPException:
        raise   
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        write_audit.log(event="write", client=client,
                        sql=(wc.normalized_sql if wc else req.sql)[:1000],
                        status="error", error=f"unhandled: {str(e)[:300]}",
                        mutation_status=mutation_status, duration_ms=elapsed, **base)
        raise



def _recount_ds(ds: dict, password: str, wc) -> int:
    if wc.insert_rows is not None:
        return wc.insert_rows
    return source_client.count(ds, password, wc.count_sql(), config.SOURCE_TIMEOUT_MS)


def _ds_write_allowed(dsid: int, req: WriteRequest) -> set[str]:
    selected = platformdb.selected_tables(dsid)
    if req.role in ("owner", "admin"):
        return selected
    return selected & platformdb.write_grants(dsid, int(req.user_id or 0))


async def _write_datasource(req: WriteRequest, request: Request):
    t0 = time.perf_counter()
    client = _client_ip(request)
    dsid = req.datasource_id
    base = {"target": "mysql", "database": "", "table": "", "operation": "",
            "estimated_rows": 0}
    actor = {"datasource_id": dsid, "tenant_id": req.tenant_id, "user_id": req.user_id}
    wc = None

    def _audit(**fields) -> str:
        return write_audit.log(event="write", client=client, **base, **actor, **fields)

    try:
        if req.tenant_id is None or req.user_id is None:
            aid = _audit(sql=req.sql[:1000], status="rejected", error="actor_required")
            raise HTTPException(status_code=400, detail={
                "ok": False, "error_code": "actor_required",
                "error_message": "数据源写通道必须由会话注入 tenant_id / user_id",
                "audit_id": aid})

        try:
            ds = platformdb.load_datasource(dsid, req.tenant_id)
        except platformdb.DataSourceNotFound as e:
            aid = _audit(sql=req.sql[:1000], status="rejected", error=str(e))
            raise HTTPException(status_code=404, detail={
                "ok": False, "error_code": "datasource_not_found",
                "error_message": str(e), "audit_id": aid})
        try:
            password = platformdb.password_of(ds)
        except platformdb.PasswordUnavailable as e:
            aid = _audit(sql=req.sql[:1000], status="error", error=str(e))
            raise HTTPException(status_code=500, detail={
                "ok": False, "error_code": "password_unavailable",
                "error_message": str(e), "audit_id": aid})
        base["database"] = ds["database_name"]

        try:
            wc = check_write_sql(req.sql, allowed_tables=_ds_write_allowed(dsid, req),
                                 database=ds["database_name"])
        except WriteForbidden as e:
            aid = _audit(sql=req.sql[:1000], status="rejected", error=str(e))
            raise HTTPException(status_code=403, detail={
                "ok": False, "error_code": "write_forbidden",
                "error_message": str(e), "audit_id": aid})
        except GuardError as e:
            aid = _audit(sql=req.sql[:1000], status="rejected", error=str(e))
            raise HTTPException(status_code=400, detail={
                "ok": False, "error_code": "guard_rejected",
                "error_message": str(e), "audit_id": aid})
        except Exception as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            aid = _audit(sql=req.sql[:1000], status="error",
                         error=f"guard 基础设施异常: {str(e)[:300]}", duration_ms=elapsed)
            return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                 error_code="precheck_failed",
                                 error_message=f"写前检查基础设施异常：{str(e)[:300]}",
                                 elapsed_ms=elapsed, **base)
        base.update(target=wc.target, database=wc.database, table=wc.table,
                    operation=wc.operation)

        allowed, bstate = breaker.allow(dsid)
        if not allowed:
            aid = _audit(sql=wc.normalized_sql[:1000], status="rejected",
                         error="source_unavailable", breaker_state=bstate)
            return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                 breaker_state=bstate,
                                 error_code="source_unavailable",
                                 error_message="分析服务繁忙（源库直写已熔断），请稍后再试",
                                 **base)

        try:
            estimated = await asyncio.to_thread(_recount_ds, ds, password, wc)
        except source_client.SourceTimeout as e:
            breaker.failure(dsid)
            elapsed = int((time.perf_counter() - t0) * 1000)
            aid = _audit(sql=wc.normalized_sql[:1000], status="error",
                         error=f"precheck: {str(e)[:400]}", duration_ms=elapsed,
                         breaker_state=breaker.state_name(dsid))
            return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                 breaker_state=breaker.state_name(dsid),
                                 error_code="source_timeout",
                                 error_message=str(e), elapsed_ms=elapsed, **base)
        except source_client.SourceError as e:
            infra = e.errno in _INFRA_ERRNOS
            if infra:
                breaker.failure(dsid)
            elapsed = int((time.perf_counter() - t0) * 1000)
            aid = _audit(sql=wc.normalized_sql[:1000], status="error",
                         error=f"precheck: {str(e)[:400]}", errno=e.errno,
                         duration_ms=elapsed)
            return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                 breaker_state=breaker.state_name(dsid),
                                 error_code="source_unavailable" if infra else "precheck_failed",
                                 error_message=f"行数复核失败：{e}", elapsed_ms=elapsed, **base)
        breaker.success(dsid)
        base["estimated_rows"] = estimated

        if over_write_limit(estimated):
            msg = (f"预估影响 {estimated} 行，超过单次上限 {config.MAX_WRITE_ROWS}，已拒绝执行；"
                   "请缩小 WHERE 范围分批操作，或由管理员手工执行")
            aid = _audit(sql=wc.normalized_sql[:1000], status="rejected", error=msg)
            return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                 breaker_state=breaker.state_name(dsid),
                                 error_code="too_many_rows", error_message=msg, **base)

        preview_data = None
        if req.dry_run and req.transactional_preview:
            try:
                preview_data = await asyncio.to_thread(
                    source_client.transactional_preview, ds, password,
                    wc.normalized_sql, config.PREVIEW_SAMPLE_ROWS)
            except source_client.WritePreviewError as e:
                aid = _audit(sql=wc.normalized_sql[:1000], status="rejected", error=e.code)
                return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                     error_code=e.code, error_message=str(e), **base)
            except source_client.SourceTimeout as e:
                breaker.failure(dsid)
                aid = _audit(sql=wc.normalized_sql[:1000], status="error",
                             error=f"preview: {str(e)[:400]}")
                return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                     error_code="source_timeout", error_message=str(e), **base)
            except source_client.SourceError as e:
                aid = _audit(sql=wc.normalized_sql[:1000], status="error",
                             error=f"preview: {str(e)[:400]}")
                return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                     error_code="preview_failed", error_message=str(e), **base)

        if req.dry_run:
            try:
                token = previews.issue(tenant_id=req.tenant_id, user_id=req.user_id,
                                       datasource_id=dsid, sql=wc.normalized_sql,
                                       estimated_rows=estimated, data=preview_data)
            except PreviewError as e:
                aid = _audit(sql=wc.normalized_sql[:1000], status="rejected", error=str(e))
                raise HTTPException(status_code=e.status, detail={
                    "ok": False, "error_code": e.code, "error_message": str(e),
                    "audit_id": aid})
            aid = _audit(sql=wc.normalized_sql[:1000], status="dry_run",
                         actual_rows=0, preview_issued=True)
            pv = preview_data or {}
            return WriteResponse(ok=True, audit_id=aid, actual_rows=0, preview_token=token,
                                 preview_id=token,
                                 preview_hash=pv.get("preview_hash", ""),
                                 preview_rows=pv.get("after_rows", []),
                                 before_rows=pv.get("before_rows", []),
                                 affected_rows=pv.get("affected_rows", 0),
                                 auto_pk=bool(pv.get("auto_pk")),
                                 source="mysql", breaker_state=breaker.state_name(dsid),
                                 **base)

        if not req.preview_token:
            aid = _audit(sql=wc.normalized_sql[:1000], status="rejected",
                         error="preview_token_required")
            raise HTTPException(status_code=400, detail={
                "ok": False, "error_code": "preview_token_required",
                "error_message": "执行写操作前必须先 dry_run 获取 preview_token",
                "audit_id": aid})
        try:
            entry = previews.consume(req.preview_token, tenant_id=req.tenant_id,
                                     user_id=req.user_id, datasource_id=dsid,
                                     sql=wc.normalized_sql)
        except PreviewError as e:
            aid = _audit(sql=wc.normalized_sql[:1000], status="rejected", error=str(e))
            raise HTTPException(status_code=e.status, detail={
                "ok": False, "error_code": e.code, "error_message": str(e),
                "audit_id": aid})

        if entry.data:
            try:
                await asyncio.to_thread(source_client.check_preview_unchanged,
                                        ds, password, entry.data)
            except source_client.WritePreviewError as e:
                aid = _audit(sql=wc.normalized_sql[:1000], status="rejected", error=e.code)
                raise HTTPException(status_code=409, detail={
                    "ok": False, "error_code": e.code, "error_message": str(e),
                    "audit_id": aid})
            except source_client.SourceError as e:
                aid = _audit(sql=wc.normalized_sql[:1000], status="error",
                             error=f"recheck: {str(e)[:300]}")
                raise HTTPException(status_code=409, detail={
                    "ok": False, "error_code": "preview_recheck_failed",
                    "error_message": str(e), "audit_id": aid})

        lock = await asyncio.to_thread(write_locks.acquire, dsid, wc.table)
        if lock is None:
            aid = _audit(sql=wc.normalized_sql[:1000], status="rejected", error="write_busy")
            raise HTTPException(status_code=429, detail={
                "ok": False, "error_code": "write_busy",
                "error_message": "同一张表正在写入，请稍后再试", "audit_id": aid})
        captured = None
        try:
            if entry.data and entry.data.get("auto_pk"):
                captured = await asyncio.to_thread(
                    source_client.execute_insert_capture, ds, password,
                    wc.normalized_sql, entry.data)
                actual = captured["affected_rows"]
            else:
                actual = await asyncio.to_thread(source_client.execute, ds, password,
                                                 wc.normalized_sql)
        except source_client.WritePreviewError as e:
            aid = _audit(sql=wc.normalized_sql[:1000], status="error", error=e.code)
            return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                 error_code=e.code, error_message=str(e), **base)
        except source_client.SourceTimeout as e:
            breaker.failure(dsid)
            elapsed = int((time.perf_counter() - t0) * 1000)
            aid = _audit(sql=wc.normalized_sql[:1000], status="error",
                         error=str(e)[:500], duration_ms=elapsed,
                         breaker_state=breaker.state_name(dsid))
            return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                 breaker_state=breaker.state_name(dsid),
                                 error_code="source_timeout",
                                 error_message=str(e), elapsed_ms=elapsed, **base)
        except source_client.SourceError as e:
            infra = e.errno in _INFRA_ERRNOS
            if infra:
                breaker.failure(dsid)
            elapsed = int((time.perf_counter() - t0) * 1000)
            aid = _audit(sql=wc.normalized_sql[:1000], status="error",
                         error=str(e)[:500], errno=e.errno, duration_ms=elapsed)
            return WriteResponse(ok=False, audit_id=aid, source="mysql",
                                 breaker_state=breaker.state_name(dsid),
                                 error_code="source_unavailable" if infra else "mysql_error",
                                 error_message=str(e)[:500], elapsed_ms=elapsed, **base)
        finally:
            write_locks.release(lock)

        breaker.success(dsid)
        elapsed = int((time.perf_counter() - t0) * 1000)
        aid = _audit(sql=wc.normalized_sql[:1000], status="ok", actual_rows=actual,
                     duration_ms=elapsed, preview_consumed=True)
        undo_deadline, undo_available = "", False
        after_rows = (captured["after_rows"] if captured
                      else (entry.data or {}).get("after_rows", []))
        if entry.data and (not captured or after_rows):
            info = journal.record(
                audit_id=aid, tenant_id=req.tenant_id, user_id=req.user_id,
                datasource_id=dsid, database=wc.database, table=wc.table,
                operation=wc.operation, sql=wc.normalized_sql,
                preview_hash=entry.data["preview_hash"],
                pk_cols=entry.data["pk_cols"],
                before_rows=entry.data["before_rows"],
                after_rows=after_rows)
            undo_deadline = datetime.fromtimestamp(
                info["undo_deadline"], tz=timezone.utc).isoformat()
            undo_available = True
        return WriteResponse(ok=True, audit_id=aid, actual_rows=actual,
                             source="mysql", breaker_state=breaker.state_name(dsid),
                             elapsed_ms=elapsed, undo_deadline=undo_deadline,
                             undo_available=undo_available, **base)
    except HTTPException:
        raise   
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        write_audit.log(event="write", client=client,
                        sql=(wc.normalized_sql if wc else req.sql)[:1000],
                        status="error", error=f"unhandled: {str(e)[:300]}",
                        duration_ms=elapsed, **base, **actor)
        raise



def _load_ds_or_404(datasource_id: int, tenant_id: int | None) -> dict:
    try:
        return platformdb.load_datasource(datasource_id, tenant_id)
    except platformdb.DataSourceNotFound as e:
        raise HTTPException(status_code=404, detail={
            "ok": False, "error_code": "datasource_not_found", "error_message": str(e)})


def _ds_table_meta(logical_db: str, base: str, phase: str,
                   ch_meta: TableMeta | None) -> TableMeta:
    if ch_meta is not None:
        return ch_meta.model_copy(update={
            "database": logical_db,
            "name": f"v_{base}",
            "full_name": f"{logical_db}.v_{base}",
            "dedup_hint": "",
        })
    hint = (f"镜像不可查（phase={phase or 'pending'}）：查询将转源库限额直读"
            "（LIMIT 200 / 3s，禁聚合/JOIN）")
    return TableMeta(database=logical_db, name=base, full_name=f"{logical_db}.{base}",
                     engine="MySQL(源库)", total_rows=None, comment="",
                     is_view=False, dedup_hint=hint)


def _ds_catalog_items(ds: dict) -> list[TableMeta]:
    dsid = ds["id"]
    logical = ds["database_name"]
    states = _table_states(dsid)
    selected = platformdb.selected_tables(dsid)
    known = platformdb.known_tables(dsid) or set()
    ch_db = f"af_ds_{dsid}"
    by_name: dict[str, TableMeta] = {}
    try:
        by_name = {t.name: t for t in catalog.list_tables(ch_db)}
    except chclient.ClickHouseError:
        by_name = {}   
    items = []
    for base in sorted(known):
        cdc = (base in selected and states.get(base) == "cdc"
               and f"v_{base}" in by_name)
        items.append(_ds_table_meta(logical, base, states.get(base, ""),
                                    by_name.get(f"v_{base}") if cdc else None))
    return items


@app.get("/catalog/databases")
def databases(datasource_id: int | None = Query(default=None, ge=1),
              tenant_id: int | None = Query(default=None, ge=0)):
    if datasource_id is None:
        return {"databases": sorted(config.ALLOWED_DATABASES)}
    ds = _load_ds_or_404(datasource_id, tenant_id)
    return {"databases": [ds["database_name"]], "datasource_id": ds["id"]}


@app.get("/catalog/tables")
def tables(database: str = Query(default=None), refresh: bool = False,
           datasource_id: int | None = Query(default=None, ge=1),
           tenant_id: int | None = Query(default=None, ge=0)):
    if datasource_id is not None:
        ds = _load_ds_or_404(datasource_id, tenant_id)
        items = _ds_catalog_items(ds)
        return {"database": ds["database_name"], "datasource_id": ds["id"],
                "tables": [t.model_dump() for t in items]}
    db = database or config.CH_DATABASE
    try:
        items = catalog.list_tables(db, refresh=refresh)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"database": db, "tables": [t.model_dump() for t in items]}


@app.get("/catalog/schema")
def schema(database: str = Query(default=None), table: str = Query(...),
           refresh: bool = False,
           datasource_id: int | None = Query(default=None, ge=1),
           tenant_id: int | None = Query(default=None, ge=0)):
    if datasource_id is not None:
        return _ds_schema(datasource_id, tenant_id, table, refresh)
    db = database or config.CH_DATABASE
    try:
        result = catalog.get_schema(db, table, refresh=refresh)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result.model_dump()


def _ds_schema(datasource_id: int, tenant_id: int | None, table: str,
               refresh: bool) -> dict:
    ds = _load_ds_or_404(datasource_id, tenant_id)
    dsid = ds["id"]
    logical = ds["database_name"]
    states = _table_states(dsid)
    selected = platformdb.selected_tables(dsid)
    known = platformdb.known_tables(dsid)
    base = _base_table(table)
    if known and base not in known:
        raise HTTPException(status_code=404, detail=f"表 {table} 不在该数据源的表清单中")

    if table.startswith("v_"):
        if not (base in selected and states.get(base) == "cdc"):
            raise HTTPException(status_code=404, detail=(
                f"视图 {table} 不可用（phase={states.get(base) or 'pending'}）："
                "镜像未就绪时请查询源表名，网关会转源库限额直读"))
        try:
            result = catalog.get_schema(f"af_ds_{dsid}", table, refresh=refresh)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        result.table = result.table.model_copy(update={
            "database": logical, "name": table,
            "full_name": f"{logical}.{table}", "dedup_hint": ""})
        return result.model_dump()

    try:
        password = platformdb.password_of(ds)
    except platformdb.PasswordUnavailable as e:
        raise HTTPException(status_code=500, detail={
            "ok": False, "error_code": "password_unavailable", "error_message": str(e)})
    try:
        cols = source_client.describe(ds, password, base)
    except source_client.SourceError as e:
        raise HTTPException(status_code=503, detail={
            "ok": False, "error_code": "source_unavailable", "error_message": str(e)})
    if not cols:
        raise HTTPException(status_code=404, detail=f"表 {base} 在源库不存在")
    return TableSchema(
        table=_ds_table_meta(logical, base, states.get(base, ""), None),
        columns=[ColumnMetaOut(name=c["name"], type=c["type"],
                               comment=c["comment"], position=c["position"])
                 for c in cols],
    ).model_dump()


@app.get("/catalog/summary")
def summary(refresh: bool = False,
            datasource_id: int | None = Query(default=None, ge=1),
            tenant_id: int | None = Query(default=None, ge=0)):
    if datasource_id is not None:
        ds = _load_ds_or_404(datasource_id, tenant_id)
        items = [t for t in _ds_catalog_items(ds) if t.is_view]
        logical = ds["database_name"]
        lines = [f"数据库 {logical}（数据源镜像，只读，ClickHouse）："
                 f"共 {len(items)} 个可查镜像视图",
                 "取数约定：只列 phase=cdc 的 v_ 视图（已内置 argMax 去重 + 墓碑过滤），"
                 "不要用 FINAL；未列出的表不可查镜像，需 fresh=true 走源库限额直读"
                 "（LIMIT 200 / 3s，禁聚合/JOIN）。", ""]
        for t in items:
            rows = f"{t.total_rows:,} 行" if t.total_rows is not None else "-"
            block = [f"## {t.full_name}  [{t.engine}, {rows}]"]
            if t.comment:
                block.append(f"   说明：{t.comment}")
            try:
                detail = catalog.get_schema(f"af_ds_{ds['id']}", t.name)
                for c in detail.columns:
                    cm = f"  -- {c.comment}" if c.comment else ""
                    block.append(f"   - {c.name} {c.type}{cm}")
            except (ValueError, chclient.ClickHouseError):
                pass
            lines.append("\n".join(block))
            lines.append("")
        return {"database": logical, "datasource_id": ds["id"], "text": "\n".join(lines)}

    items = catalog.list_tables(config.CH_DATABASE, refresh=refresh)
    lines = [f"数据库 {config.CH_DATABASE}（ClickHouse，只读）：共 {len(items)} 个表/视图",
             "取数约定：事实/维度表为 ReplacingMergeTree 多版本表，"
             "请优先查 v_ 开头的最新版本视图（已内置 argMax 去重），不要用 FINAL。", ""]
    for t in sorted(items, key=lambda x: (not x.is_view, x.name)):
        rows = f"{t.total_rows:,} 行" if t.total_rows is not None else "-"
        header = f"## {t.full_name}  [{t.engine}, {rows}]"
        block = [header]
        if t.comment:
            block.append(f"   说明：{t.comment}")
        if t.dedup_hint:
            block.append(f"   ⚠ {t.dedup_hint}")
        try:
            detail = catalog.get_schema(t.database, t.name)
            comments = {c.name: c.comment for c in detail.columns}
            if not any(comments.values()) and t.name.startswith("v_"):
                try:
                    phys = catalog.get_schema(t.database, t.name[2:])
                    comments = {c.name: c.comment for c in phys.columns}
                except ValueError:
                    pass
            for c in detail.columns:
                cm = f"  -- {comments.get(c.name, '')}" if comments.get(c.name) else ""
                block.append(f"   - {c.name} {c.type}{cm}")
        except ValueError:
            pass
        lines.append("\n".join(block))
        lines.append("")
    return {"database": config.CH_DATABASE, "text": "\n".join(lines)}



@app.post("/catalog/refresh")
def refresh_catalog():
    catalog.invalidate()
    return {"ok": True, "message": "catalog cache invalidated"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=False)
