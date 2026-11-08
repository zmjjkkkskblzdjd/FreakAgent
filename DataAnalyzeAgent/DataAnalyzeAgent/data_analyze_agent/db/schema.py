
from __future__ import annotations

from config import get_app_config
from data_analyze_agent.core import TableMeta
from data_analyze_agent.db import gateway

__all__ = ["introspect", "get_table_meta"]


def introspect(db_name: str | None = None) -> list[TableMeta]:
    expected = get_app_config().gateway.database
    if db_name is not None and db_name != expected:
        raise ValueError(
            f"当前仅支持网关配置库 {expected!r}，收到 {db_name!r}；多库支持未开放"
        )
    return gateway.introspect()


def get_table_meta(db_name: str, table_name: str) -> TableMeta:
    for meta in introspect(db_name):
        if meta.table_name == table_name:
            return meta
    raise ValueError(f"库 {get_app_config().gateway.database} 中不存在表 {table_name}")
