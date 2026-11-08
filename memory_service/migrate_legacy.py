from __future__ import annotations

import argparse

import chromadb

from memory_service.store import COLLECTION, MemoryStore, _VALID_TYPES

_LEGACY = {
    "qa_memory": {   
        "memory_type": lambda m: m.get("memory_type") or m.get("type") or "general",
        "source": lambda m: m.get("source") or f"session:{m.get('session_id', '')}",
        "fallback_type": "general",      
    },
    "da_memory": {
        "memory_type": lambda m: m.get("memory_type") or "sql",
        "source": lambda m: m.get("source") or f"thread:{m.get('source_thread_id', '')}",
        "fallback_type": "sql",          
    },
}


def _normalize(m: dict, rules: dict) -> dict:
    mtype = rules["memory_type"](m)
    if mtype not in _VALID_TYPES:
        mtype = rules["fallback_type"]
    return {"user_id": m.get("user_id", ""),
            "tenant_id": m.get("tenant_id", "default"),
            "memory_type": mtype,
            "source": rules["source"](m),
            "importance": m.get("importance", 1.0),
            "access_count": m.get("access_count", 0),
            "last_accessed_at": m.get("last_accessed_at", 0.0),
            "status": m.get("status", "active"),
            "created_at": m.get("created_at")}


def migrate(store: MemoryStore, legacy_dir: str, collection: str, dry_run: bool = False) -> int:
    client = chromadb.PersistentClient(path=legacy_dir)
    try:
        col = client.get_collection(collection)
    except Exception:
        return 0                        
    got = col.get(include=["documents", "metadatas"])
    rules = _LEGACY[collection]
    n = 0
    for text, meta in zip(got["documents"], got["metadatas"]):
        if dry_run:
            n += 1
            continue
        store.add(text, _normalize(meta or {}, rules))
        n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qa-dir", default="", help="qa_harness 记忆库目录（含 qa_memory）")
    ap.add_argument("--da-dir", default="", help="DataAnalyzeAgent 记忆库目录（含 da_memory）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = MemoryStore()
    total = 0
    if args.qa_dir:
        total += migrate(store, args.qa_dir, "qa_memory", args.dry_run)
    if args.da_dir:
        total += migrate(store, args.da_dir, "da_memory", args.dry_run)
    print(f"{'[dry-run] ' if args.dry_run else ''}migrated {total} memories -> {COLLECTION}")


if __name__ == "__main__":
    main()
