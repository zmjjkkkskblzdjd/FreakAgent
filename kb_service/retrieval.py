from __future__ import annotations

import jieba
import numpy as np
from rank_bm25 import BM25Okapi


class BM25Index:

    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        tokenized = [list(jieba.cut(c["content"])) for c in chunks]
        self.idx_to_id = [c["chunk_id"] for c in chunks]
        self.bm25 = BM25Okapi(tokenized)

    def search(self, query: str, n: int, allowed_ids: set[str] | None) -> dict:
        tokens = list(jieba.cut(query))
        scores = self.bm25.get_scores(tokens)

        if allowed_ids:
            allowed_indices = [i for i, doc_id in enumerate(self.idx_to_id)
                               if doc_id in allowed_ids]
            allowed_scores = [scores[i] for i in allowed_indices]
            max_score = max(allowed_scores) if allowed_scores and max(allowed_scores) > 0 else 1
        else:
            max_score = max(scores) if max(scores) > 0 else 1

        top_indices = np.argsort(scores)[::-1][:n]

        results = {}
        for idx in top_indices:
            if scores[idx] > 0:
                doc_id = self.idx_to_id[idx]
                if allowed_ids is not None and doc_id not in allowed_ids:
                    continue
                results[doc_id] = {
                    "chunk_id": doc_id,
                    "content": self.chunks[idx]["content"],
                    "source": self.chunks[idx].get("source", "unknown"),
                    "doc_type": self.chunks[idx].get("doc_type", ""),
                    "chunk_index": self.chunks[idx].get("chunk_index", -1),
                    "keyword_score": float(scores[idx] / max_score),
                }
        return results


class HierarchicalRetriever:

    def __init__(
        self,
        store,
        vector_weight: float = 0.6,
        keyword_weight: float = 0.4,
        score_threshold: float = 0.35,
        reranker=None,
    ):
        assert getattr(store, "hierarchical_enabled", False), \
            "HierarchicalRetriever 需要 store.enable_hierarchical=True"

        self.store = store
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self.score_threshold = score_threshold
        self.reranker = reranker

        parents = store.get_all_parent_chunks()
        self.bm25 = BM25Index(parents) if parents else None

        self._child_count = store.child_collection.count()
        self._summary_count = store.summary_collection.count()

    def retrieve(
        self,
        query: str,
        n: int = 5,
        top_k_child: int | None = None,
        top_k_summary: int | None = None,
        use_rerank: bool = True,
    ) -> list[dict]:
        if self.bm25 is None or (self._child_count == 0 and self._summary_count == 0):
            return []

        has_rerank = (use_rerank and self.reranker is not None
                      and getattr(self.reranker, "available", False))
        pool_n = max(n * 4, n + 5) if has_rerank else n
        k_child = top_k_child or (pool_n if has_rerank else n * 2)
        k_summary = top_k_summary or max(1, n // 2)

        vec_hits = self.store.query_parent_child(
            query, top_k_child=k_child, top_k_summary=k_summary,
        )
        vec: dict[str, dict] = {}
        for h in vec_hits:
            pid = h.get("parent_id") or h["chunk_id"]
            h["chunk_id"] = pid
            vec[pid] = h
        parent_ids_from_vec = set(vec.keys())

        allowed = parent_ids_from_vec if parent_ids_from_vec else None
        kw = self.bm25.search(query, k_child, allowed)

        combined: dict[str, dict] = {}

        for pid, data in vec.items():
            combined[pid] = {
                "chunk_id": pid,
                "content": data["content"],       
                "source": data.get("source", "unknown"),
                "doc_type": data.get("doc_type", ""),
                "chunk_index": data.get("chunk_index", -1),
                "score": data["score"],            
                "keyword_score": 0.0,
                "combined_score": data["score"] * self.vector_weight,
                "parent_id": pid,
                "hit_type": data.get("hit_type", "child"),
            }

        for pid, data in kw.items():
            if pid in combined:
                combined[pid]["keyword_score"] = data["keyword_score"]
                combined[pid]["combined_score"] += data["keyword_score"] * self.keyword_weight
            else:
                combined[pid] = {
                    "chunk_id": pid,
                    "content": data["content"],
                    "source": data.get("source", "unknown"),
                    "doc_type": data.get("doc_type", ""),
                    "chunk_index": data.get("chunk_index", -1),
                    "score": 0.0,
                    "keyword_score": data["keyword_score"],
                    "combined_score": data["keyword_score"] * self.keyword_weight,
                    "parent_id": pid,
                    "hit_type": "bm25",
                }

        sorted_results = sorted(
            combined.values(),
            key=lambda x: x["combined_score"],
            reverse=True,
        )

        final = []
        for r in sorted_results[:pool_n]:
            if not has_rerank and r["combined_score"] < self.score_threshold:
                continue
            in_vec = r["score"] > 0
            in_kw = r["keyword_score"] > 0
            match_type = "hybrid" if (in_vec and in_kw) else ("vector" if in_vec else "bm25")
            r["match_type"] = match_type
            r["combined_score"] = float(r["combined_score"])
            r["keyword_score"] = float(r["keyword_score"])
            r["score"] = float(r["combined_score"])
            final.append(r)

        if has_rerank:
            final = self.reranker.rerank(query, final, top_k=n)
        else:
            final = final[:n]

        return final


class MultiKbRetriever:

    def __init__(self, members: list[tuple[str, str, object]]):
        self.members = members

    def retrieve(self, query: str, n: int = 5, **kwargs) -> list[dict]:
        merged: dict[tuple[str, str], dict] = {}
        multi = len(self.members) > 1
        for kb_id, kb_name, retriever in self.members:
            for hit in retriever.retrieve(query, n=n, **kwargs):
                cid = hit.get("chunk_id") or ""
                key = (kb_id, cid)
                score = float(hit.get("score", 0.0))
                if key in merged and merged[key]["score"] >= score:
                    continue
                item = dict(hit)
                item["kb_id"] = kb_id
                item["kb_name"] = kb_name
                item["score"] = score
                if multi:
                    item["source"] = f"{kb_name}/{hit.get('source', 'unknown')}"
                merged[key] = item

        hits = sorted(merged.values(), key=lambda h: h["score"], reverse=True)
        return hits[:n]

