# -*- coding: utf-8 -*-
from __future__ import annotations

import pymysql

from . import config


class MySQLWriteError(Exception):
    def __init__(self, message: str, errno: int | None = None):
        super().__init__(message)
        self.errno = errno


def _conn(database: str):
    if database == "sales_biz":
        host, port, user, password = (config.MYSQL_BIZ_HOST, config.MYSQL_BIZ_PORT,
                                      config.MYSQL_BIZ_USER, config.MYSQL_BIZ_PASSWORD)
        err = "未配置 QG_MYSQL_BIZ_USER（biz_rw 账号缺失，写车道未启用）"
    else:
        host, port, user, password = (config.MYSQL_HOST, config.MYSQL_PORT,
                                      config.MYSQL_USER, config.MYSQL_PASSWORD)
        err = "未配置 QG_MYSQL_USER（meta_rw 账号缺失，写车道未启用）"
    if not user:
        raise MySQLWriteError(err)
    try:
        return pymysql.connect(
            host=host, port=port,
            user=user, password=password,
            database=database, charset="utf8mb4",
            connect_timeout=6, autocommit=False,
        )
    except pymysql.MySQLError as e:
        raise MySQLWriteError(f"无法连接 MySQL({host}:{port}/{database}): {e}",
                              e.args[0] if e.args else None) from None


def count(sql: str, database: str) -> int:
    conn = _conn(database)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
        return int(row[0]) if row else 0
    except pymysql.MySQLError as e:
        raise MySQLWriteError(f"MySQL 复核查询失败: {e}", e.args[0] if e.args else None) from None
    finally:
        conn.close()


def execute(sql: str, database: str) -> int:
    conn = _conn(database)
    try:
        with conn.cursor() as cur:
            affected = cur.execute(sql)
        conn.commit()
        return int(affected)
    except pymysql.MySQLError as e:
        conn.rollback()
        raise MySQLWriteError(f"MySQL 执行失败: {e}", e.args[0] if e.args else None) from None
    finally:
        conn.close()
