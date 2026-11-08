# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal

import pymysql
import sqlglot
from pymysql.constants import FIELD_TYPE
from sqlglot import exp

from . import config
from .guard import BANNED_FUNC_NAMES, BANNED_FUNC_PREFIXES, BANNED_TABLE_FUNCS, GuardError


class SourceError(Exception):

    def __init__(self, message: str, errno: int | None = None) -> None:
        super().__init__(message)
        self.errno = errno


class SourceTimeout(SourceError):
    pass


_TYPE_NAMES = {v: k for k, v in vars(FIELD_TYPE).items() if isinstance(v, int)}
_FORBIDDEN_RE = re.compile(
    r"\b(FOR\s+UPDATE|LOCK\s+IN\s+SHARE\s+MODE|INTO\s+OUTFILE|INTO\s+DUMPFILE|"
    r"FORMAT|PROCEDURE\s+ANALYSE)\b", re.IGNORECASE)


@dataclass
class DirectCheck:
    sql: str
    limit: int
    rewritten: bool
    table: str


def check_direct_sql(raw_sql: str, database: str | None = None,
                     aliases: dict[str, str] | None = None) -> DirectCheck:
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
    if not isinstance(tree, exp.Select):
        raise GuardError(f"直读只允许单表 SELECT，检测到 {type(tree).__name__}")

    for node in tree.walk():
        if isinstance(node, (exp.Group, exp.Having, exp.Distinct, exp.Window,
                             exp.Subquery, exp.Join, exp.Union, exp.CTE)):
            raise GuardError(
                "降级直读禁止 GROUP BY/聚合/DISTINCT/窗口/JOIN/子查询/UNION/CTE")
        if isinstance(node, exp.AggFunc):
            raise GuardError("降级直读禁止聚合函数")
        if isinstance(node, exp.Anonymous):
            fname = (node.name or "").lower()
            if fname in BANNED_TABLE_FUNCS:
                raise GuardError(f"禁止使用表函数 {fname}()")
            if fname in BANNED_FUNC_NAMES or any(
                    fname.startswith(p) for p in BANNED_FUNC_PREFIXES):
                raise GuardError(f"禁止使用函数 {fname}()")
        elif isinstance(node, exp.Func):
            fname = (getattr(node, "name", "") or "").lower()
            if fname in BANNED_FUNC_NAMES:
                raise GuardError(f"禁止使用函数 {fname}()")

    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1:
        raise GuardError("直读只允许引用一张表（禁止 JOIN / 多表）")
    tbl = tables[0]
    if not tbl.name:
        raise GuardError("禁止以表函数作为数据源")
    db = (tbl.db or "").strip()
    if db:
        if not database or db.lower() != database.lower():
            raise GuardError(f"表 {tbl.name} 只能引用当前数据源库（{database}）")
    name = tbl.name
    alias = (aliases or {}).get(name)
    if alias:
        tbl.set("this", exp.to_identifier(alias))
        name = alias

    rebuilt = tree.sql(dialect="mysql")
    if _FORBIDDEN_RE.search(rebuilt):
        raise GuardError("直读禁止 FOR UPDATE / INTO OUTFILE / FORMAT 等子句")

    limit_node = tree.args.get("limit")
    rewritten = False
    if limit_node is not None:
        try:
            limit_val = int(limit_node.expression.name)
        except (AttributeError, ValueError, TypeError):
            raise GuardError("LIMIT 必须是整数常量")
        if limit_val > config.SOURCE_MAX_ROWS:
            tree = tree.limit(config.SOURCE_MAX_ROWS, dialect="mysql")
            rewritten = True
    else:
        tree = tree.limit(config.SOURCE_MAX_ROWS, dialect="mysql")
        rewritten = True

    return DirectCheck(sql=tree.sql(dialect="mysql") + ";",
                       limit=config.SOURCE_MAX_ROWS, rewritten=rewritten, table=name)



def _connect(ds: dict, password: str, timeout_sec: float):
    return pymysql.connect(
        host=ds["host"], port=int(ds["port"]), user=ds["username"],
        password=password, database=ds["database_name"], charset="utf8mb4",
        connect_timeout=max(1, min(5, int(timeout_sec) + 1)),
        read_timeout=max(1, int(timeout_sec) + 1),
        write_timeout=max(1, int(timeout_sec) + 1),
        autocommit=True,
    )


def _kill(ds: dict, password: str, conn_id: int) -> None:
    try:
        conn = pymysql.connect(
            host=ds["host"], port=int(ds["port"]), user=ds["username"],
            password=password, database=ds["database_name"], charset="utf8mb4",
            connect_timeout=2, read_timeout=2, write_timeout=2, autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute("KILL QUERY %s", (conn_id,))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — 熔断/超时路径不得二次抛出
        pass


def _run_with_timeout(ds, password, fn, timeout_ms: int):
    timeout_sec = max(0.5, timeout_ms / 1000.0)
    try:
        conn = _connect(ds, password, timeout_sec)
    except pymysql.MySQLError as e:
        raise SourceError(f"无法连接源库: {e}", e.args[0] if e.args else None) from None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT CONNECTION_ID()")
            row = cur.fetchone()
        conn_id = int(row[0]) if row else 0
    except pymysql.MySQLError as e:
        conn.close()
        raise SourceError(f"无法获取源库连接 ID: {e}",
                          e.args[0] if e.args else None) from None

    killed = threading.Event()
    timer = None
    if conn_id:
        def _killer():
            killed.set()
            _kill(ds, password, conn_id)
        timer = threading.Timer(timeout_sec, _killer)
        timer.daemon = True
        timer.start()

    started = time.monotonic()
    try:
        result = fn(conn)
        return result
    except pymysql.MySQLError as e:
        errno = e.args[0] if e.args else None
        elapsed = time.monotonic() - started
        if killed.is_set() or errno in (1317, 2013, 2014) or elapsed >= timeout_sec - 0.1:
            raise SourceTimeout(f"源库查询超过 {timeout_ms}ms，已终止", errno) from None
        raise SourceError(f"源库执行失败: {e}", errno) from None
    finally:
        if timer is not None:
            timer.cancel()
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, dt_time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<binary {len(value)} bytes>"
    return str(value)


def query(ds: dict, password: str, sql: str, timeout_ms: int | None = None) -> dict:
    timeout = int(timeout_ms or config.SOURCE_TIMEOUT_MS)

    def _fn(conn):
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall() or []
            desc = cur.description or []
        return rows, desc

    rows, desc = _run_with_timeout(ds, password, _fn, timeout)
    columns = [{"name": d[0], "type": _TYPE_NAMES.get(d[1], str(d[1]))} for d in desc]
    data = [[_jsonable(v) for v in row] for row in rows[:config.SOURCE_MAX_ROWS]]
    return {"columns": columns, "rows": data,
            "truncated": len(rows) >= config.SOURCE_MAX_ROWS}


def count(ds: dict, password: str, sql: str, timeout_ms: int | None = None) -> int:
    timeout = int(timeout_ms or config.SOURCE_TIMEOUT_MS)

    def _fn(conn):
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    return _run_with_timeout(ds, password, _fn, timeout)


def execute(ds: dict, password: str, sql: str, timeout_ms: int | None = None) -> int:
    timeout = int(timeout_ms or max(5000, config.SOURCE_TIMEOUT_MS))

    def _fn(conn):
        with conn.cursor() as cur:
            affected = cur.execute(sql)
        conn.commit()
        return int(affected)

    return _run_with_timeout(ds, password, _fn, timeout)


def execute_insert_capture(ds: dict, password: str, sql: str, data: dict,
                           timeout_ms: int | None = None) -> dict:
    timeout = int(timeout_ms or max(5000, config.SOURCE_TIMEOUT_MS))
    database, table = data["database"], data["table"]
    pk_cols, auto_col = data["pk_cols"], data["auto_pk_col"]
    literals = dict(data.get("pk_literals") or {})

    def _fn(conn):
        with conn.cursor() as cur:
            affected = int(cur.execute(sql))
            generated = getattr(cur, "lastrowid", None)
            if not generated:
                cur.execute("SELECT LAST_INSERT_ID()")
                row = cur.fetchone()
                generated = row[0] if row else None
            if not generated:
                raise WritePreviewError(
                    "insert_pk_unknown", "无法取得自增主键实际值，撤回不可用")
            literals[auto_col] = str(int(generated))
            where = " AND ".join(f"`{c}`={v}" for c, v in literals.items())
            after = _read_rows(cur, f"SELECT * FROM `{database}`.`{table}` "
                                    f"WHERE {where} LIMIT 1")
        conn.commit()
        return {"affected_rows": affected, "after_rows": after,
                "pk_literals": literals}

    return _run_with_timeout(ds, password, _fn, timeout)



class WritePreviewError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UndoConflict(Exception):
    pass


def _pk_columns(cur, database: str, table: str) -> list[str]:
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' "
        "ORDER BY ORDINAL_POSITION", (database, table))
    return [r[0] for r in (cur.fetchall() or [])]


def _read_rows(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [d[0] for d in (cur.description or [])]
    return [dict(zip(cols, [_jsonable(v) for v in row]))
            for row in (cur.fetchall() or [])]


def _select_sql(tree, table_exp, limit: int) -> str:
    from sqlglot import exp
    sel = exp.select("*").from_(table_exp.copy())
    where = tree.args.get("where")
    if where is not None:
        sel = sel.where(where.copy())
    return sel.sql(dialect="mysql") + f" LIMIT {int(limit)}"


def _insert_pk_literals(tree, pk_cols: list[str]) -> dict[str, str]:
    from sqlglot import exp
    if not isinstance(tree.this, exp.Schema) or tree.expression is None:
        return {}
    cols = [c.name for c in tree.this.expressions]
    first = tree.expression.expressions[0]
    values = list(first.expressions) if isinstance(first, exp.Tuple) else list(first)
    idx = {c: i for i, c in enumerate(cols)}
    out = {}
    for pk in pk_cols:
        if pk not in idx or idx[pk] >= len(values):
            return {}
        value = values[idx[pk]]
        if not isinstance(value, exp.Literal):
            return {}
        out[pk] = value.sql(dialect="mysql")
    return out


def _auto_increment_column(cur, database: str, table: str) -> str:
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND EXTRA LIKE %s "
        "ORDER BY ORDINAL_POSITION LIMIT 1",
        (database, table, "%auto_increment%"))
    row = cur.fetchone()
    return str(row[0]) if row else ""


def _insert_row_count(tree) -> int:
    from sqlglot import exp
    if tree.expression is None:
        return 0
    return len(list(tree.expression.expressions))


def _pk_literals_with_auto(tree, pk_cols: list[str], auto_col: str,
                           generated: int) -> dict[str, str]:
    from sqlglot import exp
    if not isinstance(tree.this, exp.Schema) or tree.expression is None:
        return {}
    cols = [c.name for c in tree.this.expressions]
    first = tree.expression.expressions[0]
    values = list(first.expressions) if isinstance(first, exp.Tuple) else list(first)
    idx = {c: i for i, c in enumerate(cols)}
    out: dict[str, str] = {}
    for pk in pk_cols:
        if pk == auto_col:
            out[pk] = str(int(generated))
            continue
        if pk not in idx or idx[pk] >= len(values):
            return {}
        value = values[idx[pk]]
        if not isinstance(value, exp.Literal):
            return {}
        out[pk] = value.sql(dialect="mysql")
    return out


def _preview_hash(sql: str, pk_set: list[list], affected: int) -> str:
    payload = json.dumps({"sql": sql, "pk": pk_set, "affected": affected},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def transactional_preview(ds: dict, password: str, sql: str,
                          max_rows: int | None = None,
                          timeout_ms: int | None = None) -> dict:
    import sqlglot
    from sqlglot import exp

    rows_limit = int(max_rows or config.PREVIEW_SAMPLE_ROWS)
    timeout = int(timeout_ms or max(5000, config.SOURCE_TIMEOUT_MS))
    tree = sqlglot.parse_one(sql, dialect="mysql")
    operation = (tree.key or "").lower()
    table_exp = tree.find(exp.Table)
    if not table_exp or operation not in ("insert", "update", "delete"):
        raise WritePreviewError("preview_unsupported", "仅支持单表 INSERT/UPDATE/DELETE 预览")
    database = ds.get("database_name") or ""
    table = table_exp.name

    def _fn(conn):
        conn.autocommit(False)
        with conn.cursor() as cur:
            pk_cols = _pk_columns(cur, database, table)
            if not pk_cols:
                raise WritePreviewError(
                    "write_no_primary_key",
                    "目标表没有主键，无法生成可撤回的写操作；请先补主键")

            auto_pk = False
            auto_col = ""
            if operation == "insert":
                pk_literals = _insert_pk_literals(tree, pk_cols)
                if not pk_literals:
                    auto_col = _auto_increment_column(cur, database, table)
                    if auto_col not in pk_cols or _insert_row_count(tree) != 1:
                        raise WritePreviewError(
                            "insert_requires_pk",
                            "INSERT 必须显式写出主键列与主键值；"
                            "仅单行 INSERT 且主键含自增列时可自动推导")
                    if not _pk_literals_with_auto(tree, pk_cols, auto_col, 0):
                        raise WritePreviewError(
                            "insert_requires_pk",
                            "非自增主键列必须显式写出字面量，才能预览与撤回")
                    auto_pk = True
                before: list[dict] = []
                affected = int(cur.execute(sql))
                if auto_pk:
                    generated = getattr(cur, "lastrowid", None)
                    if not generated:
                        cur.execute("SELECT LAST_INSERT_ID()")
                        row = cur.fetchone()
                        generated = row[0] if row else None
                    pk_literals = _pk_literals_with_auto(tree, pk_cols, auto_col,
                                                         int(generated or 0))
                    if not pk_literals:
                        raise WritePreviewError(
                            "insert_requires_pk",
                            "非自增主键列必须显式写出字面量，才能预览与撤回")
                where = " AND ".join(f"`{c}`={v}" for c, v in pk_literals.items())
                after = _read_rows(cur, f"SELECT * FROM `{database}`.`{table}` "
                                        f"WHERE {where} LIMIT {rows_limit}")
            else:
                before = _read_rows(cur, _select_sql(tree, table_exp, rows_limit))
                affected = int(cur.execute(sql))
                after = _read_rows(cur, _select_sql(tree, table_exp, rows_limit))

            pk_set = [[row.get(c) for c in pk_cols] for row in before]
            fingerprint = _preview_hash(sql, pk_set, affected)
        conn.rollback()
        return {
            "operation": operation, "database": database, "table": table,
            "pk_cols": pk_cols, "before_rows": before, "after_rows": after,
            "affected_rows": affected, "preview_hash": fingerprint,
            "auto_pk": auto_pk, "auto_pk_col": auto_col,
            "pk_literals": dict(pk_literals) if operation == "insert" else {},
            "truncated": max(len(before), len(after)) >= rows_limit,
        }

    return _run_with_timeout(ds, password, _fn, timeout)


def check_preview_unchanged(ds: dict, password: str, data: dict,
                            timeout_ms: int | None = None) -> None:
    operation = data["operation"]
    pk_cols = data["pk_cols"]
    before_rows = data["before_rows"]
    if not before_rows:
        return
    database, table = data["database"], data["table"]
    timeout = int(timeout_ms or max(5000, config.SOURCE_TIMEOUT_MS))

    def _fn(conn):
        with conn.cursor() as cur:
            for row in before_rows:
                where = " AND ".join(f"`{c}`=%s" for c in pk_cols)
                cur.execute(f"SELECT * FROM `{database}`.`{table}` WHERE {where} "
                            f"LIMIT 1", [row.get(c) for c in pk_cols])
                cols = [d[0] for d in (cur.description or [])]
                current = dict(zip(cols, [(_jsonable(v)) for v in (cur.fetchone() or [])]))
                expected_exists = operation != "insert"
                if expected_exists and not current:
                    raise WritePreviewError("preview_stale", "数据在预览后已被删除，请重新预览")
                if not expected_exists and current:
                    raise WritePreviewError("preview_stale", "主键已被占用，请重新预览")
                if current and any(current.get(k) != row.get(k) for k in row):
                    raise WritePreviewError("preview_stale", "数据在预览后已被修改，请重新预览")
        return None

    _run_with_timeout(ds, password, _fn, timeout)


def undo_write(ds: dict, password: str, record: dict,
               timeout_ms: int | None = None) -> int:
    database, table = record["database"], record["table_name"]
    operation, pk_cols = record["operation"], record["pk_cols"]
    after_rows = record["after_rows"]
    timeout = int(timeout_ms or max(5000, config.SOURCE_TIMEOUT_MS))

    def _fn(conn):
        conn.autocommit(False)
        with conn.cursor() as cur:
            for row in after_rows:
                where = " AND ".join(f"`{c}`=%s" for c in pk_cols)
                cur.execute(f"SELECT * FROM `{database}`.`{table}` WHERE {where} LIMIT 1",
                            [row.get(c) for c in pk_cols])
                cols = [d[0] for d in (cur.description or [])]
                current = dict(zip(cols, [(_jsonable(v)) for v in (cur.fetchone() or [])]))
                if operation == "delete" and current:
                    raise UndoConflict("记录已被重建，撤回会覆盖新数据")
                if operation != "delete" and not current:
                    raise UndoConflict("记录在确认后又被删除，无法安全撤回")
                if current and any(current.get(k) != row.get(k) for k in row):
                    raise UndoConflict("记录在确认后又被修改，撤回会覆盖这些改动")
            done = 0
            for stmt in record["inverse_sql"]:
                cur.execute(stmt)
                done += 1
        conn.commit()
        return done

    return _run_with_timeout(ds, password, _fn, timeout)


def describe(ds: dict, password: str, table: str,
             timeout_ms: int | None = None) -> list[dict]:
    timeout = int(timeout_ms or config.SOURCE_TIMEOUT_MS)

    def _fn(conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT, ORDINAL_POSITION "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                "ORDER BY ORDINAL_POSITION", (ds["database_name"], table))
            return cur.fetchall() or []

    rows = _run_with_timeout(ds, password, _fn, timeout)
    return [{"name": r[0], "type": r[1], "comment": r[2] or "",
             "position": int(r[3])} for r in rows]
