from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from typing import Optional

import zstandard as zstd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, insert, select, update

from allfusion_auth.db import get_engine
from allfusion_auth.deps import AuthUser, require_user
from allfusion_auth.models import chat_session, thread_message, thread_registry
from unified_agent.graph import checkpoint

router = APIRouter(prefix="/sessions", tags=["sessions"])

_SESSION_STATUS_ACTIVE = 0
_SESSION_STATUS_DELETED = 2
_PREVIEW_SESSION = 200
_PREVIEW_MESSAGE = 512
_TOKENS_PER_CHAR = 0.75                 

_zc = zstd.ZstdCompressor(level=3)
_zd = zstd.ZstdDecompressor()

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id() -> str:
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ts << 80) | rand
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _pack(text: str) -> dict:
    raw = (text or "").encode("utf-8")
    compressed = _zc.compress(raw) if raw else b""
    return {
        "content_zstd": compressed or None,
        "content_encoding": 1,
        "content_bytes": len(compressed),
        "content_sha256": hashlib.sha256(raw).hexdigest() if raw else "",
        "content_preview": _one_line(text)[:_PREVIEW_MESSAGE],
    }


def _unpack(row) -> str:
    if row.content_encoding != 1 or not row.content_zstd:
        return ""
    try:
        return _zd.decompress(bytes(row.content_zstd)).decode("utf-8")
    except zstd.ZstdError:
        return ""


def _dt(ts: datetime | None) -> float | None:
    return ts.timestamp() if ts else None



def _session_row(conn, thread_id: str, user: AuthUser) -> Optional[dict]:
    row = conn.execute(
        select(chat_session).where(and_(
            chat_session.c.thread_id == thread_id,
            chat_session.c.tenant_id == user.tenant_id,
            chat_session.c.user_id == user.user_id,
            chat_session.c.status != _SESSION_STATUS_DELETED,
        ))
    ).first()
    return dict(row._mapping) if row else None


def assert_owner(thread_id: str, user: AuthUser) -> dict:
    with get_engine().connect() as conn:
        row = _session_row(conn, thread_id, user)
    if row is None:
        raise HTTPException(status_code=404, detail={
            "error": "session_not_found", "hint": "会话不存在",
        })
    return row


def _bump_session(conn, session_id: str, *, seq: int, preview: str | None,
                  title: str | None = None) -> None:
    now = datetime.now()
    values = {
        "message_count": seq,
        "last_msg_at": now,
        "updated_at": now,
    }
    if preview is not None:
        values["last_preview"] = _one_line(preview)[:_PREVIEW_SESSION]
    if title:
        values["title"] = title
    conn.execute(update(chat_session).where(chat_session.c.id == session_id).values(**values))


def _title_from(message: str) -> str:
    text = _one_line(message)
    if not text:
        return "新对话"
    return text[:20] + ("…" if len(text) > 20 else "")


def set_registry(thread_id: str, status: str) -> None:
    now = datetime.now()
    with get_engine().begin() as conn:
        conn.execute(update(thread_registry)
                     .where(thread_registry.c.thread_id == thread_id)
                     .values(status=status, last_active_at=now))



def begin_turn(user: AuthUser, thread_id: str, message: str) -> dict:
    now = datetime.now()
    request_id = new_id()
    with get_engine().begin() as conn:
        session = _session_row(conn, thread_id, user)
        if session is None:
            raise HTTPException(status_code=404, detail={
                "error": "session_not_found", "hint": "会话不存在",
            })
        seq = int(session["message_count"]) + 1
        user_msg_id = new_id()
        conn.execute(insert(thread_message).values(
            id=user_msg_id, tenant_id=user.tenant_id, user_id=user.user_id,
            session_id=session["id"], thread_id=thread_id, seq=seq,
            role="user", status="complete", request_id=request_id,
            token_count=int(len(message or "") * _TOKENS_PER_CHAR),
            created_at=now, **_pack(message)))
        title = None
        if session["title"] in ("", "新对话") and (message or "").strip():
            title = _title_from(message)
        _bump_session(conn, session["id"], seq=seq, preview=message, title=title)
        conn.execute(update(thread_registry)
                     .where(thread_registry.c.thread_id == thread_id)
                     .values(status="running", last_active_at=now))
    return {"session_id": session["id"], "request_id": request_id,
            "thread_id": thread_id, "_user": user,
            "user_message_id": user_msg_id, "user_seq": seq}


def start_assistant(turn: dict, user: AuthUser | None = None, *, role: str = "assistant",
                    model: str | None = None) -> str:
    user = user or turn["_user"]
    msg_id = new_id()
    seq = turn["user_seq"] + 1
    with get_engine().begin() as conn:
        conn.execute(insert(thread_message).values(
            id=msg_id, tenant_id=user.tenant_id, user_id=user.user_id,
            session_id=turn["session_id"], thread_id=turn["thread_id"],
            seq=seq, role=role, status="streaming",
            request_id=new_id(), model=model,
            token_count=0, created_at=datetime.now(),
            **_pack("")))
        _bump_session(conn, turn["session_id"], seq=seq, preview=None)
    turn["assistant_id"] = msg_id
    turn["assistant_seq"] = seq
    return msg_id


def update_assistant(msg_id: str, text: str, *,
                     status: str | None = None,
                     metadata: dict | None = None,
                     model: str | None = None,
                     trace_id: str | None = None) -> None:
    values: dict = {**_pack(text)}
    if status:
        values["status"] = status
    if metadata is not None:
        values["metadata_json"] = json.dumps(metadata, ensure_ascii=False)
    if model:
        values["model"] = model
    if trace_id:
        values["trace_id"] = trace_id
    values["token_count"] = int(len(text or "") * _TOKENS_PER_CHAR)
    with get_engine().begin() as conn:
        conn.execute(update(thread_message)
                     .where(thread_message.c.id == msg_id).values(**values))


def finish_turn(turn: dict, answer: str, *,
                status: str = "complete",
                metadata: dict | None = None,
                model: str | None = None,
                trace_id: str | None = None) -> str:
    msg_id = start_assistant(turn, turn["_user"], model=model)
    finalize_assistant(turn, answer, status=status, metadata=metadata,
                       model=model, trace_id=trace_id)
    return msg_id


def finalize_assistant(turn: dict, answer: str, *, status: str = "complete",
                       metadata: dict | None = None,
                       model: str | None = None,
                       trace_id: str | None = None) -> None:
    msg_id = turn["assistant_id"]
    update_assistant(msg_id, answer, status=status, metadata=metadata,
                     model=model, trace_id=trace_id)
    with get_engine().begin() as conn:
        _bump_session(conn, turn["session_id"], seq=turn["assistant_seq"],
                      preview=answer or None)
    set_registry(turn["thread_id"],
                 "done" if status == "complete" else status)


def resume_turn(user: AuthUser, thread_id: str) -> dict:
    session = assert_owner(thread_id, user)
    row = latest_interrupted(session["id"])
    if row is not None:
        set_message_status(row["id"], "streaming")
        return {"session_id": session["id"], "thread_id": thread_id,
                "request_id": row["request_id"], "assistant_id": row["id"],
                "assistant_seq": int(row["seq"]), "user_seq": int(row["seq"]) - 1,
                "_user": user}
    turn = {"session_id": session["id"], "thread_id": thread_id,
            "request_id": new_id(), "user_seq": int(session["message_count"]),
            "_user": user}
    start_assistant(turn, user)
    return turn


def latest_interrupted(session_id: str) -> Optional[dict]:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(thread_message.c.id, thread_message.c.seq,
                   thread_message.c.thread_id, thread_message.c.request_id)
            .where(and_(thread_message.c.session_id == session_id,
                        thread_message.c.status == "interrupted"))
            .order_by(thread_message.c.seq.desc()).limit(1)
        ).first()
    return dict(row._mapping) if row else None


def set_message_status(msg_id: str, status: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(thread_message)
                     .where(thread_message.c.id == msg_id).values(status=status))


def mark_stale_streaming() -> int:
    with get_engine().begin() as conn:
        result = conn.execute(update(thread_message)
                              .where(thread_message.c.status == "streaming")
                              .values(status="error",
                                      metadata_json=json.dumps(
                                          {"error": "stream_interrupted"},
                                          ensure_ascii=False)))
    return int(result.rowcount or 0)



def get_message(session_id: str, message_id: str, tenant_id: int) -> Optional[dict]:
    with get_engine().connect() as conn:
        row = conn.execute(select(thread_message).where(and_(
            thread_message.c.id == message_id,
            thread_message.c.session_id == session_id,
            thread_message.c.tenant_id == tenant_id,
        ))).first()
    return dict(row._mapping) if row else None


def delete_message(session_id: str, message_id: str) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(delete(thread_message).where(and_(
            thread_message.c.id == message_id,
            thread_message.c.session_id == session_id)))
    return int(result.rowcount or 0)


def remaining_history(session_id: str, tenant_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(thread_message.c.role, thread_message.c.content_zstd,
                   thread_message.c.content_encoding)
            .where(and_(thread_message.c.session_id == session_id,
                        thread_message.c.tenant_id == tenant_id,
                        thread_message.c.role.in_(("user", "assistant"))))
            .order_by(thread_message.c.seq.asc())
        ).fetchall()
    out = []
    for row in rows:
        text = _unpack(row)
        if text.strip():
            out.append({"role": row.role, "content": text})
    return out


def refresh_preview(session_id: str) -> None:
    now = datetime.now()
    with get_engine().begin() as conn:
        last = conn.execute(
            select(thread_message.c.content_preview, thread_message.c.created_at)
            .where(thread_message.c.session_id == session_id)
            .order_by(thread_message.c.seq.desc()).limit(1)
        ).first()
        conn.execute(update(chat_session)
                     .where(chat_session.c.id == session_id)
                     .values(last_preview=(last.content_preview or "")[:_PREVIEW_SESSION]
                             if last else "",
                             last_msg_at=last.created_at if last else None,
                             updated_at=now))



class PatchIn(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None


@router.post("")
def create_session(user: AuthUser = Depends(require_user)) -> dict:
    thread_id = f"t{user.tenant_id}-u{user.user_id}-{uuid.uuid4().hex}"
    session_id = new_id()
    now = datetime.now()
    with get_engine().begin() as conn:
        conn.execute(insert(chat_session).values(
            id=session_id, tenant_id=user.tenant_id, user_id=user.user_id,
            thread_id=thread_id, title="新对话", pinned=0,
            status=_SESSION_STATUS_ACTIVE, message_count=0, last_preview="",
            created_at=now, updated_at=now))
        conn.execute(insert(thread_registry).values(
            thread_id=thread_id, tenant_id=user.tenant_id, user_id=user.user_id,
            session_id=session_id, status="idle", last_active_at=now))
    return {"thread_id": thread_id, "session_id": session_id}


@router.get("")
def list_sessions(limit: int = 50, updated_before: float | None = None,
                  user: AuthUser = Depends(require_user)) -> dict:
    limit = max(1, min(int(limit), 100))
    stmt = select(
        chat_session.c.thread_id, chat_session.c.title, chat_session.c.pinned,
        chat_session.c.message_count, chat_session.c.last_preview,
        chat_session.c.last_msg_at, chat_session.c.created_at,
        chat_session.c.updated_at,
    ).where(and_(
        chat_session.c.tenant_id == user.tenant_id,
        chat_session.c.user_id == user.user_id,
        chat_session.c.status == _SESSION_STATUS_ACTIVE,
    ))
    if updated_before:
        stmt = stmt.where(chat_session.c.updated_at < datetime.fromtimestamp(updated_before))
    stmt = stmt.order_by(chat_session.c.pinned.desc(),
                         func.coalesce(chat_session.c.last_msg_at,
                                       chat_session.c.updated_at).desc(),
                         chat_session.c.updated_at.desc()).limit(limit + 1)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    sessions = [{
        "thread_id": r.thread_id, "title": r.title, "pinned": bool(r.pinned),
        "message_count": int(r.message_count or 0),
        "last_preview": r.last_preview or "",
        "last_msg_at": _dt(r.last_msg_at),
        "created_at": _dt(r.created_at), "updated_at": _dt(r.updated_at),
    } for r in rows]
    return {"sessions": sessions, "has_more": has_more}


@router.patch("/{thread_id}")
def patch_session(thread_id: str, req: PatchIn,
                  user: AuthUser = Depends(require_user)) -> dict:
    session = assert_owner(thread_id, user)
    values: dict = {"updated_at": datetime.now()}
    if req.title is not None:
        values["title"] = (req.title.strip() or "新对话")[:128]
    if req.pinned is not None:
        values["pinned"] = 1 if req.pinned else 0
    with get_engine().begin() as conn:
        conn.execute(update(chat_session)
                     .where(chat_session.c.id == session["id"]).values(**values))
    return {"ok": True}


@router.get("/{thread_id}/messages")
def list_messages(thread_id: str, before_seq: int | None = None, limit: int = 50,
                  user: AuthUser = Depends(require_user)) -> dict:
    session = assert_owner(thread_id, user)
    limit = max(1, min(int(limit), 200))
    stmt = select(thread_message).where(and_(
        thread_message.c.tenant_id == user.tenant_id,
        thread_message.c.session_id == session["id"],
    ))
    if before_seq is not None:
        stmt = stmt.where(thread_message.c.seq < int(before_seq))
    stmt = stmt.order_by(thread_message.c.seq.desc()).limit(limit + 1)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    has_more = len(rows) > limit
    rows = list(reversed(rows[:limit]))
    messages = []
    for r in rows:
        metadata = None
        if r.metadata_json:
            try:
                metadata = json.loads(r.metadata_json)
            except (ValueError, TypeError):
                metadata = None
        messages.append({
            "id": r.id, "role": r.role, "status": r.status, "seq": int(r.seq),
            "content": _unpack(r),
            "metadata": metadata,
            "model": r.model, "created_at": _dt(r.created_at),
            "request_id": r.request_id,
        })
    return {
        "thread_id": thread_id,
        "messages": messages,
        "has_more": has_more,
        "next_before_seq": messages[0]["seq"] if (has_more and messages) else None,
    }


@router.delete("/{thread_id}")
def delete_session(thread_id: str, user: AuthUser = Depends(require_user)) -> dict:
    session = assert_owner(thread_id, user)
    try:
        checkpoint.get_checkpointer().delete_thread(thread_id)
    except Exception:       
        pass
    with get_engine().begin() as conn:
        conn.execute(update(chat_session)
                     .where(chat_session.c.id == session["id"])
                     .values(status=_SESSION_STATUS_DELETED,
                             updated_at=datetime.now()))
        conn.execute(delete(thread_message)
                     .where(thread_message.c.session_id == session["id"]))
        conn.execute(delete(thread_registry)
                     .where(thread_registry.c.thread_id == thread_id))
    return {"ok": True}
