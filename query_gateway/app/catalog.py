# -*- coding: utf-8 -*-
import re
import threading
import time
from typing import Any

from . import chclient, config
from .models import ColumnMetaOut, TableMeta, TableSchema

VIEW_ENGINES = {"View", "MaterializedView", "LiveView", "Distributed"}

AF_DS_RE = re.compile(r"^af_ds_\d+$")


def is_af_ds(database: str) -> bool:
    return bool(AF_DS_RE.match(database or ""))


def is_af_ds_allowed(database: str) -> bool:
    return database in config.ALLOWED_DATABASES or is_af_ds(database)

_DEDUP_HINTS = {
    "ReplacingMergeTree": "多版本表：直接查同名 v_ 视图，或用 argMax(col,_version) GROUP BY 主键取最新版本，勿用 FINAL",
    "ReplicatedReplacingMergeTree": "多版本表：直接查同名 v_ 视图，或用 argMax(col,_version) GROUP BY 主键取最新版本，勿用 FINAL",
    "CollapsingMergeTree": "折叠表：需带 _sign=1 条件取有效行，或查对应视图",
    "SummingMergeTree": "汇总表：明细可能未合并，精确值需显式 GROUP BY 聚合",
}


class Catalog:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache_tables: dict[str, tuple[float, list[TableMeta]]] = {}
        self._cache_names: dict[str, tuple[float, set[str]]] = {}

    def _fetch_tables(self, database: str) -> list[TableMeta]:
        sql = f"""
            SELECT name, engine, total_rows, comment,
                   partition_key, sorting_key
            FROM system.tables
            WHERE database = '{database}'
            ORDER BY name
        """
        data = chclient.query_json(sql, timeout_ms=10000)
        out: list[TableMeta] = []
        for r in data["data"]:
            engine = r.get("engine") or ""
            base_engine = engine.split("(")[0].strip()
            is_view = base_engine in VIEW_ENGINES
            out.append(TableMeta(
                database=database,
                name=r["name"],
                full_name=f"{database}.{r['name']}",
                engine=engine,
                total_rows=int(r["total_rows"]) if r.get("total_rows") not in (None, "\\N") else None,
                comment=r.get("comment") or "",
                is_view=is_view,
                dedup_hint=_DEDUP_HINTS.get(base_engine, ""),
                partition_key=r.get("partition_key") or "",
                sorting_key=r.get("sorting_key") or "",
            ))
        return out

    def list_tables(self, database: str, *, refresh: bool = False) -> list[TableMeta]:
        if not is_af_ds_allowed(database):
            raise ValueError(f"库 {database} 不在允许列表 {sorted(config.ALLOWED_DATABASES)}")
        now = time.time()
        with self._lock:
            hit = self._cache_tables.get(database)
            if not refresh and hit and now - hit[0] < config.CATALOG_TTL_SEC:
                return hit[1]
        tables = self._fetch_tables(database)
        with self._lock:
            self._cache_tables[database] = (now, tables)
            self._cache_names[database] = (now, {t.name for t in tables})
        return tables

    def allowed_names(self, database: str) -> set[str]:
        now = time.time()
        with self._lock:
            hit = self._cache_names.get(database)
            if hit and now - hit[0] < config.CATALOG_TTL_SEC:
                return hit[1]
        names = {t.name for t in self.list_tables(database)}
        if is_af_ds(database):
            names = {n for n in names if n.startswith("v_")}
        return names

    def get_schema(self, database: str, table: str, *, refresh: bool = False) -> TableSchema:
        tables = self.list_tables(database, refresh=refresh)
        meta = next((t for t in tables if t.name == table), None)
        if meta is None:
            raise ValueError(f"表 {database}.{table} 不存在")
        sql = f"""
            SELECT name, type, comment, position
            FROM system.columns
            WHERE database = '{database}' AND table = '{table}'
            ORDER BY position
        """
        data = chclient.query_json(sql, timeout_ms=10000)
        cols = [ColumnMetaOut(
            name=r["name"], type=r["type"],
            comment=r.get("comment") or "", position=int(r["position"]),
        ) for r in data["data"]]
        return TableSchema(table=meta, columns=cols)

    def invalidate(self):
        with self._lock:
            self._cache_tables.clear()
            self._cache_names.clear()


catalog = Catalog()
