from kb_service.kb_config import (   
    GLOSSARY_COLLECTION,
    FEWSHOT_COLLECTION,
    QA_KB_COLLECTION,
    DEFAULT_DIR,
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_DATABASE,
    MYSQL_USER,
    MYSQL_PASSWORD,
    API_HOST,
    API_PORT,
)

__all__ = [
    "GLOSSARY_COLLECTION", "FEWSHOT_COLLECTION", "QA_KB_COLLECTION",
    "DEFAULT_DIR",
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD",
    "API_HOST", "API_PORT",
    "VectorStore", "DEFAULT_PERSIST_DIR", "search_semantic", "_HashEmbedding",
    "get_embedding", "get_embeddings_batch", "get_embedding_model",
    "resolve_model_path", "resolve_device", "EMBED_DIM", "EMBED_MODEL_TAG", "EMBED_BATCH_SIZE",
    "DocumentLoader", "TextSplitter",
    "build_chunks", "ingest_documents", "ingest_directory",
    "parse_json_array", "build_embed_text", "index_rows", "build_kb_index",
    "select_sql", "fetch_kb_rows",
    "split_structured",
    "split_parent_child", "ParentChildResult",
    "SummaryGenerator",
    "build_hierarchical_chunks", "HierarchicalChunks", "ingest_hierarchical",
]


def __getattr__(name):
    if name in ("VectorStore", "DEFAULT_PERSIST_DIR", "search_semantic", "_HashEmbedding"):
        from kb_service import store
        return getattr(store, name)
    if name in ("get_embedding", "get_embeddings_batch", "get_embedding_model",
                "resolve_model_path", "resolve_device",
                "EMBED_DIM", "EMBED_MODEL_TAG", "EMBED_BATCH_SIZE"):
        from kb_service import embeddings
        return getattr(embeddings, name)
    if name == "DocumentLoader":
        from kb_service.loader import DocumentLoader
        return DocumentLoader
    if name == "TextSplitter":
        from kb_service.splitter import TextSplitter
        return TextSplitter
    if name in ("build_chunks", "ingest_documents", "ingest_directory"):
        from kb_service import ingestion
        return getattr(ingestion, name)
    if name in ("build_hierarchical_chunks", "HierarchicalChunks", "ingest_hierarchical"):
        from kb_service import ingestion
        return getattr(ingestion, name)
    if name in ("parse_json_array", "build_embed_text", "index_rows", "build_kb_index"):
        from kb_service import kb_index
        return getattr(kb_index, name)
    if name in ("select_sql", "fetch_kb_rows"):
        from kb_service import mysql_source
        return getattr(mysql_source, name)
    if name == "split_structured":
        from kb_service.structured import split_structured
        return split_structured
    if name in ("split_parent_child", "ParentChildResult"):
        from kb_service.parent_child import split_parent_child, ParentChildResult
        return split_parent_child if name == "split_parent_child" else ParentChildResult
    if name == "SummaryGenerator":
        from kb_service.summary import SummaryGenerator
        return SummaryGenerator
    raise AttributeError(f"module 'kb_service' has no attribute {name!r}")
