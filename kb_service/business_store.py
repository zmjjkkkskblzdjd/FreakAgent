from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from kb_service.store import VectorStore

META_DB = Path(__file__).resolve().parent / "data" / "kb_meta.db"   

_DDL = """
CREATE TABLE IF NOT EXISTS business_term(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    term TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    synonyms TEXT NOT NULL DEFAULT '',
    formula TEXT NOT NULL DEFAULT '',
    related_tables TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    is_recall INTEGER NOT NULL DEFAULT 1,
    embedding_status TEXT NOT NULL DEFAULT 'PENDING',
    error_msg TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_business_term_tenant ON business_term(tenant_id);
"""

_EXTRA_COLUMNS = (
    ("formula", "TEXT NOT NULL DEFAULT ''"),
    ("related_tables", "TEXT NOT NULL DEFAULT ''"),
    ("unit", "TEXT NOT NULL DEFAULT ''"),
    ("user_id", "INTEGER NOT NULL DEFAULT 0"),
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(business_term)")}
    for name, ddl in _EXTRA_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE business_term ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_business_term_tenant_user "
                 "ON business_term(tenant_id, user_id)")
    conn.commit()


def open_conn() -> sqlite3.Connection:
    META_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(META_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def parse_json_array(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
    except (ValueError, TypeError):
        pass
    return [x.strip() for x in s.split(",") if x.strip()]


def term_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "term": row["term"], "description": row["description"],
        "synonyms": row["synonyms"], "formula": row["formula"],
        "related_tables": row["related_tables"], "unit": row["unit"],
        "is_recall": bool(row["is_recall"]),
        "user_id": int(row["user_id"] or 0), "is_public": not row["user_id"],
        "embedding_status": row["embedding_status"], "error_msg": row["error_msg"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def vector_text(row: sqlite3.Row) -> str:
    return "\n".join(p for p in (row["term"], row["synonyms"], row["description"]) if p)


def _visibility(user_id: int | None) -> tuple[str, tuple]:
    if user_id is None:
        return "", ()
    return " AND (user_id=0 OR user_id=?)", (int(user_id),)


def list_terms(conn: sqlite3.Connection, tenant_id: int, keyword: str = "",
               user_id: int | None = None) -> list[dict]:
    kw = (keyword or "").strip()
    clause, extra = _visibility(user_id)
    if kw:
        like = f"%{kw}%"
        rows = conn.execute(
            "SELECT * FROM business_term WHERE tenant_id=?" + clause +
            " AND (term LIKE ? OR description LIKE ? OR synonyms LIKE ?)"
            " ORDER BY updated_at DESC",
            (tenant_id, *extra, like, like, like)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM business_term WHERE tenant_id=?" + clause +
            " ORDER BY updated_at DESC",
            (tenant_id, *extra)).fetchall()
    return [term_dict(r) for r in rows]


def recall_terms(conn: sqlite3.Connection, tenant_id: int,
                 user_id: int | None = None) -> list[dict]:
    clause, extra = _visibility(user_id)
    rows = conn.execute(
        "SELECT * FROM business_term WHERE tenant_id=? AND is_recall=1" + clause +
        " ORDER BY id",
        (tenant_id, *extra)).fetchall()
    return [term_dict(r) for r in rows]


def insert_term(conn: sqlite3.Connection, tenant_id: int, *,
                term: str, description: str = "", synonyms: str = "",
                formula: str = "", related_tables: str = "", unit: str = "",
                is_recall: bool = True, user_id: int = 0) -> int:
    now = time.time()
    cur = conn.execute(
        "INSERT INTO business_term(tenant_id,user_id,term,description,synonyms,formula,"
        "related_tables,unit,is_recall,embedding_status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (tenant_id, int(user_id or 0), term, description, synonyms, formula,
         related_tables, unit, 1 if is_recall else 0, "PENDING", now, now))
    conn.commit()
    return int(cur.lastrowid)


def _mark_status(conn: sqlite3.Connection, tenant_id: int,
                 status: str, error: str = "") -> None:
    conn.execute(
        "UPDATE business_term SET embedding_status=?, error_msg=? WHERE tenant_id=?",
        (status, error[:500], tenant_id))
    conn.commit()


def _refresh_vectors(conn: sqlite3.Connection, tenant_id: int) -> int:
    rows = recall_terms(conn, tenant_id)
    store = VectorStore(collection_name=f"biz_terms_{tenant_id}")
    try:
        old = store.collection.get(include=[])["ids"]
        if old:
            store.collection.delete(ids=old)
        if rows:
            store.add_documents(
                [vector_text(r) for r in rows],
                metadatas=[{"term": r["term"], "tenant_id": tenant_id,
                            "user_id": int(r["user_id"] or 0)} for r in rows],
                ids=[f"t{r['id']}" for r in rows])
    finally:
        store.close()
    return len(rows)


def sync(conn: sqlite3.Connection, tenant_id: int) -> dict:
    try:
        count = _refresh_vectors(conn, tenant_id)
    except Exception as exc:        # noqa: BLE001 — 模型不可用等环境问题要落状态
        _mark_status(conn, tenant_id, "FAILED", str(exc))
        return {"ok": False, "hint": f"向量同步失败：{exc}"}
    _mark_status(conn, tenant_id, "COMPLETED")
    return {"ok": True, "count": count}
