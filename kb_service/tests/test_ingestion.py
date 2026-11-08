from kb_service.ingestion import DEFAULT_EXTENSIONS, build_chunks, ingest_documents
from kb_service.store import VectorStore, _HashEmbedding


def _md_doc():
    return {"content": "# 标题一\n\n第一节的正文内容。\n\n# 标题二\n\n第二节的内容更长一些，用来观察分节行为。\n",
            "source": "doc.md", "type": "md"}


def test_build_chunks_md_breadcrumb_and_metadata():
    ids, chunks, metas = build_chunks([_md_doc()])
    assert ids and chunks and len(ids) == len(chunks) == len(metas)
    assert any("标题一" in c for c in chunks)               
    m = metas[0]
    assert m["source"] == "doc.md" and m["doc_type"] == "md"
    assert m["total_chunks"] == len(chunks) and m["char_count"] == len(chunks[0])
    ids2, _, _ = build_chunks([_md_doc()])
    assert ids2 == ids


def test_build_chunks_skips_blank_pieces():
    ids, chunks, metas = build_chunks([{"content": "\n\n  \n", "source": "e.md", "type": "md"}])
    assert ids == [] and chunks == [] and metas == []


def test_ingest_documents_writes_store(tmp_path):
    store = VectorStore(collection_name="t_ing", persist_dir=str(tmp_path),
                        embedding=_HashEmbedding())
    try:
        n = ingest_documents([_md_doc()], store)
        assert n == len(build_chunks([_md_doc()])[1])       
        assert store.collection.count() == n
    finally:
        store.close()


def test_splitter_splits_oversized_table_paragraph():
    from kb_service.splitter import TextSplitter

    rows = ["|项目|本期发生额|上期发生额|", "|---|---|---|"]
    rows += [f"|科目{i}|{i * 1000}.00|{i * 2000}.00|" for i in range(60)]
    chunks = TextSplitter(chunk_size=400).split_table_aware("\n".join(rows))
    assert len(chunks) > 1
    assert all("|项目|本期发生额|上期发生额|" in c for c in chunks)   
    assert all(len(c) <= 400 for c in chunks)


def test_splitter_hard_splits_unbreakable_sentence():
    from kb_service.splitter import TextSplitter

    chunks = TextSplitter(chunk_size=100, chunk_overlap=0).split_by_separator("字" * 350)
    assert len(chunks) == 4 and all(len(c) <= 100 for c in chunks)


def test_splitter_detects_chinese_enumerated_heading():
    from kb_service.splitter import TextSplitter

    headings = TextSplitter().detect_headings("2、合并利润表\n|项目|本期|\n|---|---|\n")
    assert headings and "合并利润表" in headings[0][1]


def test_splitter_ignores_percentage_as_heading():
    from kb_service.splitter import TextSplitter

    headings = TextSplitter().detect_headings("0.61%\n34,117,489\n")
    assert headings == []


def test_default_extensions_cover_common_formats():
    for ext in (".md", ".txt", ".csv", ".pdf", ".docx", ".html"):
        assert ext in DEFAULT_EXTENSIONS



def test_doc_context_priority_title_h1_filename():
    from kb_service.ingestion import build_doc_context

    assert build_doc_context(
        {"title": "产品手册", "source": "a.md", "content": "# 其他标题"}
    ) == "【产品手册】"
    assert build_doc_context(
        {"source": "a.md", "content": "\n\n# 用户指南\n正文内容"}
    ) == "【用户指南】"
    assert build_doc_context(
        {"source": "notes/设计文档.md", "content": "没有标题的正文"}
    ) == "【设计文档】"
    assert build_doc_context({"source": "unknown", "content": "正文"}) == ""


def test_hierarchical_chunks_carry_doc_context():
    from kb_service.ingestion import build_hierarchical_chunks

    doc = {"content": "第一段说明文字。\n\n第二段说明文字。", "source": "specs/接口说明.txt", "type": "txt"}
    res = build_hierarchical_chunks([doc], generate_summary=False)
    assert res.parent_chunks and res.child_chunks
    assert all(p.startswith("【接口说明】") for p in res.parent_chunks)
    assert all(c.startswith("【接口说明】") for c in res.child_chunks)


def test_splitter_does_not_treat_version_or_unit_as_heading():
    from kb_service.splitter import TextSplitter

    text = "3.5英寸屏幕\nv1.2 说明\n第二章 安装步骤\n"
    headings = TextSplitter().detect_headings(text)
    texts = [h[1] for h in headings]
    assert any("安装步骤" in t for t in texts)
    assert all("3.5英寸" not in t and "v1.2" not in t for t in texts)
