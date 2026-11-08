from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass                

DEFAULT_BACKEND = "pg"


def backend_name() -> str:
    name = os.getenv("AF_VECTOR_BACKEND", DEFAULT_BACKEND).strip().lower()
    if name not in ("chroma", "pg"):
        raise ValueError(f"未知向量后端 AF_VECTOR_BACKEND={name!r}（仅支持 chroma / pg）")
    return name


def get_collection(name: str, *, path: str | None, metadata: dict | None = None,
                   embedding_function=None):
    if path is None:
        from vector_backend.chroma_backend import ChromaCollection
        return ChromaCollection(name, path=None, metadata=metadata,
                                embedding_function=embedding_function)
    if backend_name() == "pg":
        from vector_backend.pg_backend import PgCollection
        return PgCollection(name, metadata=metadata,
                            embedding_function=embedding_function)
    from vector_backend.chroma_backend import ChromaCollection
    return ChromaCollection(name, path=path, metadata=metadata,
                            embedding_function=embedding_function)


def delete_collection(name: str, *, path: str | None) -> None:
    if path is not None and backend_name() == "pg":
        from vector_backend.pg_backend import delete_collection as _delete
        _delete(name)
        return
    from vector_backend.chroma_backend import delete_collection as _delete
    _delete(name, path=path)


def list_collections(*, path: str | None) -> list[str]:
    if path is not None and backend_name() == "pg":
        from vector_backend.pg_backend import list_collections as _list
        return _list()
    from vector_backend.chroma_backend import list_collections as _list
    return _list(path=path)


def close() -> None:
    from vector_backend import pg_backend
    pg_backend.close_pools()
