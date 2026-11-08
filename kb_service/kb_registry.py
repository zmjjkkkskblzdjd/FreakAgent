from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_KB_ID = "default"
DEFAULT_PREFIX = "kb"
DEFAULT_KB_NAME = "默认知识库"

META_DB = Path(__file__).resolve().parent / "data" / "kb_meta.db"


def _query(sql: str, params: tuple = ()) -> list[dict]:
    if not META_DB.exists():
        return []
    try:
        conn = sqlite3.connect(
            f"file:{META_DB.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


_BASE_COLUMNS = "id, name, collection_prefix, embedding_model, chunk_size, overlap, created_at"


def _has_description() -> bool:
    return any(r.get("name") == "description" for r in _query("PRAGMA table_info(kb)"))


def _select_columns() -> str:
    return _BASE_COLUMNS + (", description" if _has_description() else "")


def list_kbs() -> list[dict]:
    rows = _query(f"SELECT {_select_columns()} FROM kb ORDER BY created_at ASC")
    for r in rows:
        r.setdefault("description", "")
    return rows


def get_kb(kb_id: str) -> dict | None:
    rows = _query(f"SELECT {_select_columns()} FROM kb WHERE id=?", (kb_id,))
    if not rows:
        return None
    rows[0].setdefault("description", "")
    return rows[0]


def set_kb_description(kb_id: str, description: str) -> bool:
    if not META_DB.exists():
        return False
    try:
        conn = sqlite3.connect(str(META_DB), check_same_thread=False)
        try:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(kb)").fetchall()]
            if not columns:                 
                return False
            if "description" not in columns:
                conn.execute(
                    "ALTER TABLE kb ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            cur = conn.execute("UPDATE kb SET description=? WHERE id=?",
                               ((description or "").strip(), kb_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _synthesize(kb_id: str) -> dict:
    return {
        "id": kb_id,
        "name": DEFAULT_KB_NAME if kb_id == DEFAULT_KB_ID else kb_id,
        "collection_prefix": DEFAULT_PREFIX if kb_id == DEFAULT_KB_ID else kb_id,
        "description": "",
    }


def resolve_kbs(kb_ids: list[str] | tuple[str, ...] | None) -> list[dict]:
    ids: list[str] = []
    for raw in kb_ids or []:
        kb_id = str(raw).strip()
        if kb_id and kb_id not in ids:
            ids.append(kb_id)
    if not ids:
        ids = [DEFAULT_KB_ID]

    out = []
    for kb_id in ids:
        out.append(get_kb(kb_id) or _synthesize(kb_id))
    return out
