# -*- coding: utf-8 -*-
import re

import sqlglot
from sqlglot import exp

from . import config
from .catalog import catalog


class GuardError(Exception):
    pass


BANNED_TABLE_FUNCS = {
    "remote", "remotesecure", "url", "urlcluster", "file", "s3", "s3cluster",
    "hdfs", "hdfscluster", "mysql", "postgresql", "mongodb", "redis", "sqlite",
    "odbc", "jdbc", "executable", "executablepool", "input", "values",
    "numbers", "numbers_mt", "generate_series", "merge", "merge_tree", "view",
    "cluster", "clusterallreplicas", "clusterreplicas", "dictionary", "dict",
    "null", "kafka", "nats", "rabbitmq", "azureblobstorage", "gcs", "cos", "oss",
}

BANNED_FUNC_NAMES = {"sleep", "sleepeachrow", "joinget"}
BANNED_FUNC_PREFIXES = ("dictget",)

_READ_ONLY_ROOTS = (exp.Select, exp.Union, exp.With)


class CheckResult:
    def __init__(self, sql: str, rewritten: bool, injected_limit: int | None):
        self.sql = sql
        self.rewritten = rewritten
        self.injected_limit = injected_limit


def _outer_node(tree: exp.Expression) -> exp.Expression:
    return tree.this if isinstance(tree, exp.With) else tree


def check_sql(raw_sql: str, caller_limit: int | None = None,
              allowed_databases: set[str] | None = None,
              required_prefix: str | None = None) -> CheckResult:
    allowed = set(allowed_databases) if allowed_databases else set(config.ALLOWED_DATABASES)
    prefix_hint = required_prefix or config.CH_DATABASE
    sql = (raw_sql or "").strip()
    if not sql:
        raise GuardError("空 SQL")
    if len(sql) > config.MAX_SQL_CHARS:
        raise GuardError(f"SQL 超过最大长度 {config.MAX_SQL_CHARS} 字符")

    try:
        statements = [s for s in sqlglot.parse(sql, read="clickhouse") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardError(f"SQL 解析失败：{str(e).splitlines()[0][:200]}") from None
    if len(statements) != 1:
        raise GuardError("只允许单条 SELECT，禁止多语句")
    tree = statements[0]

    if not isinstance(tree, _READ_ONLY_ROOTS):
        raise GuardError(f"只允许 SELECT 查询，检测到 {type(tree).__name__}")

    for node in tree.walk():
        if isinstance(node, exp.Anonymous):
            fname = (node.name or "").lower()
            if fname in BANNED_TABLE_FUNCS:
                raise GuardError(f"禁止使用表函数 {fname}()")
            if fname in BANNED_FUNC_NAMES or any(fname.startswith(p) for p in BANNED_FUNC_PREFIXES):
                raise GuardError(f"禁止使用函数 {fname}()")
        elif isinstance(node, exp.Func):
            fname = (getattr(node, "name", "") or getattr(node, "sql_name", lambda: "")() or "").lower()
            if fname in BANNED_FUNC_NAMES:
                raise GuardError(f"禁止使用函数 {fname}()")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for tbl in tree.find_all(exp.Table):
        tname = tbl.name
        if not tname:
            raise GuardError("禁止以表函数作为数据源（remote/url/numbers 等）")
        if tname.lower() in cte_names:
            continue
        db = tbl.db
        if not db:
            raise GuardError(
                f"表 {tname} 必须带库名前缀（如 {prefix_hint}.{tname}）")
        if db not in allowed:
            raise GuardError(f"库 {db} 不在允许列表 {sorted(allowed)}")
        if tname not in catalog.allowed_names(db):
            raise GuardError(f"表/视图 {db}.{tname} 不存在（元数据实时校验）")

    rebuilt = tree.sql(dialect="clickhouse")
    if re.search(r"\bFORMAT\b", rebuilt, re.IGNORECASE):
        raise GuardError("不允许显式 FORMAT 子句（输出格式由网关控制）")
    if re.search(r"\bINTO\s+OUTFILE\b", rebuilt, re.IGNORECASE):
        raise GuardError("不允许 INTO OUTFILE")

    outer = _outer_node(tree)
    existing_limit = outer.args.get("limit")
    if existing_limit is not None:
        limit_exp = existing_limit.expression
        try:
            limit_val = int(limit_exp.name)
        except (AttributeError, ValueError, TypeError):
            raise GuardError("LIMIT 必须是整数常量")
        if limit_val > config.MAX_LIMIT:
            raise GuardError(f"LIMIT {limit_val} 超过上限 {config.MAX_LIMIT}")
        return CheckResult(rebuilt + ";", rewritten=False, injected_limit=None)

    injected = caller_limit or (
        config.AGG_LIMIT if tree.find(exp.Group) is not None else config.DETAIL_LIMIT)
    if injected > config.MAX_LIMIT:
        raise GuardError(f"结果行上限 {injected} 超过 {config.MAX_LIMIT}")
    tree = tree.limit(injected, dialect="clickhouse")
    final_sql = tree.sql(dialect="clickhouse") + ";"
    return CheckResult(final_sql, rewritten=True, injected_limit=injected)
