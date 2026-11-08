from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from sqlalchemy import select, update

from allfusion_auth.db import get_engine
from allfusion_auth.models import app_user, user_session
from allfusion_auth.security import hash_token

SESSION_COOKIE = os.getenv("AF_SESSION_COOKIE", "af_session")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SEEN_RENEW_SEC = 60

SCOPE_DB_ADMIN = "db_admin"   
SCOPE_KB_ADMIN = "kb_admin"   
SCOPES = (SCOPE_DB_ADMIN, SCOPE_KB_ADMIN)


@dataclass(frozen=True)
class AuthUser:
    user_id: int
    tenant_id: int
    role: str
    email: str
    username: str
    session_id: int
    dev_mode: bool = False
    scopes: str = ""

    @property
    def is_owner(self) -> bool:
        return self.role == "owner" or self.dev_mode

    @property
    def scope_set(self) -> set[str]:
        return {s.strip() for s in (self.scopes or "").split(",") if s.strip()}

    def has_scope(self, scope: str) -> bool:
        return self.is_owner or scope in self.scope_set

    @property
    def is_db_admin(self) -> bool:
        return self.has_scope(SCOPE_DB_ADMIN)

    @property
    def is_kb_admin(self) -> bool:
        return self.has_scope(SCOPE_KB_ADMIN)


def auth_enabled() -> bool:
    return (os.getenv("AF_AUTH_ENABLED", "1") or "1").strip().lower() not in ("0", "false", "no")


def session_ttl_days() -> int:
    try:
        return max(1, int(os.getenv("AF_SESSION_TTL_DAYS", "7")))
    except ValueError:
        return 7


def cookie_secure() -> bool:
    return (os.getenv("AF_COOKIE_SECURE", "0") or "0").strip().lower() in ("1", "true", "yes")


def allowed_origins() -> set[str]:
    raw = os.getenv(
        "AF_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def _dev_user() -> AuthUser:
    return AuthUser(
        user_id=int(os.getenv("AF_DEV_USER_ID", "0")),
        tenant_id=int(os.getenv("AF_DEV_TENANT_ID", "0")),
        role="owner", email="dev@local", username="dev", session_id=0, dev_mode=True,
        scopes=",".join(SCOPES),
    )


def verify_origin(request: Request) -> None:
    if (os.getenv("AF_ORIGIN_STRICT", "1") or "1").strip().lower() in ("0", "false", "no"):
        return
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    if not source:
        raise HTTPException(status_code=403, detail={
            "error": "origin_required",
            "hint": "缺少 Origin/Referer，写请求已被拒绝",
        })
    parts = urlsplit(source)
    if request.headers.get("host") and parts.netloc == request.headers.get("host"):
        return                      
    if f"{parts.scheme}://{parts.netloc}" not in allowed_origins():
        raise HTTPException(status_code=403, detail={
            "error": "origin_denied",
            "hint": "请求来源不在白名单（AF_ALLOWED_ORIGINS）",
        })


def lookup_user(token: str) -> AuthUser | None:
    if not token:
        return None
    now = datetime.now()
    with get_engine().connect() as conn:
        row = conn.execute(
            select(
                user_session.c.id, user_session.c.tenant_id, user_session.c.user_id,
                user_session.c.last_seen_at,
                app_user.c.email, app_user.c.username, app_user.c.role,
                app_user.c.scopes,
            ).select_from(
                user_session.join(app_user, app_user.c.id == user_session.c.user_id)
            ).where(
                user_session.c.token_hash == hash_token(token),
                user_session.c.revoked_at.is_(None),
                user_session.c.expires_at > now,
                app_user.c.status == 1,
            )
        ).first()
        if row is None:
            return None
        if row.last_seen_at is None or (now - row.last_seen_at).total_seconds() > _SEEN_RENEW_SEC:
            conn.execute(update(user_session)
                         .where(user_session.c.id == row.id)
                         .values(last_seen_at=now))
            conn.commit()
    return AuthUser(
        user_id=int(row.user_id), tenant_id=int(row.tenant_id),
        role=row.role, email=row.email, username=row.username,
        session_id=int(row.id), scopes=row.scopes or "",
    )


def get_current_user(request: Request) -> AuthUser | None:
    if not auth_enabled():
        user = _dev_user()
        request.state.user = user
        return user
    user = lookup_user(request.cookies.get(SESSION_COOKIE, ""))
    request.state.user = user
    return user


def require_user(request: Request) -> AuthUser:
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail={
            "error": "unauthorized", "hint": "请先登录",
        })
    if request.method in _WRITE_METHODS:
        verify_origin(request)
    return user


def _require_scope(request: Request, scope: str, hint: str) -> AuthUser:
    user = require_user(request)
    if not user.has_scope(scope):
        raise HTTPException(status_code=403, detail={
            "error": "forbidden", "hint": hint,
        })
    return user


def require_db_admin(request: Request) -> AuthUser:
    return _require_scope(request, SCOPE_DB_ADMIN, "需要数据库/平台管理员权限")


def require_owner(request: Request) -> AuthUser:
    user = require_user(request)
    if not user.is_owner:
        raise HTTPException(status_code=403, detail={
            "error": "forbidden", "hint": "仅租户所有者可以重置成员密码",
        })
    return user


def require_kb_admin(request: Request) -> AuthUser:
    return _require_scope(request, SCOPE_KB_ADMIN, "需要知识库管理员权限")
