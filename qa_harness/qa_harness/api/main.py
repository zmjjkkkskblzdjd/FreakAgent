from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException, Query

from knowledge import kb_config
from knowledge.vector_store import VectorStore

app = FastAPI(
    title="qa_harness-kb-service",
    version="0.1.0",
    description="语义知识库检索服务：glossary/fewshot 向量检索",
)

_KB_COLLECTIONS = {
    "glossary": kb_config.GLOSSARY_COLLECTION,
    "fewshot": kb_config.FEWSHOT_COLLECTION,
}

_stores: dict[str, VectorStore] = {}


def _store_for(kb: str) -> VectorStore:
    if kb not in _KB_COLLECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"未知知识库：{kb}（可选：{sorted(_KB_COLLECTIONS)}）",
        )
    if kb not in _stores:
        _stores[kb] = VectorStore(_KB_COLLECTIONS[kb])
    return _stores[kb]


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "qa_harness-kb-service"}


@app.get("/api/kbs")
def list_kbs() -> dict:
    return {"kbs": list(_KB_COLLECTIONS.keys())}


@app.get("/api/search")
def search(kb: str = Query(...), query: str = Query(...),
           top_k: int = Query(5, ge=1, le=50)) -> dict:
    store = _store_for(kb)
    return {"results": store.search(query, n_results=top_k)}


if __name__ == "__main__":
    uvicorn.run("api.main:app", host=kb_config.API_HOST,
                port=kb_config.API_PORT, reload=False)
