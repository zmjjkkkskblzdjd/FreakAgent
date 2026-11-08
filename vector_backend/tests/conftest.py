from __future__ import annotations

import os
import uuid

import pytest

from vector_backend.tests._support import Hash512

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/allfusion_vectors"


def _dsn() -> str:
    return os.getenv("AF_VECTOR_DSN", DEFAULT_DSN)


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = _dsn()
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=3):
            pass
    except Exception as e:      # noqa: BLE001 — 环境不可用，整组跳过
        pytest.skip(f"PostgreSQL 不可达（{dsn}）：{e}")
    return dsn


@pytest.fixture()
def pg_collection(pg_dsn, monkeypatch):
    from vector_backend.pg_backend import PgCollection, delete_collection

    monkeypatch.setenv("AF_VECTOR_BACKEND", "pg")
    monkeypatch.setenv("AF_VECTOR_DSN", pg_dsn)
    monkeypatch.setenv("AF_VECTOR_DIM", "512")

    name = f"test_{uuid.uuid4().hex}"
    col = PgCollection(name, dsn=pg_dsn, embedding_function=Hash512())
    yield col
    delete_collection(name, dsn=pg_dsn)


@pytest.fixture()
def unique_name():
    return f"test_{uuid.uuid4().hex}"
