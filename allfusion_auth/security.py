from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError
from sqlalchemy import and_, delete, func, insert, select

from allfusion_auth.models import login_attempt

_hasher = PasswordHasher()          

PASSWORD_MIN_LEN = 8
LOCK_FAILURES = 5
LOCK_MINUTES = 15
_TOKEN_BYTES = 32



def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_problem(password: str, *, username: str = "", email: str = "") -> str | None:
    pwd = password or ""
    if len(pwd) < PASSWORD_MIN_LEN:
        return f"密码至少 {PASSWORD_MIN_LEN} 位"
    categories = sum(bool(re.search(p, pwd)) for p in
                     (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^0-9A-Za-z]"))
    if categories < 2:
        return "密码需包含字母、数字、符号中的至少两类"
    if username and pwd.lower() == username.lower():
        return "密码不能与用户名相同"
    if email and pwd.lower() == email.lower():
        return "密码不能与邮箱相同"
    return None


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True



def new_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()



def _window_start(now: datetime) -> datetime:
    return now - timedelta(minutes=LOCK_MINUTES)


def is_login_locked(conn, account: str, ip: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    since = _window_start(now)
    last_ok = conn.execute(
        select(func.max(login_attempt.c.created_at)).where(and_(
            login_attempt.c.account == account,
            login_attempt.c.ip == ip,
            login_attempt.c.ok == 1,
        ))
    ).scalar()
    if last_ok is not None:
        since = max(since, last_ok)
    count = conn.execute(
        select(func.count()).select_from(login_attempt).where(and_(
            login_attempt.c.account == account,
            login_attempt.c.ip == ip,
            login_attempt.c.ok == 0,
            login_attempt.c.created_at >= since,
        ))
    ).scalar_one()
    return int(count) >= LOCK_FAILURES


def record_login_attempt(conn, *, tenant_id: int | None, account: str,
                         ip: str, ok: bool, now: datetime | None = None) -> None:
    conn.execute(insert(login_attempt).values(
        tenant_id=tenant_id,
        account=(account or "")[:150],
        ip=(ip or "")[:45],
        ok=1 if ok else 0,
        created_at=now or datetime.now(),
    ))


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def clear_failed_attempts(conn, account: str) -> int:
    result = conn.execute(delete(login_attempt).where(and_(
        login_attempt.c.account == (account or "")[:150],
        login_attempt.c.ok == 0,
    )))
    return int(result.rowcount or 0)
