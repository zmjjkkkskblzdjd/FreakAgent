from __future__ import annotations

import pytest
from fastapi import HTTPException

from allfusion_auth import deps
from allfusion_auth.deps import AuthUser, require_db_admin, require_kb_admin


def _user(role: str = "admin", scopes: str = "") -> AuthUser:
    return AuthUser(user_id=1, tenant_id=1, role=role, email="a@example.com",
                    username="a", session_id=1, scopes=scopes)


@pytest.mark.parametrize("scopes,db,kb", [
    ("", False, False),
    ("db_admin", True, False),
    ("kb_admin", False, True),
    ("db_admin,kb_admin", True, True),
    (" kb_admin , db_admin ", True, True),
])
def test_scope_matrix(scopes: str, db: bool, kb: bool):
    user = _user(scopes=scopes)
    assert user.is_owner is False
    assert user.is_db_admin is db
    assert user.is_kb_admin is kb


def test_owner_has_every_scope():
    user = _user(role="owner")
    assert (user.is_owner, user.is_db_admin, user.is_kb_admin) == (True, True, True)


def test_dev_mode_has_every_scope():
    user = AuthUser(user_id=0, tenant_id=0, role="member", email="dev@local",
                    username="dev", session_id=0, dev_mode=True)
    assert (user.is_db_admin, user.is_kb_admin) == (True, True)


def test_require_db_admin_rejects_kb_only(monkeypatch):
    user = _user(scopes="kb_admin")
    monkeypatch.setattr(deps, "require_user", lambda request: user)
    with pytest.raises(HTTPException) as exc:
        require_db_admin(object())
    assert exc.value.status_code == 403
    assert require_kb_admin(object()) is user


def test_require_kb_admin_rejects_db_only(monkeypatch):
    user = _user(scopes="db_admin")
    monkeypatch.setattr(deps, "require_user", lambda request: user)
    with pytest.raises(HTTPException) as exc:
        require_kb_admin(object())
    assert exc.value.status_code == 403
    assert require_db_admin(object()) is user
