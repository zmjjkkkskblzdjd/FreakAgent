from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from allfusion_auth.db import get_engine
from kb_service import business_store, mysql_source


def _payload(row: dict) -> dict:
    return {
        "term": str(row.get("name") or "").strip(),
        "description": (row.get("note") or "").strip(),
        "synonyms": ",".join(business_store.parse_json_array(row.get("aliases"))),
        "formula": (row.get("formula") or "").strip(),
        "related_tables": ",".join(
            business_store.parse_json_array(row.get("related_tables"))),
        "unit": (row.get("unit") or "").strip(),
    }


def _tenants(explicit_ids: list[int]) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT id, name FROM tenant ORDER BY id")).all()
    picked = [{"id": int(r.id), "name": r.name or str(r.id)} for r in rows]
    if explicit_ids:
        keep = set(explicit_ids)
        picked = [t for t in picked if t["id"] in keep]
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description="kb_glossary → 租户业务库术语")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只盘点）")
    ap.add_argument("--tenant-ids", default="", help="只处理指定租户，逗号分隔")
    ap.add_argument("--overwrite", action="store_true", help="同名术语覆盖字段")
    ap.add_argument("--skip-sync", action="store_true", help="不重建向量集合")
    args = ap.parse_args()

    explicit = [int(x) for x in args.tenant_ids.split(",") if x.strip().isdigit()]

    try:
        rows = mysql_source.fetch_kb_rows("kb_glossary")
    except Exception as exc:            # noqa: BLE001 — 连不上就明确失败
        print(f"✗ 读取 kb_glossary 失败：{exc}", file=sys.stderr)
        return 2

    payloads = [p for p in (_payload(r) for r in rows) if p["term"]]
    if not payloads:
        print("✗ kb_glossary 没有可迁移的启用行（enabled=1）", file=sys.stderr)
        return 2

    try:
        tenants = _tenants(explicit)
    except Exception as exc:            # noqa: BLE001
        print(f"✗ 读取 tenant 表失败：{exc}", file=sys.stderr)
        return 2
    if not tenants:
        print("✗ 没有匹配的租户", file=sys.stderr)
        return 2

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] kb_glossary {len(payloads)} 条 → {len(tenants)} 个租户")
    if not args.apply:
        for t in tenants:
            print(f"  tenant {t['id']}（{t['name']}）：将写入 {len(payloads)} 条")
        print("  加 --apply 执行；写库后会逐租户重建向量。")
        return 0

    conn = business_store.open_conn()
    try:
        total_added = total_updated = 0
        for t in tenants:
            tid = t["id"]
            existing = {x["term"] for x in business_store.list_terms(conn, tid)}
            added = updated = 0
            for p in payloads:
                if p["term"] in existing:
                    if not args.overwrite:
                        continue
                    conn.execute(
                        "UPDATE business_term SET description=?, synonyms=?, formula=?,"
                        " related_tables=?, unit=?, is_recall=1, embedding_status='PENDING',"
                        " error_msg='', updated_at=? WHERE tenant_id=? AND term=?",
                        (p["description"], p["synonyms"], p["formula"],
                         p["related_tables"], p["unit"], time.time(), tid, p["term"]))
                    conn.commit()
                    updated += 1
                    continue
                business_store.insert_term(conn, tid, **p, is_recall=True)
                added += 1
            total_added += added
            total_updated += updated
            sync = {"ok": True, "count": "skipped"} if args.skip_sync else \
                business_store.sync(conn, tid)
            state = "同步 ok" if sync.get("ok") else f"同步失败：{sync.get('hint')}"
            print(f"  tenant {tid}（{t['name']}）：新增 {added} / 覆盖 {updated} / {state}")
    finally:
        conn.close()

    print(f"✓ 完成：新增 {total_added} 条，覆盖 {total_updated} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
