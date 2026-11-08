from __future__ import annotations

import unified_agent.adapters.rag_adapter as ra
from unified_agent.agents import recall_catalog as rc


class _FakeRetriever:
    def __init__(self, hits):
        self.hits = hits

    def retrieve(self, query, n=5, **kwargs):
        return list(self.hits[:n])


def _hit(kb, cid, score):
    return {"chunk_id": f"{kb}-{cid}", "source": f"{cid}.docx",
            "content": f"{kb} 内容 {cid}", "score": score}


def _members(*specs):
    return [(kb, name, None, _FakeRetriever(hits)) for kb, name, hits in specs]


def _patch(monkeypatch, members):
    monkeypatch.setattr(ra, "kb_retrievers", lambda kb_ids=None: members)


def test_each_kb_keeps_one_slot_then_fill_by_score(monkeypatch):
    members = _members(
        ("a", "库A", [_hit("a", 1, 0.9), _hit("a", 2, 0.7),
                       _hit("a", 3, 0.5), _hit("a", 4, 0.45)]),
        ("b", "库B", [_hit("b", 1, 0.8), _hit("b", 2, 0.6)]),
    )
    _patch(monkeypatch, members)
    cards, scope = rc._rag_cards("q", ["a", "b"])
    titles = [c.split("。")[0] for c in cards]
    assert titles == [
        "【内部文档】库A/1.docx", "【内部文档】库B/1.docx",
        "【内部文档】库A/2.docx", "【内部文档】库B/2.docx",
        "【内部文档】库A/3.docx",
    ]
    assert scope["effective_ids"] == ["a", "b"] and scope["empty_ids"] == []


def test_empty_or_low_score_kb_is_marked(monkeypatch):
    members = _members(
        ("a", "库A", [_hit("a", 1, 0.8)]),
        ("b", "库B", []),                        
        ("c", "库C", [_hit("c", 1, 0.39)]),      
    )
    _patch(monkeypatch, members)
    cards, scope = rc._rag_cards("q", ["a", "b", "c"])
    assert cards[0].startswith("【内部文档】库A/")
    notices = [c for c in cards if c.startswith("【知识库】")]
    assert notices == [
        "【知识库】库B：本轮无相关资料（未检索到有效内容）",
        "【知识库】库C：本轮无相关资料（未检索到有效内容）",
    ]
    assert scope["effective_ids"] == ["a"]
    assert scope["empty_ids"] == ["b", "c"]


def test_scope_stamped_with_question(monkeypatch):
    _patch(monkeypatch, _members(("a", "库A", [_hit("a", 1, 0.8)])))
    monkeypatch.setattr(rc, "_business_table_cards", lambda q, s: [])
    monkeypatch.setattr(rc, "_metric_cards", lambda q, s: [])
    monkeypatch.setattr(rc, "_catalog_snapshot",
                        lambda s: {"built_at": 0, "kbs": [], "dbs": []})
    out = rc.recall({"question": "原话", "resolved_question": "补全版", "kb_ids": ["a"]})
    assert out["kb_scope"]["question"] == "补全版"


def test_turn_datasources_prefers_explicit_selection(monkeypatch):
    monkeypatch.setattr(rc, "_tenant_datasources", lambda tid: [7, 8])
    assert rc._turn_datasources({"tenant_id": "3", "datasource_id": 9}) == [9]
    assert rc._turn_datasources({"tenant_id": "3", "datasource_id": None}) == [7, 8]
    assert rc._turn_datasources({}) == []


def test_table_cards_empty_without_tenant_datasource(monkeypatch):
    monkeypatch.setattr(rc, "_tenant_datasources", lambda tid: [])
    assert rc._business_table_cards("上月成交额", {"tenant_id": "12"}) == []


def test_metric_cards_from_tenant_business_store(monkeypatch):
    terms = [
        {"id": 172, "term": "成交额", "synonyms": "GMV,gmv",
         "formula": "sum(sales_amount)", "unit": "元",
         "related_tables": "v_fact_sales_order_line"},
        {"id": 173, "term": "毛利", "synonyms": "", "formula": "", "unit": "", "related_tables": ""},
    ]
    monkeypatch.setattr(rc, "_tenant_terms", lambda tid, uid=None: terms)
    monkeypatch.setattr(rc, "_tenant_metric_hits",
                        lambda tid, q, uid=None: [{"id": "t172"}, {"id": "t999"}, {"id": 173}])
    cards = rc._metric_cards("成交额", {"tenant_id": "12", "user_id": 14})
    assert cards == [
        "【指标】成交额。同义词：GMV、gmv",
        "【指标】毛利。同义词：无",
    ]


def test_table_cards_fetch_schema_only_for_top_candidates(monkeypatch):
    import contextlib

    class _FakeGateway:
        def __init__(self):
            self.schema_calls = []

        def use_datasource(self, *args, **kwargs):
            return contextlib.nullcontext()

        def list_tables(self):
            return [{"name": f"t{i}", "comment": "成交额" if i == 1 else ""} for i in range(10)]

        def is_business_table(self, name):
            return True

        def get_table_schema(self, name):
            self.schema_calls.append(name)
            return {"columns": [{"name": "amt", "type": "decimal", "comment": "金额"}]}

    fake = _FakeGateway()
    monkeypatch.setattr(rc, "gateway", fake)
    monkeypatch.setattr(rc, "_tenant_datasources", lambda tid: [7])
    rc._TABLE_META_CACHE.clear()
    rc._COLUMN_CACHE.clear()

    cards = rc._business_table_cards("成交额", {"tenant_id": "12"})
    assert len(fake.schema_calls) <= rc._TOP_N_BU_SCHEMA      
    assert len(cards) == rc._TOP_N_BU
    assert cards[0].startswith("【业务表】t1")                 
    assert "核心字段：amt(金额)" in cards[0]


def test_scoped_kb_ids_rejects_stale_scope():
    state = {"question": "新问题", "resolved_question": "新问题",
             "kb_ids": ["a", "b"],
             "kb_scope": {"question": "旧问题", "effective_ids": ["a"]}}
    assert ra._scoped_kb_ids(state) is None
    state["kb_scope"]["question"] = "新问题"
    assert ra._scoped_kb_ids(state) == ("a",)


def test_scoped_kb_ids_all_empty_means_no_retrieval():
    state = {"question": "q", "kb_ids": ["a", "b"],
             "kb_scope": {"question": "q", "effective_ids": []}}
    assert ra._scoped_kb_ids(state) == ()


def test_explicit_empty_kb_returns_no_cards_and_closed_scope(monkeypatch):
    called = {"flat": 0}

    def _flat(*_a, **_k):
        called["flat"] += 1
        return [_hit("d", 1, 0.9)]

    monkeypatch.setattr(rc, "_retrieve", _flat)
    cards, scope = rc._rag_cards("q", [])
    assert cards == []
    assert called["flat"] == 0
    assert scope == {"effective_ids": [], "empty_ids": [], "empty_names": []}



def _snap_state():
    return {"tenant_id": "1", "kb_ids": ["default"]}


def test_snapshot_cache_hit_skips_second_build(monkeypatch):
    rc._SNAPSHOT_CACHE.clear()
    calls = {"kb": 0, "table": 0}

    def _fake_sources(prefix):
        calls["kb"] += 1
        return [{"source": "loong/financial/report_600519_2020Q1.pdf"}]

    def _fake_metas(ds_id, state):
        calls["table"] += 1
        return [{"name": "sales_dw.fact_sales", "comment": "销量"}]

    monkeypatch.setattr(rc, "_kb_sources", _fake_sources)
    monkeypatch.setattr(rc, "_cached_table_metas", _fake_metas)
    monkeypatch.setattr(rc, "_turn_datasources", lambda s: [3])
    monkeypatch.setattr(rc, "resolve_kbs", lambda ids: [
        {"id": "default", "name": "默认库", "collection_prefix": "kb", "description": "A股"}])

    first = rc._catalog_snapshot(_snap_state())
    second = rc._catalog_snapshot(_snap_state())

    assert first is second
    assert calls == {"kb": 1, "table": 1}
    assert first["kbs"][0]["doc_count"] == 1


def test_render_snapshot_caps_length_and_omits_empty_description():
    kbs = [{
        "kb_id": "default", "kb_name": "默认库", "description": "", "doc_count": 275,
        "clusters": [{"prefix": "loong/financial/report_*", "n": 275,
                      "samples": ["report_600519_2020Q1", "report_000651_2020Q1"]}],
    }]
    dbs = [{
        "datasource_id": 3, "table_count": 120,
        "groups": [{"prefix": f"sales_dw{i}", "n": 20,
                    "tables": [{"name": f"sales_dw{i}.t{j}", "comment": ""}
                               for j in range(6)]} for i in range(40)],
    }]
    text = rc._render_snapshot({"built_at": 0, "kbs": kbs, "dbs": dbs})
    assert len(text) <= 800
    assert "【知识库】" in text and "【数据源】" in text
    assert "描述：" not in text           


def test_db_profile_keeps_at_most_six_tables(monkeypatch):
    monkeypatch.setattr(rc, "_turn_datasources", lambda s: [3])
    monkeypatch.setattr(rc, "_cached_table_metas",
                        lambda ds_id, state: [{"name": f"sales_dw.t{i}", "comment": ""}
                                             for i in range(10)])
    prof = rc._db_profile({"tenant_id": "1"})
    assert prof["table_count"] == 10
    group = prof["groups"][0]
    assert group["prefix"] == "sales_dw" and group["n"] == 10
    assert len(group["tables"]) == rc._SNAPSHOT_DB_GROUPS == 6


def test_recall_returns_snapshot_hints_and_block_ms(monkeypatch):
    events = []
    monkeypatch.setattr(rc, "emit_event", lambda *a, **k: events.append(a))
    monkeypatch.setattr(rc, "_catalog_snapshot", lambda s: {
        "built_at": 0,
        "kbs": [{"kb_id": "default", "kb_name": "默认库", "description": "A股",
                 "doc_count": 1, "clusters": []}],
        "dbs": []})
    monkeypatch.setattr(rc, "_business_table_cards", lambda q, s: [])
    monkeypatch.setattr(rc, "_metric_cards", lambda q, s: [])
    monkeypatch.setattr(rc, "_rag_cards", lambda q, ids: ([], {}))

    out = rc.recall({"question": "q", "resolved_question": "q", "kb_ids": ["default"]})
    snap = out["catalog_hints"]["snapshot"]
    assert isinstance(snap, str) and "【知识库】" in snap
    payload = events[0][3]
    assert "snapshot" in payload["ms"]
    assert payload["cards"]["snapshot_chars"] == len(snap)
