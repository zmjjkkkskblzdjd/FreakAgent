# -*- coding: utf-8 -*-
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = ROOT / "docs" / "clickhouse" / "001_init.sql"
CH_URL = os.getenv("AF_CH_URL", "http://192.168.1.136:8123").rstrip("/")
DB = os.getenv("AF_CH_DB", "allfusion_trace")


def statements(sql: str) -> list[str]:
    lines = [l for l in sql.split("\n") if not l.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def query(sql: str) -> str:
    req = urllib.request.Request(f"{CH_URL}/?query=" + urllib.parse.quote(sql),
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    user, password = os.getenv("AF_CH_USER"), os.getenv("AF_CH_PASSWORD")
    if user:
        req.add_header("X-ClickHouse-User", user)
    if password:
        req.add_header("X-ClickHouse-Key", password)
    return urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace").strip()


def main() -> int:
    for stmt in statements(SQL_FILE.read_text(encoding="utf-8")):
        req = urllib.request.Request(f"{CH_URL}/", data=stmt.encode("utf-8"),
                                     headers={"Content-Type": "text/plain; charset=utf-8"})
        user, password = os.getenv("AF_CH_USER"), os.getenv("AF_CH_PASSWORD")
        if user:
            req.add_header("X-ClickHouse-User", user)
        if password:
            req.add_header("X-ClickHouse-Key", password)
        urllib.request.urlopen(req, timeout=20).read()

    count = query(f"SELECT count() FROM system.tables WHERE database='{DB}' AND name='trace_span'")
    if count != "1":
        print(f"FAIL {DB}.trace_span 不存在")
        return 1
    ttl = query(f"SELECT engine_full FROM system.tables WHERE database='{DB}' AND name='trace_span'")
    ttl_line = next((s for s in ttl.replace("\n", " ").split(" TTL ") if "INTERVAL" in s.upper()), "")
    version = query("SELECT version()")
    print(f"OK   ClickHouse {version} {CH_URL}")
    print(f"OK   {DB}.trace_span TTL: {'TTL ' + ttl_line.strip() if ttl_line else '缺失'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
