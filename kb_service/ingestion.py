import hashlib
import logging
import re
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from kb_service.loader import DocumentLoader
from kb_service.splitter import TextSplitter
from kb_service.store import VectorStore
from kb_service.parent_child import split_parent_child, ParentChildResult
from kb_service.summary import SummaryGenerator, SummaryResult

logger = logging.getLogger(__name__)

_DOC_CONTEXT_MAX = 60


def build_doc_context(doc: dict) -> str:
    title = (doc.get("title") or "").strip()
    content = doc.get("content") or ""
    if not title:
        m = re.search(r"^#[ \t]+(\S.*?)[ \t]*$", content, re.M)
        if m:
            title = m.group(1).strip()
    if not title:
        source = str(doc.get("source") or "")
        if source and source != "unknown":
            title = Path(source).stem
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        return ""
    return f"【{title[:_DOC_CONTEXT_MAX]}】"

DEFAULT_EXCLUDE_DIRS = ['.git', '__pycache__', 'node_modules', '.venv',
                        'chroma_db', 'BAAI--bge-small-zh-v1.5']
DEFAULT_EXTENSIONS = ['.md', '.txt', '.csv', '.pdf', '.docx',
                      '.html', '.htm',
                      '.pptx', '.xlsx',
                      '.png', '.jpg', '.jpeg',
                      '.tif', '.tiff', '.bmp']



def build_chunks(
        documents: list[dict],
        chunk_size: int = 400,   
        chunk_overlap: int = 50,
) -> tuple[list[str], list[str], list[dict]]:
    splitter = TextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    all_ids: list[str] = []
    all_chunks: list[str] = []
    all_metadatas: list[dict] = []

    for doc in documents:
        content = doc["content"]
        source = doc.get("source", "unknown")
        doc_type = doc.get("type", "txt")  

        if doc_type == "md":
            pieces = splitter.split_markdown(content)
        elif doc_type == "txt":
            pieces = splitter.split_smart(content)  
        else:
            pieces = splitter.split_by_separator(content)

        ctx = build_doc_context(doc)      
        indexed_at = datetime.now().isoformat(timespec="seconds")

        for i, chunk in enumerate(pieces):
            if not chunk.strip():
                continue
            all_ids.append(
                hashlib.md5(f"{source}#{i}#{chunk}".encode("utf-8")).hexdigest()
            )
            all_chunks.append(ctx + chunk)
            all_metadatas.append({
                "source": source,
                "doc_type": doc_type,
                "chunk_index": i,
                "total_chunks": len(pieces),
                "char_count": len(ctx) + len(chunk),
                "indexed_at": indexed_at,
            })

    return all_ids, all_chunks, all_metadatas


def ingest_documents(
        documents: list[dict],
        store: VectorStore,
        chunk_size: int = 400,
        chunk_overlap: int = 50,
        batch_size: int = 50,
) -> int:
    ids, chunks, metadatas = build_chunks(documents, chunk_size, chunk_overlap)
    print(f"总计 {len(chunks)} 个 chunk，开始索引...")
    store.add_documents(chunks, metadatas=metadatas, ids=ids, batch_size=batch_size)
    return len(chunks)


def ingest_directory(
        dir_path: str,
        store: VectorStore,
        exclude_dirs: list[str] = None,
        extensions: list[str] = None,
        chunk_size: int = 400,
        chunk_overlap: int = 50,
) -> int:
    documents = DocumentLoader.load_directory(
        dir_path,
        exclude_dirs=exclude_dirs or DEFAULT_EXCLUDE_DIRS,
        extensions=extensions or DEFAULT_EXTENSIONS,
    )
    return ingest_documents(documents, store,
                            chunk_size=chunk_size, chunk_overlap=chunk_overlap)



@dataclass
class HierarchicalChunks:
    parent_ids: list[str]
    parent_chunks: list[str]
    parent_metas: list[dict]

    child_ids: list[str]
    child_chunks: list[str]
    child_metas: list[dict]

    summary_ids: list[str]
    summary_chunks: list[str]
    summary_metas: list[dict]


def build_hierarchical_chunks(
    documents: list[dict],
    parent_size: int = 1000,
    child_size: int = 400,
    child_overlap: int = 50,
    parent_mode: str = "paragraph",
    generate_summary: bool = True,
    summary_min_chars: int = 50,
) -> HierarchicalChunks:
    indexed_at = datetime.now().isoformat(timespec="seconds")

    parent_ids: list[str] = []
    parent_chunks: list[str] = []
    parent_metas: list[dict] = []

    child_ids: list[str] = []
    child_chunks: list[str] = []
    child_metas: list[dict] = []

    summary_ids: list[str] = []
    summary_chunks: list[str] = []
    summary_metas: list[dict] = []

    summary_gen = SummaryGenerator() if generate_summary else None
    total_summaries = 0

    for doc in documents:
        content = doc["content"]
        source = doc.get("source", "unknown")
        doc_type = doc.get("type", "txt")

        result: ParentChildResult = split_parent_child(
            content=content,
            source=source,
            doc_type=doc_type,
            parent_size=parent_size,
            child_size=child_size,
            child_overlap=child_overlap,
            parent_mode=parent_mode,
        )

        p_count = len(result.parents)
        c_count = sum(len(children) for children in result.children)
        print(f"[{source}] {p_count} parents / {c_count} children", flush=True)

        ctx = build_doc_context(doc)

        for pi in range(p_count):
            parent_ids.append(result.parent_ids[pi])
            parent_chunks.append(ctx + result.parents[pi])
            pm = dict(result.parent_metas[pi])
            pm["has_children"] = True
            pm["child_count"] = len(result.children[pi])
            pm["indexed_at"] = indexed_at
            parent_metas.append(pm)

        global_ci = 0
        for pi, children in enumerate(result.children):
            for ci, (chunk, cid, cm) in enumerate(zip(children, result.child_ids[pi], result.child_metas[pi])):
                child_ids.append(cid)
                child_chunks.append(ctx + chunk)
                cm = dict(cm)
                cm["global_index"] = global_ci
                cm["indexed_at"] = indexed_at
                cm["has_summary"] = False   
                child_metas.append(cm)
                global_ci += 1

        if summary_gen is not None:
            for pi, parent_text in enumerate(result.parents):
                pid = result.parent_ids[pi]

                if len(parent_text.strip()) < summary_min_chars:
                    logger.debug(f"[{source}] parent#{pi} 太短（{len(parent_text)} 字符），跳过摘要")
                    for cm in result.child_metas[pi]:
                        cm["has_summary"] = False
                    continue

                sres: SummaryResult = summary_gen.generate(parent_text, min_chars=summary_min_chars)
                if not sres.success or not sres.text:
                    logger.warning(f"[{source}] parent#{pi} 摘要失败：{sres}")
                    continue

                sid = hashlib.md5(f"{source}#summary#{pid}".encode("utf-8")).hexdigest()
                summary_ids.append(sid)
                summary_chunks.append(sres.text)
                summary_metas.append({
                    "source": source,
                    "doc_type": doc_type,
                    "parent_index": pi,
                    "parent_id": pid,
                    "summary_type": "llm",
                    "model": sres.model_used,
                    "char_count": len(sres.text),
                    "indexed_at": indexed_at,
                })

                for cm in result.child_metas[pi]:
                    cm["has_summary"] = True

                total_summaries += 1
                print(f"[{source}] parent#{pi} 摘要 OK ({sres.model_used}, {sres.latency_ms}ms)", flush=True)

    print(
        f"build_hierarchical_chunks 完成：{len(parent_ids)} parents / "
        f"{len(child_ids)} children / {len(summary_ids)} summaries",
        flush=True,
    )

    return HierarchicalChunks(
        parent_ids=parent_ids,
        parent_chunks=parent_chunks,
        parent_metas=parent_metas,
        child_ids=child_ids,
        child_chunks=child_chunks,
        child_metas=child_metas,
        summary_ids=summary_ids,
        summary_chunks=summary_chunks,
        summary_metas=summary_metas,
    )


def ingest_hierarchical(
    documents: list[dict],
    store: VectorStore,
    parent_size: int = 1000,
    child_size: int = 400,
    child_overlap: int = 50,
    parent_mode: str = "paragraph",
    generate_summary: bool = True,
    batch_size: int = 50,
) -> dict:
    hc = build_hierarchical_chunks(
        documents,
        parent_size=parent_size,
        child_size=child_size,
        child_overlap=child_overlap,
        parent_mode=parent_mode,
        generate_summary=generate_summary,
    )

    return store.add_hierarchical(
        parent_ids=hc.parent_ids,
        parent_chunks=hc.parent_chunks,
        parent_metas=hc.parent_metas,
        child_ids=hc.child_ids,
        child_chunks=hc.child_chunks,
        child_metas=hc.child_metas,
        summary_ids=hc.summary_ids,
        summary_chunks=hc.summary_chunks,
        summary_metas=hc.summary_metas,
        batch_size=batch_size,
    )
