import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from knowledge import VectorStore, ingest_directory

DOCS_DIR = PROJECT_ROOT / "data" / "docs"
CHROMA_DIR = PROJECT_ROOT / "data" / "chroma_db"
COLLECTION_NAME = "qa_kb"


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    if not any(DOCS_DIR.rglob("*.md")):
        print(f"⚠️ {DOCS_DIR} 下还没有 .md 文件，请先放 5~10 篇教程笔记再跑本脚本。")
        return

    store = VectorStore(COLLECTION_NAME, persist_dir=str(CHROMA_DIR))
    try:
        before = store.get_stats()["total_documents"]

        n_chunks = ingest_directory(str(DOCS_DIR), store)

        after = store.get_stats()["total_documents"]
        print(f"\n本次切出 chunk：{n_chunks}")
        print(f"库内总量：{before} → {after}")
        print(f"文档来源：{store.list_sources()}")
        if after == before:
            print("✅ 与跑之前总量一致（幂等）")
        else:
            print("✅ 有新内容写入；再跑一遍总量应保持不变")
    finally:
        store.close()


if __name__ == "__main__":
    main()
