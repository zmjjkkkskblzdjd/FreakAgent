import hashlib
import os
from pathlib import Path

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from kb_service.embeddings import get_embedding, get_embeddings_batch
from kb_service.kb_config import DEFAULT_DIR
from vector_backend import factory

DEFAULT_PERSIST_DIR = DEFAULT_DIR


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


class VectorStore:

    HIERARCHICAL_PARENT = "kb_parent_chunks"
    HIERARCHICAL_CHILD = "kb_child_chunks"
    HIERARCHICAL_SUMMARY = "kb_summaries"

    def __init__(
        self,
        collection_name: str = "qa_kb",
        persist_dir: str = DEFAULT_PERSIST_DIR,
        embedding=None,
        enable_hierarchical: bool = False,
        hierarchical_prefix: str | None = None,
    ):
        if persist_dir is not None and factory.backend_name() == "chroma":
            Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self._embedding = embedding

        self.collection_name = collection_name
        self.collection = factory.get_collection(
            collection_name, path=persist_dir, metadata=self._hnsw_meta())

        self.hierarchical_prefix = hierarchical_prefix or "kb"
        self.HIERARCHICAL_PARENT = f"{self.hierarchical_prefix}_parent_chunks"
        self.HIERARCHICAL_CHILD = f"{self.hierarchical_prefix}_child_chunks"
        self.HIERARCHICAL_SUMMARY = f"{self.hierarchical_prefix}_summaries"
        self.hierarchical_enabled = enable_hierarchical
        self.parent_collection = None
        self.child_collection = None
        self.summary_collection = None
        if enable_hierarchical:
            self.parent_collection = factory.get_collection(
                self.HIERARCHICAL_PARENT, path=persist_dir, metadata=self._hnsw_meta())
            self.child_collection = factory.get_collection(
                self.HIERARCHICAL_CHILD, path=persist_dir, metadata=self._hnsw_meta())
            self.summary_collection = factory.get_collection(
                self.HIERARCHICAL_SUMMARY, path=persist_dir, metadata=self._hnsw_meta())

        flat_count = self.collection.count()
        h_info = ""
        if enable_hierarchical:
            h_info = (
                f" | hierarchical: "
                f"parent={self.parent_collection.count()}, "
                f"child={self.child_collection.count()}, "
                f"summary={self.summary_collection.count()}"
            )
        print(f"向量库 '{collection_name}' 已加载"
              f"（backend={factory.backend_name()}, {persist_dir}），"
              f"包含 {flat_count} 个 flat chunk{h_info}")

    @staticmethod
    def _hnsw_meta() -> dict:
        return {
            "hnsw:space": "cosine",
            "hnsw:construction_ef": 200,
            "hnsw:M": 16,
        }


    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._embedding(texts) if self._embedding else get_embeddings_batch(texts)

    def _embed_one(self, text: str) -> list[float]:
        return (self._embedding([text])[0] if self._embedding else get_embedding(text))


    def add_documents(
            self,
            documents: list[str],
            metadatas: list[dict] = None,
            ids: list[str] = None,
            batch_size: int = 50,
    ) -> list[str]:
        if ids is None:
            ids = [hashlib.md5(doc.encode("utf-8")).hexdigest() for doc in documents]

        seen = {}
        unique_ids = []
        for id_ in ids:
            if id_ in seen:
                seen[id_] += 1
                unique_ids.append(f"{id_}_{seen[id_]}")
            else:
                seen[id_] = 0
                unique_ids.append(id_)
        ids = unique_ids

        if metadatas is None:
            metadatas = [None] * len(documents)

        print(f"正在嵌入 {len(documents)} 个 chunk...")
        embeddings = self._embed_batch(documents)

        for i in range(0, len(documents), batch_size):
            batch_end = min(i + batch_size, len(documents))
            self.collection.upsert(
                ids=ids[i:batch_end],
                documents=documents[i:batch_end],
                embeddings=embeddings[i:batch_end],
                metadatas=metadatas[i:batch_end]
            )

        print(f"✅ 成功写入 {len(documents)} 个 flat chunk")
        return ids

    def get_all_chunks(self, where: dict = None) -> list[dict]:
        kwargs = {"include": ["documents", "metadatas"]}
        if where:
            kwargs["where"] = where
        results = self.collection.get(**kwargs)
        hits = []
        for cid, content, meta in zip(results["ids"], results["documents"], results["metadatas"]):
            meta = meta or {}
            hits.append({
                "chunk_id": cid, "content": content,
                "source": meta.get("source", "unknown"),
                "doc_type": meta.get("doc_type", ""),
                "chunk_index": meta.get("chunk_index", -1),
            })
        return hits

    def get_by_ids(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        results = self.collection.get(ids=ids, include=["documents", "metadatas"])
        hits = []
        for cid, content, meta in zip(results["ids"], results["documents"], results["metadatas"]):
            meta = meta or {}
            hits.append({
                "chunk_id": cid, "content": content,
                "source": meta.get("source", "unknown"),
                "doc_type": meta.get("doc_type", ""),
                "chunk_index": meta.get("chunk_index", -1),
            })
        return hits

    def query(
            self,
            query: str,
            n_results: int = 5,
            where: dict = None,
    ) -> list[dict]:
        total = self.collection.count()
        if total == 0:
            return []

        n_results = min(n_results, total)

        kwargs = {
            "query_embeddings": [self._embed_one(query)],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where is not None:
            kwargs["where"] = where

        results = self.collection.query(**kwargs)

        hits = []
        for chunk_id, content, metadata, distance in zip(
                results.get("ids", [[]])[0],
                results.get("documents", [[]])[0],
                results.get("metadatas", [[]])[0],
                results.get("distances", [[]])[0],
        ):
            metadata = metadata or {}
            hits.append({
                "chunk_id": chunk_id,
                "content": content,
                "source": metadata.get("source", "unknown"),
                "doc_type": metadata.get("doc_type", ""),
                "chunk_index": metadata.get("chunk_index", -1),
                "score": 1 - distance,
            })
        return hits

    def search(self, query: str, n_results: int = 5, where: dict = None) -> list[dict]:
        total = self.collection.count()
        if total == 0:
            return []
        n_results = min(n_results, total)
        kwargs = {
            "query_embeddings": [self._embed_one(query)],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where is not None:
            kwargs["where"] = where
        results = self.collection.query(**kwargs)
        hits = []
        for cid, content, metadata, distance in zip(
                results.get("ids", [[]])[0],
                results.get("documents", [[]])[0],
                results.get("metadatas", [[]])[0],
                results.get("distances", [[]])[0],
        ):
            hits.append({
                "id": cid,
                "content": content,
                "metadata": metadata or {},
                "score": 1 - distance,
            })
        return hits

    def close(self) -> None:
        for col in [self.collection, self.parent_collection,
                    self.child_collection, self.summary_collection]:
            if col is not None:
                col.close()

    def get_stats(self) -> dict:
        stats = {
            "flat_documents": self.collection.count(),
            "flat_collection_name": self.collection.name,
        }
        if self.hierarchical_enabled:
            stats.update({
                "parent_documents": self.parent_collection.count(),
                "child_documents": self.child_collection.count(),
                "summary_documents": self.summary_collection.count(),
            })
        return stats

    def clear(self) -> None:
        for col in [self.collection, self.parent_collection,
                    self.child_collection, self.summary_collection]:
            if col is None:
                continue
            if col.count() == 0:
                continue
            ids = col.get(include=[])["ids"]
            if ids:
                col.delete(ids=ids)
            print(f"已清空 collection '{col.name}'")

    def list_sources(self) -> list[str]:
        if self.collection.count() == 0:
            return []
        results = self.collection.get(include=["metadatas"])
        return sorted(set(
            (m or {}).get("source", "unknown") for m in results["metadatas"]
        ))


    def get_all_parent_chunks(self) -> list[dict]:
        if not self.hierarchical_enabled:
            raise RuntimeError("未启用 hierarchical 模式")
        results = self.parent_collection.get(include=["documents", "metadatas"])
        hits = []
        for pid, content, meta in zip(results["ids"], results["documents"], results["metadatas"]):
            meta = meta or {}
            hits.append({
                "chunk_id": pid, "content": content,
                "source": meta.get("source", "unknown"),
                "doc_type": meta.get("doc_type", ""),
                "chunk_index": meta.get("chunk_idx", -1),
            })
        return hits

    def list_parent_meta(self) -> list[dict]:
        if not self.hierarchical_enabled or self.parent_collection is None:
            return []
        results = self.parent_collection.get(include=["metadatas"])
        return [m or {} for m in (results.get("metadatas") or [])]

    def get_parent_by_ids(self, parent_ids: list[str]) -> dict[str, dict]:
        if not parent_ids or not self.hierarchical_enabled:
            return {}
        results = self.parent_collection.get(ids=parent_ids, include=["documents", "metadatas"])
        out = {}
        for pid, content, meta in zip(results["ids"], results["documents"], results["metadatas"]):
            meta = meta or {}
            out[pid] = {
                "chunk_id": pid, "content": content,
                "source": meta.get("source", "unknown"),
                "doc_type": meta.get("doc_type", ""),
                "chunk_index": meta.get("chunk_idx", -1),
            }
        return out


    def add_hierarchical(
        self,
        parent_ids: list[str],
        parent_chunks: list[str],
        parent_metas: list[dict],
        child_ids: list[str],
        child_chunks: list[str],
        child_metas: list[dict],
        summary_ids: list[str],
        summary_chunks: list[str],
        summary_metas: list[dict],
        batch_size: int = 50,
    ) -> dict:
        if not self.hierarchical_enabled:
            raise RuntimeError("VectorStore 未启用 hierarchical 模式，初始化时传 enable_hierarchical=True")

        if parent_chunks:
            for i in range(0, len(parent_chunks), batch_size):
                end = min(i + batch_size, len(parent_chunks))
                self.parent_collection.upsert(
                    ids=parent_ids[i:end],
                    documents=parent_chunks[i:end],
                    metadatas=parent_metas[i:end],
                    embeddings=self._embed_batch(parent_chunks[i:end]),
                )
            print(f"  parents: {len(parent_chunks)}")

        if child_chunks:
            for i in range(0, len(child_chunks), batch_size):
                end = min(i + batch_size, len(child_chunks))
                self.child_collection.upsert(
                    ids=child_ids[i:end],
                    documents=child_chunks[i:end],
                    metadatas=child_metas[i:end],
                    embeddings=self._embed_batch(child_chunks[i:end]),
                )
            print(f"  children: {len(child_chunks)}")

        if summary_chunks:
            for i in range(0, len(summary_chunks), batch_size):
                end = min(i + batch_size, len(summary_chunks))
                self.summary_collection.upsert(
                    ids=summary_ids[i:end],
                    documents=summary_chunks[i:end],
                    metadatas=summary_metas[i:end],
                    embeddings=self._embed_batch(summary_chunks[i:end]),
                )
            print(f"  summaries: {len(summary_chunks)}")

        return {
            "parents": len(parent_chunks),
            "children": len(child_chunks),
            "summaries": len(summary_chunks),
        }

    def query_parent_child(
        self,
        query: str,
        top_k_child: int = 5,
        top_k_summary: int = 3,
    ) -> list[dict]:
        if not self.hierarchical_enabled:
            raise RuntimeError("未启用 hierarchical 模式")

        child_total = self.child_collection.count()
        summary_total = self.summary_collection.count()
        if child_total == 0 and summary_total == 0:
            return []

        query_vec = self._embed_one(query)

        child_hits = []
        if child_total > 0:
            k = min(top_k_child, child_total)
            r = self.child_collection.query(
                query_embeddings=[query_vec],
                n_results=k,
                include=["metadatas", "distances"],
            )
            for cid, dist, meta in zip(
                r.get("ids", [[]])[0],
                r.get("distances", [[]])[0],
                r.get("metadatas", [[]])[0],
            ):
                meta = meta or {}
                child_hits.append({
                    "chunk_id": cid,
                    "score": 1 - dist,
                    "hit_type": "child",
                    "parent_id": meta.get("parent_id", ""),
                    "child_id": cid,
                    "source": meta.get("source", "unknown"),
                    "doc_type": meta.get("doc_type", ""),
                })

        summary_hits = []
        if summary_total > 0:
            k = min(top_k_summary, summary_total)
            r = self.summary_collection.query(
                query_embeddings=[query_vec],
                n_results=k,
                include=["metadatas", "distances"],
            )
            for sid, dist, meta in zip(
                r.get("ids", [[]])[0],
                r.get("distances", [[]])[0],
                r.get("metadatas", [[]])[0],
            ):
                meta = meta or {}
                summary_hits.append({
                    "chunk_id": sid,
                    "score": 1 - dist,
                    "hit_type": "summary",
                    "parent_id": meta.get("parent_id", ""),
                    "child_id": None,
                    "source": meta.get("source", "unknown"),
                    "doc_type": meta.get("doc_type", ""),
                })

        all_hits = child_hits + summary_hits
        dedup: dict[str, dict] = {}
        for h in all_hits:
            pid = h["parent_id"] or h["chunk_id"]
            if pid not in dedup or h["score"] > dedup[pid]["score"]:
                dedup[pid] = h
        all_hits = sorted(dedup.values(), key=lambda h: h["score"], reverse=True)

        parent_ids = list({h["parent_id"] for h in all_hits if h.get("parent_id")})
        parent_map: dict[str, str] = {}
        if parent_ids:
            parent_results = self.parent_collection.get(
                ids=parent_ids, include=["documents"]
            )
            for pid, content in zip(parent_results["ids"], parent_results["documents"]):
                parent_map[pid] = content

        for h in all_hits:
            pid = h.get("parent_id")
            if pid and pid in parent_map:
                h["content"] = parent_map[pid]
            else:
                cid = h.get("child_id") or h["chunk_id"]
                c = self.child_collection.get(ids=[cid], include=["documents"])
                h["content"] = c["documents"][0] if c["documents"] else ""

        return all_hits



_SEMANTIC_KB_COLLECTIONS = {"glossary": "glossary", "fewshot": "fewshot"}
_SEARCH_STORES: dict[str, "VectorStore"] = {}


def search_semantic(kb: str, query: str, top_k: int = 5,
                    where: dict | None = None) -> list[dict]:
    name = _SEMANTIC_KB_COLLECTIONS.get(kb)
    if not name:
        raise ValueError(f"非法 kb：{kb}（仅允许 glossary / fewshot）")
    store = _SEARCH_STORES.get(name)
    if store is None:
        store = _SEARCH_STORES[name] = VectorStore(name)
    return store.search(query, n_results=top_k, where=where or None)
