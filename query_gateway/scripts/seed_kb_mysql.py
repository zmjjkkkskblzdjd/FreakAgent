# -*- coding: utf-8 -*-
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pymysql
import yaml

MYSQL_HOST = os.getenv("KB_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("KB_MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("KB_MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("KB_MYSQL_PASSWORD", "")
DB = "sales_meta"

YAML_PATH = (Path(__file__).resolve().parents[2]
             / "DataAnalyzeAgent" / "DataAnalyzeAgent" / "metrics" / "dictionary.yaml")


def post_mysql(sql: str) -> None:  
    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
                           password=MYSQL_PASSWORD, database=DB, charset="utf8mb4",
                           connect_timeout=6)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    if not YAML_PATH.is_file():
        sys.exit(f"找不到底稿 {YAML_PATH}")
    raw = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    metrics = raw.get("metrics") or []
    if not metrics:
        sys.exit("dictionary.yaml 缺少非空 metrics 列表")

    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
                           password=MYSQL_PASSWORD, database=DB, charset="utf8mb4",
                           connect_timeout=6)
    try:
        with conn.cursor() as cur:
            for k in ("domain", "database", "default_time_field", "default_status_filter"):
                if raw.get(k):
                    cur.execute(
                        "INSERT INTO kb_meta (meta_key, meta_value) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE meta_value = VALUES(meta_value)",
                        (k, str(raw[k])),
                    )
            cur.execute("TRUNCATE TABLE kb_glossary")
            for m in metrics:
                cur.execute(
                    "INSERT INTO kb_glossary (name, aliases, formula, related_tables, unit, note) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        m["name"],
                        json.dumps(m.get("aliases") or [], ensure_ascii=False),
                        m["formula"],
                        json.dumps(m.get("related_tables") or [], ensure_ascii=False),
                        str(m.get("unit") or ""),
                        str(m.get("note") or ""),
                    ),
                )
            cur.execute("SELECT COUNT(*) FROM kb_glossary")
            n = cur.fetchone()[0]
        conn.commit()
        print(f"✓ 底稿迁移完成：kb_glossary {n} 条，kb_meta 4 键（{DB}@{MYSQL_HOST}）")
        print("  下一步：python sync_kb_to_ch.py 同步 ClickHouse")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
