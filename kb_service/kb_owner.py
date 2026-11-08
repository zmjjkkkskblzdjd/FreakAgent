from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from allfusion_auth.db import get_engine
from allfusion_auth.models import kb_grant, kb_owner

log = logging.getLogger(__name__)

DEFAULT_KB_ID = "default"
PERM_READ = "read"
PERM_WRITE = "write"
PERMS = (PERM_READ, PERM_WRITE)



def owned_ids(tenant_id: int) -> set[str]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(kb_owner.c.kb_id).where(kb_owner.c.tenant_id == tenant_id)).all()
    return {r.kb_id for r in rows}


def created_ids(tenant_id: int, user_id: int) -> set[str]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(kb_owner.c.kb_id).where(
            kb_owner.c.tenant_id == tenant_id,
            kb_owner.c.user_id == user_id)).all()
    return {r.kb_id for r in rows}


def is_owned(kb_id: str, tenant_id: int) -> bool:
    if kb_id == DEFAULT_KB_ID:
        return True
    with get_engine().connect() as conn:
        row = conn.execute(select(kb_owner.c.kb_id).where(
            kb_owner.c.kb_id == kb_id,
            kb_owner.c.tenant_id == tenant_id)).first()
    return row is not None


def creator_of(kb_id: str, tenant_id: int) -> int | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(kb_owner.c.user_id).where(
            kb_owner.c.kb_id == kb_id,
            kb_owner.c.tenant_id == tenant_id)).first()
    return int(row.user_id) if row is not None else None


def register(kb_id: str, tenant_id: int, user_id: int) -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(kb_owner).values(
                kb_id=kb_id[:64], tenant_id=tenant_id, user_id=user_id,
                created_at=datetime.now()))
    except IntegrityError:
        pass


def unregister(kb_id: str, tenant_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(kb_owner).where(
            kb_owner.c.kb_id == kb_id,
            kb_owner.c.tenant_id == tenant_id))



def _grant_fallback(e: OperationalError) -> None:
    log.warning("kb_grant 不可用（请先执行 docs/mysql/005_kb_grant.sql）：%s", e)


def granted_ids(tenant_id: int, user_id: int) -> set[str]:
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(select(kb_grant.c.kb_id).where(
                kb_grant.c.tenant_id == tenant_id,
                kb_grant.c.user_id == user_id)).all()
        return {r.kb_id for r in rows}
    except OperationalError as e:
        _grant_fallback(e)
        return set()


def grant_perm(kb_id: str, tenant_id: int, user_id: int) -> str | None:
    try:
        with get_engine().connect() as conn:
            row = conn.execute(select(kb_grant.c.perm).where(
                kb_grant.c.kb_id == kb_id,
                kb_grant.c.tenant_id == tenant_id,
                kb_grant.c.user_id == user_id)).first()
        return str(row.perm) if row is not None else None
    except OperationalError as e:
        _grant_fallback(e)
        return None


def list_grants(kb_id: str, tenant_id: int) -> list[dict]:
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(select(
                kb_grant.c.user_id, kb_grant.c.perm,
                kb_grant.c.granted_by, kb_grant.c.created_at).where(
                kb_grant.c.kb_id == kb_id,
                kb_grant.c.tenant_id == tenant_id).order_by(
                kb_grant.c.user_id.asc())).all()
        return [{"user_id": int(r.user_id), "perm": r.perm,
                 "granted_by": int(r.granted_by),
                 "created_at": r.created_at.isoformat() if r.created_at else ""}
                for r in rows]
    except OperationalError as e:
        _grant_fallback(e)
        return []


def set_grant(kb_id: str, tenant_id: int, user_id: int, perm: str,
              granted_by: int) -> None:
    if perm not in PERMS:
        raise ValueError(f"perm 必须是 {PERMS}: {perm}")
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(kb_grant).values(
                kb_id=kb_id[:64], user_id=user_id, tenant_id=tenant_id,
                perm=perm, granted_by=granted_by, created_at=datetime.now()))
    except IntegrityError:
        with get_engine().begin() as conn:
            conn.execute(update(kb_grant).where(
                kb_grant.c.kb_id == kb_id,
                kb_grant.c.user_id == user_id).values(
                perm=perm, granted_by=granted_by, created_at=datetime.now()))


def revoke_grant(kb_id: str, tenant_id: int, user_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(kb_grant).where(
            kb_grant.c.kb_id == kb_id,
            kb_grant.c.tenant_id == tenant_id,
            kb_grant.c.user_id == user_id))


def clear_grants(kb_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(kb_grant).where(kb_grant.c.kb_id == kb_id))



def visible_ids(tenant_id: int, user_id: int, is_kb_admin: bool = False) -> set[str]:
    if is_kb_admin:
        ids = owned_ids(tenant_id)
    else:
        ids = created_ids(tenant_id, user_id) | granted_ids(tenant_id, user_id)
    return ids | {DEFAULT_KB_ID}


def can_read(kb_id: str, tenant_id: int, user_id: int, is_kb_admin: bool = False) -> bool:
    return kb_id == DEFAULT_KB_ID or kb_id in visible_ids(tenant_id, user_id, is_kb_admin)


def can_write(kb_id: str, tenant_id: int, user_id: int, is_kb_admin: bool = False) -> bool:
    if kb_id == DEFAULT_KB_ID:
        return bool(is_kb_admin)
    if is_kb_admin:
        return is_owned(kb_id, tenant_id)
    if creator_of(kb_id, tenant_id) == user_id:
        return True
    return grant_perm(kb_id, tenant_id, user_id) == PERM_WRITE


def can_delete(kb_id: str, tenant_id: int, user_id: int, is_kb_admin: bool = False) -> bool:
    if kb_id == DEFAULT_KB_ID:
        return False
    if is_kb_admin:
        return is_owned(kb_id, tenant_id)
    return creator_of(kb_id, tenant_id) == user_id


def filter_visible(kb_ids: list[str], tenant_id: int, user_id: int,
                   is_kb_admin: bool = False) -> tuple[list[str], list[str]]:
    visible = visible_ids(tenant_id, user_id, is_kb_admin)
    kept, dropped = [], []
    for kb_id in kb_ids:
        (kept if kb_id in visible else dropped).append(kb_id)
    return kept, dropped
