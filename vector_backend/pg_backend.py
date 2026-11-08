from __future__ import annotations

import json
import os
import threading

import psycopg
from psycopg_pool import ConnectionPool

from vector_backend.filters import compile_where

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/allfusion_vectors"
DEFAULT_DIM = 512
_EF_SEARCH = 100
_SCHEMA_LOCK = 0x61665F76656374  

_POOLS: dict[str, ConnectionPool] = {}
_POOL_LOCK = threading.Lock()


def default_dsn() -> str:
    return os.getenv("AF_VECTOR_DSN", DEFAULT_DSN).strip() or DEFAULT_DSN


def vector_dim() -> int:
    return int(os.getenv("AF_VECTOR_DIM", str(DEFAULT_DIM)))


def _configure(conn) -> None:
    conn.execute(f"SET hnsw.ef_search = {_EF_SEARCH}")
    conn.execute("SET hnsw.iterative_scan = 'relaxed_order'")
    conn.commit()


def get_pool(dsn: str | None = None) -> ConnectionPool:
    dsn = (dsn or default_dsn()).strip()
    pool = _POOLS.get(dsn)
    if pool is not None:
        return pool
    with _POOL_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None:
            pool = ConnectionPool(dsn, min_size=1, max_size=8, open=True,
                                  configure=_configure)
            _POOLS[dsn] = pool
        return pool


def close_pools() -> None:
    for pool in list(_POOLS.values()):
        try:
            pool.close()
        except Exception:       # noqa: BLE001 — 关闭阶段的异常不该掩盖主流程
            pass
    _POOLS.clear()


def ensure_schema(dsn: str | None = None, dim: int | None = None) -> None:
    dim = dim or vector_dim()
    pool = get_pool(dsn)
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id text PRIMARY KEY,
                collection text NOT NULL,
                document text,
                embedding vector({dim}),
                metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS chunks_collection_idx "
                     "ON chunks(collection)")
        conn.execute("CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw "
                     "ON chunks USING hnsw (embedding vector_cosine_ops) "
                     "WITH (m = 16, ef_construction = 200)")


def _vec_literal(vec) -> str:
    return json.dumps([float(x) for x in vec], separators=(",", ":"))


def _json_literal(meta) -> str:
    return json.dumps(meta or {}, ensure_ascii=False)


def _parse_vector(raw: str | None) -> list[float] | None:
    if raw is None:
        return None
    return json.loads(raw)


class PgCollection:

    def __init__(self, name: str, *, dsn: str | None = None,
                 metadata: dict | None = None, embedding_function=None,
                 dim: int | None = None):
        self._name = name
        self._dsn = (dsn or default_dsn()).strip()
        self._ef = embedding_function
        self._dim = dim or vector_dim()
        ensure_schema(self._dsn, self._dim)


    @property
    def name(self) -> str:
        return self._name

    def count(self) -> int:
        with get_pool(self._dsn).connection() as conn:
            row = conn.execute(
                "SELECT count(*) FROM chunks WHERE collection = %s", (self._name,))
            return int(row.fetchone()[0])


    def _embed(self, documents: list[str] | None) -> list[list[float]] | None:
        if documents is None:
            return None
        if self._ef is None:
            raise ValueError(f"collection {self._name!r} 未提供 embedding_function，"
                             "无法从 documents 计算向量")
        return self._ef(list(documents))

    def upsert(self, ids: list[str], documents: list[str] | None = None,
               embeddings: list[list[float]] | None = None,
               metadatas: list[dict] | None = None) -> None:
        n = len(ids)
        if documents is not None and len(documents) != n:
            raise ValueError("upsert: documents 与 ids 长度不一致")
        if embeddings is None:
            embeddings = self._embed(documents)
        if embeddings is None:
            raise ValueError("upsert 需要 embeddings，或提供 documents + embedding_function")
        if len(embeddings) != n:
            raise ValueError("upsert: embeddings 与 ids 长度不一致")
        if metadatas is None:
            metadatas = [None] * n
        elif len(metadatas) != n:
            raise ValueError("upsert: metadatas 与 ids 长度不一致")

        rows = [(id_, self._name,
                 documents[i] if documents is not None else None,
                 _vec_literal(embeddings[i]),
                 _json_literal(metadatas[i])) for i, id_ in enumerate(ids)]
        sql = """
            INSERT INTO chunks (id, collection, document, embedding, metadata)
            VALUES (%s, %s, %s, %s::vector, %s::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                collection = EXCLUDED.collection,
                document   = EXCLUDED.document,
                embedding  = EXCLUDED.embedding,
                metadata   = EXCLUDED.metadata
        """
        with get_pool(self._dsn).connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)

    def update(self, ids: list[str], metadatas: list[dict] | None = None,
               documents: list[str] | None = None,
               embeddings: list[list[float]] | None = None) -> None:
        if not ids:
            return
        if metadatas is None and documents is None and embeddings is None:
            raise ValueError("update 至少需要 metadatas/documents/embeddings 之一")
        if documents is not None and len(documents) != len(ids):
            raise ValueError("update: documents 与 ids 长度不一致")
        if metadatas is not None and len(metadatas) != len(ids):
            raise ValueError("update: metadatas 与 ids 长度不一致")
        if embeddings is None and documents is not None:
            embeddings = self._embed(documents)
        if embeddings is not None and len(embeddings) != len(ids):
            raise ValueError("update: embeddings 与 ids 长度不一致")

        rows = []
        for i, id_ in enumerate(ids):
            rows.append((
                documents[i] if documents is not None else None,
                _vec_literal(embeddings[i]) if embeddings is not None else None,
                _json_literal(metadatas[i]) if metadatas is not None else None,
                id_, self._name,
            ))
        sql = """
            UPDATE chunks SET
                document  = COALESCE(%s, document),
                embedding = COALESCE(%s::vector, embedding),
                metadata  = COALESCE(%s::jsonb, metadata)
            WHERE id = %s AND collection = %s
        """
        with get_pool(self._dsn).connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)

    def delete(self, ids: list[str] | None = None, where: dict | None = None) -> None:
        if ids is None and where is None:
            raise ValueError("delete 需要 ids 或 where（禁止无范围删除）")
        sql = "DELETE FROM chunks WHERE collection = %s"
        params: list = [self._name]
        if ids is not None:
            sql += " AND id = ANY(%s)"
            params.append(list(ids))
        cond, cond_params = compile_where(where)
        if cond:
            sql += " AND " + cond
            params.extend(cond_params)
        with get_pool(self._dsn).connection() as conn:
            conn.execute(sql, params)


    def get(self, ids: list[str] | None = None, where: dict | None = None,
            include: list[str] | None = None, limit: int | None = None,
            offset: int | None = None) -> dict:
        include = ["documents", "metadatas"] if include is None else list(include)
        for key in include:
            if key not in ("documents", "metadatas", "embeddings"):
                raise ValueError(f"get 不支持的 include 项：{key}")

        cols = ["id"]
        if "documents" in include:
            cols.append("document")
        if "metadatas" in include:
            cols.append("metadata")
        if "embeddings" in include:
            cols.append("embedding::text AS embedding_text")

        sql = f"SELECT {', '.join(cols)} FROM chunks WHERE collection = %s"
        params: list = [self._name]
        if ids is not None:
            sql += " AND id = ANY(%s)"
            params.append(list(ids))
        cond, cond_params = compile_where(where)
        if cond:
            sql += " AND " + cond
            params.extend(cond_params)
        sql += " ORDER BY created_at, id"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))
        if offset is not None:
            sql += " OFFSET %s"
            params.append(int(offset))

        with get_pool(self._dsn).connection() as conn:
            rows = conn.execute(sql, params).fetchall()

        out: dict = {"ids": [r[0] for r in rows]}
        idx = 1
        if "documents" in include:
            out["documents"] = [r[idx] for r in rows]
            idx += 1
        if "metadatas" in include:
            out["metadatas"] = [r[idx] for r in rows]
            idx += 1
        if "embeddings" in include:
            out["embeddings"] = [_parse_vector(r[idx]) for r in rows]
        return out

    def _resolve_query_vectors(self, query_embeddings, query_texts) -> list[list[float]]:
        if query_embeddings is not None:
            return list(query_embeddings)
        if query_texts is not None:
            if self._ef is None:
                raise ValueError(f"collection {self._name!r} 未提供 embedding_function，"
                                 "无法从 query_texts 计算向量")
            return list(self._ef(list(query_texts)))
        raise ValueError("query 需要 query_embeddings 或 query_texts")

    def query(self, query_embeddings=None, query_texts=None, n_results: int = 10,
              where: dict | None = None, include: list[str] | None = None) -> dict:
        include = (["documents", "metadatas", "distances"]
                   if include is None else list(include))
        for key in include:
            if key not in ("documents", "metadatas", "distances", "embeddings"):
                raise ValueError(f"query 不支持的 include 项：{key}")

        vectors = self._resolve_query_vectors(query_embeddings, query_texts)
        cond, cond_params = compile_where(where)

        cols = ["id", "document", "metadata", "embedding::text AS embedding_text"]
        sql = ("SELECT " + ", ".join(cols) +
               ", embedding <=> %s::vector AS distance FROM chunks WHERE collection = %s")
        params: list = []
        if cond:
            sql += " AND " + cond
        sql += " ORDER BY embedding <=> %s::vector LIMIT %s"

        out: dict = {"ids": []}
        for key in ("documents", "metadatas", "distances", "embeddings"):
            if key in include:
                out[key] = []
        with get_pool(self._dsn).connection() as conn:
            for vec in vectors:
                lit = _vec_literal(vec)
                rows = conn.execute(sql, [lit, self._name, *cond_params, lit,
                                          int(n_results)]).fetchall()
                out["ids"].append([r[0] for r in rows])
                if "documents" in include:
                    out["documents"].append([r[1] for r in rows])
                if "metadatas" in include:
                    out["metadatas"].append([r[2] for r in rows])
                if "distances" in include:
                    out["distances"].append([float(r[4]) for r in rows])
                if "embeddings" in include:
                    out["embeddings"].append([_parse_vector(r[3]) for r in rows])
        return out

    def close(self) -> None:
        return None


def delete_collection(name: str, *, dsn: str | None = None) -> None:
    ensure_schema(dsn)
    with get_pool(dsn).connection() as conn:
        conn.execute("DELETE FROM chunks WHERE collection = %s", (name,))


def list_collections(*, dsn: str | None = None) -> list[str]:
    ensure_schema(dsn)
    with get_pool(dsn).connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT collection FROM chunks ORDER BY collection").fetchall()
    return [r[0] for r in rows]
