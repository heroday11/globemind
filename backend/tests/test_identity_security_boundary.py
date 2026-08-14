from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest

from api.core import identity_security
from api.services import auth as auth_service

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"


def test_service_auth_preserves_identity_security_compatibility_exports() -> None:
    assert auth_service.SECRET_KEY == identity_security.SECRET_KEY
    assert auth_service.ALGORITHM == identity_security.ALGORITHM
    assert (
        auth_service.ACCESS_TOKEN_EXPIRE_HOURS
        == identity_security.ACCESS_TOKEN_EXPIRE_HOURS
    )
    assert auth_service._ActiveIdentity is identity_security.ActiveIdentity
    assert auth_service._hash_password is identity_security.hash_password
    assert (
        auth_service._password_auth_version
        is identity_security.password_auth_version
    )
    assert auth_service.create_access_token is identity_security.create_access_token
    assert (
        auth_service.get_user_from_access_token
        is identity_security.get_user_from_access_token
    )
    assert (
        auth_service.verify_login_password
        is identity_security.verify_login_password
    )


def test_bcrypt_and_legacy_password_semantics_are_unchanged() -> None:
    hashed = identity_security.hash_password("database-password-1")

    assert hashed.startswith("$2")
    assert identity_security.verify_login_password("database-password-1", hashed) == (
        True,
        False,
    )
    assert identity_security.verify_login_password("wrong-password", hashed) == (
        False,
        False,
    )
    assert identity_security.verify_login_password("legacy", "legacy") == (True, True)
    assert identity_security.verify_login_password("wrong", "legacy") == (False, True)
    assert identity_security.verify_login_password("anything", None) == (False, False)
    assert identity_security.verify_login_password("anything", "$2-invalid") == (
        False,
        False,
    )


def test_bcrypt_72_byte_compatibility_policy_is_unchanged() -> None:
    first_72 = "a" * 72
    hashed = identity_security.hash_password(first_72 + "ignored-by-hash")

    assert identity_security.verify_login_password(first_72, hashed) == (True, False)
    assert identity_security.verify_login_password(first_72 + "x", hashed) == (
        False,
        False,
    )


def test_jwt_round_trip_auth_version_and_fail_closed_cases() -> None:
    password_hash = identity_security.hash_password("database-password-1")
    token = identity_security.create_access_token(
        42,
        "alice",
        password_hash=password_hash,
    )

    identity = identity_security.get_user_from_access_token(token)
    assert identity is not None
    assert identity["user_id"] == 42
    assert identity["username"] == "alice"
    assert identity["auth_version"] == identity_security.password_auth_version(
        password_hash
    )
    assert identity["issued_at"] == pytest.approx(
        datetime.now(timezone.utc).timestamp(), abs=5
    )
    assert identity["expires_at"] > identity["issued_at"]
    assert len(identity["jti"]) >= 20
    # Direct primitive callers stay compatible, but these tokens are explicit
    # untracked sessions. Real login routes issue tracked sessions.
    assert identity["session_tracking"] == "untracked"

    header, payload, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered = ".".join((header, payload, replacement + signature[1:]))
    assert identity_security.get_user_from_access_token(tampered) is None
    assert identity_security.get_user_from_access_token("") is None
    assert identity_security.get_user_from_access_token(None) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"sub": "7", "username": "alice"},
        {
            "sub": "7",
            "username": "alice",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        {
            "sub": "0",
            "username": "alice",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=1),
        },
        {
            "sub": "7",
            "username": "   ",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=1),
        },
    ],
)
def test_jwt_parser_rejects_incomplete_expired_or_invalid_identity(
    payload: dict[str, object],
) -> None:
    token = jwt.encode(
        payload,
        identity_security.SECRET_KEY,
        algorithm=identity_security.ALGORITHM,
    )

    assert identity_security.get_user_from_access_token(token) is None


@pytest.mark.parametrize(
    "module_order",
    [
        ("api.core.db", "api.core.http_security", "api.services.auth"),
        ("api.services.auth", "api.core.http_security", "api.core.db"),
        ("api.core.http_security", "api.core.db", "api.services.auth"),
    ],
)
def test_identity_security_fresh_import_order_has_no_cycle(
    module_order: tuple[str, ...],
) -> None:
    script = f"""
import importlib
for module_name in {module_order!r}:
    importlib.import_module(module_name)
from api.core import identity_security
from api.services import auth
assert auth._hash_password is identity_security.hash_password
assert auth.create_access_token is identity_security.create_access_token
assert auth.get_user_from_access_token is identity_security.get_user_from_access_token
"""
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "PYTHONPATH": str(BACKEND_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "1",
            "DB_USER": "globemind_test",
            "DB_NAME": "globemind_test",
            "DB_SSLMODE": "disable",
            "GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT": "1",
            "JWT_SECRET_KEY": "identity-boundary-test-secret-000000000000000000",
        }
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_import_boundary_baseline_records_resolved_core_debt() -> None:
    baseline = json.loads(
        (PROJECT_ROOT / "quality/import-boundaries-baseline.json").read_text(
            encoding="utf-8"
        )
    )["rules"]

    assert baseline["backend-core-imports-services"] == {}
    direct_environment = baseline["backend-direct-environment-read"]
    assert "backend/api/core/db.py" not in direct_environment
    assert "backend/api/core/http_security.py" not in direct_environment
    assert "backend/api/services/auth.py" not in direct_environment
