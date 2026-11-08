# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select  # noqa: E402

from allfusion_auth.db import dispose_engine, get_engine  # noqa: E402
from allfusion_auth.models import thread_registry  # noqa: E402
from unified_agent.graph import checkpoint  # noqa: E402

_COMPLETED = ("done", "cancelled")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清理过期 LangGraph thread 快照")
    parser.add_argument("--days", type=int, default=0,
                        help="保留天数，默认读 AF_CKPT_RETENTION_DAYS（7）")
    parser.add_argument("--dry-run", action="store_true", help="只列出，不删除")
    parser.add_argument("--limit", type=int, default=0, help="单次最多处理条数")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    days = args.days or int(os.getenv("AF_CKPT_RETENTION_DAYS", "7"))
    cutoff = datetime.now() - timedelta(days=days)

    engine = get_engine()
    with engine.connect() as conn:
        stmt = (select(thread_registry.c.thread_id, thread_registry.c.status,
                       thread_registry.c.last_active_at)
                .where(thread_registry.c.status.in_(_COMPLETED),
                       thread_registry.c.last_active_at < cutoff)
                .order_by(thread_registry.c.last_active_at))
        if args.limit > 0:
            stmt = stmt.limit(args.limit)
        rows = conn.execute(stmt).all()

    print(f"[扫描] 保留 {days} 天，截止 {cutoff:%Y-%m-%d %H:%M:%S}，"
          f"命中 {len(rows)} 个 thread")
    if not rows:
        dispose_engine()
        return 0
    if args.dry_run:
        for r in rows:
            print(f"  - {r.thread_id}  status={r.status}  "
                  f"last_active_at={r.last_active_at:%Y-%m-%d %H:%M:%S}")
        dispose_engine()
        return 0

    saver = checkpoint.get_checkpointer()
    deleted = 0
    failed: list[tuple[str, str]] = []
    for r in rows:
        try:
            saver.delete_thread(r.thread_id)
        except Exception as e:  # noqa: BLE001 — 单条失败不影响其余
            failed.append((r.thread_id, str(e)[:200]))
            continue
        with engine.begin() as conn:
            conn.execute(delete(thread_registry)
                         .where(thread_registry.c.thread_id == r.thread_id))
        deleted += 1
        print(f"  [删除] {r.thread_id}")

    checkpoint.dispose_checkpointer()
    dispose_engine()
    print(f"[完成] 已删除 {deleted} 个，失败 {len(failed)} 个")
    for thread_id, reason in failed:
        print(f"  [失败] {thread_id}: {reason}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
