from __future__ import annotations

import pytest

from vector_backend.tests._support import Hash512


def _seed(col) -> None:
    col.upsert(
        ids=["a", "b", "c"],
        documents=["成交额口径：含退款", "GMV 定义：下单金额合计", "退货率统计口径"],
        metadatas=[
            {"source": "x.md", "chunk_index": 0},
            {"source": "y.md", "chunk_index": 1},
            {"source": "x.md", "chunk_index": 2},
        ],
    )


def test_upsert_count_query_shapes(pg_collection):
    col = pg_collection
    assert col.count() == 0
    _seed(col)                       
    assert col.count() == 3

    res = col.query(query_texts=["成交额口径"], n_results=2)
    assert isinstance(res["ids"], list) and isinstance(res["ids"][0], list)
    assert res["ids"][0][0] == "a"                    
    assert len(res["documents"][0]) == 2
    assert len(res["metadatas"][0]) == 2
    assert len(res["distances"][0]) == 2
    assert all(0.0 <= d <= 2.0 for d in res["distances"][0])

    res2 = col.query(query_embeddings=[Hash512()(["成交额口径"])[0]],
                     n_results=1, include=["distances"])
    assert "documents" not in res2 and "metadatas" not in res2
    assert res2["ids"][0] == ["a"]


def test_get_include_and_empty(pg_collection):
    col = pg_collection
    _seed(col)
    only_ids = col.get(include=[])
    assert set(only_ids["ids"]) == {"a", "b", "c"} and len(only_ids) == 1

    by_id = col.get(ids=["a"])
    assert by_id["ids"] == ["a"]
    assert by_id["documents"][0] == "成交额口径：含退款"
    assert by_id["metadatas"][0]["source"] == "x.md"

    with_emb = col.get(include=["embeddings"])
    assert len(with_emb["embeddings"][0]) == 512

    assert col.get(ids=["nope"])["ids"] == []


def test_query_empty_collection(pg_collection):
    res = pg_collection.query(query_texts=["x"], n_results=3)
    assert res["ids"] == [[]]
    assert res["distances"] == [[]]


def test_where_dsl(pg_collection):
    col = pg_collection
    _seed(col)

    assert set(col.get(where={"source": "x.md"})["ids"]) == {"a", "c"}
    assert set(col.get(where={"source": {"$in": ["y.md"]}})["ids"]) == {"b"}
    assert set(col.get(where={"source": {"$ne": "x.md"}})["ids"]) == {"b"}
    assert set(col.get(where={"chunk_index": {"$gte": 1}})["ids"]) == {"b", "c"}
    assert set(col.get(where={"chunk_index": {"$lt": 2}})["ids"]) == {"a", "b"}
    assert set(col.get(where={"$and": [{"source": "x.md"},
                                       {"chunk_index": {"$gte": 2}}]})["ids"]) == {"c"}
    assert set(col.get(where={"$or": [{"source": "y.md"},
                                      {"chunk_index": 0}]})["ids"]) == {"a", "b"}
    assert col.query(query_texts=["退货率"], n_results=1,
                     where={"source": "x.md"})["ids"][0] == ["c"]


@pytest.mark.parametrize("bad", [
    {"source": {"$contains": "x"}},       
    {"$and": []},                          
])
def test_unsupported_where_fails_loud(pg_collection, bad):
    with pytest.raises(ValueError):
        pg_collection.get(where=bad)


def test_update_and_delete(pg_collection):
    col = pg_collection
    _seed(col)

    col.update(ids=["a"], metadatas=[{"source": "x.md", "tag": "new"}])
    meta = col.get(ids=["a"])["metadatas"][0]
    assert meta["tag"] == "new" and meta["source"] == "x.md"

    col.delete(ids=["b"])
    assert col.count() == 2
    col.delete(where={"source": "x.md"})
    assert col.count() == 0
    with pytest.raises(ValueError):
        col.delete()                        


def test_collection_isolation(pg_dsn, unique_name):
    from vector_backend.pg_backend import PgCollection, delete_collection

    name1, name2 = unique_name + "_1", unique_name + "_2"
    c1 = PgCollection(name1, dsn=pg_dsn)
    c2 = PgCollection(name2, dsn=pg_dsn)
    c1.upsert(ids=["a"], documents=["一"], embeddings=[[1.0] + [0.0] * 511])
    c2.upsert(ids=["b"], documents=["二"], embeddings=[[1.0] + [0.0] * 511])
    try:
        assert c1.count() == 1 and c2.count() == 1
        assert c1.get(include=[])["ids"] == ["a"]
        delete_collection(name1, dsn=pg_dsn)
        assert c1.count() == 0 and c2.count() == 1     
    finally:
        delete_collection(name1, dsn=pg_dsn)
        delete_collection(name2, dsn=pg_dsn)
