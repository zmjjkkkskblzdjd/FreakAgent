# -*- coding: utf-8 -*-
import os
import re
import subprocess
import sys
from pathlib import Path

import pgserver
from langgraph.checkpoint.postgres import PostgresSaver
from pgserver._commands import POSTGRES_BIN_PATH
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parent.parent
PGDATA = Path(os.getenv("AF_PGDATA") or (ROOT / "data" / "pgdata")).resolve()


def ensure_pgdata() -> None:
    if (PGDATA / "PG_VERSION").exists():
        return
    PGDATA.parent.mkdir(parents=True, exist_ok=True)
    initdb = POSTGRES_BIN_PATH / ("initdb.exe" if os.name == "nt" else "initdb")
    subprocess.run([str(initdb), "--auth=trust", "--auth-local=trust", "--encoding=utf8",
                    "-U", "postgres", "-D", str(PGDATA)],
                   check=True, capture_output=True)


def main() -> int:
    ensure_pgdata()
    server = pgserver.get_server(PGDATA, cleanup_mode="stop")
    uri = server.get_uri(database="postgres")
    safe_uri = re.sub(r"://([^:/]+):[^@]*@", r"://\1:***@", uri)

    pool = ConnectionPool(conninfo=uri, max_size=4, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0})
    try:
        PostgresSaver(pool).setup()
        with pool.connection() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")]
            version = conn.execute("SHOW server_version").fetchone()[0]
    finally:
        pool.close()

    print(f"OK   pgdata  {PGDATA}")
    print(f"OK   PG {version}  {safe_uri}")
    print(f"OK   checkpointer 表 {len(tables)} 张: {', '.join(tables)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
