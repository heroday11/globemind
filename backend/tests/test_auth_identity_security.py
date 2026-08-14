from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from api.core import db as db_module  # noqa: E402
from api.routes import auth as auth_routes  # noqa: E402
from api.routes.auth import LoginRequest, RegisterRequest, login, register  # noqa: E402
from api.services.auth import (  # noqa: E402
    _hash_password,
    create_access_token,
    get_active_user_from_access_token,
    get_current_user_optional,
    get_current_user_required,
    get_user_from_access_token,
    is_admin_user,
    validate_active_user_identity,
)


class _Query:
    def __init__(self, row: object | None):
        self._row = row

    def filter(self, *_args: object, **_kwargs: object) -> "_Query":
        return self

    def first(self) -> object | None:
        return self._row


class _Session:
    def __init__(self, row: object | None):
        self._row = row
        self.commit_calls = 0

    def query(self, *_args: object, **_kwargs: object) -> _Query:
        return _Query(self._row)

    def commit(self) -> None:
        self.commit_calls += 1

    def refresh(self, _row: object) -> None:
        return None

    def close(self) -> None:
        return None


def _user_row(
    *,
    password_hash: str,
    username: str = "admin",
    active: bool = True,
    role: str = "admin",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        username=username,
        password_hash=password_hash,
        full_name="Test Admin",
        email="admin@example.test",
        phone="",
        created_at=None,
        updated_at=None,
        is_active=active,
        last_login_at=None,
        role=role,
        avatar_url="",
        api_keys=None,
        active_provider=None,
        default_model=None,
        base_url=None,
    )


class _RegistrationSession:
    def __init__(self) -> None:
        self.added = None
        self.commit_calls = 0

    def query(self, *_args: object, **_kwargs: object) -> _Query:
        return _Query(None)

    def add(self, row: object) -> None:
        self.added = row

    def commit(self) -> None:
        self.commit_calls += 1

    def refresh(self, row: object) -> None:
        row.id = 17

    def rollback(self) -> None:
        return None


def test_registration_minimizes_optional_identity_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_routes, "ensure_assistant_user_defaults", lambda _username: None)
    db = _RegistrationSession()

    response = register(
        RegisterRequest(
            username="minimal-user",
            email="minimal@example.test",
            password="secure-pass-123",
            confirm_password="secure-pass-123",
        ),
        db,
    )

    assert db.commit_calls == 1
    assert db.added.full_name is None
    assert db.added.phone is None
    assert response["user"]["full_name"] == ""
    assert response["user"]["phone"] == ""


def test_production_never_uses_environment_admin_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "environment-password-1")
    monkeypatch.setenv("ALLOW_DEV_ADMIN_PASSWORD_LOGIN", "1")
    db = _Session(_user_row(password_hash=_hash_password("database-password-1")))

    with pytest.raises(HTTPException) as exc_info:
        login(LoginRequest(username="admin", password="environment-password-1"), db)

    assert exc_info.value.status_code == 401
    assert db.commit_calls == 0


def test_development_admin_fallback_requires_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "environment-password-1")
    monkeypatch.delenv("ALLOW_DEV_ADMIN_PASSWORD_LOGIN", raising=False)
    db = _Session(_user_row(password_hash=_hash_password("database-password-1")))

    with pytest.raises(HTTPException) as exc_info:
        login(LoginRequest(username="admin", password="environment-password-1"), db)

    assert exc_info.value.status_code == 401


def test_development_admin_fallback_is_bound_to_active_database_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "environment-password-1")
    monkeypatch.setenv("ALLOW_DEV_ADMIN_PASSWORD_LOGIN", "1")
    row = _user_row(password_hash=_hash_password("database-password-1"))
    db = _Session(row)

    response = login(LoginRequest(username="admin", password="environment-password-1"), db)

    identity = get_user_from_access_token(response["access_token"])
    assert identity is not None
    assert identity["user_id"] == row.id
    assert identity["auth_version"]
    assert db.commit_calls == 0


@pytest.mark.parametrize(
    ("active", "role"),
    [(False, "admin"), (True, "user")],
)
def test_development_admin_fallback_rejects_non_admin_identity(
    monkeypatch: pytest.MonkeyPatch,
    active: bool,
    role: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "environment-password-1")
    monkeypatch.setenv("ALLOW_DEV_ADMIN_PASSWORD_LOGIN", "1")
    db = _Session(
        _user_row(
            password_hash=_hash_password("database-password-1"),
            active=active,
            role=role,
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        login(LoginRequest(username="admin", password="environment-password-1"), db)

    assert exc_info.value.status_code == 401


def test_active_identity_requires_matching_live_user_and_password_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_hash = "stored-password-hash-v1"
    row = _user_row(password_hash=password_hash, username="alice", role="user")
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _Session(row))
    token = create_access_token(row.id, row.username, password_hash=password_hash)

    identity = get_active_user_from_access_token(token)

    assert identity is not None
    assert identity["user_id"] == row.id
    assert identity["username"] == "alice"
    assert identity["role"] == "user"


def test_optional_bearer_resolves_active_identity_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_hash = "stored-password-hash-v1"
    row = _user_row(password_hash=password_hash, username="alice", role="admin")
    sessions: list[_Session] = []

    def session_factory() -> _Session:
        session = _Session(row)
        sessions.append(session)
        return session

    monkeypatch.setattr(db_module, "SessionLocal", session_factory)
    token = create_access_token(row.id, row.username, password_hash=password_hash)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    identity = get_current_user_optional(credentials)
    required_identity = get_current_user_required(user=identity)

    assert required_identity["role"] == "admin"
    assert is_admin_user(required_identity) is True
    assert len(sessions) == 1


def test_optional_auth_without_credentials_stays_anonymous_without_db_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        db_module,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be queried")),
    )

    assert get_current_user_optional(None) is None


def test_optional_auth_treats_disabled_bearer_as_anonymous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_hash = "stored-password-hash-v1"
    row = _user_row(
        password_hash=password_hash,
        username="alice",
        role="admin",
        active=False,
    )
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _Session(row))
    token = create_access_token(row.id, row.username, password_hash=password_hash)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    assert get_current_user_optional(credentials) is None


def test_admin_role_on_plain_dictionary_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _Session(None))

    assert is_admin_user(
        {
            "user_id": 7,
            "username": "admin",
            "auth_version": "forged",
            "role": "admin",
        }
    ) is False


@pytest.mark.parametrize(
    "row",
    [
        None,
        _user_row(password_hash="stored-password-hash-v1", username="renamed"),
        _user_row(password_hash="stored-password-hash-v1", username="alice", active=False),
        _user_row(password_hash="stored-password-hash-v2", username="alice"),
    ],
)
def test_missing_renamed_disabled_or_password_changed_user_revokes_token(
    monkeypatch: pytest.MonkeyPatch,
    row: object | None,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _Session(row))
    token = create_access_token(7, "alice", password_hash="stored-password-hash-v1")

    assert get_active_user_from_access_token(token) is None


def test_required_dependency_rejects_nonexistent_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _Session(None))
    token = create_access_token(42, "does-not-exist", password_hash="unused")
    decoded = get_user_from_access_token(token)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user_required(user=decoded)

    assert exc_info.value.status_code == 401


def test_legacy_token_is_rejected_in_production_even_if_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _user_row(password_hash="stored-password-hash-v1", username="alice")
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _Session(row))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_DEV_LEGACY_AUTH_TOKENS", "1")
    legacy_token = create_access_token(row.id, row.username)

    assert get_active_user_from_access_token(legacy_token) is None


def test_legacy_token_compatibility_requires_explicit_development_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _user_row(password_hash="stored-password-hash-v1", username="alice")
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _Session(row))
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_DEV_LEGACY_AUTH_TOKENS", "1")
    legacy_token = create_access_token(row.id, row.username)

    assert get_active_user_from_access_token(legacy_token) is not None


def test_identity_lookup_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_module, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    assert validate_active_user_identity(
        {"user_id": 7, "username": "alice", "auth_version": "anything"}
    ) is None
