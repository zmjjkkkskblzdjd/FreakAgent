# -*- coding: utf-8 -*-
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, internal_auth, main as gateway, source_client  # noqa: E402
from app.breaker import BreakerRegistry  # noqa: E402
from app.guard import GuardError  # noqa: E402
from app.preview import PreviewError, PreviewStore  # noqa: E402



def test_direct_sql_forces_limit_200():
    chk = source_client.check_direct_sql("SELECT id FROM t1", "shop")
    assert chk.table == "t1"
    assert "LIMIT 200" in chk.sql and chk.rewritten


def test_direct_sql_shrinks_oversized_limit():
    chk = source_client.check_direct_sql("SELECT id FROM t1 LIMIT 9999", "shop")
    assert "LIMIT 200" in chk.sql and chk.rewritten


def test_direct_sql_keeps_small_limit():
    chk = source_client.check_direct_sql("SELECT id FROM t1 LIMIT 10", "shop")
    assert "LIMIT 10" in chk.sql and not chk.rewritten


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM t1",
    "SELECT a FROM t1 GROUP BY a",
    "SELECT DISTINCT a FROM t1",
    "SELECT * FROM t1 JOIN t2 ON t1.id = t2.id",
    "SELECT * FROM t1, t2",
    "SELECT * FROM (SELECT id FROM t1) x",
    "SELECT * FROM t1 UNION SELECT * FROM t2",
    "SELECT id FROM t1 FOR UPDATE",
])
def test_direct_sql_rejects_heavy_forms(sql):
    with pytest.raises(GuardError):
        source_client.check_direct_sql(sql, "shop")


def test_direct_sql_rejects_foreign_database():
    with pytest.raises(GuardError):
        source_client.check_direct_sql("SELECT id FROM other.t1", "shop")



def test_breaker_opens_then_half_open_single_probe():
    b = BreakerRegistry(fails=3, open_sec=0.05, max_open_sec=0.2)
    for _ in range(3):
        b.failure(1)
    allowed, state = b.allow(1)
    assert not allowed and state == "open"
    time.sleep(0.15)
    allowed, state = b.allow(1)
    assert allowed and state == "half_open"
    allowed2, _ = b.allow(1)               
    assert not allowed2
    b.success(1)
    allowed3, state3 = b.allow(1)
    assert allowed3 and state3 == "closed"


def test_breaker_half_open_failure_doubles_backoff():
    b = BreakerRegistry(fails=1, open_sec=0.05, max_open_sec=10)
    b.failure(1)
    time.sleep(0.15)
    assert b.allow(1)[1] == "half_open"
    b.failure(1)                            
    assert b.allow(1) == (False, "open")



def test_preview_token_is_single_use():
    store = PreviewStore(ttl=60, rate_per_min=100)
    token = store.issue(tenant_id=1, user_id=2, datasource_id=3,
                        sql="INSERT INTO t1 (id) VALUES (1)", estimated_rows=1)
    entry = store.consume(token, tenant_id=1, user_id=2, datasource_id=3,
                          sql="INSERT INTO t1 (id) VALUES (1)")
    assert entry.datasource_id == 3
    with pytest.raises(PreviewError) as ei:
        store.consume(token, tenant_id=1, user_id=2, datasource_id=3,
                      sql="INSERT INTO t1 (id) VALUES (1)")
    assert ei.value.code == "preview_token_used_or_expired"


def test_preview_token_burned_even_on_mismatch():
    store = PreviewStore(ttl=60, rate_per_min=100)
    token = store.issue(tenant_id=1, user_id=2, datasource_id=3,
                        sql="DELETE FROM t1 WHERE id = 1", estimated_rows=1)
    with pytest.raises(PreviewError) as ei:
        store.consume(token, tenant_id=1, user_id=2, datasource_id=3,
                      sql="DELETE FROM t1 WHERE id = 2")
    assert ei.value.code == "preview_token_mismatch"
    with pytest.raises(PreviewError) as ei2:      
        store.consume(token, tenant_id=1, user_id=2, datasource_id=3,
                      sql="DELETE FROM t1 WHERE id = 1")
    assert ei2.value.code == "preview_token_used_or_expired"


def test_preview_token_expires():
    store = PreviewStore(ttl=0.01, rate_per_min=100)
    token = store.issue(tenant_id=1, user_id=2, datasource_id=3,
                        sql="INSERT INTO t1 (id) VALUES (1)", estimated_rows=1)
    time.sleep(0.02)
    with pytest.raises(PreviewError) as ei:
        store.consume(token, tenant_id=1, user_id=2, datasource_id=3,
                      sql="INSERT INTO t1 (id) VALUES (1)")
    assert ei.value.code == "preview_token_used_or_expired"


def test_preview_issue_rate_limited():
    store = PreviewStore(ttl=60, rate_per_min=2)
    for _ in range(2):
        store.issue(tenant_id=1, user_id=2, datasource_id=3,
                    sql="INSERT INTO t1 (id) VALUES (1)", estimated_rows=1)
    with pytest.raises(PreviewError) as ei:
        store.issue(tenant_id=1, user_id=2, datasource_id=3,
                    sql="INSERT INTO t1 (id) VALUES (1)", estimated_rows=1)
    assert ei.value.code == "preview_rate_limited" and ei.value.status == 429



def test_signature_required_when_secret_configured(monkeypatch):
    monkeypatch.setattr(config, "GATEWAY_SECRET", "s3cret")
    client = TestClient(gateway.app)
    assert client.get("/catalog/databases").status_code == 401

    body = b""
    headers = internal_auth.signed_headers("s3cret", "/catalog/databases", body)
    assert client.get("/catalog/databases", headers=headers).status_code == 200

    bad = internal_auth.signed_headers("s3cret", "/catalog/databases", body,
                                       ts=int(time.time()) - 10_000)
    resp = client.get("/catalog/databases", headers=bad)
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "signature_expired"


def test_signature_exempt_when_secret_empty(monkeypatch):
    monkeypatch.setattr(config, "GATEWAY_SECRET", "")
    assert TestClient(gateway.app).get("/catalog/databases").status_code == 200



_DS = {"id": 7, "tenant_id": 1, "name": "shop", "db_type": "mysql",
       "host": "127.0.0.1", "port": 3306, "database_name": "shop",
       "schema_name": None, "username": "ro", "password_cipher": b"",
       "key_version": 1, "max_rows": 200, "query_timeout_sec": 3}

_INSERT_SQL = "INSERT INTO shop.t1 (id) VALUES (1)"


@pytest.fixture
def ds_env(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(config, "GATEWAY_SECRET", "")
    monkeypatch.setattr(gateway.platformdb, "load_datasource",
                        lambda dsid, tenant_id=None: dict(_DS))
    monkeypatch.setattr(gateway.platformdb, "password_of", lambda ds: "pwd")
    monkeypatch.setattr(gateway.platformdb, "selected_tables", lambda dsid: {"t1"})
    monkeypatch.setattr(gateway.platformdb, "write_grants", lambda dsid, uid: set())
    monkeypatch.setattr(gateway, "_recount_ds", lambda ds, pwd, wc: 1)
    monkeypatch.setattr(gateway.source_client, "execute",
                        lambda ds, pwd, sql, timeout_ms=None: 1)
    monkeypatch.setattr(gateway.write_audit, "log",
                        lambda **kw: calls.append(kw) or "00000000-000")
    gateway.previews.clear()
    gateway.breaker.reset()
    return calls


def test_write_requires_preview_token(ds_env):
    client = TestClient(gateway.app)
    resp = client.post("/write", json={"sql": _INSERT_SQL, "datasource_id": 7,
                                       "tenant_id": 1, "user_id": 2, "role": "admin"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "preview_token_required"
    assert ds_env[-1]["status"] == "rejected"


def test_write_dry_run_then_execute_and_replay_rejected(ds_env):
    client = TestClient(gateway.app)
    payload = {"sql": _INSERT_SQL, "datasource_id": 7, "tenant_id": 1,
               "user_id": 2, "role": "admin"}
    dry = client.post("/write", json={**payload, "dry_run": True})
    assert dry.status_code == 200, dry.text
    token = dry.json()["preview_token"]
    assert token and dry.json()["source"] == "mysql"

    ok = client.post("/write", json={**payload, "preview_token": token})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert ok.json()["actual_rows"] == 1

    replay = client.post("/write", json={**payload, "preview_token": token})
    assert replay.status_code == 400
    assert replay.json()["detail"]["error_code"] == "preview_token_used_or_expired"


def test_write_member_without_grant_is_forbidden(ds_env):
    client = TestClient(gateway.app)
    resp = client.post("/write", json={"sql": _INSERT_SQL, "datasource_id": 7,
                                       "tenant_id": 1, "user_id": 2,
                                       "role": "member", "dry_run": True})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "write_forbidden"


def test_write_requires_actor(ds_env):
    client = TestClient(gateway.app)
    resp = client.post("/write", json={"sql": _INSERT_SQL, "datasource_id": 7,
                                       "dry_run": True})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "actor_required"
