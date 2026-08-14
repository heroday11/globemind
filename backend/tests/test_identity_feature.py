from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from api.features.identity import LoginRequest, authenticate_login
from api.routes import auth as auth_route
from api.services.auth import _hash_password


class _Query:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def filter(self, *_criteria: object) -> "_Query":
        return self

    def first(self) -> object | None:
        return self._row


class _Session:
    def __init__(self, row: object | None) -> None:
        self.row = row
        self.commit_calls = 0
        self.refresh_calls = 0

    def query(self, *_entities: object) -> _Query:
        return _Query(self.row)

    def commit(self) -> None:
        self.commit_calls += 1

    def refresh(self, _row: object) -> None:
        self.refresh_calls += 1


def _user(password: str, *, active: bool = True, role: str = "user") -> Any:
    return SimpleNamespace(
        id=7,
        username="alice",
        password_hash=_hash_password(password),
        is_active=active,
        role=role,
        updated_at=None,
        last_login_at=None,
    )


def test_auth_route_uses_identity_public_contract() -> None:
    assert auth_route.LoginRequest is LoginRequest
    assert auth_route.authenticate_login is authenticate_login


def test_identity_application_records_successful_database_login() -> None:
    row = _user("database-password-1")
    db = _Session(row)

    result = authenticate_login(db, "alice", "database-password-1")

    assert result is row
    assert db.commit_calls == 1
    assert db.refresh_calls == 1
    assert row.updated_at is not None
    assert row.last_login_at is not None


def test_identity_application_rejects_disabled_user_without_writing() -> None:
    db = _Session(_user("database-password-1", active=False))

    assert authenticate_login(db, "alice", "database-password-1") is None
    assert db.commit_calls == 0


def test_development_fallback_requires_matching_active_admin() -> None:
    row = _user("database-password-1", role="admin")
    db = _Session(row)

    result = authenticate_login(
        db,
        "alice",
        "development-password-1",
        development_admin=("alice", "development-password-1"),
    )

    assert result is row
    assert db.commit_calls == 0
