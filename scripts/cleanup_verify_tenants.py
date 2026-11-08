from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from allfusion_auth.db import get_engine
from vector_backend import factory as vector_factory

ALLFUSION_ROOT = Path(__file__).resolve().parents[1]
KB_META_DB = ALLFUSION_ROOT / "kb_service" / "data" / "kb_meta.db"
KB_VECTOR_DIR = ALLFUSION_ROOT / "data" / "kb_db"
PG_DATA = ALLFUSION_ROOT / "data" / "pgdata"
TENANT_NAME_RE = re.compile(r"^(verify|lock|lock2)_\d+$")

TENANT_TABLES = [
    "login_attempt", "user_session", "audit_log",
    "thread_message", "session_summary", "thread_registry", "chat_session",
    "model_route", "model_provider",
    "datasource_table", "datasource",
    "kb_owner", "kb_grant",
]

DATASOURCE_CHILD_TABLES = [
    "datasource_write_grant", "datasource_mirror_table", "datasource_mirror",
]

ACCOUNT_TABLES = ["login_attempt"]


def discover(conn, explicit_ids: list[int]) -> list[dict]:
    rows = conn.execute(text("SELECT id, name FROM tenant ORDER BY id")).all()
    picked = []
    for r in rows:
        tid = int(r.id)
        if tid in explicit_ids or TENANT_NAME_RE.match((r.name or "").strip()):
            picked.append({"id": tid, "name": r.name})
    return picked


def count_rows(conn, table: str, tenant_ids: list[int]) -> int:
    if not tenant_ids:
        return 0
    marks = ",".join(str(int(t)) for t in tenant_ids)      
    return int(conn.execute(text(
        f"SELECT count(*) FROM {table} WHERE tenant_id IN ({marks})")).scalar_one())


def collect_users(conn, tenant_ids: list[int]) -> list[str]:
    if not tenant_ids:
        return []
    marks = ",".join(str(int(t)) for t in tenant_ids)
    rows = conn.execute(text(
        f"SELECT email FROM app_user WHERE tenant_id IN ({marks})")).all()
    return [r.email for r in rows if r.email]


def collect_threads(conn, tenant_ids: list[int]) -> list[str]:
    if not tenant_ids:
        return []
    marks = ",".join(str(int(t)) for t in tenant_ids)
    rows = conn.execute(text(
        f"SELECT thread_id FROM thread_registry WHERE tenant_id IN ({marks})"
        f" UNION SELECT thread_id FROM chat_session WHERE tenant_id IN ({marks})")).all()
    return sorted({str(r.thread_id) for r in rows if r.thread_id})


def collect_kbs(conn, tenant_ids: list[int]) -> list[str]:
    if not tenant_ids:
        return []
    marks = ",".join(str(int(t)) for t in tenant_ids)
    rows = conn.execute(text(
        f"SELECT kb_id FROM kb_owner WHERE tenant_id IN ({marks})")).all()
    return sorted({str(r.kb_id) for r in rows if r.kb_id and r.kb_id != "default"})


def collect_datasources(conn, tenant_ids: list[int]) -> list[int]:
    if not tenant_ids:
        return []
    marks = ",".join(str(int(t)) for t in tenant_ids)
    rows = conn.execute(text(
        f"SELECT id FROM datasource WHERE tenant_id IN ({marks})")).all()
    return sorted({int(r.id) for r in rows})


def protected_tenants(conn, username_prefix: str) -> set[int]:
    if not username_prefix:
        return set()
    rows = conn.execute(text(
        "SELECT DISTINCT tenant_id FROM app_user WHERE username LIKE :p"),
        {"p": f"{username_prefix}%"}).all()
    return {int(r.tenant_id) for r in rows}


def count_business_terms(tenant_ids: list[int]) -> int:
    if not tenant_ids or not KB_META_DB.exists():
        return 0
    marks = ",".join(str(int(t)) for t in tenant_ids)
    conn = sqlite3.connect(KB_META_DB)
    try:
        return int(conn.execute(
            f"SELECT count(*) FROM business_term WHERE tenant_id IN ({marks})").fetchone()[0])
    except sqlite3.OperationalError:        
        return 0
    finally:
        conn.close()


def purge_business_terms(tenant_ids: list[int]) -> int:
    if not tenant_ids:
        return 0
    marks = ",".join(str(int(t)) for t in tenant_ids)
    removed = 0
    if KB_META_DB.exists():
        conn = sqlite3.connect(KB_META_DB)
        try:
            cur = conn.execute(f"DELETE FROM business_term WHERE tenant_id IN ({marks})")
            removed = cur.rowcount or 0
            conn.commit()
        except sqlite3.OperationalError as e:       
            print(f"[BIZ] 跳过 business_term: {e}")
        finally:
            conn.close()
    if KB_VECTOR_DIR.exists() or vector_factory.backend_name() == "pg":
        try:
            for tid in tenant_ids:
                try:
                    vector_factory.delete_collection(
                        f"biz_terms_{int(tid)}", path=str(KB_VECTOR_DIR))
                except Exception:               # noqa: BLE001 — 集合不存在即已删
                    pass
        except Exception as e:                  # noqa: BLE001
            print(f"[BIZ] 向量集合清理失败（可稍后手动重跑）: {e}")
    return removed


def pg_port() -> int | None:
    pid_file = PG_DATA / "postmaster.pid"
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().splitlines()[3].strip())
    except (IndexError, ValueError):
        return None


def purge_pg_snapshots(thread_ids: list[str]) -> int:
    if not thread_ids:
        return 0
    port = pg_port()
    if not port:
        print("[PG] 未发现 postmaster.pid，跳过快照清理（残留不可达）")
        return 0
    try:
        import psycopg
    except ImportError:
        print("[PG] psycopg 未安装，跳过快照清理")
        return 0
    total = 0
    try:
        with psycopg.connect(host="127.0.0.1", port=port, user="postgres",
                             dbname="postgres", connect_timeout=5) as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                try:
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE thread_id = ANY(%s)", (thread_ids,))
                    total += cur.rowcount or 0
                except Exception as e:      # noqa: BLE001 — 表不存在即跳过
                    print(f"[PG] 跳过 {table}: {e}")
    except Exception as e:                  # noqa: BLE001 — PG 不可用不影响主清理
        print(f"[PG] 连接失败，跳过快照清理: {e}")
        return 0
    return total


def purge_local_kbs(kb_ids: list[str], apply: bool) -> int:
    if not kb_ids:
        return 0
    print(f"[KB] 待清理知识库: {kb_ids}")
    if not apply:
        return 0
    removed = 0
    if KB_VECTOR_DIR.exists() or vector_factory.backend_name() == "pg":
        try:
            for kb_id in kb_ids:
                for suffix in ("_parent_chunks", "_child_chunks", "_summaries", "_flat"):
                    try:
                        vector_factory.delete_collection(
                            f"{kb_id}{suffix}", path=str(KB_VECTOR_DIR))
                    except Exception:       # noqa: BLE001 — 集合不存在即已删
                        pass
        except Exception as e:              # noqa: BLE001
            print(f"[KB] 向量集合清理失败（可稍后手动重跑）: {e}")
    if KB_META_DB.exists():
        conn = sqlite3.connect(KB_META_DB)
        try:
            for kb_id in kb_ids:
                conn.execute("DELETE FROM doc WHERE kb_id=?", (kb_id,))
                cur = conn.execute("DELETE FROM kb WHERE id=?", (kb_id,))
                removed += cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="清理验收一次性租户及关联数据")
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认 dry-run）")
    ap.add_argument("--tenant-ids", default="", help="额外指定租户 id，逗号分隔")
    ap.add_argument("--keep-kb", action="store_true", help="不清理本地知识库残留")
    ap.add_argument("--protect-username", default="kbverify",
                    help="该前缀账号所在租户一律不删（默认 kbverify）")
    args = ap.parse_args()
    explicit = [int(x) for x in args.tenant_ids.split(",") if x.strip().isdigit()]

    try:
        engine = get_engine()
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as e:
        print("[FATAL] MySQL 不可达，未做任何修改。确认 hadoop03（192.168.1.136:3306）"
              "已开机并可从本机连接后重跑。")
        print(f"        原因: {str(e).splitlines()[0]}")
        return 2

    with engine.connect() as conn:
        tenants = discover(conn, explicit)
        protected = protected_tenants(conn, args.protect_username)
        skipped = [t for t in tenants if t["id"] in protected]
        tenants = [t for t in tenants if t["id"] not in protected]
        if skipped:
            print("== 受保护，跳过 ==")
            for t in skipped:
                print(f"  tenant {t['id']}: {t['name']}")
        if not tenants:
            print("没有匹配的一次性租户，无需清理。")
            return 0
        ids = [t["id"] for t in tenants]
        print("== 目标租户 ==")
        for t in tenants:
            print(f"  tenant {t['id']}: {t['name']}")
        print("\n== 关联行数 ==")
        for table in TENANT_TABLES:
            print(f"  {table}: {count_rows(conn, table, ids)}")
        emails = collect_users(conn, ids)
        print(f"\n== 账号（{len(emails)}）== " + ", ".join(emails))
        threads = collect_threads(conn, ids)
        print(f"== 会话 thread（{len(threads)}）== " + ", ".join(threads[:10])
              + (" …" if len(threads) > 10 else ""))
        kbs = collect_kbs(conn, ids)
        print(f"== 归属知识库（{len(kbs)}）== " + (", ".join(kbs) or "无"))
        ds_ids = collect_datasources(conn, ids)
        print(f"== 数据源（{len(ds_ids)}）== "
              + (", ".join(str(i) for i in ds_ids) or "无"))
        print(f"== 业务术语（SQLite）== {count_business_terms(ids)} 行")

        if not args.apply:
            print("\n[dry-run] 未做任何修改；确认后加 --apply 执行。")
            return 0

        marks = ",".join(str(int(i)) for i in ids)
        with engine.begin() as tx:
            if ds_ids:
                marks_ds = ",".join(str(int(i)) for i in ds_ids)
                for table in DATASOURCE_CHILD_TABLES:
                    tx.execute(text(
                        f"DELETE FROM {table} WHERE datasource_id IN ({marks_ds})"))
            for table in TENANT_TABLES:
                tx.execute(text(f"DELETE FROM {table} WHERE tenant_id IN ({marks})"))
            if emails:
                marks_acc = ",".join(":" + f"a{i}" for i in range(len(emails)))
                tx.execute(text(
                    f"DELETE FROM login_attempt WHERE account IN ({marks_acc})"),
                    {f"a{i}": m for i, m in enumerate(emails)})
            tx.execute(text(f"DELETE FROM app_user WHERE tenant_id IN ({marks})"))
            tx.execute(text(f"DELETE FROM tenant WHERE id IN ({marks})"))
        print(f"\n[OK] MySQL 已删除 {len(ids)} 个租户及其关联行。")

    pg_deleted = purge_pg_snapshots(threads)
    print(f"[PG] 已删除快照行: {pg_deleted}")
    biz_deleted = purge_business_terms(ids)
    print(f"[BIZ] 已删除业务术语行: {biz_deleted}（含 biz_terms_* 集合）")
    if not args.keep_kb:
        kb_deleted = purge_local_kbs(kbs, apply=True)
        print(f"[KB] 已删除 kb_meta 行: {kb_deleted}")
    print("清理完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
