# -*- coding: utf-8 -*-
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as gateway          # noqa: E402
from app.write_guard import WriteCheck   # noqa: E402


def _wc_stub() -> WriteCheck:
    return WriteCheck(operation="insert", target="clickhouse", database="sales_dw",
                      table="dim_region",
                      normalized_sql="INSERT INTO sales_dw.dim_region (region_id) VALUES (1)",
                      where_sql=None, set_sql=None, insert_rows=1)


def test_unhandled_exception_is_audited_then_500(monkeypatch):
    monkeypatch.setattr(gateway, "check_write_sql", lambda sql: _wc_stub())

    def _boom(_wc):
        raise ValueError("模拟 recount 键名假设崩塌")
    monkeypatch.setattr(gateway, "_recount", _boom)

    calls = []
    monkeypatch.setattr(gateway.write_audit, "log",
                        lambda **kw: calls.append(kw) or "00000000-000")

    client = TestClient(gateway.app, raise_server_exceptions=False)
    resp = client.post("/write",
                       json={"sql": "INSERT INTO sales_dw.dim_region (region_id) VALUES (1)"})

    assert resp.status_code == 500, f"期望 500，实际 {resp.status_code}: {resp.text[:200]}"
    err = [c for c in calls if c.get("status") == "error" and "unhandled:" in c.get("error", "")]
    assert len(err) == 1, f"兜底审计应恰好 1 条，实际调用：{calls}"
    assert "模拟 recount 键名假设崩塌" in err[0]["error"]
    assert err[0]["sql"].startswith("INSERT INTO sales_dw.dim_region")  
    assert err[0]["mutation_status"] == ""
    assert len(calls) == 1, f"不得重复审计，实际 {len(calls)} 条：{calls}"
