# -*- coding: utf-8 -*-
import os
import re
import secrets
import string
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
GATEWAY_ENV = ROOT / "query_gateway" / ".env"
SQL_DIR = ROOT / "docs" / "mysql"
HOST = os.getenv("AF_MYSQL_HOST", "192.168.1.136")
PORT = int(os.getenv("AF_MYSQL_PORT", "3306"))
DB = "allfusion"
APP_USER = "allfusion_app"
PLACEHOLDER = "__AF_MYSQL_APP_PASSWORD__"


def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", path.read_text(encoding="utf-8"), re.M))


def statements(sql: str) -> list[str]:
    lines = [l for l in sql.split("\n") if not l.lstrip().startswith("--")]
    return [s.strip() for s in re.split(r";\s*(?:\n|$)", "\n".join(lines)) if s.strip()]


def ensure_env(keys: dict[str, str]) -> None:
    existing = parse_env(ENV)
    missing = {k: v for k, v in keys.items() if not existing.get(k)}
    if not missing:
        return
    with ENV.open("a", encoding="utf-8") as f:
        f.write("\n# init_mysql.py 生成（本文件不入 git）\n")
        for k, v in missing.items():
            f.write(f"{k}={v}\n")


def main() -> int:
    admin_pw = (os.getenv("AF_MYSQL_ADMIN_PASSWORD", "").strip()
                or parse_env(GATEWAY_ENV).get("QG_MYSQL_ADMIN_PASSWORD", "").strip())
    if not admin_pw:
        print("FAIL 缺少 AF_MYSQL_ADMIN_PASSWORD（或 query_gateway/.env 的 QG_MYSQL_ADMIN_PASSWORD）")
        return 1

    env_data = parse_env(ENV)
    app_pw = (os.getenv("AF_MYSQL_PASSWORD", "").strip()
              or env_data.get("AF_MYSQL_PASSWORD", "").strip())
    if not app_pw:
        alphabet = string.ascii_letters + string.digits
        app_pw = "".join(secrets.choice(alphabet) for _ in range(24))

    conn = pymysql.connect(host=HOST, port=PORT, user="root", password=admin_pw,
                           charset="utf8mb4", connect_timeout=6)
    try:
        with conn.cursor() as cur:
            for stmt in statements((SQL_DIR / "001_init.sql").read_text(encoding="utf-8")):
                cur.execute(stmt)
            cur.execute("SELECT COUNT(*) FROM mysql.user WHERE user=%s AND host='%%'", (APP_USER,))
            user_exists = bool(cur.fetchone()[0])
            for stmt in statements((SQL_DIR / "002_account.sql").read_text(encoding="utf-8")
                                   .replace(PLACEHOLDER, app_pw)):
                if stmt.upper().startswith("CREATE USER"):
                    if user_exists:
                        cur.execute("SET PASSWORD FOR 'allfusion_app'@'%%' = PASSWORD(%s)", (app_pw,))
                        continue
                cur.execute(stmt)
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (DB,))
            table_count = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    app = pymysql.connect(host=HOST, port=PORT, user=APP_USER, password=app_pw, database=DB,
                          charset="utf8mb4", connect_timeout=6)
    try:
        with app.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.execute("SHOW GRANTS")
            grants = " | ".join(r[0] for r in cur.fetchall())
    finally:
        app.close()

    ensure_env({
        "AF_MYSQL_PASSWORD": app_pw,
        "AF_MYSQL_DSN": (f"mysql+pymysql://{APP_USER}:{app_pw}@{HOST}:{PORT}/{DB}"
                         f"?charset=utf8mb4"),
        "AF_SECRET_KEY": __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode(),
    })

    print(f"OK   MySQL {HOST}:{PORT} 库 {DB} 表数={table_count}")
    print(f"OK   {APP_USER} 实连成功；GRANT: {grants}")
    print(f"OK   AllFusion/.env 就绪（AF_MYSQL_PASSWORD / AF_MYSQL_DSN / AF_SECRET_KEY）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
