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

class HybridRetriever:
    def __init__(self, store, vector_weight=0.6, keyword_weight=0.4, score_threshold=0.35, reranker=None):
        self.store = store
        chunks = store.get_all_chunks()
        self.bm25 = BM25Index(chunks) if chunks else None   
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self.score_threshold = score_threshold
        self.reranker = reranker       

    def retrieve(self, query, n=5, where=None) -> list[dict]:
        if self.bm25 is None or self.store.collection.count() == 0:    
            return []
        has_rerank = (self.reranker is not None
                      and getattr(self.reranker, "available", False))
        pool_n = max(n * 4, n + 5) if has_rerank else n
        fetch_n = pool_n if has_rerank else n * 2
        vec = {h["chunk_id"]: h for h in self.store.query(query, n_results=fetch_n, where=where)}
        allowed = {c["chunk_id"] for c in self.store.get_all_chunks(where)} if where else None
        kw  = self.bm25.search(query, fetch_n, allowed)
        combined = {}

        for doc_id, data in vec.items():
            combined[doc_id] = {
                "chunk_id": doc_id,
                "content": data["content"],
                "source": data["source"],
                "doc_type": data["doc_type"],
                "chunk_index": data["chunk_index"],
                "score": data["score"],
                "keyword_score": 0.0,
                "combined_score": data["score"] * self.vector_weight
            }

        for doc_id, data in kw.items():
            if doc_id in combined:
                combined[doc_id]["keyword_score"] = data["keyword_score"]
                combined[doc_id]["combined_score"] += data["keyword_score"] * self.keyword_weight
            else:
                combined[doc_id] = {
                    "chunk_id": doc_id,
                    "content": data["content"],
                    "source": data["source"],
                    "doc_type": data["doc_type"],
                    "chunk_index": data["chunk_index"],
                    "score": 0.0,
                    "keyword_score": data["keyword_score"],
                    "combined_score": data["keyword_score"] * self.keyword_weight
                }

        sorted_results = sorted(
            combined.values(),
            key=lambda x: x["combined_score"],
            reverse=True
        )

        final = []
        for r in sorted_results[:pool_n]:
            if not has_rerank and r["combined_score"] < self.score_threshold:
                continue
            in_vec = r["score"] > 0
            in_kw  = r["keyword_score"] > 0
            combined = float(r["combined_score"])   
            r["keyword_score"] = float(r["keyword_score"])
            r["match_type"] = "hybrid" if (in_vec and in_kw) else ("vector" if in_vec else "bm25")
            r["combined_score"] = combined
            r["score"] = combined
            final.append(r)

        if has_rerank:
            final = self.reranker.rerank(query, final, top_k=n)
        else:
            final = final[:n]

        return final