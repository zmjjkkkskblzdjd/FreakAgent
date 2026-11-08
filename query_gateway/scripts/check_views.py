# -*- coding: utf-8 -*-
import importlib
import json
import urllib.request
import urllib.parse

for mod in ("fastapi", "uvicorn", "sqlglot", "pydantic", "httpx"):
    try:
        m = importlib.import_module(mod)
        print(f"  [OK]   {mod} {getattr(m, '__version__', '')}")
    except ImportError:
        print(f"  [MISS] {mod}")

CH = "http://192.168.1.136:8123/"
def q(sql):
    url = CH + "?" + urllib.parse.urlencode({"query": sql + " FORMAT JSON"})
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())["data"]

rows = q("""SELECT count() AS lines, round(sum(sales_amount)/10000,1) AS gmv_wan,
                   round(sum(profit_amount)/10000,1) AS profit_wan
            FROM sales_dw.v_fact_sales_order_line
            WHERE order_status='completed' AND toYear(order_date)=2024""")
print("\n视图 v_fact_sales_order_line 2024 成交:", rows[0])

rows = q("""SELECT p.category, count() AS lines
            FROM sales_dw.v_fact_sales_order_line f
            INNER JOIN sales_dw.v_dim_product p ON f.product_id=p.product_id
            WHERE f.order_status='completed' AND toYear(f.order_date)=2024
            GROUP BY p.category ORDER BY lines DESC LIMIT 3""")
print("视图 JOIN 测试:")
for r in rows:
    print(" ", r)
