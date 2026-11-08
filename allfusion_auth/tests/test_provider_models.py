from __future__ import annotations

from types import SimpleNamespace

from auth_service.app.providers import _models_url, _parse_models, _provider_payload


def test_models_url_standard():
    assert (_models_url("https://api.deepseek.com/v1", "/chat/completions")
            == "https://api.deepseek.com/v1/models")


def test_models_url_custom_gateway_path():
    assert (_models_url("http://127.0.0.1:8000", "/v1/chat/completions")
            == "http://127.0.0.1:8000/v1/models")


def test_models_url_trailing_slash_and_default():
    assert (_models_url("https://open.bigmodel.cn/api/paas/v4/", None)
            == "https://open.bigmodel.cn/api/paas/v4/models")


def test_parse_openai_shape():
    payload = {"object": "list", "data": [
        {"id": "deepseek-chat", "object": "model"},
        {"id": "deepseek-reasoner", "object": "model"},
    ]}
    assert _parse_models(payload) == [
        {"id": "deepseek-chat", "name": "deepseek-chat"},
        {"id": "deepseek-reasoner", "name": "deepseek-reasoner"},
    ]


def test_parse_variants_dedupe_and_skip_bad():
    payload = {"models": [
        {"model": "glm-4.6", "display_name": "GLM-4.6"},
        {"name": "glm-4.6"},                       
        "qwen3-max",                                
        {"id": ""},                                 
        {"id": "x" * 200},                          
    ]}
    assert _parse_models(payload) == [
        {"id": "glm-4.6", "name": "GLM-4.6"},
        {"id": "qwen3-max", "name": "qwen3-max"},
    ]


def test_parse_unknown_shape_returns_empty():
    assert _parse_models({"foo": "bar"}) == []
    assert _parse_models(None) == []


def _row(**kw):
    base = dict(id=7, name="自建网关", provider_type="custom",
                base_url="https://gw.local/v1", completions_path="/chat/completions",
                enabled=1, api_key_cipher=None, key_version=1, key_fingerprint="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_payload_merges_selected_models_not_in_catalog():
    payload = _provider_payload(_row(), {7: ["qwen3-max"]})
    assert payload["models"] == [{"id": "qwen3-max", "name": "qwen3-max"}]
    assert payload["selected_models"] == ["qwen3-max"]
    assert payload["desc"] == "自定义供应商"


def test_payload_keeps_catalog_and_merges_extra():
    payload = _provider_payload(_row(provider_type="deepseek"), {7: ["deepseek-v4-pro", "new-model"]})
    ids = [m["id"] for m in payload["models"]]
    assert "deepseek-v4-pro" in ids
    assert ids.count("deepseek-v4-pro") == 1        
    assert "new-model" in ids
