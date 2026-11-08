from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from kb_service.store import VectorStore
from kb_service.summary import SummaryGenerator


def _summary_id(parent_id: str, content: str) -> str:
    return hashlib.sha1(f"{parent_id}:{content}".encode("utf-8")).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser(description="Loong 摘要补录")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 篇（0=全部）")
    ap.add_argument("--batch-size", type=int, default=50, help="ChromaDB 写入 batch size")
    ap.add_argument("--force", action="store_true", help="覆盖已有 summary")
    args = ap.parse_args()

    store = VectorStore(enable_hierarchical=True)
    generator = SummaryGenerator()

    existing_summary_parents: set[str] = set()
    if not args.force and store.summary_collection is not None:
        all_summaries = store.summary_collection.get(include=["metadatas"])
        if all_summaries and all_summaries.get("metadatas"):
            existing_summary_parents = {
                m.get("parent_id", "") for m in all_summaries["metadatas"] if m.get("parent_id")
            }

    parents = store.get_all_parent_chunks()
    if args.limit > 0:
        parents = parents[:args.limit]

    print(f"待处理 parent: {len(parents)} 条")
    print(f"已有 summary 的 parent: {len(existing_summary_parents)} 条（{'覆盖模式' if args.force else '跳过模式'}）")

    summary_ids: list[str] = []
    summary_chunks: list[str] = []
    summary_metas: list[dict] = []
    skipped = 0
    failed = 0

    for i, p in enumerate(parents, 1):
        parent_id = p.get("parent_id", "")
        content = p.get("content", "")
        source = p.get("source", "unknown")

        if not parent_id or not content:
            print(f"  [{i}/{len(parents)}] 跳过：parent_id 或 content 为空")
            skipped += 1
            continue

        if not args.force and parent_id in existing_summary_parents:
            print(f"  [{i}/{len(parents)}] 跳过：已有 summary ({source})")
            skipped += 1
            continue

        try:
            result = generator.generate(content)
            summary = result.text if result.success else ""
            if not summary or not summary.strip():
                print(f"  [{i}/{len(parents)}] 失败：摘要为空 ({source})")
                failed += 1
                continue

            sid = _summary_id(parent_id, summary)
            summary_ids.append(sid)
            summary_chunks.append(summary)
            summary_metas.append({
                "parent_id": parent_id,
                "source": source,
                "doc_type": p.get("doc_type", "financial_report"),
                "chunk_index": p.get("chunk_index", 0),
            })
            print(f"  [{i}/{len(parents)}] 成功：{source} -> {len(summary)} 字")

            if len(summary_ids) >= args.batch_size:
                store.summary_collection.upsert(
                    ids=summary_ids,
                    documents=summary_chunks,
                    metadatas=summary_metas,
                    embeddings=store._embed_batch(summary_chunks),
                )
                print(f"  >>> 批量写入 {len(summary_ids)} 条")
                summary_ids, summary_chunks, summary_metas = [], [], []

        except Exception as e:
            print(f"  [{i}/{len(parents)}] 失败：{e} ({source})")
            failed += 1

    if summary_ids:
        store.summary_collection.upsert(
            ids=summary_ids,
            documents=summary_chunks,
            metadatas=summary_metas,
            embeddings=store._embed_batch(summary_chunks),
        )
        print(f"  >>> 批量写入 {len(summary_ids)} 条")

    print(f"\n===== 补录完成 =====")
    print(f"  成功: {len(parents) - skipped - failed}")
    print(f"  跳过: {skipped}")
    print(f"  失败: {failed}")
    print(f"  summary collection 总数: {store.summary_collection.count()}")


if __name__ == "__main__":
    main()
