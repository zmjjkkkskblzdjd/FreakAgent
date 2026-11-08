from __future__ import annotations

import sqlglot
from sqlglot import exp


def resolve_scope(tenant_id: str) -> dict:
    return {"read_filters": None, "write_constraints": None}


class PolicyInjectionError(RuntimeError):
    pass


def inject_read_filters(sql: str, filters: dict) -> str:
    if not filters:
        return sql
    tree = sqlglot.parse_one(sql, read="clickhouse")
    if not isinstance(tree, exp.Select):
        raise PolicyInjectionError("读过滤只对 SELECT 注入")
    where = sqlglot.parse_one(filters["where"], read="clickhouse")
    tree = tree.where(where)
    return tree.sql(dialect="clickhouse")