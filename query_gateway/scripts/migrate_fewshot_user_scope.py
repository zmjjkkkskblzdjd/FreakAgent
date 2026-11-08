# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pymysql

GATEWAY_ENV = Path(__file__).resolve().parents[1] / ".env"
CH_HTTP = os.getenv("QG_CH_HTTP", "http://192.168.1.136:8123/")
DB = "sales_meta"
TABLE = "kb_fewshot"
COLUMN = "user_id"
INDEX = "idx_kb_fewshot_user"


def _env(name: str, default: str = "") -> str:
    val = os.getenv(name, "").strip()
    if val:
        return val
    if not GATEWAY_ENV.is_file():
        return default
    for line in GATEWAY_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(name + "=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or default
    return default


def _mysql() -> dict:
    return {
        "host": _env("QG_MYSQL_HOST", "192.168.1.136"),
        "port": int(_env("QG_MYSQL_PORT", "3306") or 3306),
        "user": "root",
        "password": _env("QG_MYSQL_ADMIN_PASSWORD", ""),
        "database": DB,
        "charset": "utf8mb4",
        "connect_timeout": 6,
    }


def _has_column(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
            (DB, TABLE, COLUMN))
        return int(cur.fetchone()[0]) > 0


def _ch(sql: str) -> str:
    req = urllib.request.Request(CH_HTTP, data=sql.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _ch_has_column() -> bool:
    q = urllib.parse.urlencode({
        "query": f"SELECT count() FROM system.columns WHERE database='sales_dw' "
                 f"AND table='{TABLE}' AND name='{COLUMN}'",
    })
    with urllib.request.urlopen(CH_HTTP + "?" + q, timeout=30) as resp:
        return int((resp.read().decode("utf-8") or "0").strip() or 0) > 0


def main() -> int:
    ap = argparse.ArgumentParser(description="fewshot 例题用户级归属迁移")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认 dry-run）")
    args = ap.parse_args()

    cfg = _mysql()
    if not cfg["password"]:
        print("[FATAL] 缺少 QG_MYSQL_ADMIN_PASSWORD（query_gateway/.env），未做任何修改。")
        return 2
    try:
        conn = pymysql.connect(**cfg)
    except Exception as e:                       # noqa: BLE001
        print(f"[FATAL] MySQL 不可达，未做任何修改：{type(e).__name__}: {e}")
        return 2

    try:
        has_col = _has_column(conn)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(f"SELECT count(*) AS n FROM {TABLE}")
            total = int(cur.fetchone()["n"])
            if has_col:
                cur.execute(f"SELECT count(*) AS n FROM {TABLE} WHERE user_id='' AND enabled=1")
            else:
                cur.execute(f"SELECT count(*) AS n FROM {TABLE} WHERE enabled=1")
            legacy = int(cur.fetchone()["n"])
        print(f"== MySQL {DB}.{TABLE} == 行数 {total}，列 {COLUMN}: "
              f"{'已存在' if has_col else '缺失'}，待下架存量 {legacy} 行")

        try:
            ch_has_col = _ch_has_column()
        except Exception as e:                   # noqa: BLE001
            print(f"[FATAL] ClickHouse 不可达，未做任何修改：{type(e).__name__}: {e}")
            return 2
        print(f"== ClickHouse sales_dw.{TABLE} == 列 {COLUMN}: "
              f"{'已存在' if ch_has_col else '缺失'}")

        if not args.apply:
            print("\n[dry-run] 将执行：")
            if not has_col:
                print(f"  ALTER TABLE {TABLE} ADD COLUMN {COLUMN} "
                      f"VARCHAR(64) NOT NULL DEFAULT ''")
                print(f"  ALTER TABLE {TABLE} ADD INDEX {INDEX} ({COLUMN})")
            print(f"  UPDATE {TABLE} SET enabled=0 WHERE user_id='' AND enabled=1")
            if not ch_has_col:
                print(f"  ALTER TABLE sales_dw.{TABLE} ADD COLUMN IF NOT EXISTS "
                      f"{COLUMN} String DEFAULT ''")
            print("确认后加 --apply 执行。")
            return 0

        with conn.cursor() as cur:
            if not has_col:
                cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} "
                            f"VARCHAR(64) NOT NULL DEFAULT '' COMMENT "
                            f"'归属用户 id（用户级隔离）'")
                cur.execute(f"ALTER TABLE {TABLE} ADD INDEX {INDEX} ({COLUMN})")
                print(f"[OK] MySQL 已加列 {COLUMN} + 索引 {INDEX}")
            cur.execute(f"UPDATE {TABLE} SET enabled=0 WHERE user_id='' AND enabled=1")
            off = cur.rowcount or 0
        conn.commit()
        print(f"[OK] MySQL 存量下架 {off} 行（enabled=0，行保留可回滚）")

        if not ch_has_col:
            _ch(f"ALTER TABLE sales_dw.{TABLE} ADD COLUMN IF NOT EXISTS "
                f"{COLUMN} String DEFAULT ''")
            print(f"[OK] ClickHouse 已加列 {COLUMN}")
        print("迁移完成。下一步：python sync_kb_to_ch.py 重灌 CH 副本。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
