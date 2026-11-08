# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import config


def _lit(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


def build_inverse(*, database: str, table: str, operation: str,
                  pk_cols: list[str], before_rows: list[dict],
                  after_rows: list[dict]) -> list[str]:
    target = f"`{database}`.`{table}`"
    if operation == "update":
        stmts = []
        for row in before_rows:
            sets = ", ".join(f"`{c}`={_lit(v)}" for c, v in row.items())
            where = " AND ".join(f"`{c}`={_lit(row.get(c))}" for c in pk_cols)
            stmts.append(f"UPDATE {target} SET {sets} WHERE {where}")
        return stmts
    if operation == "delete":
        if not before_rows:
            return []
        cols = ", ".join(f"`{c}`" for c in before_rows[0].keys())
        values = ", ".join(
            "(" + ", ".join(_lit(v) for v in row.values()) + ")"
            for row in before_rows)
        return [f"INSERT INTO {target} ({cols}) VALUES {values}"]
    if operation == "insert":
        stmts = []
        for row in after_rows:
            if not all(row.get(c) is not None for c in pk_cols):
                continue
            where = " AND ".join(f"`{c}`={_lit(row.get(c))}" for c in pk_cols)
            stmts.append(f"DELETE FROM {target} WHERE {where}")
        return stmts
    return []


class WriteJournal:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path or config.WRITE_JOURNAL_DB)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS write_journal (
                    audit_id TEXT PRIMARY KEY,
                    tenant_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    datasource_id INTEGER,
                    database TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    preview_hash TEXT NOT NULL,
                    pk_cols TEXT NOT NULL,
                    before_rows TEXT NOT NULL,
                    after_rows TEXT NOT NULL,
                    inverse_sql TEXT NOT NULL,
                    committed_at REAL NOT NULL,
                    undo_deadline REAL NOT NULL,
                    status TEXT NOT NULL
                )""")

    def record(self, *, audit_id: str, tenant_id: int, user_id: int,
               datasource_id: int | None, database: str, table: str,
               operation: str, sql: str, preview_hash: str,
               pk_cols: list[str], before_rows: list[dict],
               after_rows: list[dict]) -> dict:
        now = time.time()
        deadline = now + config.UNDO_WINDOW_SEC
        inverse = build_inverse(database=database, table=table, operation=operation,
                                pk_cols=pk_cols, before_rows=before_rows,
                                after_rows=after_rows)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO write_journal VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (audit_id, tenant_id, user_id, datasource_id, database, table,
                 operation, sql, preview_hash, json.dumps(pk_cols),
                 json.dumps(before_rows, ensure_ascii=False),
                 json.dumps(after_rows, ensure_ascii=False),
                 json.dumps(inverse, ensure_ascii=False),
                 now, deadline, "committed"))
        return {"audit_id": audit_id, "undo_deadline": deadline,
                "inverse_rows": len(inverse)}

    def get(self, audit_id: str, *, tenant_id: int, user_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM write_journal WHERE audit_id=? AND tenant_id=? AND user_id=?",
                (audit_id, tenant_id, user_id)).fetchone()
        return self._decode(row) if row else None

    def mark_undone(self, audit_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE write_journal SET status='undone' WHERE audit_id=?",
                         (audit_id,))

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in ("pk_cols", "before_rows", "after_rows", "inverse_sql"):
            d[key] = json.loads(d[key])
        return d


journal = WriteJournal()
