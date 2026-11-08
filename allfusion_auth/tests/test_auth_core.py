from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from allfusion_auth import crypto, redact, security



@pytest.fixture()
def fernet_key(monkeypatch):
    key = crypto.generate_fernet_key()
    monkeypatch.setenv("AF_SECRET_KEY", key)
    monkeypatch.delenv("AF_SECRET_KEY_OLD", raising=False)
    return key


def test_encrypt_decrypt_roundtrip(fernet_key):
    cipher, version = crypto.encrypt_secret("sk-abcdef123456")
    assert isinstance(cipher, bytes) and cipher != b"sk-abcdef123456"
    assert version >= 1
    assert crypto.decrypt_secret(cipher, version) == "sk-abcdef123456"


def test_encrypt_none_is_empty(fernet_key):
    assert crypto.encrypt_secret(None)[0] == b""
    assert crypto.decrypt_secret(b"") == ""
    assert crypto.decrypt_secret(None) == ""


def test_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("AF_SECRET_KEY", raising=False)
    with pytest.raises(crypto.CryptoConfigError):
        crypto.encrypt_secret("x")


def test_rotation_old_key_still_decrypts(monkeypatch):
    old_key = crypto.generate_fernet_key()
    monkeypatch.setenv("AF_SECRET_KEY", old_key)
    monkeypatch.delenv("AF_SECRET_KEY_OLD", raising=False)
    cipher, _ = crypto.encrypt_secret("legacy-secret")

    new_key = crypto.generate_fernet_key()
    monkeypatch.setenv("AF_SECRET_KEY", new_key)
    monkeypatch.setenv("AF_SECRET_KEY_OLD", old_key)

    assert crypto.decrypt_secret(cipher) == "legacy-secret"
    reencrypted, _ = crypto.rotate_secret(cipher)
    monkeypatch.delenv("AF_SECRET_KEY_OLD", raising=False)
    assert crypto.decrypt_secret(reencrypted) == "legacy-secret"


def test_tampered_cipher_raises(fernet_key):
    cipher, _ = crypto.encrypt_secret("secret")
    with pytest.raises(crypto.SecretDecryptError):
        crypto.decrypt_secret(cipher[:-4] + b"AAAA")


def test_mask_and_fingerprint():
    assert crypto.mask_secret("") == ""
    assert crypto.mask_secret("short") == "*****"
    assert crypto.mask_secret("sk-1234567890abcd") == "sk-…abcd"
    assert crypto.key_fingerprint("same") == crypto.key_fingerprint("same")
    assert crypto.key_fingerprint("a") != crypto.key_fingerprint("b")



def test_password_hash_and_verify():
    h = security.hash_password("Abcd1234!")
    assert h.startswith("$argon2")
    assert security.verify_password(h, "Abcd1234!")
    assert not security.verify_password(h, "wrong-pass")
    assert not security.verify_password("not-a-hash", "Abcd1234!")
    assert not security.needs_rehash(h)


@pytest.mark.parametrize("pwd,ok", [
    ("short1!", False),          
    ("abcdefgh", False),         
    ("abcdefg1", True),
    ("ABCDEFG1", True),
    ("abcdEFG!", True),
])
def test_password_problem(pwd, ok):
    assert (security.password_problem(pwd) is None) is ok


def test_password_rejects_username_and_email():
    assert security.password_problem("alice123", username="alice123") is not None
    assert security.password_problem("a@b.com1", email="A@B.COM1") is not None


def test_session_token_pairs():
    token, digest = security.new_session_token()
    assert token != digest
    assert security.hash_token(token) == digest
    assert len(digest) == 64
    assert security.new_session_token()[0] != token



def test_redact_nested_dict():
    payload = {
        "api_key": "sk-live-123",
        "model": "glm-4",
        "nested": {"password": "p@ss", "items": [{"Authorization": "Bearer abc.def"}]},
        "dsn": "mysql+pymysql://u:p@host/db",
    }
    out = redact.redact(payload)
    assert out["api_key"] == redact.MASK
    assert out["model"] == "glm-4"
    assert out["nested"]["password"] == redact.MASK
    assert out["nested"]["items"][0]["Authorization"] == redact.MASK
    assert out["dsn"] == redact.MASK


def test_redact_text_patterns():
    text = ("connect mysql://user:pass@db:3306/x?api_key=abc&limit=10 "
            "with header Authorization: Bearer eyJhbGciOi.abc-123")
    out = redact.redact_text(text)
    assert "user:pass@" not in out
    assert "api_key=abc" not in out
    assert "eyJhbGciOi.abc-123" not in out
    assert "mysql://***@db:3306/x" in out


def test_redact_depth_guard():
    obj = {}
    cur = obj
    for _ in range(15):
        nxt = {}
        cur["next"] = nxt
        cur = nxt
    assert redact.redact(obj) is not None   



@pytest.fixture()
def conn():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE login_attempt ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " tenant_id INTEGER, account TEXT NOT NULL, ip TEXT NOT NULL,"
            " ok INTEGER NOT NULL, created_at DATETIME NOT NULL)"))
        yield c
    engine.dispose()


def test_lock_after_five_failures(conn):
    now = datetime.now()
    for i in range(security.LOCK_FAILURES - 1):
        security.record_login_attempt(conn, tenant_id=1, account="a@x.com",
                                      ip="1.1.1.1", ok=False, now=now)
    assert not security.is_login_locked(conn, "a@x.com", "1.1.1.1", now)
    security.record_login_attempt(conn, tenant_id=1, account="a@x.com",
                                  ip="1.1.1.1", ok=False, now=now)
    assert security.is_login_locked(conn, "a@x.com", "1.1.1.1", now)
    assert not security.is_login_locked(conn, "a@x.com", "2.2.2.2", now)
    assert not security.is_login_locked(conn, "b@x.com", "1.1.1.1", now)


def test_success_resets_window(conn):
    t0 = datetime.now() - timedelta(minutes=5)
    for _ in range(security.LOCK_FAILURES):
        security.record_login_attempt(conn, tenant_id=1, account="a@x.com",
                                      ip="1.1.1.1", ok=False, now=t0)
    assert security.is_login_locked(conn, "a@x.com", "1.1.1.1", t0)
    security.record_login_attempt(conn, tenant_id=1, account="a@x.com",
                                  ip="1.1.1.1", ok=True, now=datetime.now())
    assert not security.is_login_locked(conn, "a@x.com", "1.1.1.1", datetime.now())


def test_old_failures_outside_window_ignored(conn):
    old = datetime.now() - timedelta(minutes=security.LOCK_MINUTES + 5)
    for _ in range(security.LOCK_FAILURES):
        security.record_login_attempt(conn, tenant_id=1, account="a@x.com",
                                      ip="1.1.1.1", ok=False, now=old)
    assert not security.is_login_locked(conn, "a@x.com", "1.1.1.1", datetime.now())
