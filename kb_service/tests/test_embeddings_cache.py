import time

import numpy as np
import pytest

from kb_service import embeddings as emb


class _FakeModel:

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.calls: list[list[str]] = []

    def encode(self, texts, *, batch_size=None, normalize_embeddings=True):
        single = isinstance(texts, str)
        batch = [texts] if single else list(texts)
        self.calls.append(batch)
        mat = np.ones((len(batch), self.dim), dtype=np.float32)
        return mat[0] if single else mat


@pytest.fixture()
def fake(monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(emb, "_embedding_model", model)
    monkeypatch.setattr(emb, "_CACHE_TTL", 600.0)
    monkeypatch.setattr(emb, "_CACHE_MAX", 512)
    monkeypatch.delenv("AF_EMBED_CACHE", raising=False)
    emb.clear_embed_cache()
    yield model
    emb.clear_embed_cache()


def _encoded(fake: _FakeModel) -> list[str]:
    return [t for call in fake.calls for t in call]


def test_same_text_encoded_once(fake):
    a = emb.get_embedding("华东区 8 月销售额")
    b = emb.get_embedding("华东区 8 月销售额")
    assert _encoded(fake) == ["华东区 8 月销售额"]      
    assert a == b
    stats = emb.embed_cache_stats()
    assert stats["miss"] == 1 and stats["hit"] == 1


def test_exact_text_only(fake):
    emb.get_embedding("成交额")
    emb.get_embedding("成交额 ")                        
    assert _encoded(fake) == ["成交额", "成交额 "]


def test_ttl_expiry_reencodes(fake, monkeypatch):
    monkeypatch.setattr(emb, "_CACHE_TTL", 0.01)
    emb.get_embedding("口径")
    time.sleep(0.02)
    emb.get_embedding("口径")
    assert _encoded(fake) == ["口径", "口径"]           
    assert emb.embed_cache_stats()["expired"] == 1


def test_batch_reuses_cached_items(fake):
    emb.get_embeddings_batch(["甲", "乙"])
    emb.get_embeddings_batch(["甲", "乙"])
    assert _encoded(fake) == ["甲", "乙"]               


def test_batch_dedupes_within_call(fake):
    emb.get_embeddings_batch(["甲", "甲", "乙"])
    assert _encoded(fake) == ["甲", "乙"]               


def test_large_batch_bypasses_cache(fake):
    texts = [f"灌库块{i}" for i in range(emb._CACHE_BATCH_LIMIT + 1)]
    emb.get_embeddings_batch(texts)
    emb.get_embeddings_batch(texts)
    assert len(_encoded(fake)) == 2 * len(texts)         
    assert emb.embed_cache_stats()["size"] == 0


def test_cache_can_be_disabled(fake, monkeypatch):
    monkeypatch.setenv("AF_EMBED_CACHE", "off")
    emb.get_embedding("关闭")
    emb.get_embedding("关闭")
    assert _encoded(fake) == ["关闭", "关闭"]
    assert emb.embed_cache_stats()["size"] == 0


def test_cache_bounded(fake, monkeypatch):
    monkeypatch.setattr(emb, "_CACHE_MAX", 2)
    for text in ("一", "二", "三"):
        emb.get_embedding(text)
    assert emb.embed_cache_stats()["size"] == 2         
