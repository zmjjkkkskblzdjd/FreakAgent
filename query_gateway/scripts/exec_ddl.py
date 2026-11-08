import urllib.request
import urllib.parse
import json
import re
from pathlib import Path

CH_HTTP = "http://192.168.1.136:8123/"
DDL_FILE = Path(__file__).parent.parent / "sql" / "01_sales_dw_ddl.sql"


def ch_execute(sql: str):
    sql = sql.strip()
    if not sql:
        return
    req = urllib.request.Request(CH_HTTP, data=sql.encode("utf-8"),
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace").strip()
    first_line = [l for l in sql.split("\n") if l.strip()][0][:80]
    print(f"  OK  {first_line}")
    if body:
        print(f"      -> {body}")


def split_statements(text: str):
    lines = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("--"):
            continue
        lines.append(line)
    text = "\n".join(lines)
    return [s for s in re.split(r";\s*(?:\n|$)", text) if s.strip()]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        DDL_FILE = Path(sys.argv[1])
    sql_text = DDL_FILE.read_text(encoding="utf-8")
    stmts = split_statements(sql_text)
    print(f"共 {len(stmts)} 条语句，发往 {CH_HTTP}")
    for i, stmt in enumerate(stmts, 1):
        print(f"[{i}/{len(stmts)}]", end="")
        ch_execute(stmt)

    verify = ("SELECT name, engine FROM system.tables "
              "WHERE database='sales_dw' ORDER BY name FORMAT JSON")
    req = urllib.request.Request(CH_HTTP + "?query=" + urllib.parse.quote(verify),
                                 headers={"Content-Type": "text/plain"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    print("\nsales_dw 表清单:")
    for row in data["data"]:
        print(f"  {row['name']:<26} {row['engine']}")
