from __future__ import annotations

from kb_service.kb_config import (
    MYSQL_DATABASE, MYSQL_HOST, MYSQL_PASSWORD, MYSQL_PORT, MYSQL_USER,
)


def select_sql(table: str, user_id: str | None = None) -> str:
    if table == "kb_glossary":
        return "SELECT * FROM kb_glossary WHERE enabled = 1"
    if table == "kb_fewshot":
        sql = "SELECT * FROM kb_fewshot WHERE enabled = 1 AND status = 'published'"
        if user_id is not None:
            sql += " AND user_id = %(user_id)s"
        return sql
    raise ValueError(f"非法表名：{table}（仅允许 kb_glossary / kb_fewshot）")


def fetch_kb_rows(table: str, user_id: str | None = None) -> list[dict]:
    import pymysql
    from pymysql.cursors import DictCursor
    sql = select_sql(table, user_id)
    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
        password=MYSQL_PASSWORD, database=MYSQL_DATABASE,
        charset="utf8mb4", cursorclass=DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, {"user_id": user_id} if user_id is not None else None)
            return cur.fetchall()
    finally:
        conn.close()