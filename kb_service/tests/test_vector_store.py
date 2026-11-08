import pytest

from kb_service.store import DEFAULT_PERSIST_DIR, VectorStore, _HashEmbedding, search_semantic


@pytest.fixture()
def store(tmp_path):
    s = VectorStore(collection_name="t_kb", persist_dir=str(tmp_path),
                    embedding=_HashEmbedding())
    yield s
    s.close()


def test_default_persist_dir_points_to_shared_data():
    assert DEFAULT_PERSIST_DIR.replace("\\", "/").endswith("AllFusion/data/kb_db")


def test_add_documents_auto_md5_ids_and_query_roundtrip(store):
    ids = store.add_documents(["成交额口径说明：含退款", "GMV 定义：下单金额合计"])
    assert len(ids) == 2 and len(set(ids)) == 2            
    hits = store.query("成交额口径", n_results=1)
    assert hits and "成交额" in hits[0]["content"]          
    assert 0.0 <= hits[0]["score"] <= 1.0
    assert hits[0]["source"] == "unknown"                   


def test_add_documents_explicit_ids_upsert_idempotent(store):
    ids = store.add_documents(["同一条口径记忆"], ids=["kb-1"])
    ids2 = store.add_documents(["同一条口径记忆（更新）"], ids=["kb-1"])
    assert ids == ids2 == ["kb-1"]
    assert store.collection.count() == 1                    
    got = store.get_by_ids(["kb-1"])
    assert "更新" in got[0]["content"]


def test_add_documents_duplicate_content_ids_dedup_suffix(store):
    store.add_documents(["甲内容", "乙内容"], ids=["dup", "dup"])
    assert store.collection.count() == 2


def test_search_returns_raw_id_and_metadata(store):
    store.add_documents(["指标：GMV。同义说法：总交易额"],
                        metadatas=[{"kb": "glossary", "metric": "GMV"}], ids=["g-1"])
    hits = store.search("GMV 总交易额", n_results=3)
    assert len(hits) == 1
    assert hits[0]["id"] == "g-1"                           
    assert hits[0]["metadata"]["metric"] == "GMV"
    assert "score" in hits[0]


def test_query_and_search_empty_collection(store):
    assert store.query("任何问题") == []
    assert store.search("任何问题") == []
    assert store.get_stats() == {"flat_documents": 0, "flat_collection_name": "t_kb"}


def test_get_all_chunks_and_where_filter(store):
    store.add_documents(["A 文档片段", "B 文档片段"],
                        metadatas=[{"source": "a.md"}, {"source": "b.md"}],
                        ids=["a1", "b1"])
    assert {c["chunk_id"] for c in store.get_all_chunks()} == {"a1", "b1"}
    only_a = store.get_all_chunks(where={"source": "a.md"})
    assert [c["chunk_id"] for c in only_a] == ["a1"]
    assert store.list_sources() == ["a.md", "b.md"]         


def test_clear_truncates_collection(store):
    store.add_documents(["待清空片段"], ids=["x1"])
    store.clear()
    assert store.collection.count() == 0                    
    store.clear()                                           


def test_search_semantic_whitelists_kb(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        search_semantic("nope", "q", 3)
    monkeypatch.setattr("kb_service.store.DEFAULT_PERSIST_DIR", str(tmp_path))
    monkeypatch.setattr("kb_service.store._SEARCH_STORES", {})
    import kb_service.store as st
    orig_init = VectorStore.__init__

    def pinned_init(self, collection_name="qa_kb", persist_dir=None, embedding=None):
        orig_init(self, collection_name, str(tmp_path),
                  embedding=embedding or _HashEmbedding())

    monkeypatch.setattr(VectorStore, "__init__", pinned_init)
    hits = search_semantic("glossary", "GMV 口径", top_k=2)
    assert hits == []                                       
    assert "glossary" in st._SEARCH_STORES                  
