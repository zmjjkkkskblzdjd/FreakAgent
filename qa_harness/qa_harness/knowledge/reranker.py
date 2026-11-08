from __future__ import annotations

import os
from typing import Optional


class LocalReranker:

    _instance: Optional["LocalReranker"] = None
    _instance_path: str = ""

    def __new__(cls, model_path: str = "", device: str = "cpu") -> "LocalReranker":
        if cls._instance is not None and cls._instance_path == model_path:
            return cls._instance
        inst = super().__new__(cls)
        cls._instance = inst
        cls._instance_path = model_path
        return inst

    def __init__(self, model_path: str, device: str = "cpu"):
        if self.__class__._instance is self and getattr(self, "_loaded_path", "") == model_path:
            return
        self.model_path = model_path
        self.device = device
        self._model = None
        self._tokenizer = None
        self._kind = ""
        self._available = False
        self._loaded_path = model_path
        self._load()


    def _load(self) -> None:
        if not self.model_path or not os.path.isdir(self.model_path):
            return
        try:
            import torch
            from transformers import AutoConfig

            config = AutoConfig.from_pretrained(self.model_path, trust_remote_code=True)
            archs = list(getattr(config, "architectures", None) or [])
            self._kind = "cross" if any("SequenceClassification" in a for a in archs) else "listwise"

            load_kwargs = {"trust_remote_code": True}
            if str(self.device).startswith("cuda"):
                load_kwargs["dtype"] = "auto"
            else:
                load_kwargs["dtype"] = torch.float32

            if self._kind == "cross":
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                self._tokenizer = AutoTokenizer.from_pretrained(
                    self.model_path, trust_remote_code=True)
                self._model = AutoModelForSequenceClassification.from_pretrained(
                    self.model_path, **load_kwargs)
            else:
                from transformers import AutoModel

                self._model = AutoModel.from_pretrained(self.model_path, **load_kwargs)

            self._model.to(self.device)
            self._model.eval()
            self._available = True
        except Exception:
            self._available = False
            self._model = None
            self._tokenizer = None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def score_kind(self) -> str:
        return "sigmoid" if self._kind == "cross" else "cosine"


    def rerank(self, query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
        if not candidates:
            return []
        if not self._available or len(candidates) <= top_k:
            return candidates[:top_k]
        if self._kind == "cross":
            return self._rerank_cross(query, candidates, top_k)
        return self._rerank_listwise(query, candidates, top_k)

    def _rerank_cross(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        import torch

        pairs = [[query, str(c.get("content", ""))] for c in candidates]
        batch = self._tokenizer(
            pairs, padding=True, truncation=True, max_length=512, return_tensors="pt"
        ).to(self.device)
        with torch.no_grad():
            logits = self._model(**batch).logits.view(-1).float()
        scores = torch.sigmoid(logits).cpu().tolist()

        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
        results = []
        for i in order[:top_k]:
            candidate = dict(candidates[i])
            candidate["score"] = float(scores[i])
            candidate["rerank_score"] = float(scores[i])
            results.append(candidate)
        return results

    def _rerank_listwise(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        documents = [str(c.get("content", "")) for c in candidates]
        max_docs = 64          
        all_results: list[dict] = []

        for start in range(0, len(documents), max_docs):
            batch_docs = documents[start:start + max_docs]
            try:
                batch_results = self._model.rerank(query, batch_docs)
            except Exception:
                for i, doc in enumerate(batch_docs):
                    all_results.append({"index": start + i, "relevance_score": 0.0, "document": doc})
                continue
            for r in batch_results:
                all_results.append({
                    "index": start + int(r["index"]),
                    "relevance_score": float(r["relevance_score"]),
                    "document": r["document"],
                })

        all_results.sort(key=lambda x: x["relevance_score"], reverse=True)
        results = []
        for r in all_results[:top_k]:
            candidate = candidates[r["index"]]
            candidate["score"] = float(r["relevance_score"])
            candidate["rerank_score"] = float(r["relevance_score"])
            results.append(candidate)

        return results
