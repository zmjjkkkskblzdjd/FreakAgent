import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  

import kb_service.store as kb_store
from data_analyze_agent.metrics import fewshot, matcher, semantic


class _Cfg:

    class metrics:
        qa_harness_url = "http://qa-fake:8001"
        qa_harness_timeout_ms = 1000

    class retrieval:
        glossary_top_k = 5
        fewshot_top_k = 3

    class memory:                  
        user_id = "local"


def _patch_cfg(monkeypatch):
    for mod in (semantic, matcher, fewshot):
        monkeypatch.setattr(mod, "get_app_config", lambda: _Cfg)



def test_search_cards_local_direct(monkeypatch):
    _patch_cfg(monkeypatch)
    seen = {}

    def fake_local(kb, query, top_k):
        seen["args"] = (kb, query, top_k)
        return [{"id": "7", "content": "指标：GMV",
                 "metadata": {"metric": "GMV"}, "score": 0.8}]

    monkeypatch.setattr(kb_store, "search_semantic", fake_local)
    hits = semantic.search_cards("glossary", "GMV 口径", 5)
    assert hits[0]["metadata"]["metric"] == "GMV"
    assert seen["args"] == ("glossary", "GMV 口径", 5)      


def test_search_cards_falls_back_to_http(monkeypatch):
    _patch_cfg(monkeypatch)

    def broken_local(kb, query, top_k):
        raise RuntimeError("本地共享库不可用（未灌库/打开失败）")

    monkeypatch.setattr(kb_store, "search_semantic", broken_local)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"id": "1", "content": "c",
                                 "metadata": {}, "score": 0.6}]}

    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"], captured["params"] = url, params
        return _Resp()

    monkeypatch.setattr(semantic.httpx, "get", fake_get)
    hits = semantic.search_cards("glossary", "q", 5)
    assert hits[0]["id"] == "1"                             
    assert captured["url"].endswith("/api/search")
    assert captured["params"]["kb"] == "glossary"


def test_search_cards_silent_when_both_down(monkeypatch):
    _patch_cfg(monkeypatch)

    def broken_local(kb, query, top_k):
        raise RuntimeError("local unavailable")

    monkeypatch.setattr(kb_store, "search_semantic", broken_local)

    def broken_http(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(semantic.httpx, "get", broken_http)
    assert semantic.search_cards("glossary", "q", 5) == []  


def test_search_cards_http_skipped_without_url(monkeypatch):
    class _NoUrl(_Cfg):
        class metrics(_Cfg.metrics):
            qa_harness_url = ""

    for mod in (semantic, matcher, fewshot):
        monkeypatch.setattr(mod, "get_app_config", lambda: _NoUrl)
    monkeypatch.setattr(kb_store, "search_semantic",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError))
    called = []
    monkeypatch.setattr(semantic.httpx, "get",
                        lambda *a, **kw: called.append(1))
    assert semantic.search_cards("fewshot", "q", 3) == []
    assert not called



def test_matcher_semantic_maps_and_filters(monkeypatch):
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(
        matcher, "search_cards", lambda kb, q, top_k: [
            {"id": "7", "content": "指标：GMV",
             "metadata": {"metric": "GMV"}, "score": 0.8},
            {"id": "8", "content": "低置信噪声",
             "metadata": {"metric": "噪音指标"}, "score": 0.3},   
            {"id": "9", "content": "缺 metric 元数据",
             "metadata": {}, "score": 0.9},                       
        ])
    assert matcher._semantic_search("GMV 口径") == [("GMV", "GMV", 0.8)]



def test_fewshot_semantic_renders_cards(monkeypatch):
    _patch_cfg(monkeypatch)
    seen = {}

    def fake_search(kb, q, top_k, where=None):
        seen["where"] = where
        return [
            {"id": "1", "content": "【例题】上月GMV", "metadata": {}, "score": 0.7},
            {"id": "2", "content": "", "metadata": {}, "score": 0.5},   
        ]

    monkeypatch.setattr(fewshot, "search_cards", fake_search)
    cards = fewshot.match_fewshots_semantic("上月 GMV", top_k=3)
    assert seen["where"] == {"user_id": "local"}      
    assert len(cards) == 1
    assert cards[0].kind == "fewshot" and cards[0].match_type == "vector"
    assert cards[0].card_id == "fewshot:semantic:0"


def test_fewshot_semantic_short_circuit_on_zero_topk(monkeypatch):
    _patch_cfg(monkeypatch)
    called = []
    monkeypatch.setattr(fewshot, "search_cards",
                        lambda *a, **kw: called.append(1))
    assert fewshot.match_fewshots_semantic("q", top_k=0) == []
    assert not called                                       



def test_match_metrics_semantic_fills_gaps_only(monkeypatch):
    _patch_cfg(monkeypatch)

    class _Entry:
        def __init__(self, name, aliases=()):
            self.name = name
            self.aliases = aliases

    class _Dict:
        metrics = [_Entry("GMV", ("总交易额",)), _Entry("退款金额")]
        alias_index = {"gmv": "GMV"}

        @staticmethod
        def get(name):
            return next((e for e in _Dict.metrics if e.name == name), None)

    monkeypatch.setattr(matcher, "load_dictionary", lambda: _Dict())
    monkeypatch.setattr(
        matcher, "search_cards", lambda kb, q, top_k: [
            {"id": "7", "content": "指标：GMV",              
             "metadata": {"metric": "GMV"}, "score": 0.8},
            {"id": "10", "content": "指标：退款金额",
             "metadata": {"metric": "退款金额"}, "score": 0.9},   
        ])
    hits = matcher.match_metrics("GMV 口径")                
    by_name = {h.entry.name: h for h in hits}
    assert by_name["GMV"].level == 1                        
    assert by_name["退款金额"].level == 3                   
    assert by_name["退款金额"].score == 0.9
