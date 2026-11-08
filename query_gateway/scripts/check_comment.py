# -*- coding: utf-8 -*-
import json
import urllib.request
import urllib.parse
from pathlib import Path

CH = "http://192.168.1.136:8123/"
def q(sql):
    url = CH + "?" + urllib.parse.urlencode({"query": sql + " FORMAT JSON"})
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())["data"]

print("### system.columns comment 实测")
rows = q("SELECT name, type, comment FROM system.columns "
         "WHERE database='sales_dw' AND table='fact_sales_order_line' ORDER BY position LIMIT 5")
for r in rows:
    print(f"  {r['name']:<16}{r['type']:<22} comment={r['comment']!r}")

print("\n### 尝试 COMMENT ON COLUMN")
try:
    import urllib.request as u
    req = u.Request(CH, data="COMMENT ON COLUMN sales_dw.fact_sales_order_line.sales_amount IS '成交金额，元'".encode("utf-8"),
                    headers={"Content-Type": "text/plain; charset=utf-8"}, method="POST")
    u.urlopen(req, timeout=10).read()
    rows = q("SELECT comment FROM system.columns "
             "WHERE database='sales_dw' AND table='fact_sales_order_line' AND name='sales_amount'")
    print("  COMMENT ON COLUMN 后:", rows)
except Exception as e:
    print("  不支持:", e)

print("\n### 审计日志（最后 3 条）")
log = Path(__file__).resolve().parent.parent / "logs" / "audit.jsonl"
if log.exists():
    lines = log.read_text(encoding="utf-8").strip().split("\n")
    for line in lines[-3:]:
        d = json.loads(line)
        print(f"  {d['ts']} ok={d['ok']} rejected_by={d.get('rejected_by','-')} "
              f"rows={d.get('row_count','-')} elapsed={d.get('elapsed_ms','-')}ms")
