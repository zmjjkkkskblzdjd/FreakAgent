from __future__ import annotations

import html
import os
import random
import re
import secrets
import string
import time
import uuid
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from allfusion_auth import security
from allfusion_auth.db import get_engine, ping
from allfusion_auth.deps import (
    AuthUser, SCOPE_DB_ADMIN, SCOPE_KB_ADMIN, SCOPES, allowed_origins, auth_enabled,
    cookie_secure, require_db_admin, require_owner, require_user, session_ttl_days,
    SESSION_COOKIE, verify_origin,
)
from allfusion_auth.logging_redact import install_exception_handler, install_logging_redact
from allfusion_auth.models import app_user, audit_log, tenant, user_session
from allfusion_auth.redact import redact_text
from auth_service.app.datasources import router as datasources_router
from auth_service.app.mirror import router as mirror_router
from auth_service.app.providers import router as providers_router

install_logging_redact()

app = FastAPI(title="auth_service", version="0.1.0")
install_exception_handler(app)
app.include_router(providers_router)
app.include_router(datasources_router)
app.include_router(mirror_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


_CAPTCHA: dict[str, tuple[str, float]] = {}
_CAPTCHA_TTL = 300
_CAPTCHA_MAX = 3000
_REG_WINDOW_SEC = 3600
_REG_LIMIT = 10
_REG_HITS: dict[str, list[float]] = {}


def _captcha_enabled() -> bool:
    return _env_flag("AF_CAPTCHA_ENABLED", True)


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _gc_captcha(now: float) -> None:
    expired = [k for k, (_, exp) in _CAPTCHA.items() if exp <= now]
    for k in expired:
        _CAPTCHA.pop(k, None)
    if len(_CAPTCHA) > _CAPTCHA_MAX:        
        for k in list(_CAPTCHA)[: len(_CAPTCHA) - _CAPTCHA_MAX]:
            _CAPTCHA.pop(k, None)


def _captcha_svg(text: str) -> str:
    rnd = random.Random()
    chars = "".join(
        f'<text x="{10 + i * 18}" y="{28 + rnd.randint(-3, 3)}" font-size="20" '
        f'font-family="Consolas,monospace" fill="#1d4ed8" '
        f'transform="rotate({rnd.randint(-16, 16)} {10 + i * 18} 26)">'
        f'{html.escape(ch)}</text>'
        for i, ch in enumerate(text)
    )
    lines = "".join(
        f'<line x1="{rnd.randint(0, 140)}" y1="{rnd.randint(0, 44)}" '
        f'x2="{rnd.randint(0, 140)}" y2="{rnd.randint(0, 44)}" '
        f'stroke="#94a3b8" stroke-width="1" opacity="0.55"/>' for _ in range(4)
    )
    dots = "".join(
        f'<circle cx="{rnd.randint(2, 138)}" cy="{rnd.randint(2, 42)}" r="1" '
        f'fill="#cbd5e1"/>' for _ in range(24)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="140" height="44" '
        f'viewBox="0 0 140 44"><rect width="140" height="44" rx="6" fill="#f8fafc"/>'
        f"{lines}{dots}{chars}</svg>"
    )


def _issue_captcha() -> dict:
    a, b = random.randint(1, 9), random.randint(1, 9)
    if random.random() < 0.5:
        question, answer = f"{a} + {b} = ?", str(a + b)
    else:
        if a < b:
            a, b = b, a
        question, answer = f"{a} - {b} = ?", str(a - b)
    cid = uuid.uuid4().hex
    now = time.time()
    _gc_captcha(now)
    _CAPTCHA[cid] = (answer, now + _CAPTCHA_TTL)
    return {"enabled": True, "captcha_id": cid, "svg": _captcha_svg(question),
            "expires_in": _CAPTCHA_TTL}


def _consume_captcha(captcha_id: str | None, code: str | None) -> bool:
    if not _captcha_enabled():
        return True
    cid = (captcha_id or "").strip()
    if not cid:
        return False
    item = _CAPTCHA.pop(cid, None)
    if item is None:
        return False
    answer, exp = item
    if time.time() > exp:
        return False
    return secrets.compare_digest(answer, (code or "").strip())



_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fa5.-]{2,32}$")


def _now() -> datetime:
    return datetime.now()


def _audit(conn, *, tenant_id: int, user_id: int | None, action: str,
           target: str = "", request: Request | None = None, detail: str = "") -> None:
    conn.execute(insert(audit_log).values(
        tenant_id=tenant_id, user_id=user_id, action=action,
        target=target[:128], ip=_client_ip(request) if request else None,
        detail_json=redact_text(detail)[:4000] if detail else None,
        created_at=_now(),
    ))


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def _set_session_cookie(resp: Response, token: str) -> None:
    days = session_ttl_days()
    resp.set_cookie(
        key=SESSION_COOKIE, value=token,
        max_age=days * 86400, path="/",
        httponly=True, samesite="lax", secure=cookie_secure(),
    )


def _issue_session(conn, row, request: Request) -> str:
    now = _now()
    token, token_hash = security.new_session_token()
    conn.execute(insert(user_session).values(
        tenant_id=row.tenant_id, user_id=row.id, token_hash=token_hash,
        ip=_client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:255],
        created_at=now, last_seen_at=now,
        expires_at=now + timedelta(days=session_ttl_days()),
    ))
    conn.execute(update(app_user).where(app_user.c.id == row.id)
                 .values(last_login_at=now))
    return token


def _split_scopes(raw: str | None) -> set[str]:
    return {s.strip() for s in (raw or "").split(",") if s.strip()}


def _me_payload(user: AuthUser) -> dict:
    return {
        "user": {"id": user.user_id, "email": user.email,
                 "username": user.username, "role": user.role,
                 "scopes": sorted(user.scope_set),
                 "is_owner": user.is_owner,
                 "is_db_admin": user.is_db_admin,
                 "is_kb_admin": user.is_kb_admin},
        "tenant": {"id": user.tenant_id},
        "auth_enabled": auth_enabled(),
    }


def _fetch_tenant_name(conn, tenant_id: int) -> str:
    name = conn.execute(
        select(tenant.c.name).where(tenant.c.id == tenant_id)).scalar()
    return name or ""


def _default_tenant_id(conn) -> int | None:
    raw = (os.environ.get("AF_SINGLE_TENANT_ID") or "").strip()
    if raw.isdigit():
        row = conn.execute(
            select(tenant.c.id).where(tenant.c.id == int(raw))).first()
        if row:
            return int(row.id)
    row = conn.execute(
        select(app_user.c.tenant_id)
        .group_by(app_user.c.tenant_id)
        .order_by(func.count().desc(), app_user.c.tenant_id.asc())
        .limit(1)).first()
    return int(row.tenant_id) if row else None



@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "auth", "mysql": ping()}


@app.get("/auth/captcha")
def captcha() -> dict:
    if not _captcha_enabled():
        return {"enabled": False, "captcha_id": "", "svg": ""}
    return _issue_captcha()


class RegisterIn(BaseModel):
    email: str
    username: str
    password: str
    captcha_id: str | None = None
    captcha_code: str | None = None
    tenant_name: str | None = None


@app.post("/auth/register")
def register(req: RegisterIn, request: Request, response: Response) -> dict:
    if not _env_flag("AF_REGISTER_ENABLED", True):
        raise HTTPException(status_code=403, detail={
            "error": "register_disabled", "hint": "当前未开放注册",
        })
    verify_origin(request)

    ip = _client_ip(request)
    now_ts = time.time()
    hits = [t for t in _REG_HITS.get(ip, []) if now_ts - t < _REG_WINDOW_SEC]
    if len(hits) >= _REG_LIMIT:
        raise HTTPException(status_code=429, detail={
            "error": "register_rate_limited", "hint": "注册过于频繁，请稍后再试",
        })
    hits.append(now_ts)
    _REG_HITS[ip] = hits

    email = (req.email or "").strip().lower()
    username = (req.username or "").strip()
    if not _EMAIL_RE.match(email) or len(email) > 150:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_email", "hint": "邮箱格式不正确",
        })
    if not _USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail={
            "error": "invalid_username",
            "hint": "用户名需 2–32 位，可用中英文、数字、下划线、点、短横线",
        })
    problem = security.password_problem(req.password, username=username, email=email)
    if problem:
        raise HTTPException(status_code=400, detail={
            "error": "weak_password", "hint": problem,
        })
    if not _consume_captcha(req.captcha_id, req.captcha_code):
        raise HTTPException(status_code=400, detail={
            "error": "captcha_invalid", "hint": "验证码错误或已过期",
        })

    tenant_name = (req.tenant_name or "").strip() or f"{username} 的空间"
    password_hash = security.hash_password(req.password)

    try:
        with get_engine().begin() as conn:
            dup = conn.execute(
                select(app_user.c.id).where(app_user.c.email == email)).first()
            if dup:
                raise HTTPException(status_code=409, detail={
                    "error": "email_taken", "hint": "该邮箱已注册",
                })
            tenant_id = _default_tenant_id(conn)
            if tenant_id is None:
                tenant_id = conn.execute(insert(tenant).values(
                    name=tenant_name[:64], status=1, created_at=_now(),
                )).inserted_primary_key[0]
                role = "owner"
            else:
                role = "member"
            user_id = conn.execute(insert(app_user).values(
                tenant_id=tenant_id, email=email, username=username[:64],
                password_hash=password_hash, role=role, status=1,
                created_at=_now(),
            )).inserted_primary_key[0]
            row = conn.execute(select(app_user).where(app_user.c.id == user_id)).one()
            token = _issue_session(conn, row, request)
            joined_name = _fetch_tenant_name(conn, tenant_id)
            _audit(conn, tenant_id=tenant_id, user_id=user_id, action="register",
                   target=f"user:{user_id}", request=request,
                   detail="bootstrap_owner" if role == "owner" else "join_as_member")
    except IntegrityError:
        raise HTTPException(status_code=409, detail={
            "error": "email_taken", "hint": "该邮箱已注册",
        })

    _set_session_cookie(response, token)
    return {"ok": True, "user": {"id": user_id, "email": email,
                                 "username": username, "role": role,
                                 "scopes": [], "is_owner": role == "owner",
                                 "is_db_admin": role == "owner",
                                 "is_kb_admin": role == "owner"},
            "tenant": {"id": tenant_id, "name": joined_name}}


class LoginIn(BaseModel):
    account: str
    password: str
    captcha_id: str | None = None
    captcha_code: str | None = None


@app.post("/auth/login")
def login(req: LoginIn, request: Request, response: Response) -> dict:
    verify_origin(request)
    if _captcha_enabled() and _env_flag("AF_CAPTCHA_LOGIN", False):
        if not _consume_captcha(req.captcha_id, req.captcha_code):
            raise HTTPException(status_code=400, detail={
                "error": "captcha_invalid", "hint": "验证码错误或已过期",
            })

    account = (req.account or "").strip()
    ip = _client_ip(request)
    if not account or not req.password:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_credentials", "hint": "账号与密码不能为空",
        })
    account_key = account.lower()[:150]

    with get_engine().begin() as conn:
        if security.is_login_locked(conn, account_key, ip):
            raise HTTPException(status_code=429, detail={
                "error": "login_locked",
                "hint": f"失败次数过多，请 {security.LOCK_MINUTES} 分钟后再试",
            })
        row = conn.execute(
            select(app_user).where(
                (app_user.c.email == account_key) | (app_user.c.username == account)
            ).limit(1)
        ).first()
        ok = bool(row) and row.status == 1 and security.verify_password(
            row.password_hash, req.password)
        security.record_login_attempt(
            conn, tenant_id=(row.tenant_id if row else None),
            account=account_key, ip=ip, ok=ok)
        if not ok:
            _audit(conn, tenant_id=(row.tenant_id if row else 0),
                   user_id=(row.id if row else None), action="login_failed",
                   target=account_key, request=request)
            conn.commit()          
            raise HTTPException(status_code=401, detail={
                "error": "invalid_credentials", "hint": "账号或密码错误",
            })
        if security.needs_rehash(row.password_hash):
            conn.execute(update(app_user).where(app_user.c.id == row.id)
                         .values(password_hash=security.hash_password(req.password)))
            _audit(conn, tenant_id=row.tenant_id, user_id=row.id,
                   action="password_rehash", target=f"user:{row.id}",
                   request=request, detail="argon2id params upgraded on login")
        token = _issue_session(conn, row, request)
        _audit(conn, tenant_id=row.tenant_id, user_id=row.id,
               action="login", target=f"user:{row.id}", request=request)

    _set_session_cookie(response, token)
    scopes = _split_scopes(row.scopes)
    is_owner = row.role == "owner"
    return {"ok": True, "user": {"id": row.id, "email": row.email,
                                 "username": row.username, "role": row.role,
                                 "scopes": sorted(scopes),
                                 "is_owner": is_owner,
                                 "is_db_admin": is_owner or SCOPE_DB_ADMIN in scopes,
                                 "is_kb_admin": is_owner or SCOPE_KB_ADMIN in scopes},
            "tenant": {"id": row.tenant_id}}


@app.post("/auth/logout")
def logout(request: Request, response: Response,
           user: AuthUser = Depends(require_user)) -> dict:
    if not user.dev_mode:
        with get_engine().begin() as conn:
            conn.execute(update(user_session)
                         .where(user_session.c.id == user.session_id)
                         .values(revoked_at=_now()))
            _audit(conn, tenant_id=user.tenant_id, user_id=user.user_id,
                   action="logout", request=request)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/auth/me")
def me(request: Request, user: AuthUser = Depends(require_user)) -> dict:
    payload = _me_payload(user)
    if not user.dev_mode:
        with get_engine().connect() as conn:
            payload["tenant"]["name"] = _fetch_tenant_name(conn, user.tenant_id)
    return payload


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


@app.post("/auth/password")
def change_password(req: PasswordIn, request: Request,
                    user: AuthUser = Depends(require_user)) -> dict:
    problem = security.password_problem(req.new_password, username=user.username,
                                        email=user.email)
    if problem:
        raise HTTPException(status_code=400, detail={
            "error": "weak_password", "hint": problem,
        })
    with get_engine().begin() as conn:
        row = conn.execute(select(app_user).where(app_user.c.id == user.user_id)).one()
        if not security.verify_password(row.password_hash, req.old_password):
            raise HTTPException(status_code=400, detail={
                "error": "wrong_password", "hint": "原密码不正确",
            })
        conn.execute(update(app_user).where(app_user.c.id == user.user_id)
                     .values(password_hash=security.hash_password(req.new_password)))
        conn.execute(update(user_session).where(
            user_session.c.user_id == user.user_id,
            user_session.c.id != user.session_id,
            user_session.c.revoked_at.is_(None),
        ).values(revoked_at=_now()))
        _audit(conn, tenant_id=user.tenant_id, user_id=user.user_id,
               action="password_change", request=request)
    return {"ok": True}



@app.get("/admin/users")
def admin_list_users(limit: int = Query(50, ge=1, le=200),
                     offset: int = Query(0, ge=0),
                     q: str = "",
                     user: AuthUser = Depends(require_db_admin)) -> dict:
    if user.dev_mode:
        return {"users": [], "total": 0}
    with get_engine().connect() as conn:
        crit = [app_user.c.tenant_id == user.tenant_id]
        keyword = (q or "").strip()
        if keyword:
            like = f"%{keyword}%"
            crit.append((app_user.c.email.like(like)) | (app_user.c.username.like(like)))
        total = conn.execute(
            select(app_user.c.id).where(*crit)).all()
        rows = conn.execute(
            select(app_user.c.id, app_user.c.email, app_user.c.username,
                   app_user.c.role, app_user.c.scopes, app_user.c.status,
                   app_user.c.last_login_at, app_user.c.created_at)
            .where(*crit).order_by(app_user.c.id.asc())
            .limit(limit).offset(offset)
        ).all()
    return {"total": len(total), "users": [
        {"id": r.id, "email": r.email, "username": r.username, "role": r.role,
         "scopes": sorted(_split_scopes(r.scopes)),
         "status": r.status,
         "last_login_at": r.last_login_at.isoformat() if r.last_login_at else None,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]}


class AdminUserIn(BaseModel):
    email: str
    username: str
    password: str


@app.post("/admin/users")
def admin_create_user(req: AdminUserIn, request: Request,
                      admin: AuthUser = Depends(require_db_admin)) -> dict:
    verify_origin(request)
    email = (req.email or "").strip().lower()
    username = (req.username or "").strip()
    if not _EMAIL_RE.match(email) or len(email) > 150:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_email", "hint": "邮箱格式不正确",
        })
    if not _USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail={
            "error": "invalid_username",
            "hint": "用户名需 2–32 位，可用中英文、数字、下划线、点、短横线",
        })
    problem = security.password_problem(req.password, username=username, email=email)
    if problem:
        raise HTTPException(status_code=400, detail={
            "error": "weak_password", "hint": problem,
        })
    try:
        with get_engine().begin() as conn:
            dup = conn.execute(
                select(app_user.c.id).where(app_user.c.email == email)).first()
            if dup:
                raise HTTPException(status_code=409, detail={
                    "error": "email_taken", "hint": "该邮箱已注册",
                })
            user_id = conn.execute(insert(app_user).values(
                tenant_id=admin.tenant_id, email=email, username=username[:64],
                password_hash=security.hash_password(req.password),
                role="member", status=1, created_at=_now(),
            )).inserted_primary_key[0]
            created_at = conn.execute(
                select(app_user.c.created_at).where(app_user.c.id == user_id)).scalar()
            _audit(conn, tenant_id=admin.tenant_id, user_id=admin.user_id,
                   action="user_create", target=f"user:{user_id}", request=request)
    except IntegrityError:
        raise HTTPException(status_code=409, detail={
            "error": "email_taken", "hint": "该邮箱已注册",
        })
    return {"ok": True, "user": {
        "id": user_id, "email": email, "username": username, "role": "member",
        "scopes": [], "status": 1, "last_login_at": None,
        "created_at": created_at.isoformat() if created_at else None}}


class UserPatchIn(BaseModel):
    status: int | None = None
    role: str | None = None
    scopes: list[str] | None = None


@app.patch("/admin/users/{user_id}")
def admin_patch_user(user_id: int, req: UserPatchIn, request: Request,
                     admin: AuthUser = Depends(require_db_admin)) -> dict:
    if user_id == admin.user_id and req.status == 0:
        raise HTTPException(status_code=400, detail={
            "error": "self_disable", "hint": "不能禁用自己",
        })
    if (req.role is not None or req.scopes is not None) and not admin.is_owner:
        raise HTTPException(status_code=403, detail={
            "error": "forbidden", "hint": "只有租户 owner 能调整角色与管理域",
        })
    if req.role is not None and user_id == admin.user_id:
        raise HTTPException(status_code=400, detail={
            "error": "self_role_change", "hint": "不能修改自己的角色",
        })
    with get_engine().begin() as conn:
        row = conn.execute(select(app_user).where(
            app_user.c.id == user_id,
            app_user.c.tenant_id == admin.tenant_id)).first()
        if row is None:
            raise HTTPException(status_code=404, detail={"error": "user_not_found"})
        values = {}
        if req.status is not None:
            values["status"] = 1 if req.status else 0
        if req.role is not None:
            if req.role not in ("owner", "admin", "member"):
                raise HTTPException(status_code=400, detail={
                    "error": "invalid_role", "hint": "角色只能是 owner/admin/member",
                })
            values["role"] = req.role
        if req.scopes is not None:
            wanted = {s.strip() for s in req.scopes if s.strip()}
            bad = wanted - set(SCOPES)
            if bad:
                raise HTTPException(status_code=400, detail={
                    "error": "invalid_scope", "hint": f"未知管理域: {','.join(sorted(bad))}",
                })
            values["scopes"] = ",".join(sorted(wanted))
        if values:
            conn.execute(update(app_user).where(app_user.c.id == user_id).values(**values))
        if values.get("status") == 0:
            conn.execute(update(user_session).where(
                user_session.c.user_id == user_id,
                user_session.c.revoked_at.is_(None),
            ).values(revoked_at=_now()))
        _audit(conn, tenant_id=admin.tenant_id, user_id=admin.user_id,
               action="admin_patch_user", target=f"user:{user_id}",
               request=request, detail=str(values))
    return {"ok": True}


class ResetIn(BaseModel):
    new_password: str | None = None


def _random_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if not security.password_problem(pwd):
            return pwd


@app.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: int, request: Request,
                         req: ResetIn | None = None,
                         admin: AuthUser = Depends(require_owner)) -> dict:
    temp = (req.new_password if req and req.new_password else None)
    if temp:
        problem = security.password_problem(temp)
        if problem:
            raise HTTPException(status_code=400, detail={
                "error": "weak_password", "hint": problem,
            })
        temp_password = None
    else:
        temp = _random_password()
        temp_password = temp

    with get_engine().begin() as conn:
        row = conn.execute(select(app_user.c.id, app_user.c.email).where(
            app_user.c.id == user_id,
            app_user.c.tenant_id == admin.tenant_id)).first()
        if row is None:
            raise HTTPException(status_code=404, detail={"error": "user_not_found"})
        conn.execute(update(app_user).where(app_user.c.id == user_id)
                     .values(password_hash=security.hash_password(temp)))
        security.clear_failed_attempts(conn, row.email)   
        conn.execute(update(user_session).where(
            user_session.c.user_id == user_id,
            user_session.c.revoked_at.is_(None),
        ).values(revoked_at=_now()))
        _audit(conn, tenant_id=admin.tenant_id, user_id=admin.user_id,
               action="admin_reset_password", target=f"user:{user_id}",
               request=request)
    return {"ok": True, "temp_password": temp_password}


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run("auth_service.app.main:app",
                host=os.getenv("AF_AUTH_HOST", "127.0.0.1"),
                port=int(os.getenv("AF_AUTH_PORT", "8004")),
                workers=1)
