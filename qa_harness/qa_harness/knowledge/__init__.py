import sys
from pathlib import Path

_ALLFUSION_ROOT = str(Path(__file__).resolve().parents[3])
if _ALLFUSION_ROOT not in sys.path:
    sys.path.insert(0, _ALLFUSION_ROOT)

from kb_service.kb_config import (
    GLOSSARY_COLLECTION,
    FEWSHOT_COLLECTION,
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_DATABASE,
    MYSQL_USER,
    MYSQL_PASSWORD,
    API_HOST,
    API_PORT,
)
from kb_service.loader import DocumentLoader
from kb_service.splitter import TextSplitter
from kb_service.mysql_source import select_sql, fetch_kb_rows
from kb_service.kb_index import parse_json_array, build_embed_text, index_rows, build_kb_index

__all__ = [
    "DocumentLoader",
    "TextSplitter",
    "GLOSSARY_COLLECTION",
    "FEWSHOT_COLLECTION",
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "API_HOST",
    "API_PORT",
    "select_sql",
    "fetch_kb_rows",
    "parse_json_array",
    "build_embed_text",
    "index_rows",
    "build_kb_index",
    "get_embedding",
    "get_embeddings_batch",
    "build_chunks",
    "ingest_directory",
    "ingest_documents",
    "DEFAULT_PERSIST_DIR",
    "VectorStore",
    "HybridRetriever",
    "LocalReranker",
]


def __getattr__(name):
    if name in ("get_embedding", "get_embeddings_batch"):
        from kb_service.embeddings import get_embedding, get_embeddings_batch
        return locals()[name]
    if name in ("build_chunks", "ingest_directory", "ingest_documents"):
        from kb_service.ingestion import build_chunks, ingest_directory, ingest_documents
        return locals()[name]
    if name in ("DEFAULT_PERSIST_DIR", "VectorStore"):
        from kb_service.store import DEFAULT_PERSIST_DIR, VectorStore
        return locals()[name]
    if name == "HybridRetriever":
        from .retrieval import HybridRetriever
        return HybridRetriever
    if name == "LocalReranker":
        from .reranker import LocalReranker
        return LocalReranker
    raise AttributeError(f"module 'knowledge' has no attribute {name!r}")
