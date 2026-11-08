from __future__ import annotations

import uuid

from kb_service.store import VectorStore
from vector_backend.tests._support import Hash512

TOPICS = ["成交额口径", "GMV 定义", "退货率统计", "客单价计算", "毛利率口径"]
DOCS = [f"{TOPICS[i % 5]} 说明第 {i} 条：含退款与优惠" for i in range(30)]
IDS = [f"d{i}" for i in range(30)]
METAS = [{"source": f"s{i % 5}.md", "chunk_index": i} for i in range(30)]
QUERIES = ["成交额口径 含退款", "退货率怎么统计", "毛利率口径说明"]


def _overlap(a: list[dict], b: list[dict]) -> int:
    return len({h["chunk_id"] for h in a} & {h["chunk_id"] for h in b})


def test_chroma_pg_topk_parity(pg_dsn, monkeypatch, tmp_path):
    from vector_backend.pg_backend import delete_collection

    emb = Hash512()

    monkeypatch.setenv("AF_VECTOR_BACKEND", "chroma")
    chroma = VectorStore(collection_name=f"test_{uuid.uuid4().hex}",
                         persist_dir=str(tmp_path), embedding=emb)
    chroma.add_documents(DOCS, metadatas=METAS, ids=IDS)

    pname = f"test_{uuid.uuid4().hex}"
    monkeypatch.setenv("AF_VECTOR_BACKEND", "pg")
    monkeypatch.setenv("AF_VECTOR_DSN", pg_dsn)
    monkeypatch.setenv("AF_VECTOR_DIM", "512")
    pg = VectorStore(collection_name=pname, persist_dir=str(tmp_path), embedding=emb)
    pg.add_documents(DOCS, metadatas=METAS, ids=IDS)

    try:
        for q in QUERIES:
            a = chroma.query(q, n_results=5)
            b = pg.query(q, n_results=5)
            assert _overlap(a, b) >= 4, f"{q}: top-5 重合不足"
            same = {h["chunk_id"]: h for h in a}
            for hit in b:
                if hit["chunk_id"] in same:
                    assert abs(hit["score"] - same[hit["chunk_id"]]["score"]) < 0.02

        a = chroma.query("成交额口径", n_results=5, where={"source": "s0.md"})
        b = pg.query("成交额口径", n_results=5, where={"source": "s0.md"})
        assert {h["chunk_id"] for h in a} == {h["chunk_id"] for h in b}
    finally:
        pg.close()
        delete_collection(pname, dsn=pg_dsn)
        chroma.close()
