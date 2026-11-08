# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import source_client                                  # noqa: E402
from app.write_journal import WriteJournal, build_inverse       # noqa: E402

DB = "sales_biz"


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.description = None
        self._rows = []
        self._one = None
        self.rowcount = 0
        self.lastrowid = self.db.lastrowid

    def execute(self, sql, params=None):
        s = sql.lower().strip()
        self.db.sqls.append(sql)
        if "information_schema" in s:
            self.description = [("COLUMN_NAME",)]
            if "extra" in s:                       
                self._rows = [(self.db.auto_col,)] if self.db.auto_col else []
            else:                                  
                self._rows = [(c,) for c in self.db.pk_cols]
            self._one = self._rows[0] if self._rows else None
        elif s.startswith("select"):
            self.description = [(c, None) for c in self.db.columns]
            self._rows = list(self.db.select_rows)
            self._one = self._rows[0] if self._rows else None
        else:
            self.rowcount = 1
        return self.rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDB:
    def __init__(self, rows=None, pk_cols=("id",), auto_col=None, lastrowid=101):
        self.columns = ["id", "amount"]
        self.pk_cols = list(pk_cols)
        self.auto_col = auto_col
        self.lastrowid = lastrowid
        self.select_rows = list(rows or [(1, 100)])
        self.sqls = []
        self.committed = False
        self.rolled_back = False

    def autocommit(self, _flag):
        return None

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _drive(monkeypatch, db):
    monkeypatch.setattr(source_client, "_run_with_timeout",
                        lambda _ds, _pw, fn, _timeout: fn(db))



def test_build_inverse_update():
    stmts = build_inverse(database=DB, table="t", operation="update", pk_cols=["id"],
                          before_rows=[{"id": 1, "amount": 50}], after_rows=[])
    assert stmts == ["UPDATE `sales_biz`.`t` SET `id`=1, `amount`=50 WHERE `id`=1"]


def test_build_inverse_delete_restores_rows():
    stmts = build_inverse(database=DB, table="t", operation="delete", pk_cols=["id"],
                          before_rows=[{"id": 1, "amount": 50}], after_rows=[])
    assert stmts[0].startswith("INSERT INTO `sales_biz`.`t` (`id`, `amount`) VALUES")


def test_build_inverse_insert_deletes_by_pk():
    stmts = build_inverse(database=DB, table="t", operation="insert", pk_cols=["id"],
                          before_rows=[], after_rows=[{"id": 1, "amount": 100}])
    assert stmts == ["DELETE FROM `sales_biz`.`t` WHERE `id`=1"]


def test_build_inverse_escapes_strings():
    stmts = build_inverse(database=DB, table="t", operation="update", pk_cols=["id"],
                          before_rows=[{"id": 1, "name": "O'Reilly"}], after_rows=[])
    assert "'O''Reilly'" in stmts[0]



def test_journal_roundtrip_and_scope(tmp_path):
    j = WriteJournal(tmp_path / "wj.db")
    info = j.record(audit_id="a1", tenant_id=1, user_id=2, datasource_id=3,
                    database=DB, table="t", operation="update", sql="UPDATE t SET x=1 WHERE id=1",
                    preview_hash="h1", pk_cols=["id"],
                    before_rows=[{"id": 1, "x": 0}], after_rows=[{"id": 1, "x": 1}])
    assert info["inverse_rows"] == 1
    rec = j.get("a1", tenant_id=1, user_id=2)
    assert rec["status"] == "committed" and rec["pk_cols"] == ["id"]
    assert rec["undo_deadline"] > rec["committed_at"]
    j.mark_undone("a1")
    assert j.get("a1", tenant_id=1, user_id=2)["status"] == "undone"
    assert j.get("a1", tenant_id=9, user_id=9) is None       



def test_transactional_preview_update_rolls_back(monkeypatch):
    db = FakeDB(rows=[(1, 100)])
    _drive(monkeypatch, db)
    data = source_client.transactional_preview(
        {"database_name": DB}, "pw",
        "UPDATE `sales_biz`.`t` SET amount=100 WHERE id=1")
    assert db.rolled_back is True and db.committed is False
    assert data["operation"] == "update" and data["pk_cols"] == ["id"]
    assert data["affected_rows"] == 1
    assert data["before_rows"][0]["amount"] == 100
    assert data["preview_hash"]


def test_insert_without_explicit_pk_is_rejected(monkeypatch):
    db = FakeDB()
    _drive(monkeypatch, db)
    with pytest.raises(source_client.WritePreviewError) as exc:
        source_client.transactional_preview(
            {"database_name": DB}, "pw", "INSERT INTO `sales_biz`.`t` VALUES (1, 100)")
    assert exc.value.code == "insert_requires_pk"


def test_insert_auto_pk_preview_derives_pk(monkeypatch):
    db = FakeDB(rows=[(101, 100)], auto_col="id", lastrowid=101)
    _drive(monkeypatch, db)
    data = source_client.transactional_preview(
        {"database_name": DB}, "pw",
        "INSERT INTO `sales_biz`.`t` (`amount`) VALUES (100)")
    assert data["auto_pk"] is True and data["auto_pk_col"] == "id"
    assert data["pk_literals"] == {"id": "101"}
    assert data["after_rows"][0]["id"] == 101
    assert db.rolled_back is True and db.committed is False


def test_insert_multi_row_auto_pk_is_rejected(monkeypatch):
    db = FakeDB(auto_col="id")
    _drive(monkeypatch, db)
    with pytest.raises(source_client.WritePreviewError) as exc:
        source_client.transactional_preview(
            {"database_name": DB}, "pw",
            "INSERT INTO `sales_biz`.`t` (`amount`) VALUES (100), (200)")
    assert exc.value.code == "insert_requires_pk"


def test_insert_auto_pk_requires_other_pk_literals(monkeypatch):
    db = FakeDB(pk_cols=("tenant_id", "id"), auto_col="id")
    _drive(monkeypatch, db)
    with pytest.raises(source_client.WritePreviewError) as exc:
        source_client.transactional_preview(
            {"database_name": DB}, "pw",
            "INSERT INTO `sales_biz`.`t` (`amount`) VALUES (100)")
    assert exc.value.code == "insert_requires_pk"
    assert not any(s.lower().startswith("insert into") for s in db.sqls)


def test_execute_insert_capture_uses_commit_time_pk(monkeypatch):
    db = FakeDB(rows=[(102, 100)], auto_col="id", lastrowid=102)
    _drive(monkeypatch, db)
    data = {"database": DB, "table": "t", "pk_cols": ["id"], "auto_pk_col": "id",
            "pk_literals": {"id": "101"}}          
    out = source_client.execute_insert_capture(
        {"database_name": DB}, "pw",
        "INSERT INTO `sales_biz`.`t` (`amount`) VALUES (100)", data)
    assert out["affected_rows"] == 1 and out["pk_literals"] == {"id": "102"}
    assert out["after_rows"][0]["id"] == 102
    assert db.committed is True


def test_table_without_primary_key_is_rejected(monkeypatch):
    db = FakeDB(pk_cols=[])
    _drive(monkeypatch, db)
    with pytest.raises(source_client.WritePreviewError) as exc:
        source_client.transactional_preview(
            {"database_name": DB}, "pw",
            "UPDATE `sales_biz`.`t` SET amount=1 WHERE id=1")
    assert exc.value.code == "write_no_primary_key"


def test_preview_recheck_detects_concurrent_change(monkeypatch):
    db = FakeDB(rows=[(1, 999)])              
    _drive(monkeypatch, db)
    data = {"operation": "update", "database": DB, "table": "t", "pk_cols": ["id"],
            "before_rows": [{"id": 1, "amount": 100}], "after_rows": []}
    with pytest.raises(source_client.WritePreviewError) as exc:
        source_client.check_preview_unchanged({"database_name": DB}, "pw", data)
    assert exc.value.code == "preview_stale"


def test_preview_recheck_passes_when_unchanged(monkeypatch):
    db = FakeDB(rows=[(1, 100)])
    _drive(monkeypatch, db)
    data = {"operation": "update", "database": DB, "table": "t", "pk_cols": ["id"],
            "before_rows": [{"id": 1, "amount": 100}], "after_rows": []}
    source_client.check_preview_unchanged({"database_name": DB}, "pw", data)



def _record():
    return {"database": DB, "table_name": "t", "operation": "update",
            "pk_cols": ["id"], "after_rows": [{"id": 1, "amount": 100}],
            "inverse_sql": ["UPDATE `sales_biz`.`t` SET `amount`=50 WHERE `id`=1"]}


def test_undo_conflicts_when_row_changed_after_commit(monkeypatch):
    db = FakeDB(rows=[(1, 999)])
    _drive(monkeypatch, db)
    with pytest.raises(source_client.UndoConflict):
        source_client.undo_write({"database_name": DB}, "pw", _record())
    assert db.committed is False


def test_undo_success_executes_inverse(monkeypatch):
    db = FakeDB(rows=[(1, 100)])
    _drive(monkeypatch, db)
    done = source_client.undo_write({"database_name": DB}, "pw", _record())
    assert done == 1 and db.committed is True
    assert any("SET `amount`=50" in s for s in db.sqls)
