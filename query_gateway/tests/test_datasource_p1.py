# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import chclient, config, main as gateway, source_client  # noqa: E402
from app.catalog import catalog  # noqa: E402
from app.guard import GuardError, check_sql  # noqa: E402

_DS = {"id": 7, "tenant_id": 1, "name": "shop", "db_type": "mysql",
       "host": "127.0.0.1", "port": 3306, "database_name": "shop",
       "schema_name": None, "username": "ro", "password_cipher": b"",
       "key_version": 1, "max_rows": 200, "query_timeout_sec": 3}

_SQL = "SELECT id FROM shop.t1"



def test_rewrite_source_tables_to_mirror_views():
    tree, names = gateway._parse_mysql_tables(
        "SELECT a.id FROM shop.t1 a JOIN shop.t2 b ON a.id = b.id", "shop")
    assert names == ["t1", "t2"]
    sql = gateway._rewrite_for_ch(tree, "af_ds_7", {"t1", "t2"})
    assert "af_ds_7.v_t1" in sql and "af_ds_7.v_t2" in sql
    assert "shop." not in sql


def test_rewrite_accepts_view_name_and_keeps_it():
    tree, names = gateway._parse_mysql_tables("SELECT id FROM shop.v_t1", "shop")
    assert names == ["v_t1"]
    sql = gateway._rewrite_for_ch(tree, "af_ds_7", {"t1"})
    assert "af_ds_7.v_t1" in sql and "v_v_" not in sql


def test_rewrite_rejects_foreign_database():
    with pytest.raises(GuardError):
        gateway._parse_mysql_tables("SELECT id FROM other.t1", "shop")


def test_rewrite_rejects_multi_statement():
    with pytest.raises(GuardError):
        gateway._parse_mysql_tables("SELECT 1; SELECT 2", "shop")



def test_guard_accepts_af_ds_views_only(monkeypatch):
    monkeypatch.setattr(catalog, "allowed_names", lambda db: {"v_t1"})
    res = check_sql("SELECT id FROM af_ds_7.v_t1", allowed_databases={"af_ds_7"},
                    required_prefix="shop")
    assert "v_t1" in res.sql
    with pytest.raises(GuardError):          
        check_sql("SELECT id FROM af_ds_7.t1", allowed_databases={"af_ds_7"})
    with pytest.raises(GuardError):          
        check_sql("SELECT id FROM sales_dw.t1", allowed_databases={"af_ds_7"})


def test_direct_sql_view_alias_fallback():
    chk = source_client.check_direct_sql("SELECT id FROM shop.v_t1", "shop",
                                         aliases={"v_t1": "t1"})
    assert chk.table == "t1" and "v_t1" not in chk.sql



@pytest.fixture
def env(monkeypatch):
    calls = {"ch": 0, "src": 0, "sql": []}
    state = {"phases": {"t1": "cdc"}, "selected": {"t1"},
             "mirror": {"status": "running", "lag_ms": 120, "ch_database": "af_ds_7"},
             "ch": "ok", "src": "ok"}
    monkeypatch.setattr(config, "GATEWAY_SECRET", "")
    monkeypatch.setattr(gateway.platformdb, "load_datasource",
                        lambda dsid, tenant_id=None: dict(_DS))
    monkeypatch.setattr(gateway.platformdb, "password_of", lambda ds: "pwd")
    monkeypatch.setattr(gateway.platformdb, "known_tables", lambda dsid: {"t1"})
    monkeypatch.setattr(gateway.platformdb, "selected_tables",
                        lambda dsid: set(state["selected"]))
    monkeypatch.setattr(gateway.platformdb, "table_phases",
                        lambda dsid: dict(state["phases"]))
    monkeypatch.setattr(gateway.platformdb, "mirror_state",
                        lambda dsid: dict(state["mirror"]))
    monkeypatch.setattr(gateway.catalog, "allowed_names", lambda db: {"v_t1"})
    monkeypatch.setattr(gateway.audit, "log", lambda **kw: None)
    gateway.breaker.reset()

    def _ch(sql, timeout_ms=None):
        calls["ch"] += 1
        if state["ch"] == "down":
            raise chclient.ClickHouseError(
                f"无法连接 ClickHouse({config.CH_HTTP}): refused")
        return {"meta": [{"name": "id", "type": "Int32"}],
                "data": [{"id": 1}], "rows": 1,
                "statistics": {"elapsed": 0.01}}

    def _src(ds, pwd, sql, timeout_ms=None):
        calls["src"] += 1
        calls["sql"].append(sql)
        if state["src"] == "down":
            raise source_client.SourceError("无法连接源库: refused", 2003)
        return {"columns": [{"name": "id", "type": "INT"}],
                "rows": [[1]], "truncated": False}

    monkeypatch.setattr(gateway.chclient, "query_json", _ch)
    monkeypatch.setattr(gateway.source_client, "query", _src)

    def set_route(**kw):
        state.update(kw)

    return calls, set_route


def test_query_routes_cdc_table_to_clickhouse(env):
    calls, _ = env
    resp = TestClient(gateway.app).post(
        "/query", json={"sql": _SQL, "datasource_id": 7, "tenant_id": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["source"] == "clickhouse"
    assert body["lag_ms"] == 120 and body["degraded"] is False
    assert body["source_table_states"] == {"t1": "cdc"}
    assert "af_ds_7.v_t1" in body["executed_sql"]
    assert calls["ch"] == 1 and calls["src"] == 0


def test_query_non_cdc_phase_falls_back_to_source(env):
    calls, set_route = env
    set_route(phases={"t1": "failed"})
    resp = TestClient(gateway.app).post(
        "/query", json={"sql": _SQL, "datasource_id": 7, "tenant_id": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "mysql-direct" and body["degraded"] is True
    assert "t1" in body["mirror_note"] and "failed" in body["mirror_note"]
    assert calls["ch"] == 0 and calls["src"] == 1


def test_query_large_staleness_is_not_a_fault(env):
    calls, set_route = env
    set_route(mirror={"status": "running", "lag_ms": 9000, "ch_database": "af_ds_7"})
    resp = TestClient(gateway.app).post(
        "/query", json={"sql": _SQL, "datasource_id": 7, "tenant_id": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "clickhouse" and body["lag_ms"] == 9000
    assert "仅供参考" in body["mirror_note"]
    assert calls["src"] == 0


def test_query_mirror_failed_never_returns_clickhouse(env):
    calls, set_route = env
    set_route(mirror={"status": "failed", "lag_ms": None, "ch_database": "af_ds_7"})
    resp = TestClient(gateway.app).post(
        "/query", json={"sql": _SQL, "datasource_id": 7, "tenant_id": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "mysql-direct" and "failed" in body["mirror_note"]
    assert calls["ch"] == 0 and calls["src"] == 1


def test_query_clickhouse_unreachable_is_503_without_source(env):
    calls, set_route = env
    set_route(ch="down")
    resp = TestClient(gateway.app).post(
        "/query", json={"sql": _SQL, "datasource_id": 7, "tenant_id": 1})
    assert resp.status_code == 503
    assert resp.json()["detail"]["error_code"] == "analytics_unavailable"
    assert calls["src"] == 0          


def test_query_fresh_bypasses_clickhouse(env):
    calls, _ = env
    resp = TestClient(gateway.app).post(
        "/query", json={"sql": _SQL, "datasource_id": 7, "tenant_id": 1,
                        "fresh": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "mysql-direct" and "fresh" in body["mirror_note"]
    assert calls["ch"] == 0 and calls["src"] == 1


def test_query_mirror_not_running_falls_back_to_source(env):
    calls, set_route = env
    set_route(mirror={})
    resp = TestClient(gateway.app).post(
        "/query", json={"sql": _SQL, "datasource_id": 7, "tenant_id": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "mysql-direct"
    assert calls["ch"] == 0 and calls["src"] == 1


def test_query_fallback_when_source_down_is_503(env):
    calls, set_route = env
    set_route(phases={"t1": "drift"}, src="down")
    resp = TestClient(gateway.app).post(
        "/query", json={"sql": _SQL, "datasource_id": 7, "tenant_id": 1})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error_code"] == "mirror_table_unavailable"
    assert detail["source_table_states"] == {"t1": "drift"}
    assert calls["ch"] == 0


def test_query_rejects_unknown_table(env):
    calls, _ = env
    resp = TestClient(gateway.app).post(
        "/query", json={"sql": "SELECT id FROM shop.nope",
                        "datasource_id": 7, "tenant_id": 1})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "unknown_table"
    assert calls["ch"] == 0 and calls["src"] == 0
