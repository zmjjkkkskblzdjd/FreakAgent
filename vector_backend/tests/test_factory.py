from __future__ import annotations

import pytest

from vector_backend import factory


def test_default_backend_is_pg(monkeypatch):
    monkeypatch.delenv("AF_VECTOR_BACKEND", raising=False)
    assert factory.backend_name() == "pg"


def test_backend_name_rejects_unknown(monkeypatch):
    monkeypatch.setenv("AF_VECTOR_BACKEND", "nope")
    with pytest.raises(ValueError):
        factory.backend_name()


def test_path_none_forces_chroma_even_when_pg(monkeypatch):
    monkeypatch.setenv("AF_VECTOR_BACKEND", "pg")
    col = factory.get_collection("test_path_none", path=None)
    assert col.name == "test_path_none"
    assert col.count() == 0
