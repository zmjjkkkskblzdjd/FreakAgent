from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select

from allfusion_auth.db import get_engine
from allfusion_auth.deps import AuthUser, require_db_admin, require_kb_admin, require_user
from allfusion_auth.logging_redact import install_exception_handler, install_logging_redact
from allfusion_auth.models import app_user
from kb_service import business_store, kb_owner
from kb_service.doc_parser_client import DocParserError, parse_files
from kb_service.ingestion import build_hierarchical_chunks
from kb_service.kb_config import DEFAULT_DIR
from kb_service.kb_registry import DEFAULT_KB_ID, DEFAULT_PREFIX
from kb_service.retrieval import HierarchicalRetriever
from kb_service.store import VectorStore
from vector_backend import factory as vector_factory

_META_DB = Path(__file__).resolve().parents[1] / "data" / "kb_meta.db"

app = FastAPI(title="kb_service", version="0.1.0")


@app.on_event("startup")
def _startup_migrate() -> None:
    _conn().close()
install_logging_redact()
install_exception_handler(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)
router = APIRouter(dependencies=[Depends(require_user)])


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "kb_service"}

_DDL = """
CREATE TABLE IF NOT EXISTS kb(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    collection_prefix TEXT NOT NULL,
    embedding_model TEXT NOT NULL DEFAULT 'bge-small-zh-v1.5',
    chunk_size INTEGER NOT NULL DEFAULT 1000,
    overlap INTEGER NOT NULL DEFAULT 50,
    created_at REAL NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    summary_enabled INTEGER NOT NULL DEFAULT 0,
    summary_model TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS doc(
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL,
    name TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '解析中',
    chunks INTEGER NOT NULL DEFAULT 0,
    chunk_ids TEXT NOT NULL DEFAULT '{}',
    preview TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""



def _ensure_kb_columns(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(kb)").fetchall()}
    if "description" not in cols:
        conn.execute("ALTER TABLE kb ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    if "summary_enabled" not in cols:
        conn.execute("ALTER TABLE kb ADD COLUMN summary_enabled INTEGER NOT NULL DEFAULT 0")
    if "summary_model" not in cols:
        conn.execute("ALTER TABLE kb ADD COLUMN summary_model TEXT NOT NULL DEFAULT ''")
    conn.commit()


def _conn() -> sqlite3.Connection:
    _META_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_META_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    _ensure_kb_columns(conn)            
    business_store.ensure_schema(conn)      
    row = conn.execute("SELECT id FROM kb WHERE id=?", (DEFAULT_KB_ID,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO kb(id,name,collection_prefix,created_at) VALUES(?,?,?,?)",
            (DEFAULT_KB_ID, "默认知识库", DEFAULT_PREFIX, time.time()))
        conn.commit()
    return conn


def _kb_row(kb_id: str) -> sqlite3.Row:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM kb WHERE id=?", (kb_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "kb_not_found", "hint": f"知识库不存在: {kb_id}"})
    return row


def _visible_kb_row(kb_id: str, user: AuthUser) -> sqlite3.Row:
    row = _kb_row(kb_id)
    if not kb_owner.can_read(kb_id, user.tenant_id, user.user_id, user.is_kb_admin):
        raise HTTPException(status_code=404,
                            detail={"error": "kb_not_found", "hint": f"知识库不存在: {kb_id}"})
    return row


def _writable_kb_row(kb_id: str, user: AuthUser) -> sqlite3.Row:
    row = _visible_kb_row(kb_id, user)
    if not kb_owner.can_write(kb_id, user.tenant_id, user.user_id, user.is_kb_admin):
        raise HTTPException(status_code=403,
                            detail={"error": "kb_readonly",
                                    "hint": "该知识库为只读，写权限需管理员分配"})
    return row


def _deletable_kb_row(kb_id: str, user: AuthUser) -> sqlite3.Row:
    row = _visible_kb_row(kb_id, user)
    if not kb_owner.can_delete(kb_id, user.tenant_id, user.user_id, user.is_kb_admin):
        raise HTTPException(status_code=403,
                            detail={"error": "kb_delete_forbidden",
                                    "hint": "仅创建者或管理员可删除该知识库"})
    return row


def _admin_kb_row(kb_id: str, user: AuthUser) -> sqlite3.Row:
    if kb_id == DEFAULT_KB_ID:
        raise HTTPException(status_code=400,
                            detail={"error": "protected_kb",
                                    "hint": "默认知识库全员只读，无需/不能分配"})
    row = _kb_row(kb_id)
    if not kb_owner.is_owned(kb_id, user.tenant_id):
        raise HTTPException(status_code=404,
                            detail={"error": "kb_not_found", "hint": f"知识库不存在: {kb_id}"})
    return row


def _store_for_prefix(prefix: str) -> VectorStore:
    return VectorStore(collection_name=f"{prefix}_flat", enable_hierarchical=True,
                       hierarchical_prefix=prefix)


def _update_doc(doc_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = _conn()
    try:
        conn.execute(f"UPDATE doc SET {sets} WHERE id=?", (*fields.values(), doc_id))
        conn.commit()
    finally:
        conn.close()


def _doc_dict(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "kb_id": row["kb_id"], "name": row["name"],
            "size": row["size"], "status": row["status"], "chunks": row["chunks"],
            "preview": row["preview"], "error": row["error"],
            "created_at": row["created_at"]}



class KbIn(BaseModel):
    name: str = ""
    description: str = ""
    summary_enabled: bool = False
    summary_model: str = ""


class RenameIn(BaseModel):
    name: str


class KbDescriptionIn(BaseModel):
    description: str = ""


@router.get("/kbs")
def list_kbs(user: AuthUser = Depends(require_user)) -> dict:
    visible = kb_owner.visible_ids(user.tenant_id, user.user_id, user.is_kb_admin)
    placeholders = ",".join("?" for _ in visible)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT k.*, (SELECT COUNT(*) FROM doc d WHERE d.kb_id=k.id) AS doc_count"
            " FROM kb k WHERE k.id IN (" + placeholders + ")"
            " ORDER BY k.created_at ASC", tuple(visible)).fetchall()
    finally:
        conn.close()
    return {"kbs": [{"id": r["id"], "name": r["name"], "doc_count": r["doc_count"],
                     "description": (r["description"] if "description" in r.keys() else ""),
                     "summary_enabled": (1 if r["summary_enabled"] else 0)
                                         if "summary_enabled" in r.keys() else 0,
                     "summary_model": (r["summary_model"] or "")
                                       if "summary_model" in r.keys() else "",
                     "embedding_model": r["embedding_model"],
                     "chunk_size": r["chunk_size"], "overlap": r["overlap"],
                     "created_at": r["created_at"],
                     "can_write": kb_owner.can_write(r["id"], user.tenant_id,
                                                     user.user_id, user.is_kb_admin),
                     "can_delete": kb_owner.can_delete(r["id"], user.tenant_id,
                                                       user.user_id, user.is_kb_admin),
                     "can_manage_grants": (r["id"] != DEFAULT_KB_ID
                                           and user.is_kb_admin
                                           and kb_owner.is_owned(r["id"], user.tenant_id))}
                    for r in rows]}


@router.post("/kbs")
def create_kb(req: KbIn, user: AuthUser = Depends(require_user)) -> dict:
    kb_id = "kb_" + uuid.uuid4().hex[:8]
    name = (req.name or "").strip() or "未命名知识库"
    prefix = kb_id                  
    description = (req.description or "").strip()[:500]
    summary_enabled = 1 if req.summary_enabled else 0
    summary_model = (req.summary_model or "").strip() if summary_enabled else ""
    if summary_enabled:
        try:
            provider_id, model = summary_model.split(":", 1)
            int(provider_id)
        except ValueError:
            provider_id, model = "", ""
        if not provider_id or not model.strip():
            raise HTTPException(status_code=400,
                                detail={"error": "invalid_summary_model",
                                        "hint": "开启文档摘要时必须选择一个已配置的模型"})
        summary_model = f"{int(provider_id)}:{model.strip()}"
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO kb(id,name,collection_prefix,created_at,description,"
            "summary_enabled,summary_model) VALUES(?,?,?,?,?,?,?)",
            (kb_id, name, prefix, time.time(), description,
             summary_enabled, summary_model))
        conn.commit()
    finally:
        conn.close()
    kb_owner.register(kb_id, user.tenant_id, user.user_id)   
    return {"id": kb_id, "name": name, "doc_count": 0, "collection_prefix": prefix,
            "description": description, "summary_enabled": summary_enabled,
            "summary_model": summary_model}


@router.patch("/kbs/{kb_id}")
def rename_kb(kb_id: str, req: RenameIn, user: AuthUser = Depends(require_user)) -> dict:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail={"error": "empty_name", "hint": "名称不能为空"})
    _writable_kb_row(kb_id, user)
    conn = _conn()
    try:
        conn.execute("UPDATE kb SET name=? WHERE id=?", (name, kb_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": kb_id, "name": name}


@router.patch("/kbs/{kb_id}/description")
def set_kb_description(kb_id: str, req: KbDescriptionIn,
                       user: AuthUser = Depends(require_user)) -> dict:
    _writable_kb_row(kb_id, user)
    description = (req.description or "").strip()[:500]
    conn = _conn()
    try:
        conn.execute("UPDATE kb SET description=? WHERE id=?", (description, kb_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": kb_id, "description": description}


@router.delete("/kbs/{kb_id}")
def delete_kb(kb_id: str, user: AuthUser = Depends(require_user)) -> dict:
    if kb_id == DEFAULT_KB_ID:
        raise HTTPException(status_code=400,
                            detail={"error": "protected_kb", "hint": "默认知识库与聊天共用，不能删除"})
    row = _deletable_kb_row(kb_id, user)
    prefix = row["collection_prefix"]
    for suffix in ("_parent_chunks", "_child_chunks", "_summaries", "_flat"):
        try:
            vector_factory.delete_collection(f"{prefix}{suffix}", path=DEFAULT_DIR)
        except Exception:       # noqa: BLE001 — 集合不存在即视为已删除
            pass
    conn = _conn()
    try:
        conn.execute("DELETE FROM doc WHERE kb_id=?", (kb_id,))
        conn.execute("DELETE FROM kb WHERE id=?", (kb_id,))
        conn.commit()
    finally:
        conn.close()
    kb_owner.unregister(kb_id, user.tenant_id)
    kb_owner.clear_grants(kb_id)
    return {"ok": True}



class GrantIn(BaseModel):
    user_id: int
    perm: str = kb_owner.PERM_READ


@router.get("/members")
def list_members(user: AuthUser = Depends(require_kb_admin)) -> dict:
    conn = get_engine().connect()
    try:
        rows = conn.execute(
            select(app_user.c.id, app_user.c.username, app_user.c.email, app_user.c.role)
            .where(app_user.c.tenant_id == user.tenant_id, app_user.c.status == 1)
            .order_by(app_user.c.id.asc())).all()
    finally:
        conn.close()
    return {"users": [{"id": r.id, "username": r.username,
                       "email": r.email, "role": r.role} for r in rows]}


@router.get("/kbs/{kb_id}/grants")
def list_kb_grants(kb_id: str, user: AuthUser = Depends(require_kb_admin)) -> dict:
    _admin_kb_row(kb_id, user)
    return {"kb_id": kb_id, "creator": kb_owner.creator_of(kb_id, user.tenant_id),
            "grants": kb_owner.list_grants(kb_id, user.tenant_id)}


@router.put("/kbs/{kb_id}/grants")
def put_kb_grant(kb_id: str, req: GrantIn,
                 user: AuthUser = Depends(require_kb_admin)) -> dict:
    _admin_kb_row(kb_id, user)
    perm = (req.perm or "").strip().lower()
    if perm not in kb_owner.PERMS:
        raise HTTPException(status_code=400,
                            detail={"error": "bad_perm", "hint": "perm 只能是 read 或 write"})
    conn = get_engine().connect()
    try:
        target = conn.execute(select(app_user.c.id).where(
            app_user.c.id == req.user_id,
            app_user.c.tenant_id == user.tenant_id)).first()
    finally:
        conn.close()
    if target is None:
        raise HTTPException(status_code=404,
                            detail={"error": "user_not_found", "hint": "该用户不属于本租户"})
    kb_owner.set_grant(kb_id, user.tenant_id, req.user_id, perm, user.user_id)
    return {"ok": True, "kb_id": kb_id, "grants": kb_owner.list_grants(kb_id, user.tenant_id)}


@router.delete("/kbs/{kb_id}/grants/{user_id}")
def delete_kb_grant(kb_id: str, user_id: int,
                    user: AuthUser = Depends(require_kb_admin)) -> dict:
    _admin_kb_row(kb_id, user)
    kb_owner.revoke_grant(kb_id, user.tenant_id, user_id)
    return {"ok": True, "kb_id": kb_id, "grants": kb_owner.list_grants(kb_id, user.tenant_id)}



@router.get("/kbs/{kb_id}/docs")
def list_docs(kb_id: str, user: AuthUser = Depends(require_user)) -> dict:
    _visible_kb_row(kb_id, user)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM doc WHERE kb_id=? ORDER BY created_at DESC", (kb_id,)).fetchall()
    finally:
        conn.close()
    return {"docs": [_doc_dict(r) for r in rows]}


@router.post("/kbs/{kb_id}/docs")
async def upload_doc(kb_id: str, file: UploadFile = File(...),
                     user: AuthUser = Depends(require_user)) -> dict:
    row = _writable_kb_row(kb_id, user)
    name = file.filename or "unnamed"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail={"error": "empty_file", "hint": "文件为空"})

    doc_id = uuid.uuid4().hex
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO doc(id,kb_id,name,size,status,created_at) VALUES(?,?,?,?,?,?)",
            (doc_id, kb_id, name, len(data), "解析中", time.time()))
        conn.commit()
    finally:
        conn.close()

    task = asyncio.create_task(_process_doc(
        kb_id, row["collection_prefix"], doc_id, name, data,
        bool(row["summary_enabled"]), (row["summary_model"] or ""),
        int(user.tenant_id)))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"id": doc_id, "name": name, "size": len(data), "status": "解析中", "chunks": 0}


@router.delete("/kbs/{kb_id}/docs/{doc_id}")
def delete_doc(kb_id: str, doc_id: str, user: AuthUser = Depends(require_user)) -> dict:
    kb = _writable_kb_row(kb_id, user)
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM doc WHERE id=? AND kb_id=?", (doc_id, kb_id)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "doc_not_found", "hint": "文档不存在"})

    ids = json.loads(row["chunk_ids"] or "{}")
    if ids:
        store = _store_for_prefix(kb["collection_prefix"])
        try:
            for col, key in ((store.parent_collection, "parents"),
                             (store.child_collection, "children"),
                             (store.summary_collection, "summaries")):
                chunk_ids = ids.get(key) or []
                if chunk_ids and col is not None:
                    col.delete(ids=chunk_ids)
        finally:
            store.close()

    conn = _conn()
    try:
        conn.execute("DELETE FROM doc WHERE id=?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.get("/kbs/{kb_id}/docs/{doc_id}/chunks")
def doc_chunks(kb_id: str, doc_id: str, limit: int = 5,
               user: AuthUser = Depends(require_user)) -> dict:
    kb = _visible_kb_row(kb_id, user)
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM doc WHERE id=? AND kb_id=?", (doc_id, kb_id)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "doc_not_found", "hint": "文档不存在"})
    parent_ids = (json.loads(row["chunk_ids"] or "{}").get("parents") or [])[:max(1, limit)]
    if not parent_ids:
        return {"chunks": []}
    store = _store_for_prefix(kb["collection_prefix"])
    try:
        hits = store.get_parent_by_ids(parent_ids)
    finally:
        store.close()
    return {"chunks": [{"chunk_id": pid, "content": hits[pid]["content"],
                        "source": hits[pid]["source"]}
                       for pid in parent_ids if pid in hits]}



class SearchIn(BaseModel):
    query: str
    top_k: int = 5
    threshold: float = 0.0


@router.post("/kbs/{kb_id}/search")
def search(kb_id: str, req: SearchIn, user: AuthUser = Depends(require_user)) -> dict:
    kb = _visible_kb_row(kb_id, user)
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail={"error": "empty_query", "hint": "查询不能为空"})
    store = _store_for_prefix(kb["collection_prefix"])
    try:
        retriever = HierarchicalRetriever(store)
        hits = retriever.retrieve(query, n=max(1, min(req.top_k, 20)))
    finally:
        store.close()
    results = [{"id": h["chunk_id"], "content": h.get("content", ""),
                "score": round(float(h.get("score", 0.0)), 4),
                "metadata": {"source": h.get("source", "unknown"),
                             "hit_type": h.get("hit_type", ""),
                             "match_type": h.get("match_type", "")}}
               for h in hits if float(h.get("score", 0.0)) >= req.threshold]
    return {"results": results}



_TASKS: set[asyncio.Task] = set()


def _build_and_add(prefix: str, documents: list[dict], summary_enabled: bool,
                   summary_model: str, tenant_id: int) -> tuple:
    hc = build_hierarchical_chunks(
        documents,
        generate_summary=summary_enabled,
        summary_model=summary_model,
        tenant_id=tenant_id,
    )
    store = _store_for_prefix(prefix)
    try:
        counts = store.add_hierarchical(
            parent_ids=hc.parent_ids, parent_chunks=hc.parent_chunks,
            parent_metas=hc.parent_metas, child_ids=hc.child_ids,
            child_chunks=hc.child_chunks, child_metas=hc.child_metas,
            summary_ids=hc.summary_ids, summary_chunks=hc.summary_chunks,
            summary_metas=hc.summary_metas)
    finally:
        store.close()
    return hc, counts


async def _process_doc(kb_id: str, prefix: str, doc_id: str, name: str, data: bytes,
                       summary_enabled: bool, summary_model: str, tenant_id: int) -> None:
    try:
        results = await parse_files([(name, data)])
        result = results[0]
        if result.get("status") != "success":
            err = result.get("error") or {}
            raise RuntimeError(err.get("message") or f"解析失败: {name}")
        markdown = (result.get("markdown") or "").strip()
        if not markdown:
            raise RuntimeError("解析结果为空")

        _update_doc(doc_id, status="索引中")
        documents = [{"content": markdown, "source": name,
                      "type": Path(name).suffix.lstrip(".").lower() or "txt"}]
        hc, counts = await asyncio.to_thread(
            _build_and_add, prefix, documents, summary_enabled, summary_model, tenant_id)

        chunk_ids = {"parents": hc.parent_ids, "children": hc.child_ids,
                     "summaries": hc.summary_ids}
        _update_doc(doc_id, status="已索引", chunks=counts["parents"],
                    chunk_ids=json.dumps(chunk_ids, ensure_ascii=False),
                    preview=(hc.parent_chunks[0][:300] if hc.parent_chunks else ""))
    except DocParserError as e:
        _update_doc(doc_id, status="失败", error=f"[{e.code}] {e.message}")
    except Exception as e:      # noqa: BLE001 — 后台任务必须落状态，不能静默
        _update_doc(doc_id, status="失败", error=str(e))



class BusinessTermIn(BaseModel):
    term: str
    description: str = ""
    synonyms: str = ""
    formula: str = ""
    related_tables: str = ""
    unit: str = ""
    is_recall: bool = True
    is_public: bool = False


class BusinessRecallIn(BaseModel):
    is_recall: bool


@router.get("/business/terms")
def list_business_terms(keyword: str = "",
                        user: AuthUser = Depends(require_user)) -> dict:
    conn = _conn()
    try:
        terms = business_store.list_terms(conn, user.tenant_id, keyword, user.user_id)
    finally:
        conn.close()
    for t in terms:
        t["can_edit"] = _can_edit(t, user)
    return {"terms": terms}


@router.post("/business/terms")
def create_business_term(req: BusinessTermIn,
                         user: AuthUser = Depends(require_user)) -> dict:
    term = (req.term or "").strip()
    if not term:
        raise HTTPException(status_code=400,
                            detail={"error": "invalid_term", "hint": "业务名词不能为空"})
    if len(term) > 128:
        raise HTTPException(status_code=400,
                            detail={"error": "invalid_term", "hint": "业务名词最长 128 字"})
    if req.is_public and not user.is_db_admin:
        raise HTTPException(status_code=403, detail={
            "error": "forbidden", "hint": "只有管理员可以创建租户公共术语",
        })
    conn = _conn()
    try:
        term_id = business_store.insert_term(
            conn, user.tenant_id, term=term,
            description=(req.description or "").strip(),
            synonyms=(req.synonyms or "").strip(),
            formula=(req.formula or "").strip(),
            related_tables=(req.related_tables or "").strip(),
            unit=(req.unit or "").strip(), is_recall=req.is_recall,
            user_id=0 if req.is_public else user.user_id)
        sync = business_store.sync(conn, user.tenant_id)
        row = _biz_row_for_user(conn, term_id, user)
    finally:
        conn.close()
    return {"term": row, "sync": sync}


@router.patch("/business/terms/{term_id}")
def update_business_term(term_id: int, req: BusinessTermIn,
                         user: AuthUser = Depends(require_user)) -> dict:
    term = (req.term or "").strip()
    if not term:
        raise HTTPException(status_code=400,
                            detail={"error": "invalid_term", "hint": "业务名词不能为空"})
    conn = _conn()
    try:
        _editable_row(conn, term_id, user)
        conn.execute(
            "UPDATE business_term SET term=?, description=?, synonyms=?, formula=?,"
            " related_tables=?, unit=?, is_recall=?, embedding_status='PENDING',"
            " error_msg='', updated_at=? WHERE id=? AND tenant_id=?",
            (term, (req.description or "").strip(), (req.synonyms or "").strip(),
             (req.formula or "").strip(), (req.related_tables or "").strip(),
             (req.unit or "").strip(), 1 if req.is_recall else 0,
             time.time(), term_id, user.tenant_id))
        conn.commit()
        sync = business_store.sync(conn, user.tenant_id)
        row = _biz_row_for_user(conn, term_id, user)
    finally:
        conn.close()
    return {"term": row, "sync": sync}


@router.post("/business/terms/{term_id}/recall")
def set_business_recall(term_id: int, req: BusinessRecallIn,
                        user: AuthUser = Depends(require_user)) -> dict:
    conn = _conn()
    try:
        _editable_row(conn, term_id, user)
        conn.execute("UPDATE business_term SET is_recall=?, embedding_status='PENDING',"
                     " error_msg='', updated_at=? WHERE id=? AND tenant_id=?",
                     (1 if req.is_recall else 0, time.time(), term_id, user.tenant_id))
        conn.commit()
        sync = business_store.sync(conn, user.tenant_id)
        row = _biz_row_for_user(conn, term_id, user)
    finally:
        conn.close()
    return {"term": row, "sync": sync}


@router.delete("/business/terms/{term_id}")
def delete_business_term(term_id: int,
                         user: AuthUser = Depends(require_user)) -> dict:
    conn = _conn()
    try:
        _editable_row(conn, term_id, user)
        conn.execute("DELETE FROM business_term WHERE id=? AND tenant_id=?",
                     (term_id, user.tenant_id))
        conn.commit()
        sync = business_store.sync(conn, user.tenant_id)
    finally:
        conn.close()
    return {"ok": True, "sync": sync}


@router.post("/business/sync")
def sync_business_terms(user: AuthUser = Depends(require_db_admin)) -> dict:
    conn = _conn()
    try:
        return business_store.sync(conn, user.tenant_id)
    finally:
        conn.close()


def _can_edit(term: dict, user: AuthUser) -> bool:
    owner = int(term.get("user_id") or 0)
    return owner == user.user_id or (owner == 0 and user.is_db_admin)


def _editable_row(conn: sqlite3.Connection, term_id: int,
                  user: AuthUser) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM business_term WHERE id=? AND tenant_id=?",
                       (term_id, user.tenant_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404,
                            detail={"error": "term_not_found", "hint": "业务术语不存在"})
    if not _can_edit(business_store.term_dict(row), user):
        raise HTTPException(status_code=403, detail={
            "error": "forbidden", "hint": "只能修改自己的术语；公共术语由管理员维护",
        })
    return row


def _biz_row_for_user(conn: sqlite3.Connection, term_id: int,
                      user: AuthUser) -> dict:
    row = conn.execute("SELECT * FROM business_term WHERE id=? AND tenant_id=?",
                       (term_id, user.tenant_id)).fetchone()
    term = business_store.term_dict(row)
    term["can_edit"] = _can_edit(term, user)
    return term


app.include_router(router)


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run("kb_service.api.main:app",
                host=os.getenv("KB_HTTP_HOST", "127.0.0.1"),
                port=int(os.getenv("KB_HTTP_PORT", "8003")),
                workers=1)
