from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from kb_service.kb_config import FEWSHOT_COLLECTION, QA_KB_COLLECTION
from kb_service.kb_registry import resolve_kbs
from kb_service.store import VectorStore
from unified_agent.core.state import UnifiedState
from unified_agent.observability.tracer import emit_event

_DA_ROOT = Path(__file__).resolve().parents[2] / "DataAnalyzeAgent" / "DataAnalyzeAgent"
if str(_DA_ROOT) not in sys.path:
    sys.path.insert(0, str(_DA_ROOT))

try:
    from data_analyze_agent.db import gateway  # noqa: E402
except Exception:
    gateway = None  # noqa: E402  业务表卡将降级为空

_QA_ROOT = Path(__file__).resolve().parents[2] / "qa_harness" / "qa_harness"
if str(_QA_ROOT) not in sys.path:
    sys.path.append(str(_QA_ROOT))

try:
    from knowledge.retrieval import HybridRetriever
except Exception:
    HybridRetriever = None  # type: ignore[assignment]

def _reranker_device() -> str:
    import os
    forced = os.getenv("RERANKER_DEVICE", "").strip()
    if forced:
        return forced
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


_RERANKER = None
try:
    import os as _os
    if _os.getenv("QA_USE_RERANKER", "").strip() in ("1", "true", "True"):
        from knowledge.reranker import LocalReranker
        _model_path = _os.getenv("RERANKER_MODEL_PATH", "")
        _RERANKER = LocalReranker(_model_path, device=_reranker_device())
except Exception:
    _RERANKER = None

_TOP_N_RAG = 5
_KB_MIN_SCORE = float(os.getenv("KB_CARD_MIN_SCORE", "0.4"))
_TOP_N_BU = 2
_TOP_N_BU_SCHEMA = int(os.getenv("KB_TABLE_CARD_SCHEMA_TOPK", "6"))
_TOP_N_GL = 3
_TABLE_META_CACHE: dict[int, tuple[float, list[dict]]] = {}
_COLUMN_CACHE: dict[tuple[int, str], tuple[float, list[dict]]] = {}
_TABLE_CACHE_TTL = 300  

_TENANT_DS_CACHE: dict[int, tuple[float, tuple[int, ...]]] = {}
_TERMS_CACHE: dict[tuple[int, int | None], tuple[float, list[dict]]] = {}

_KB_CACHE_TTL = 300

_STORE_REGISTRY: dict[str, VectorStore] = {}
_RETRIEVER_REGISTRY: dict[str, Any] = {}


def _get_retriever(collection: str):
    if collection not in _STORE_REGISTRY:
        _STORE_REGISTRY[collection] = VectorStore(collection)
    if collection not in _RETRIEVER_REGISTRY and HybridRetriever is not None:
        _RETRIEVER_REGISTRY[collection] = HybridRetriever(
_STORE_REGISTRY[collection], reranker=_RERANKER)
    return _RETRIEVER_REGISTRY.get(collection)


def _retrieve(collection: str, query: str,top, where: dict | None = None) -> list[dict]:
    retriever = _get_retriever(collection)
    if retriever is not None:
        try:
            return retriever.retrieve(query, n=top, where=where)
        except Exception:
            pass
    from kb_service.store import search_semantic
    kb = {FEWSHOT_COLLECTION: "fewshot",
          QA_KB_COLLECTION: "qa_kb"}.get(collection)
    if not kb:
        return []
    try:
        return search_semantic(kb, query, top_k=top, where=where)
    except Exception:
        return []



def _rag_cards(query: str, kb_ids=None) -> tuple[list[str], dict]:
    if kb_ids is not None and not kb_ids:
        return [], {"effective_ids": [], "empty_ids": [], "empty_names": []}

    try:
        from unified_agent.adapters.rag_adapter import kb_retrievers
        members = kb_retrievers(kb_ids)
    except Exception:
        members = []

    if not members:
        cards = []
        for h in _retrieve(QA_KB_COLLECTION, query, _TOP_N_RAG):
            title = h.get("source") or h.get("metadata", {}).get("source") or "未知文档"
            content = h.get("content") or ""
            summary = content[:500].replace("\n", " ")
            cards.append(f"【内部文档】{title}。内容概要：{summary}")
        return cards, {}

    multi = len(members) > 1
    pools = []        
    empty = []        
    for kb_id, kb_name, _, retriever in members:
        try:
            hits = retriever.retrieve(query, n=_TOP_N_RAG, use_rerank=False)
        except Exception:      
            empty.append((kb_id, kb_name))
            continue
        hits = sorted(
            (h for h in hits
             if float(h.get("score", 0.0) or 0.0) >= _KB_MIN_SCORE),
            key=lambda h: float(h.get("score", 0.0) or 0.0), reverse=True)
        if not hits:
            empty.append((kb_id, kb_name))
            continue
        pools.append((float(hits[0].get("score", 0.0) or 0.0), kb_id, kb_name, hits))

    pools.sort(key=lambda p: p[0], reverse=True)
    chosen = []       
    leftover = []     
    for best, kb_id, kb_name, hits in pools:
        chosen.append((best, kb_id, kb_name, hits[0]))
        leftover.extend(
            (float(h.get("score", 0.0) or 0.0), kb_id, kb_name, h)
            for h in hits[1:])
    if len(chosen) > _TOP_N_RAG:
        chosen.sort(key=lambda x: x[0], reverse=True)
        chosen = chosen[:_TOP_N_RAG]
    else:
        leftover.sort(key=lambda x: x[0], reverse=True)
        chosen.extend(leftover[: _TOP_N_RAG - len(chosen)])
    chosen.sort(key=lambda x: x[0], reverse=True)

    cards = []
    for _, _, kb_name, h in chosen:
        title = h.get("source") or h.get("metadata", {}).get("source") or "未知文档"
        if multi:
            title = f"{kb_name}/{title}"
        summary = (h.get("content") or "")[:500].replace("\n", " ")
        cards.append(f"【内部文档】{title}。内容概要：{summary}")
    for _, kb_name in empty:
        cards.append(f"【知识库】{kb_name}：本轮无相关资料（未检索到有效内容）")

    scope = {
        "effective_ids": [kb_id for _, kb_id, _, _ in pools],
        "empty_ids": [kb_id for kb_id, _ in empty],
        "empty_names": [kb_name for _, kb_name in empty],
    }
    return cards, scope



def _score_text(text: str, query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    return sum(1 for t in query_tokens if t and t in text)


def _tenant_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tenant_datasources(tenant_id: int) -> list[int]:
    now = time.time()
    hit = _TENANT_DS_CACHE.get(tenant_id)
    if hit and now - hit[0] < _TABLE_CACHE_TTL:
        return list(hit[1])
    try:
        from sqlalchemy import select

        from allfusion_auth.db import get_engine
        from allfusion_auth.models import datasource
        with get_engine().connect() as conn:
            ids = tuple(int(r.id) for r in conn.execute(
                select(datasource.c.id).where(
                    datasource.c.tenant_id == tenant_id,
                    datasource.c.enabled == 1).order_by(datasource.c.id)).all())
    except Exception:           # noqa: BLE001 — 平台库不可达 → 空表卡（catalog 是增强）
        return []
    _TENANT_DS_CACHE[tenant_id] = (now, ids)
    return list(ids)


def _turn_datasources(state: UnifiedState) -> list[int]:
    explicit = _tenant_int(state.get("datasource_id"))
    if explicit:
        return [explicit]
    tid = _tenant_int(state.get("tenant_id"))
    if tid is None:
        return []
    return _tenant_datasources(tid)


def _query_tokens(query: str) -> set[str]:
    try:
        import jieba
        return set(jieba.cut(query))
    except Exception:
        return set(query)


def _business_table_cards(query: str, state: UnifiedState) -> list[str]:
    if gateway is None:
        return []
    ds_ids = _turn_datasources(state)
    if not ds_ids:
        return []
    tokens = _query_tokens(query)
    scored = []          
    for ds_id in ds_ids:
        for t in _cached_table_metas(ds_id, state):
            text = f"{t.get('name', '')} {t.get('comment') or ''}"
            scored.append((_score_text(text, tokens), ds_id, t))
    if not scored:
        return []
    scored.sort(key=lambda c: c[0], reverse=True)
    picked = []
    for score, ds_id, t in scored[:_TOP_N_BU_SCHEMA]:
        card = _render_table_card(t, _cached_columns(ds_id, t["name"], state))
        picked.append((score, card))
    picked.sort(key=lambda c: c[0], reverse=True)
    return [card for _, card in picked[:_TOP_N_BU]]


def _cached_table_metas(ds_id: int, state: UnifiedState) -> list[dict]:
    now = time.time()
    hit = _TABLE_META_CACHE.get(ds_id)
    if hit and now - hit[0] < _TABLE_CACHE_TTL:
        return hit[1]
    try:
        with gateway.use_datasource(ds_id, state.get("tenant_id"),
                                    state.get("user_id"),
                                    state.get("role") or "member"):
            metas = [t for t in gateway.list_tables()
                     if t.get("name") and gateway.is_business_table(t["name"])]
    except Exception:           # noqa: BLE001 — 网关不可达/越权 → 空卡
        return []
    _TABLE_META_CACHE[ds_id] = (now, metas)
    return metas


def _cached_columns(ds_id: int, table: str, state: UnifiedState) -> list[dict]:
    key = (ds_id, table)
    now = time.time()
    hit = _COLUMN_CACHE.get(key)
    if hit and now - hit[0] < _TABLE_CACHE_TTL:
        return hit[1]
    try:
        with gateway.use_datasource(ds_id, state.get("tenant_id"),
                                    state.get("user_id"),
                                    state.get("role") or "member"):
            columns = (gateway.get_table_schema(table) or {}).get("columns", [])
    except Exception:           # noqa: BLE001 — 单表失败不拖垮其余表
        return []
    _COLUMN_CACHE[key] = (now, columns)
    return columns


def _render_table_card(meta: dict, columns: list[dict]) -> str:
    key_fields = []
    for c in columns:
        cname = c.get("name", "")
        ctype = (c.get("type") or "").lower()
        cmt = c.get("comment") or ""
        if cname in ("id", "dt", "create_time", "update_time",
                     "created_at", "updated_at"):
            continue
        if any(kw in ctype for kw in ("string", "text", "varchar", "char")) \
                and not cmt and len(cname) > 20:
            continue
        key_fields.append(cname + (f"({cmt})" if cmt else ""))
        if len(key_fields) >= 8:
            break
    comment = meta.get("comment") or ""
    card = f"【业务表】{meta.get('name', '')}({comment})"
    if key_fields:
        card += f"。核心字段：{', '.join(key_fields)}"
    return card




def _parse_json_array(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
    except (ValueError, TypeError):
        pass
    return [x.strip() for x in s.split(",") if x.strip()]


def _tenant_terms(tenant_id: int, user_id: int | None = None) -> list[dict]:
    now = time.time()
    key = (tenant_id, user_id)
    hit = _TERMS_CACHE.get(key)
    if hit and now - hit[0] < _KB_CACHE_TTL:
        return hit[1]
    try:
        from kb_service import business_store
        conn = business_store.open_conn()
        try:
            rows = business_store.recall_terms(conn, tenant_id, user_id)
        finally:
            conn.close()
    except Exception:           # noqa: BLE001 — 库不可用 → 空指标卡
        return []
    _TERMS_CACHE[key] = (now, rows)
    return rows


def _term_where(user_id: int | None) -> dict:
    if user_id is None:
        return {"user_id": 0}
    return {"user_id": {"$in": [0, int(user_id)]}}


def _tenant_metric_hits(tenant_id: int, query: str,
                        user_id: int | None = None) -> list[dict]:
    collection = f"biz_terms_{tenant_id}"
    where = _term_where(user_id)
    retriever = _get_retriever(collection)
    if retriever is not None:
        try:
            return retriever.retrieve(query, n=_TOP_N_GL, where=where)
        except Exception:
            return []
    store = _STORE_REGISTRY.get(collection)
    if store is None:
        return []
    try:
        return store.search(query, n_results=_TOP_N_GL, where=where)
    except Exception:
        return []


def _metric_cards(query: str, state: UnifiedState) -> list[str]:
    tid = _tenant_int(state.get("tenant_id"))
    if tid is None:
        return []
    uid = _tenant_int(state.get("user_id"))
    terms = _tenant_terms(tid, uid)
    if not terms:
        return []
    by_id = {str(t["id"]): t for t in terms}
    cards = []
    for h in _tenant_metric_hits(tid, query, uid):
        cid = str(h.get("chunk_id") or h.get("id") or "")
        if cid.startswith("t"):
            cid = cid[1:]
        row = by_id.get(cid)
        if not row:
            continue
        aliases = _parse_json_array(row.get("synonyms"))
        alias_str = "、".join(a for a in aliases if a) or "无"
        cards.append(f"【指标】{row['term']}。同义词：{alias_str}")
    return cards



_SNAPSHOT_CACHE: dict[tuple, tuple[float, dict]] = {}
_SNAPSHOT_TTL = 300                 
_SNAPSHOT_MAX_CHARS = 800           
_SNAPSHOT_KB_CLUSTERS = 3           
_SNAPSHOT_DB_GROUPS = 6             
_SNAPSHOT_INFLIGHT: dict[tuple, threading.Event] = {}
_SNAPSHOT_INFLIGHT_LOCK = threading.Lock()
_SNAPSHOT_WAIT_SEC = 120            


def _get_store(collection: str) -> VectorStore:
    store = _STORE_REGISTRY.get(collection)
    if store is None:
        store = _STORE_REGISTRY[collection] = VectorStore(
            collection_name=f"{collection}_flat",
            enable_hierarchical=True,
            hierarchical_prefix=collection,
        )
    return store


def _kb_sources(prefix: str) -> list[dict]:
    try:
        return _get_store(prefix).list_parent_meta()
    except Exception:           # noqa: BLE001 — 库不可用 → 快照缺该库（增强不是必要）
        return []


def _name_prefix(stem: str) -> str:
    for i, ch in enumerate(stem):
        if ch.isdigit():
            return stem[:i]
    return stem


def _kb_clusters(sources: list[str]) -> list[dict]:
    buckets: dict[str, list[str]] = {}
    for raw in sources:
        path = str(raw or "").replace("\\", "/").strip()
        if not path:
            continue
        head, _, name = path.rpartition("/")
        stem = name.rsplit(".", 1)[0] if "." in name else name
        key = (_name_prefix(stem).strip() or "其他")
        bucket = f"{head}/{key}" if head else key
        buckets.setdefault(bucket, []).append(name)
    groups = [{"prefix": f"{bucket}*", "n": len(names),
               "samples": sorted(names)[:3]}
              for bucket, names in buckets.items()]
    groups.sort(key=lambda g: g["n"], reverse=True)
    return groups[:_SNAPSHOT_KB_CLUSTERS]


def _kb_profile(kb: dict) -> dict:
    sources = sorted({
        str((m or {}).get("source") or "unknown")
        for m in _kb_sources(kb.get("collection_prefix") or "")
    })
    return {
        "kb_id": kb.get("id") or "",
        "kb_name": kb.get("name") or kb.get("id") or "",
        "description": kb.get("description") or "",
        "doc_count": len(sources),
        "clusters": _kb_clusters(sources),
    }


def _db_profile(state: UnifiedState) -> dict:
    ds_ids = _turn_datasources(state)
    if not ds_ids:
        return {}
    groups: dict[str, list[dict]] = {}
    total = 0
    for ds_id in ds_ids:
        for t in _cached_table_metas(ds_id, state):
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            total += 1
            prefix = name.split(".", 1)[0] if "." in name else "其他"
            groups.setdefault(prefix, []).append(
                {"name": name, "comment": t.get("comment") or ""})
    out = [{"prefix": prefix, "n": len(tables),
            "tables": tables[:_SNAPSHOT_DB_GROUPS]}
           for prefix, tables in groups.items()]
    out.sort(key=lambda g: g["n"], reverse=True)
    return {
        "datasource_id": ",".join(str(i) for i in ds_ids),
        "table_count": total,
        "groups": out,
    }


def _snapshot_key(state: UnifiedState) -> tuple:
    tenant = _tenant_int(state.get("tenant_id"))
    ds_ids = tuple(_turn_datasources(state))
    raw_kb_ids = state.get("kb_ids")
    kb_ids = ("default",) if raw_kb_ids is None else tuple(raw_kb_ids)
    return (tenant, ds_ids, kb_ids)


def _catalog_snapshot(state: UnifiedState) -> dict:
    key = _snapshot_key(state)
    now = time.time()
    hit = _SNAPSHOT_CACHE.get(key)
    if hit and now - hit[0] < _SNAPSHOT_TTL:
        return hit[1]

    with _SNAPSHOT_INFLIGHT_LOCK:
        ev = _SNAPSHOT_INFLIGHT.get(key)
        owner = ev is None
        if owner:
            ev = _SNAPSHOT_INFLIGHT[key] = threading.Event()
    if not owner:
        ev.wait(timeout=_SNAPSHOT_WAIT_SEC)
        hit = _SNAPSHOT_CACHE.get(key)
        if hit:
            return hit[1]
        with _SNAPSHOT_INFLIGHT_LOCK:
            owner = _SNAPSHOT_INFLIGHT.get(key) is None
            if owner:
                ev = _SNAPSHOT_INFLIGHT[key] = threading.Event()

    try:
        raw_kb_ids = state.get("kb_ids")
        kb_ids = ("default",) if raw_kb_ids is None else tuple(raw_kb_ids)
        scoped = [] if (raw_kb_ids is not None and not raw_kb_ids) \
            else resolve_kbs(list(kb_ids))
        kbs = [_kb_profile(kb) for kb in scoped]
        db = _db_profile(state)
        built_at = time.time()
        snap = {"built_at": built_at, "kbs": kbs, "dbs": [db] if db else []}
        _SNAPSHOT_CACHE[key] = (built_at, snap)
        return snap
    finally:
        if owner:
            with _SNAPSHOT_INFLIGHT_LOCK:
                if _SNAPSHOT_INFLIGHT.get(key) is ev:
                    _SNAPSHOT_INFLIGHT.pop(key, None)
            ev.set()


def prewarm_snapshot(state: UnifiedState) -> None:
    try:
        _catalog_snapshot(state)
    except Exception:           # noqa: BLE001 — 预取失败不炸调用方
        pass


def _render_snap_text(kbs: list[dict], dbs: list[dict]) -> str:
    lines: list[str] = []
    for kb in kbs:
        head = f"【知识库】{kb.get('kb_name', '')}｜{kb.get('doc_count', 0)} 篇"
        if kb.get("description"):
            head += f"｜描述：{kb['description']}"
        lines.append(head)
        for c in kb.get("clusters") or []:
            line = f"  - {c.get('prefix', '')} {c.get('n', 0)} 篇"
            samples = "、".join(c.get("samples") or [])
            if samples:
                line += f"（例：{samples}）"
            lines.append(line)
    for db in dbs:
        lines.append(f"【数据源】#{db.get('datasource_id', '')}｜{db.get('table_count', 0)} 张表")
        for g in db.get("groups") or []:
            prefix = g.get("prefix") or "其他"
            label = f"{prefix}.*" if prefix != "其他" else "其他"
            line = f"  - {label} {g.get('n', 0)} 张"
            names = "、".join(t.get("name", "") for t in g.get("tables") or [])
            if names:
                line += f"（{names}…）"
            lines.append(line)
    return "\n".join(lines)


def _render_snapshot(snap: dict) -> str:
    kbs = list(snap.get("kbs") or [])
    dbs = list(snap.get("dbs") or [])

    text = _render_snap_text(kbs, dbs)
    if len(text) <= _SNAPSHOT_MAX_CHARS:
        return text

    trimmed_dbs = [
        {**db, "groups": [{k: v for k, v in g.items() if k != "tables"}
                          for g in db.get("groups") or []]}
        for db in dbs
    ]
    text = _render_snap_text(kbs, trimmed_dbs)
    if len(text) <= _SNAPSHOT_MAX_CHARS:
        return text

    trimmed_kbs = [{**kb, "clusters": []} for kb in kbs]
    text = _render_snap_text(trimmed_kbs, trimmed_dbs)
    if len(text) <= _SNAPSHOT_MAX_CHARS:
        return text

    return text[:_SNAPSHOT_MAX_CHARS]



def snapshot_text(state: UnifiedState) -> str:
    try:
        return _render_snapshot(_catalog_snapshot(state))
    except Exception:           # noqa: BLE001 — 库/表不可达 → 空快照
        return ""


def recall(state: UnifiedState, config=None) -> dict:
    query = state.get("resolved_question") or state.get("question", "")
    t0 = time.perf_counter()
    snap = snapshot_text(state)
    t_snap = time.perf_counter()
    rag_cards, kb_scope = _rag_cards(query, state.get("kb_ids"))
    t_rag = time.perf_counter()
    if kb_scope:
        kb_scope["question"] = query
    thread = ((config or {}).get("configurable") or {}).get("thread_id", "") \
        if isinstance(config, dict) else ""
    emit_event(
        "node_exit", "recall_catalog.blocks", "ok",
        {"ms": {"snapshot": int((t_snap - t0) * 1000),
                "table": 0,
                "metric": 0,
                "rag": int((t_rag - t_snap) * 1000)},
         "cards": {"sql": 0, "rag": len(rag_cards),
                   "snapshot_chars": len(snap)}},
        thread, state.get("session_id", ""), int((t_rag - t0) * 1000))
    return {"catalog_hints": {"sql": [], "rag": rag_cards, "snapshot": snap},
            "kb_scope": kb_scope}
