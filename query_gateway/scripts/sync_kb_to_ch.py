# -*- coding: utf-8 -*-
import json
import os
import urllib.parse
import urllib.request

import pymysql

CH_HTTP = os.getenv("KB_CH_HTTP", "http://127.0.0.1:8123/")
MYSQL_HOST = os.getenv("KB_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("KB_MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("KB_MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("KB_MYSQL_PASSWORD", "")
DB = "sales_meta"

_GLOSSARY_SQL = ("SELECT name, aliases, formula, related_tables, unit, note, formula_desc, enabled "
                 "FROM kb_glossary")
_FEWSHOT_SQL = ("SELECT id, user_id, question, reference_sql, explanation, related_tables, tags, "
                "status, enabled FROM kb_fewshot")
_META_SQL = "SELECT meta_key, meta_value FROM kb_meta"


def _ch(sql: str, data: str | None = None) -> str:
    if data is None:
        body, url = sql.encode("utf-8"), CH_HTTP
    else:
        body = data.encode("utf-8")
        url = CH_HTTP + "?" + urllib.parse.urlencode({"query": sql})
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _fetch_all(sql: str) -> list[dict]:
    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
                           password=MYSQL_PASSWORD, database=DB, charset="utf8mb4",
                           connect_timeout=6)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql)
            return list(cur.fetchall())
    finally:
        conn.close()


def _jarr(value, default="[]") -> list:
    return json.loads(value) if value else json.loads(default)


def _sync_glossary() -> int:
    rows = _fetch_all(_GLOSSARY_SQL)
    payload = "\n".join(json.dumps({
        "name": r["name"],
        "aliases": _jarr(r["aliases"]),
        "formula": r["formula"],
        "related_tables": _jarr(r["related_tables"]),
        "unit": r["unit"] or "",
        "note": r["note"] or "",
        "formula_desc": r["formula_desc"] or "",
        "enabled": int(bool(r["enabled"])),
    }, ensure_ascii=False) for r in rows)
    _ch("TRUNCATE TABLE sales_dw.kb_glossary")
    if payload:
        _ch("INSERT INTO sales_dw.kb_glossary FORMAT JSONEachRow", payload)
    return len(rows)


def _sync_fewshot() -> int:
    rows = _fetch_all(_FEWSHOT_SQL)
    payload = "\n".join(json.dumps({
        "id": int(r["id"]),
        "user_id": r["user_id"] or "",
        "question": r["question"],
        "reference_sql": r["reference_sql"],
        "explanation": r["explanation"] or "",
        "related_tables": _jarr(r["related_tables"]),
        "tags": _jarr(r["tags"]),
        "status": r["status"] or "published",
        "enabled": int(bool(r["enabled"])),
    }, ensure_ascii=False) for r in rows)
    _ch("TRUNCATE TABLE sales_dw.kb_fewshot")
    if payload:
        _ch("INSERT INTO sales_dw.kb_fewshot FORMAT JSONEachRow", payload)
    return len(rows)


def _sync_meta() -> int:
    rows = _fetch_all(_META_SQL)
    payload = "\n".join(json.dumps({"meta_key": r["meta_key"],
                                    "meta_value": r["meta_value"]},
                                   ensure_ascii=False) for r in rows)
    _ch("TRUNCATE TABLE sales_dw.kb_meta")
    if payload:
        _ch("INSERT INTO sales_dw.kb_meta FORMAT JSONEachRow", payload)
    return len(rows)


def main() -> None:
    n_g = _sync_glossary()
    n_f = _sync_fewshot()
    n_m = _sync_meta()
    print(f"✓ 同步完成（{DB} → sales_dw）：glossary {n_g} 条 / fewshot {n_f} 条 / meta {n_m} 键")


if __name__ == "__main__":
    main()
