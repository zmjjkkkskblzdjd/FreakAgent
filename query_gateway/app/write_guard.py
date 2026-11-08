# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from . import config
from .guard import (BANNED_FUNC_NAMES, BANNED_FUNC_PREFIXES,
                    BANNED_TABLE_FUNCS, GuardError)


class WriteForbidden(GuardError):
    pass


def over_write_limit(estimated: int) -> bool:
    return estimated > config.MAX_WRITE_ROWS

BIZ_MYSQL_TABLES = {"fact_sales_order_line", "dim_region", "dim_customer", "dim_product"}
MYSQL_WRITE_TABLES = {"kb_glossary", "kb_fewshot"}
_TARGET_BY_DB = {"sales_biz": ("mysql", BIZ_MYSQL_TABLES),
                 "sales_meta": ("mysql", MYSQL_WRITE_TABLES)}


@dataclass
class WriteCheck:
    operation: str          
    target: str             
    database: str
    table: str
    normalized_sql: str     
    where_sql: str | None
    set_sql: str | None     
    insert_rows: int | None  

    def count_sql(self) -> str | None:
        if self.operation == "insert":
            return None
        return f"SELECT COUNT(*) FROM {self.database}.{self.table} WHERE {self.where_sql}"


def _target_table(tree: exp.Expression) -> exp.Table:
    t = tree.this
    if isinstance(t, exp.Schema):
        t = t.this
    if not isinstance(t, exp.Table) or not t.name:
        raise GuardError("写目标必须是真实表（禁止表函数）")
    return t


def _check_banned(tree: exp.Expression) -> None:
    for node in tree.walk():
        if isinstance(node, exp.Anonymous):
            fname = (node.name or "").lower()
            if fname in BANNED_TABLE_FUNCS:
                raise GuardError(f"禁止使用表函数 {fname}()")
            if fname in BANNED_FUNC_NAMES or any(fname.startswith(p) for p in BANNED_FUNC_PREFIXES):
                raise GuardError(f"禁止使用函数 {fname}()")
        elif isinstance(node, exp.Func):
            fname = (getattr(node, "name", "") or "").lower()
            if fname in BANNED_FUNC_NAMES:
                raise GuardError(f"禁止使用函数 {fname}()")


def _route_table(tbl: exp.Table, allowed_tables: set[str] | None = None,
                 database: str | None = None) -> tuple[str, str, str]:
    db = (tbl.db or "").lower()
    name = tbl.name.lower()
    if allowed_tables is not None:
        target_db = (database or db or "").strip()
        if not target_db:
            raise GuardError("写目标库未知（数据源未配置 database_name）")
        if db and database and db != database.lower():
            raise GuardError(f"表 {tbl.name} 只能写当前数据源库（{database}）")
        if not tbl.name:
            raise GuardError("写目标必须是真实表（禁止表函数）")
        if name not in allowed_tables:
            raise WriteForbidden(f"表 {tbl.name} 不在本数据源写白名单（管理员授权范围内）")
        return "mysql", target_db, name
    if not db:
        raise GuardError(f"表 {tbl.name} 必须带库名前缀（sales_biz. 或 sales_meta.）")
    if db not in _TARGET_BY_DB:
        raise GuardError(f"库 {db} 不在写白名单（sales_dw 为 CDC 只读副本，业务写请打 sales_biz）")
    target, whitelist = _TARGET_BY_DB[db]
    if name not in whitelist:
        raise GuardError(f"表 {db}.{tbl.name} 不在写白名单 {sorted(whitelist)}")
    return target, db, name


def check_write_sql(raw_sql: str, *, allowed_tables: set[str] | None = None,
                    database: str | None = None) -> WriteCheck:
    sql = (raw_sql or "").strip()
    if not sql:
        raise GuardError("空 SQL")
    if re.search(r"\bINTO\s+OUTFILE\b", sql, re.IGNORECASE):
        raise GuardError("不允许 INTO OUTFILE")
    try:
        stmts = [s for s in sqlglot.parse(sql, read="mysql") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardError(f"SQL 解析失败：{str(e).splitlines()[0][:200]}") from None
    if len(stmts) != 1:
        raise GuardError("只允许单条语句，禁止多语句")
    tree = stmts[0]

    if not isinstance(tree, (exp.Insert, exp.Update, exp.Delete)):
        raise GuardError(f"只允许 INSERT/UPDATE/DELETE，检测到 {type(tree).__name__}（DDL 一律拒）")

    _check_banned(tree)
    tbl = _target_table(tree)
    target, db, name = _route_table(tbl, allowed_tables, database)

    where_sql = set_sql = None
    insert_rows = None

    if isinstance(tree, exp.Update):
        where = tree.args.get("where")
        if where is None:
            raise GuardError("UPDATE 必须带 WHERE（禁裸全表更新）")
        where_sql = where.this.sql(dialect="mysql")
        set_sql = ", ".join(f"{eq.this.sql(dialect='mysql')} = {eq.expression.sql(dialect='mysql')}"
                            for eq in tree.args.get("expressions", []))
        if not set_sql:
            raise GuardError("UPDATE 缺少 SET 赋值")
    elif isinstance(tree, exp.Delete):
        where = tree.args.get("where")
        if where is None:
            raise GuardError("DELETE 必须带 WHERE（禁裸全表删除）")
        where_sql = where.this.sql(dialect="mysql")
    else:  
        src = tree.expression
        if isinstance(src, exp.Values):
            insert_rows = len(src.expressions)
            if insert_rows == 0:
                raise GuardError("INSERT VALUES 为空")
        elif isinstance(src, (exp.Select, exp.Union)):
            raise GuardError("MySQL 目标仅允许 INSERT ... VALUES（禁 INSERT...SELECT）")
        else:
            raise GuardError("INSERT 只允许 VALUES")

    for t in tree.find_all(exp.Table):
        if t is tbl:
            continue
        raise GuardError(f"WHERE/SET 中禁止引用其他表（{t.sql(dialect='mysql')}）")

    normalized = tree.sql(dialect="mysql")
    if re.search(r"\bFORMAT\b", normalized, re.IGNORECASE):
        raise GuardError("不允许显式 FORMAT 子句")
    if re.search(r"\bINTO\s+OUTFILE\b", normalized, re.IGNORECASE):
        raise GuardError("不允许 INTO OUTFILE")

    return WriteCheck(operation=type(tree).__name__.lower(), target=target,
                      database=db, table=name, normalized_sql=normalized,
                      where_sql=where_sql, set_sql=set_sql,
                      insert_rows=insert_rows)
