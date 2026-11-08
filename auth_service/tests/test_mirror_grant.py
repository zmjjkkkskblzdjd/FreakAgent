# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from auth_service.app import mirror  # noqa: E402


def test_grant_reader_grants_mirror_database(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(mirror, "_CH_READER", "da_readonly")
    monkeypatch.setattr(mirror, "_CH_USER", "")
    monkeypatch.setattr(mirror, "_ch", lambda sql, timeout=30: seen.append(sql) or "")

    mirror._grant_reader(7)

    assert seen == ["GRANT SELECT ON `af_ds_7`.* TO `da_readonly`"]


def test_grant_reader_skips_when_reader_is_ddl_account(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(mirror, "_CH_READER", "default")
    monkeypatch.setattr(mirror, "_CH_USER", "default")
    monkeypatch.setattr(mirror, "_ch", lambda sql, timeout=30: seen.append(sql) or "")

    mirror._grant_reader(7)

    assert seen == []


def test_grant_reader_failure_is_reported(monkeypatch):
    def _boom(sql, timeout=30):
        raise mirror.MirrorError("clickhouse_error", 502, "ACCESS_DENIED")

    monkeypatch.setattr(mirror, "_CH_READER", "da_readonly")
    monkeypatch.setattr(mirror, "_CH_USER", "")
    monkeypatch.setattr(mirror, "_ch", _boom)

    with pytest.raises(mirror.MirrorError) as ei:
        mirror._grant_reader(7)
    assert ei.value.code == "clickhouse_grant_failed"


def test_grant_reader_rejects_illegal_identifier(monkeypatch):
    monkeypatch.setattr(mirror, "_CH_READER", "da readonly; DROP")
    monkeypatch.setattr(mirror, "_CH_USER", "")
    with pytest.raises(mirror.MirrorError) as ei:
        mirror._grant_reader(7)
    assert ei.value.code == "invalid_ch_reader"
