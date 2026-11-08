# -*- coding: utf-8 -*-
import sqlglot
from sqlglot import exp

CASES = {
    "remote表函数": "SELECT * FROM remote('127.0.0.1', system.one)",
    "url表函数": "SELECT * FROM url('http://x', CSV, 'x String')",
    "普通表": "SELECT * FROM sales_dw.v_dim_region",
    "CTE": "WITH m AS (SELECT * FROM sales_dw.v_dim_region) SELECT * FROM m",
    "FORMAT": "SELECT * FROM sales_dw.v_dim_region FORMAT JSON",
    "LIMIT": "SELECT * FROM sales_dw.v_dim_region LIMIT 10",
    "UNION": "SELECT 1 AS a UNION ALL SELECT 2",
    "numbers": "SELECT * FROM numbers(10)",
    "dictGet": "SELECT dictGetString('d','k',toUInt64(1))",
    "sleep": "SELECT sleep(3)",
}

for name, sql in CASES.items():
    print(f"\n### {name}: {sql}")
    try:
        tree = sqlglot.parse_one(sql, read="clickhouse")
        print(f"  root: {type(tree).__name__}")
        for node in tree.walk():
            label = type(node).__name__
            extra = ""
            if isinstance(node, exp.Table):
                extra = f" name={node.name!r} db={node.db!r}"
            elif isinstance(node, exp.Func):
                extra = f" sql_name={node.sql_name()!r} name={getattr(node,'name',None)!r}"
            elif isinstance(node, exp.Anonymous):
                extra = f" name={node.name!r}"
            if isinstance(node, (exp.Table, exp.Func, exp.Anonymous, exp.Command,
                                 exp.Limit, exp.Select, exp.With, exp.CTE, exp.Into)):
                print(f"  - {label}{extra}")
    except Exception as e:
        print(f"  PARSE FAIL: {type(e).__name__}: {e}")

print("\n### exp 模块中包含 Table/Func/Format 的类:")
for n in sorted(dir(exp)):
    if any(k in n for k in ("TableFunc", "Table", "Format", "Into")):
        print(" ", n)
