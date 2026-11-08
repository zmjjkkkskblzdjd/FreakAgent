import pytest

from kb_service.kb_index import build_embed_text, index_rows, parse_json_array
from kb_service.store import VectorStore, _HashEmbedding



def test_parse_json_array_variants():
    assert parse_json_array(None) == []
    assert parse_json_array("") == []
    assert parse_json_array('["GMV","总交易额"]') == ["GMV", "总交易额"]
    assert parse_json_array(["a", " b "]) == ["a", "b"]     
    assert parse_json_array("GMV, 总交易额") == ["GMV", "总交易额"]   
    assert parse_json_array("not-json") == ["not-json"]



def test_build_embed_text_glossary_excludes_formula():
    row = {"name": "GMV", "aliases": '["总交易额"]', "formula_desc": "下单金额合计",
           "note": "含退款", "formula": "SUM(amount)", "related_tables": "[`orders`]"}
    text = build_embed_text(row, "glossary")
    assert "指标：GMV" in text and "总交易额" in text and "下单金额合计" in text
    assert "SUM(amount)" not in text and "orders" not in text   


def test_build_embed_text_fewshot_excludes_sql():
    row = {"question": "上月 GMV 是多少", "explanation": "按下单时间过滤",
           "reference_sql": "SELECT 1", "tags": '["metric:GMV"]'}
    text = build_embed_text(row, "fewshot")
    assert "例题：上月 GMV 是多少" in text and "按下单时间过滤" in text
    assert "SELECT 1" not in text and "metric:GMV" not in text  


def test_build_embed_text_rejects_unknown_kb():
    with pytest.raises(ValueError):
        build_embed_text({}, "schema")



@pytest.fixture()
def store(tmp_path):
    s = VectorStore(collection_name="t_idx", persist_dir=str(tmp_path),
                    embedding=_HashEmbedding())
    yield s
    s.close()


def test_index_rows_glossary_shape(store):
    rows = [{"id": 7, "name": "GMV", "aliases": '["总交易额"]',
             "formula_desc": "下单金额合计", "note": ""}]
    assert index_rows(store, rows, "glossary") == 1
    hits = store.search("GMV 总交易额", n_results=1)
    assert hits[0]["id"] == "7"                             
    assert hits[0]["metadata"] == {"kb": "glossary", "metric": "GMV"}


def test_index_rows_fewshot_tags_to_metadata(store):
    import json
    rows = [{"id": 3, "question": "上月 GMV", "explanation": "", "tags": '["metric:GMV","time:month"]'}]
    assert index_rows(store, rows, "fewshot") == 1
    hits = store.search("上月 GMV 例题", n_results=1)
    tags_meta = hits[0]["metadata"]["tags"]
    assert isinstance(tags_meta, str)
    assert json.loads(tags_meta) == ["metric:GMV", "time:month"]


def test_index_rows_empty_rows_is_noop(store):
    assert index_rows(store, [], "glossary") == 0
    assert store.collection.count() == 0
