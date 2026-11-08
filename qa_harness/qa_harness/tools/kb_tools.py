import os
import sys
from contextvars import ContextVar

from . import tool
from knowledge import HybridRetriever

_DEFAULT_TOP_K = int(os.getenv("QA_RETRIEVE_TOP_K", "10"))
_EVIDENCE_CAP = int(os.getenv("QA_EVIDENCE_MAX", "15"))
_SOURCE_CAP = int(os.getenv("QA_EVIDENCE_PER_SOURCE_MAX", "3"))
_AUTO_MIN = int(os.getenv("QA_AUTO_QUERY_MIN", "10"))

_CURRENT_QUESTION: ContextVar[str] = ContextVar("kb_current_question", default="")


def set_current_question(question: str) -> None:
    _CURRENT_QUESTION.set(question or "")


def _merge(per_query: list[list[dict]], queries: list[str], cap: int) -> tuple[list[dict], int]:
    merged: dict[str, dict] = {}
    reserved: list[str] = []
    for results in per_query:
        for ch in results:
            cid = ch.get("chunk_id")
            if not cid:
                continue
            old = merged.get(cid)
            if old is None or float(ch.get("score") or 0) > float(old.get("score") or 0):
                merged[cid] = ch
        if results:
            cid = results[0].get("chunk_id")
            if cid and cid not in reserved:
                reserved.append(cid)

    picked = reserved[:cap]
    src_count: dict[str, int] = {}
    for cid in picked:
        src = merged[cid].get("source") or ""
        src_count[src] = src_count.get(src, 0) + 1

    dropped = max(0, len(merged) - len(picked))
    rest = sorted(
        (cid for cid in merged if cid not in set(picked)),
        key=lambda cid: float(merged[cid].get("score") or 0), reverse=True,
    )
    for cid in rest:
        if len(picked) >= cap:
            break
        src = merged[cid].get("source") or ""
        if src_count.get(src, 0) >= _SOURCE_CAP:
            continue
        picked.append(cid)
        src_count[src] = src_count.get(src, 0) + 1
    return [merged[cid] for cid in picked], dropped


def create_kb_tools(store, retriever=None):
    @tool(
        description=(
            "混合检索知识库（BM25 关键词 + 语义向量融合），"
            "返回与查询最相关的证据片段列表，每条含 chunk_id/content/source/score/match_type。"
            "只读工具；查不到时返回空列表而非报错。"
        ),
        parameter_descriptions={
            "query": "检索词或完整问题",
            "top_k": "返回片段数量上限，默认 10",
        },
    )
    def retrieve_knowledge(query: str, top_k: int = _DEFAULT_TOP_K) -> dict:
        results = (retriever or HybridRetriever(store)).retrieve(query, n=top_k)
        return {"ok": True, "evidences": results}      

    @tool(description="列出知识库中所有文档来源（文件名），用于判断知识库里大概有什么")
    def list_sources() -> dict:
        return {"ok": True, "sources": store.list_sources()}

    @tool(
        description=(
            "批量检索知识库：传入多个检索词，逐个执行混合检索后合并去重（保留最高分）。"
            "deep 档按 Plan 的 search_queries 一次性批量检索，避免多次单条调用。"
            "返回格式与 retrieve_knowledge 相同。"
        ),
        parameter_descriptions={
            "queries": "检索词列表，如 [\"re.search\", \"dataclass\"]",
            "top_k": "每个检索词返回片段数量上限，默认 10",
        },
    )
    def retrieve_batch(queries: list[str], top_k: int = _DEFAULT_TOP_K) -> dict:
        if not queries:
            return {"ok": True, "evidences": []}
        r = retriever or HybridRetriever(store)

        used: list[str] = []
        per_query: list[list[dict]] = []
        for q in queries:
            if not q or q in used:
                continue
            used.append(q)
            per_query.append(r.retrieve(q, n=top_k))

        evidences, dropped = _merge(per_query, used, _EVIDENCE_CAP)

        auto = ""
        question = _CURRENT_QUESTION.get()
        if len(evidences) < _AUTO_MIN and question and question not in used:
            auto = question
            used.append(auto)
            per_query.append(r.retrieve(auto, n=top_k))
            evidences, dropped = _merge(per_query, used, _EVIDENCE_CAP)

        qdesc = "; ".join(f"{q[:20]}({len(r)})" for q, r in zip(used, per_query))
        print(
            f"[retrieve_batch] queries={qdesc} | dedup={sum(len(x) for x in per_query)} "
            f"→ kept={len(evidences)} | dropped={dropped} | auto={auto!r}",
            file=sys.stderr,
        )
        return {"ok": True, "evidences": evidences}

    @tool(
        description="按 chunk_id 取片段全文，用于阅读检索摘要之外的完整内容或核对引用",
        parameter_descriptions={"chunk_id": "片段 ID，必须来自此前检索结果中真实存在的 chunk_id"},
    )
    def get_chunk(chunk_id: str) -> dict:
        hits = store.get_by_ids([chunk_id])
        return {"ok": bool(hits), "evidence": hits[0] if hits else None,
                "error": "" if hits else "chunk 不存在"}

    return retrieve_knowledge, list_sources, retrieve_batch, get_chunk
