from __future__ import annotations
import hashlib
import itertools
import os
import time
import uuid
from inspect import signature
from pathlib import Path

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from vector_backend import factory

SCHEMA_VERSION = 1

DEFAULT_DIR = os.getenv(
    "MEMORY_SERVICE_DIR",
    str(Path(__file__).resolve().parents[1] / "data" / "memory_db"),
)
COLLECTION = "unified_memory"
_TOP_K = 5   
_MAX_ENTRIES = int(os.getenv("MEMORY_SERVICE_MAX", "1000"))
_TYPE_QUOTA = {"sql": 2, "rag": 2, "general": 1, "lesson": 2}   
_LESSON_MAX = int(os.getenv("MEMORY_SERVICE_LESSON_MAX", "200"))
_POOL_CAP = {"lesson": _LESSON_MAX}
_VALID_TYPES = tuple(_TYPE_QUOTA)

STAGE_FORMAL = "formal"
STAGE_CANDIDATE = "candidate"
_HOT_PENALTY = 0.25                  
_PROMOTE_MIN_CONF = float(os.getenv("MEMORY_SERVICE_PROMOTE_MIN_CONF", "0.7"))
_PROMOTE_MIN_FACTS = int(os.getenv("MEMORY_SERVICE_PROMOTE_MIN_FACTS", "3"))
_PROMOTE_MIN_AGE_HOURS = float(os.getenv("MEMORY_SERVICE_PROMOTE_MIN_AGE_HOURS", "24"))
_PROMOTE_CLUSTER_SIM = float(os.getenv("MEMORY_SERVICE_PROMOTE_CLUSTER_SIM", "0.85"))


def _is_lesson(meta: dict | None) -> bool:
    return (meta or {}).get("memory_type") == "lesson"


class _HashEmbedding(EmbeddingFunction):

    def __call__(self, input: Documents) -> Embeddings:
        out = []
        for doc in input:
            vec = [0.0] * 64
            s = " ".join(doc.split())
            grams = [s[i:i + 2] for i in range(max(len(s) - 1, 1))]
            for g in grams:
                h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
                vec[h % 64] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


class _BgeEmbedding(EmbeddingFunction):

    def __call__(self, input: Documents) -> Embeddings:
        from kb_service.embeddings import get_embeddings_batch
        return get_embeddings_batch(list(input))


def _is_valid_ef(obj) -> bool:
    try:
        return tuple(signature(obj.__class__.__call__).parameters) == ("self", "input")
    except (TypeError, ValueError):
        return False


class _CallableEmbedding(EmbeddingFunction):

    def __init__(self, fn):
        self._fn = fn

    def __call__(self, input: Documents) -> Embeddings:
        return self._fn(list(input))


def _make_embedding():
    if os.getenv("MEMORY_SERVICE_EMBEDDING", "") == "hash":   
        return _HashEmbedding()
    return _BgeEmbedding()


class MemoryStore:
    def __init__(self, path: str | None = DEFAULT_DIR, embedding=None,
                 max_entries: int = _MAX_ENTRIES):
        self._path = path
        if embedding is not None and not _is_valid_ef(embedding):
            embedding = _CallableEmbedding(embedding)   
        self._col = factory.get_collection(
            COLLECTION, path=path, metadata={"hnsw:space": "cosine"},
            embedding_function=embedding)
        self._max = max_entries
        self._seq = itertools.count(int(time.time() * 1_000_000))

    @staticmethod
    def doc_id(user_id: str, tenant_id: str, text: str) -> str:
        return hashlib.sha1(f"{tenant_id}|{user_id}|{text}".encode("utf-8")).hexdigest()


    def add(self, text: str, metadata: dict) -> str:
        if not text.strip():
            return ""
        mtype = metadata.get("memory_type") or metadata.get("type") or ""
        if mtype not in _VALID_TYPES:
            raise ValueError(
                f"记忆必须打 memory_type 标签（{'|'.join(_VALID_TYPES)}），收到：{mtype!r}")
        now = time.time()
        try:
            created = float(metadata.get("created_at") or now)
        except (TypeError, ValueError):
            created = now
        meta = {"schema_version": SCHEMA_VERSION,
                "user_id": metadata.get("user_id", ""),
                "tenant_id": metadata.get("tenant_id", "default"),
                "memory_type": mtype,
                "source": metadata.get("source", ""),
                "importance": float(metadata.get("importance", 1.0)),
                "access_count": int(metadata.get("access_count", 0)),
                "last_accessed_at": float(metadata.get("last_accessed_at", 0.0)),
                "status": metadata.get("status", "active"),
                "created_at": created,
                "created_seq": float(metadata.get("created_seq") or next(self._seq))}
        meta.update({k: v for k, v in metadata.items()
                     if k not in meta and isinstance(v, (str, int, float, bool))})
        stage = str(meta.get("stage") or STAGE_FORMAL)
        dim = str(meta.get("subject_dim") or "").strip()
        value = str(meta.get("subject_value") or "").strip()
        archive_old = (stage == STAGE_FORMAL
                       and str(meta.get("status") or "active") == "active"
                       and dim and value)
        if archive_old:
            meta.setdefault("valid_from", created)
            meta.setdefault("valid_until", "")
        _id = self.doc_id(meta["user_id"], meta["tenant_id"], text)
        self._col.upsert(ids=[_id], documents=[text], metadatas=[meta])
        if archive_old:
            self._archive_same_dim(meta, _id, dim, value)
        if self._col.count() > self._max:
            self._evict_cold()
        if mtype in _POOL_CAP:
            self._evict_pool(mtype, _POOL_CAP[mtype])
        return _id

    def _archive_same_dim(self, meta: dict, new_id: str, dim: str,
                          value: str) -> None:
        cond = {"$and": [{"tenant_id": meta.get("tenant_id", "default")},
                         {"user_id": meta.get("user_id", "")},
                         {"subject_dim": dim}, {"status": "active"}]}
        valid_from = meta.get("valid_from", meta.get("created_at")) or 0.0
        updates: list[tuple[str, dict]] = []
        for row in self.find(cond):
            if row["id"] == new_id:
                continue
            old = dict(row["metadata"] or {})
            if str(old.get("stage") or STAGE_FORMAL) == STAGE_CANDIDATE:
                continue
            if str(old.get("subject_dim") or "").strip() != dim:
                continue
            old.update(self._supersede_meta(old, value, new_id))
            old["valid_until"] = float(valid_from)
            updates.append((row["id"], old))
        if updates:
            self._col.update(ids=[i for i, _ in updates],
                             metadatas=[m for _, m in updates])

    def find(self, where: dict) -> list[dict]:
        got = self._col.get(where=where, include=["metadatas", "documents"])
        return [{"id": i, "text": d, "metadata": m}
                for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])]


    def recall(self, query: str, where: dict, top_k: int = _TOP_K) -> list[dict]:
        if not query.strip():
            return []
        pool = max(top_k * 3, 9)              
        final_where = ({"$and": [where, {"status": "active"}]}
                       if where else {"status": "active"})
        res = self._col.query(query_texts=[query], where=final_where, n_results=pool)
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        hits = [{"text": d, "metadata": m, "distance": dist}
                for d, m, dist in zip(docs, metas, dists)]
        hits = self._demote_candidates(hits)   
        picked = self._quota_cut(hits, top_k)
        self._touch(picked)                   
        return picked

    @staticmethod
    def _demote_candidates(hits: list[dict]) -> list[dict]:
        out = []
        for h in hits:
            d = h.get("distance")
            if (h.get("metadata") or {}).get("stage") == STAGE_CANDIDATE \
                    and isinstance(d, (int, float)):
                h = {**h, "base_distance": float(d), "distance": float(d) + _HOT_PENALTY}
            out.append(h)
        out.sort(key=lambda h: h.get("distance")
                 if isinstance(h.get("distance"), (int, float)) else float("inf"))
        return out

    @staticmethod
    def _quota_cut(hits: list[dict], top_k: int) -> list[dict]:
        used: dict[str, int] = {}
        out: list[dict] = []
        leftovers: list[dict] = []             
        for h in hits:
            t = (h.get("metadata") or {}).get("memory_type", "general")
            if used.get(t, 0) >= _TYPE_QUOTA.get(t, 1):
                leftovers.append(h)
                continue
            used[t] = used.get(t, 0) + 1
            out.append(h)
            if len(out) >= top_k:
                return out                     
        for h in leftovers:                    
            out.append(h)
            if len(out) >= top_k:
                break
        out.sort(key=lambda h: h.get("distance")
                 if isinstance(h.get("distance"), (int, float)) else float("inf"))
        return out

    def _touch(self, picked: list[dict]) -> None:
        if not picked:
            return
        now = time.time()
        ids, metas = [], []
        for h in picked:
            m = h["metadata"]
            m["access_count"] = int(m.get("access_count", 0)) + 1
            m["last_accessed_at"] = now
            ids.append(self.doc_id(m.get("user_id", ""), m.get("tenant_id", "default"),
                                   h["text"]))
            metas.append(m)
        self._col.update(ids=ids, metadatas=metas)


    @staticmethod
    def _cold_key(item: tuple) -> tuple:
        meta = item[1] or {}
        return (float(meta.get("access_count", 0)),
                float(meta.get("last_accessed_at", 0.0)),
                float(meta.get("created_seq", 0.0)))

    def _archive_rows(self, items: list[tuple]) -> None:
        if not items:
            return
        metas = []
        for _, meta, _doc in items:
            updated = dict(meta or {})
            updated["status"] = "archived"
            metas.append(updated)
        self._col.update(ids=[i for i, _, _ in items], metadatas=metas)

    def _active_rows(self, where: dict | None = None) -> list[tuple]:
        cond = {"status": "active"}
        if where:
            cond = {"$and": [cond, where]}
        got = self._col.get(where=cond, include=["metadatas", "documents"])
        items = list(zip(got["ids"], got["metadatas"], got["documents"]))
        return sorted(items, key=self._cold_key)

    def _evict_cold(self) -> None:
        others = [it for it in self._active_rows() if not _is_lesson(it[1])]
        over = len(others) - self._max
        if over > 0:
            self._archive_rows(others[:over])

    def _evict_pool(self, mtype: str, cap: int) -> None:
        pool = [it for it in self._active_rows({"memory_type": mtype})]
        over = len(pool) - cap
        if over > 0:
            self._archive_rows(pool[:over])

    def _candidate_rows(self, cond: dict) -> tuple[list[dict], list | None, str]:
        try:
            got = self._col.get(where=cond,
                                include=["metadatas", "documents", "embeddings"])
        except (ValueError, TypeError, KeyError, NotImplementedError):
            got = self._col.get(where=cond, include=["metadatas", "documents"])
            embeddings = None
        else:
            embeddings = got.get("embeddings")
        rows = [{"id": i, "text": d, "metadata": dict(m or {})}
                for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])]
        if embeddings is None or len(embeddings) != len(rows) \
                or any(e is None for e in embeddings):
            return rows, None, "subject_dim_fallback"
        return rows, embeddings, "embedding"

    @staticmethod
    def _cosine(a, b) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    def _embedding_clusters(self, items: list[tuple[dict, list]],
                            threshold: float) -> list[list[dict]]:
        clusters: list[dict] = []
        for row, emb in items:
            best, best_sim = None, -1.0
            for c in clusters:
                sim = self._cosine(emb, c["centroid"])
                if sim > best_sim:
                    best, best_sim = c, sim
            if best is not None and best_sim >= threshold:
                n = len(best["members"])
                best["members"].append(row)
                best["centroid"] = [(x * n + y) / (n + 1)
                                    for x, y in zip(best["centroid"], emb)]
            else:
                clusters.append({"centroid": [float(x) for x in emb],
                                 "members": [row]})
        return [c["members"] for c in clusters]

    @staticmethod
    def _preimage(meta: dict, batch: str) -> dict:
        def text(key: str) -> str:
            v = meta.get(key)
            return "" if v is None else str(v)
        return {"promotion_batch": batch,
                "prev_stage": text("stage"),
                "prev_status": text("status"),
                "prev_valid_until": text("valid_until"),
                "prev_superseded_by": text("superseded_by"),
                "prev_status_reason": text("status_reason")}

    def promote_candidates(self, where: dict | None = None,
                           min_conf: float | None = None) -> dict:
        cond = {"$and": [{"stage": STAGE_CANDIDATE}, {"status": "active"}]}
        if where:
            cond = {"$and": [cond, where]}
        rows, embeddings, clustering = self._candidate_rows(cond)

        stats = {"promoted": 0, "superseded": 0, "duplicate": 0,
                 "below_threshold": 0, "below_count": 0, "too_young": 0,
                 "skipped_no_dim": 0, "skipped_no_value": 0,
                 "groups": 0, "clustering": clustering}

        if clustering == "embedding":
            by_user: dict[tuple, list[tuple[dict, list]]] = {}
            for row, emb in zip(rows, embeddings):
                key = (str(row["metadata"].get("tenant_id", "default")),
                       str(row["metadata"].get("user_id", "")))
                by_user.setdefault(key, []).append((row, emb))
            clusters = [members for items in by_user.values()
                        for members in self._embedding_clusters(items,
                                                                _PROMOTE_CLUSTER_SIM)]
        else:
            groups: dict[tuple, list[dict]] = {}
            for row in rows:
                dim = str(row["metadata"].get("subject_dim") or "").strip()
                if not dim:
                    stats["skipped_no_dim"] += 1
                    continue
                key = (str(row["metadata"].get("tenant_id", "default")),
                       str(row["metadata"].get("user_id", "")), dim)
                groups.setdefault(key, []).append(row)
            clusters = list(groups.values())
        stats["groups"] = len(clusters)

        threshold = _PROMOTE_MIN_CONF if min_conf is None else float(min_conf)
        min_age = _PROMOTE_MIN_AGE_HOURS * 3600.0
        batch = uuid.uuid4().hex          
        now = time.time()
        updates: list[tuple[str, dict]] = []
        for members in clusters:
            members.sort(key=lambda r: (
                float(r["metadata"].get("confidence", 0.0) or 0.0),
                float(r["metadata"].get("created_seq", 0.0) or 0.0)), reverse=True)
            winner = members[0]
            wmeta = winner["metadata"]
            dim = str(wmeta.get("subject_dim") or "").strip()
            if not dim:
                stats["skipped_no_dim"] += len(members)
                continue
            value = str(wmeta.get("subject_value") or "").strip()
            if not value:
                stats["skipped_no_value"] += len(members)
                continue
            if len(members) < _PROMOTE_MIN_FACTS:
                stats["below_count"] += len(members)
                continue
            ages = []
            for row in members:
                try:
                    ages.append(float(row["metadata"].get("created_at") or 0.0))
                except (TypeError, ValueError):
                    ages.append(0.0)
            earliest = min(ages)
            if earliest <= 0 or now - earliest < min_age:
                stats["too_young"] += len(members)
                continue
            if float(wmeta.get("confidence", 0.0) or 0.0) < threshold:
                stats["below_threshold"] += len(members)
                continue

            tid = str(wmeta.get("tenant_id", "default"))
            uid = str(wmeta.get("user_id", ""))
            valid_from = float(wmeta.get("created_at") or now)
            winner_meta = dict(wmeta)
            winner_meta.update(self._preimage(winner_meta, batch))
            winner_meta.update({"stage": STAGE_FORMAL,
                                "promoted_from": winner["id"],
                                "promoted_at": now,
                                "valid_from": valid_from,
                                "valid_until": ""})
            updates.append((winner["id"], winner_meta))
            stats["promoted"] += 1

            for row in members[1:]:
                meta = dict(row["metadata"])
                meta.update(self._preimage(meta, batch))
                meta.update(self._supersede_meta(meta, value, winner["id"]))
                meta["valid_until"] = valid_from
                updates.append((row["id"], meta))
                stats["duplicate" if meta["status_reason"] == "duplicate"
                      else "superseded"] += 1

            old_where = [{"subject_dim": dim}, {"tenant_id": tid}, {"user_id": uid},
                         {"status": "active"}]
            if where:
                old_where.append(where)
            for row in self.find({"$and": old_where}):
                meta = row["metadata"] or {}
                if row["id"] == winner["id"]:
                    continue
                if str(meta.get("stage") or STAGE_FORMAL) == STAGE_CANDIDATE:
                    continue                    
                meta = dict(meta)
                meta.update(self._preimage(meta, batch))
                meta.update(self._supersede_meta(meta, value, winner["id"]))
                meta["valid_until"] = valid_from
                updates.append((row["id"], meta))
                stats["duplicate" if meta["status_reason"] == "duplicate"
                      else "superseded"] += 1

        if updates:
            self._col.update(ids=[i for i, _ in updates],
                             metadatas=[m for _, m in updates])
        return stats

    def rollback_promotion(self, batch_id: str) -> dict:
        batch_id = str(batch_id or "").strip()
        if not batch_id:
            return {"ok": False, "error": "batch_id 为空"}
        rows = self.find({"promotion_batch": batch_id})
        if not rows:
            return {"ok": False, "error": f"promotion batch not found: {batch_id}",
                    "batch": batch_id, "restored": 0}
        ids, metas = [], []
        for row in rows:
            meta = dict(row["metadata"] or {})
            meta["stage"] = str(meta.get("prev_stage") or "")
            meta["status"] = str(meta.get("prev_status") or "active")
            meta["valid_until"] = self._valid_until_value(meta.get("prev_valid_until"))
            meta["superseded_by"] = str(meta.get("prev_superseded_by") or "")
            meta["status_reason"] = str(meta.get("prev_status_reason") or "")
            for key in ("promotion_batch", "prev_stage", "prev_status",
                        "prev_valid_until", "prev_superseded_by", "prev_status_reason"):
                meta[key] = ""
            ids.append(row["id"])
            metas.append(meta)
        self._col.update(ids=ids, metadatas=metas)
        return {"ok": True, "batch": batch_id, "restored": len(ids)}

    @staticmethod
    def _valid_until_value(raw) -> str | float:
        text = "" if raw is None else str(raw).strip()
        if not text:
            return ""
        try:
            return float(text)
        except ValueError:
            return ""

    @staticmethod
    def _supersede_meta(meta: dict, winner_value: str, winner_id: str) -> dict:
        same = str(meta.get("subject_value") or "") == winner_value
        return {"status": "archived",
                "status_reason": "duplicate" if same else "superseded",
                "superseded_by": winner_id}

    def archive(self, record_id: str) -> dict:
        try:
            got = self._col.get(ids=[record_id], include=["metadatas"])
            if not got["ids"]:
                return {"ok": False, "error": f"record not found: {record_id}"}
            meta = dict(got["metadatas"][0] or {})
            meta["status"] = "archived"
            self._col.update(ids=[record_id], metadatas=[meta])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def revive(self, ids: list[str]) -> None:
        if not ids:
            return
        got = self._col.get(ids=ids, include=["metadatas"])
        metas = []
        for meta in got["metadatas"]:
            updated = dict(meta or {})
            updated["status"] = "active"
            metas.append(updated)
        if metas:
            self._col.update(ids=got["ids"], metadatas=metas)

    def close(self) -> None:
        try:
            self._col.close()
        except Exception:
            pass


_STORE: MemoryStore | None = None


def get_store() -> MemoryStore:
    global _STORE
    if _STORE is None:
        _STORE = MemoryStore(embedding=_make_embedding())
    return _STORE


def promote_candidates(min_conf: float | None = None,
                       where: dict | None = None) -> dict:
    return get_store().promote_candidates(where, min_conf=min_conf)


def rollback_promotion(batch_id: str) -> dict:
    return get_store().rollback_promotion(batch_id)
