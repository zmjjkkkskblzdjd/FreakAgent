from __future__ import annotations

import chromadb


class ChromaCollection:

    def __init__(self, name: str, *, path: str | None, metadata: dict | None = None,
                 embedding_function=None):
        self._client = (chromadb.EphemeralClient() if path is None
                        else chromadb.PersistentClient(path=path))
        kwargs: dict = {"name": name}
        if metadata:
            kwargs["metadata"] = metadata
        if embedding_function is not None:
            kwargs["embedding_function"] = embedding_function
        self._col = self._client.get_or_create_collection(**kwargs)

    @property
    def name(self) -> str:
        return self._col.name

    def close(self) -> None:
        close_fn = getattr(self._client, "close", None)
        if callable(close_fn):
            close_fn()

    def __getattr__(self, item):
        return getattr(self._col, item)


def delete_collection(name: str, *, path: str | None) -> None:
    client = (chromadb.EphemeralClient() if path is None
              else chromadb.PersistentClient(path=path))
    client.delete_collection(name)


def list_collections(*, path: str | None) -> list[str]:
    client = (chromadb.EphemeralClient() if path is None
              else chromadb.PersistentClient(path=path))
    return [col.name for col in client.list_collections()]
