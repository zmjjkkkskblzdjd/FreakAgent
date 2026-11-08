# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import secrets
import string
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select, update  # noqa: E402

from allfusion_auth import security  # noqa: E402
from allfusion_auth.db import get_engine  # noqa: E402
from allfusion_auth.models import app_user, audit_log, user_session  # noqa: E402


def _random_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if not security.password_problem(pwd):
            return pwd


def main() -> int:
    parser = argparse.ArgumentParser(description="重置 AllFusion 账号密码")
    parser.add_argument("--email", required=True, help="账号邮箱")
    parser.add_argument("--password", default="", help="新密码；缺省则随机生成")
    parser.add_argument("--tenant-id", type=int, default=0, help="同邮箱跨租户时指定")
    parser.add_argument("--role", default="", choices=["", "owner", "admin", "member"],
                        help="可选：同时调整角色")
    args = parser.parse_args()

    email = args.email.strip().lower()
    if args.password:
        problem = security.password_problem(args.password, email=email)
        if problem:
            print(f"[失败] {problem}")
            return 2
    password = args.password or _random_password()

    with get_engine().begin() as conn:
        crit = [app_user.c.email == email]
        if args.tenant_id:
            crit.append(app_user.c.tenant_id == args.tenant_id)
        rows = conn.execute(select(
            app_user.c.id, app_user.c.tenant_id, app_user.c.username,
            app_user.c.role, app_user.c.status).where(*crit)).all()
        if not rows:
            print(f"[失败] 找不到账号: {email}")
            return 1
        if len(rows) > 1:
            ids = ", ".join(f"tenant={r.tenant_id}" for r in rows)
            print(f"[失败] 同邮箱存在于多个租户（{ids}），请加 --tenant-id")
            return 1

        row = rows[0]
        values = {"password_hash": security.hash_password(password)}
        if args.role:
            values["role"] = args.role
        conn.execute(update(app_user).where(app_user.c.id == row.id).values(**values))
        security.clear_failed_attempts(conn, email)          
        conn.execute(update(user_session).where(
            user_session.c.user_id == row.id,
            user_session.c.revoked_at.is_(None),
        ).values(revoked_at=datetime.now()))
        conn.execute(audit_log.insert().values(
            tenant_id=row.tenant_id, user_id=row.id, action="cli_reset_password",
            target=f"user:{row.id}", detail_json="offline reset",
            created_at=datetime.now()))

    print(f"[完成] {email}（tenant={row.tenant_id}, user={row.id}, role="
          f"{args.role or row.role}）")
    if not args.password:
        print(f"[临时密码] {password}   ← 仅显示一次，请立即修改")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
