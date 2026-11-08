# -*- coding: utf-8 -*-
import json
import os
import sys
from pathlib import Path

import pymysql

MYSQL_HOST = os.getenv("KB_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("KB_MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("KB_MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("KB_MYSQL_PASSWORD", "")
DB = "sales_meta"

CANDIDATES = (Path(__file__).resolve().parents[2]
              / "DataAnalyzeAgent" / "DataAnalyzeAgent" / "data"
              / "fewshot_candidates.jsonl")

_INSERT = ("INSERT INTO kb_fewshot (user_id, question, reference_sql, explanation, "
           "related_tables, tags, status) "
           "VALUES (%s, %s, %s, %s, %s, %s, 'published')")


def _user_of(record: dict) -> str:
    return (record.get("user_id") or os.getenv("DA_MEMORY_USER", "local") or "local").strip() or "local"


def _load() -> list[dict]:
    if not CANDIDATES.is_file():
        return []
    rows = []
    for line in CANDIDATES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _save(rows: list[dict]) -> None:
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    CANDIDATES.write_text(text + ("\n" if text else ""), encoding="utf-8")


def main() -> None:
    rows = _load()
    if not rows:
        print(f"候选队列为空（{CANDIDATES}）——先去 REPL 里跑几轮问答积累候选")
        return

    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
                           password=MYSQL_PASSWORD, database=DB, charset="utf8mb4",
                           connect_timeout=6)
    published = dropped = 0
    remaining: list[dict] = []
    print(f"待审候选 {len(rows)} 条（y 发布 / e 改tags发布 / s 跳过 / d 丢弃 / q 退出）\n")
    try:
        for i, r in enumerate(rows, 1):
            print(f"—— [{i}/{len(rows)}] " + "—" * 40)
            print(f"问题：{r['question']}")
            print(f"SQL：\n{r['reference_sql']}")
            print(f"建议 tags：{'、'.join(r.get('suggested_tags') or [])}")
            print(f"提取于：{r.get('extracted_at', '?')}")
            while True:
                action = input("操作 (y/e/s/d/q) ").strip().lower()
                if action == "q":
                    remaining.extend(rows[i - 1:])
                    _save(remaining)
                    print(f"\n已发布 {published} 条、丢弃 {dropped} 条、剩余 {len(remaining)} 条")
                    _hint_sync(published)
                    return
                if action == "s":
                    remaining.append(r)
                    break
                if action == "d":
                    dropped += 1
                    break
                if action in ("y", "e"):
                    tags = list(r.get("suggested_tags") or [])
                    if action == "e":
                        raw = input("tags（逗号分隔，如 metric:成交额,dim:channel）：")
                        tags = [t.strip() for t in raw.split(",") if t.strip()]
                    with conn.cursor() as cur:
                        cur.execute(_INSERT, (
                            _user_of(r), r["question"], r["reference_sql"],
                            r.get("explanation") or "",
                            json.dumps(r.get("related_tables") or [], ensure_ascii=False),
                            json.dumps(tags, ensure_ascii=False),
                        ))
                    conn.commit()
                    published += 1
                    print("  ✓ 已发布（published）")
                    break
                print("  无效输入")
    finally:
        conn.close()
    _save(remaining)
    print(f"\n已发布 {published} 条、丢弃 {dropped} 条、剩余 {len(remaining)} 条")
    _hint_sync(published)


def _hint_sync(published: int) -> None:
    if published:
        print("下一步：python sync_kb_to_ch.py 同步 ClickHouse 后生效")


if __name__ == "__main__":
    main()
