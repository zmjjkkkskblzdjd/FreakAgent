from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from kb_service.ingestion import ingest_hierarchical
from kb_service.store import VectorStore

BASE = Path(os.getenv("LOONG_DOC_DIR", "data/doc"))
_ZH = re.compile(r"[一-鿿]")


def _is_zh(text: str) -> bool:
    return len(_ZH.findall(text[:3000])) > 50


def load_financial_zh(max_count: int = 50) -> list[dict]:
    docs: list[dict] = []
    for p in sorted((BASE / "financial").glob("*.txt"))[:max_count]:
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
        if text and _is_zh(text):
            docs.append({"content": text, "source": f"loong/financial/{p.stem}", "type": "txt"})
    return docs


def main() -> None:
    parser = argparse.ArgumentParser(description="Loong financial 批量灌库")
    parser.add_argument("--count", type=int, default=50, help="灌库篇数（默认 50，999 表示全部）")
    parser.add_argument("--no-summary", action="store_true", help="跳过摘要生成（默认生成）")
    parser.add_argument("--clear-summaries", action="store_true", help="灌库前清空已有 summary collection")
    args = parser.parse_args()

    store = VectorStore(enable_hierarchical=True)

    if args.clear_summaries and store.hierarchical_enabled:
        print("清空已有 summary collection...")
        existing = store.summary_collection.get()
        if existing["ids"]:
            store.summary_collection.delete(ids=existing["ids"])
            print(f"  已删除 {len(existing['ids'])} 条旧摘要")
        else:
            print("  （原本就是空的）")

    docs = load_financial_zh(max_count=args.count)
    total_chars = sum(len(d["content"]) for d in docs)
    print(f"\n本次灌库：{len(docs)} 篇，共 {total_chars:,} 字符")
    print(f"摘要：{'跳过' if args.no_summary else '生成'}")

    generate_summary = not args.no_summary
    print(f"\n开始 ingest_hierarchical（parent_size=1000, child_size=400, summary={generate_summary}）...")
    result = ingest_hierarchical(docs, store, generate_summary=generate_summary)

    print(f"\n===== 灌库完成 =====")
    print(f"  documents  : {len(docs)}")
    print(f"  parents    : {result['parents']}")
    print(f"  children   : {result['children']}")
    print(f"  summaries  : {result['summaries']}")

    if store.hierarchical_enabled:
        print(f"\n===== Chroma 验证 =====")
        print(f"  parent collection  : {store.parent_collection.count()} 条")
        print(f"  child collection   : {store.child_collection.count()} 条")
        print(f"  summary collection : {store.summary_collection.count()} 条")


if __name__ == "__main__":
    main()
