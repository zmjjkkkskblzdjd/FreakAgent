from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ALLFUSION_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = [
    ALLFUSION_ROOT / "data" / "kb_db",
    ALLFUSION_ROOT / "data" / "memory_db",
]


def _to_vec_list(emb):
    if emb is None:
        return None
    return emb.tolist() if hasattr(emb, "tolist") else list(emb)


def migrate_collection(col, pg, *, batch_size: int, dry_run: bool, clear: bool) -> tuple[int, int]:
    total = col.count()
    print(f"  {col.name}: 源 {total} 条", flush=True)
    if dry_run or total == 0:
        return total, 0

    if clear:
        pg.delete(ids=pg.get(include=[])["ids"])

    written = 0
    offset = 0
    while offset < total:
        page = col.get(include=["documents", "metadatas", "embeddings"],
                       limit=batch_size, offset=offset)
        ids = page.get("ids") or []
        if not ids:
            break
        docs = page.get("documents") or [None] * len(ids)
        metas = page.get("metadatas") or [None] * len(ids)
        embs = page.get("embeddings")
        if embs is None:
            raise RuntimeError(f"{col.name}: Chroma 未返回 embeddings，无法无损迁移")
        embs = [_to_vec_list(e) for e in embs]
        pg.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embs)
        written += len(ids)
        offset += len(ids)
        print(f"    已写入 {written}/{total}", flush=True)
    return total, written


def main() -> int:
    parser = argparse.ArgumentParser(description="Chroma → pgvector 迁移")
    parser.add_argument("--source", action="append", default=None,
                        help="Chroma 目录（可多次指定，默认 kb_db + memory_db）")
    parser.add_argument("--collection", default=None, help="只迁指定 collection")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear", action="store_true", help="搬迁前清空目标 collection")
    args = parser.parse_args()

    import chromadb
    from vector_backend.pg_backend import PgCollection, default_dsn

    sources = [Path(p) for p in (args.source or DEFAULT_SOURCES)]
    print(f"目标 PG: {default_dsn()}")
    failed = 0
    for src in sources:
        if not src.exists():
            print(f"[跳过] 源目录不存在: {src}")
            continue
        client = chromadb.PersistentClient(path=str(src))
        names = sorted(c.name for c in client.list_collections())
        if args.collection:
            names = [n for n in names if n == args.collection]
        print(f"[源] {src}（{len(names)} 个集合）")
        for name in names:
            col = client.get_collection(name)
            try:
                pg = None if args.dry_run else PgCollection(name)
                total, written = migrate_collection(
                    col, pg, batch_size=args.batch_size,
                    dry_run=args.dry_run, clear=args.clear)
                if not args.dry_run:
                    target = pg.count()
                    flag = "OK" if target == total else "COUNT-MISMATCH"
                    print(f"  {name}: 目标 {target} 条 → {flag}")
                    if target != total:
                        failed += 1
            except Exception as e:      # noqa: BLE001 — 单集合失败不中断其余
                failed += 1
                print(f"  [失败] {name}: {e}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
