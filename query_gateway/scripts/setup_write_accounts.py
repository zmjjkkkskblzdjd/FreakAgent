# -*- coding: utf-8 -*-
import os
import re
import secrets
import string
import sys
import urllib.request
from pathlib import Path

import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
load_dotenv(ENV)   
CH_HTTP = "http://192.168.1.136:8123/"
MYSQL_ADMIN = {"host": "192.168.1.136", "port": 3306,
               "user": "root", "password": os.getenv("QG_MYSQL_ADMIN_PASSWORD", "").strip()}
TOLERANT_PREFIXES = ("GRANT SELECT ON system.",)        

ENV_KEYS = {"DA_READONLY_PASSWORD": "QG_CH_PASSWORD",
            "DA_WRITER_PASSWORD": "QG_CH_WRITER_PASSWORD",
            "META_RW_PASSWORD": "QG_MYSQL_PASSWORD"}


def ensure_passwords() -> dict[str, str]:
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    existing = dict(re.findall(r"^([A-Z_]+)=(.*)$", text, flags=re.M))
    out, appended = {}, []
    alphabet = string.ascii_letters + string.digits
    for key, env_name in ENV_KEYS.items():
        pw = existing.get(env_name, "").strip()
        if not pw or pw == "__填我__":
            pw = "".join(secrets.choice(alphabet) for _ in range(24))
            appended.append(f"{env_name}={pw}")
        out[key] = pw
    if appended:
        with ENV.open("a", encoding="utf-8") as f:
            f.write("\n# 写车道账号（setup_write_accounts.py 生成）\n" + "\n".join(appended) + "\n")
    for env_name, default in (("QG_CH_USER", "da_readonly"),
                              ("QG_CH_WRITER_USER", "da_writer"),
                              ("QG_MYSQL_USER", "meta_rw")):
        if not existing.get(env_name):
            with ENV.open("a", encoding="utf-8") as f:
                f.write(f"{env_name}={default}\n")
    return out


def render(template: str, pw: dict[str, str]) -> list[str]:
    sql = (ROOT / "sql" / template).read_text(encoding="utf-8")
    for k, v in pw.items():
        sql = sql.replace("${" + k + "}", v)
    lines = [l for l in sql.split("\n") if not l.lstrip().startswith("--")]
    return [s.strip() for s in re.split(r";\s*(?:\n|$)", "\n".join(lines)) if s.strip()]


def ch_admin(sql: str) -> None:
    req = urllib.request.Request(CH_HTTP, data=sql.encode("utf-8"),
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    urllib.request.urlopen(req, timeout=15).read()


def ch_as(user: str, password: str, sql: str) -> str:
    req = urllib.request.Request(CH_HTTP, data=sql.encode("utf-8"),
                                 headers={"Content-Type": "text/plain; charset=utf-8",
                                          "X-ClickHouse-User": user, "X-ClickHouse-Key": password})
    return urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")


def _mask(sql: str) -> str:
    return re.sub(r"(IDENTIFIED\s+BY\s+')[^']*(')", r"\1***\2", sql, flags=re.IGNORECASE)


def main() -> int:
    if not MYSQL_ADMIN["password"]:
        print("FAIL 缺少 QG_MYSQL_ADMIN_PASSWORD：请在 .env 中配置"
              "（仅 setup 脚本使用的 MySQL 运维通道，不设默认值）")
        return 1
    pw = ensure_passwords()
    for stmt in render("05_write_accounts_ch.sql", pw):
        try:
            ch_admin(stmt)
            print(f"OK   CH  {_mask(stmt)[:60]}")
        except Exception as e:  # noqa: BLE001
            if any(stmt.upper().startswith(p.upper()) for p in TOLERANT_PREFIXES):
                print(f"WARN CH  {_mask(stmt)[:60]} -> {str(e)[:120]}（容错跳过，见计划 Task 7 注）")
            else:
                print(f"FAIL CH  {_mask(stmt)[:60]} -> {str(e)[:200]}")
                return 1

    conn = pymysql.connect(**MYSQL_ADMIN, charset="utf8mb4", connect_timeout=6)
    try:
        with conn.cursor() as cur:
            for stmt in render("06_write_account_mysql.sql", pw):
                cur.execute(stmt)
                print(f"OK   MySQL  {_mask(stmt)[:60]}")
        conn.commit()
    finally:
        conn.close()

    print("\n—— 验证 ——")
    print("da_readonly SELECT:", ch_as("da_readonly", pw["DA_READONLY_PASSWORD"],
                                     "SELECT count() FROM sales_dw.dim_region").strip())
    try:
        ch_as("da_readonly", pw["DA_READONLY_PASSWORD"],
              "INSERT INTO sales_dw.dim_region (region_id) VALUES (0)")
        print("FAIL da_readonly 居然能 INSERT")
        return 1
    except Exception:
        print("OK   da_readonly INSERT 被拒")
    print("da_writer SELECT 1:", ch_as("da_writer", pw["DA_WRITER_PASSWORD"], "SELECT 1").strip())
    try:
        n = ch_as("da_writer", pw["DA_WRITER_PASSWORD"],
                  "SELECT count() FROM sales_dw.dim_region").strip()
        print(f"OK   da_writer 表级 SELECT sales_dw.dim_region: count={n}")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL da_writer 缺 sales_dw 表级 SELECT -> {str(e)[:200]}")
        return 1

    conn = pymysql.connect(host=MYSQL_ADMIN["host"], port=MYSQL_ADMIN["port"],
                           user="meta_rw", password=pw["META_RW_PASSWORD"],
                           database="sales_meta", charset="utf8mb4", connect_timeout=6)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sales_meta.kb_glossary (name, formula) "
                        "VALUES ('__setup_probe__', '1')")
            cur.execute("DELETE FROM sales_meta.kb_glossary WHERE name='__setup_probe__'")
            print("OK   meta_rw kb_glossary INSERT/DELETE")
            try:
                cur.execute("UPDATE sales_meta.kb_meta SET meta_value='x' WHERE meta_key='domain'")
                print("FAIL meta_rw 居然能写 kb_meta")
                return 1
            except pymysql.MySQLError:
                print("OK   meta_rw 写 kb_meta 被拒")
        conn.rollback()
    finally:
        conn.close()
    print("\n全部账号就绪。请重启网关使 .env 生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
