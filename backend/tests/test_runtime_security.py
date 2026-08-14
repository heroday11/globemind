from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import api.application as application  # noqa: E402
from api.core.runtime_security import validate_runtime_security  # noqa: E402
from api.routes.auth import LogEmailSender, _development_admin_fallback  # noqa: E402


def test_development_allows_local_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_DEFAULT_USER_PASSWORD", raising=False)

    validate_runtime_security()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("JWT_SECRET_KEY", "change-me", "JWT_SECRET_KEY"),
        ("SEED_DEFAULT_USER_PASSWORD", "1232200", "SEED_DEFAULT_USER_PASSWORD"),
        ("CORS_ORIGINS", "*", "CORS_ORIGINS"),
    ],
)
def test_production_rejects_insecure_settings(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
    message: str,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    monkeypatch.delenv("SEED_DEFAULT_USER_PASSWORD", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://globemind.top")
    monkeypatch.setenv(key, value)

    with pytest.raises(RuntimeError, match=message):
        validate_runtime_security()


def test_production_accepts_strong_explicit_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_DEFAULT_USER_PASSWORD", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://globemind.top")

    validate_runtime_security()


def test_production_does_not_require_or_validate_unused_admin_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    monkeypatch.setenv("ADMIN_PASSWORD", "1232200")
    monkeypatch.delenv("SEED_DEFAULT_USER_PASSWORD", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://globemind.top")

    validate_runtime_security()


def test_production_lifespan_does_not_mutate_schema(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_DEFAULT_USER_PASSWORD", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://globemind.top")
    monkeypatch.delenv("ALLOW_RUNTIME_SCHEMA_MUTATIONS", raising=False)
    create_calls = 0

    def fail_if_create_tables_runs():
        nonlocal create_calls
        create_calls += 1

    class ScalarResult:
        def scalar(self):
            return 0

    class FakeSession:
        def execute(self, _statement):
            return ScalarResult()

        def close(self):
            return None

    async def fake_stop_runner():
        return None

    monkeypatch.setattr(application, "create_tables", fail_if_create_tables_runs)
    monkeypatch.setattr(application, "SessionLocal", FakeSession)
    monkeypatch.setattr(application, "run_startup_schema_check", lambda _db: {"ready": True, "errors": {}})
    monkeypatch.setattr(application, "start_schedule_runner", lambda: None)
    monkeypatch.setattr(application, "stop_schedule_runner", fake_stop_runner)

    async def exercise_lifespan():
        async with application.lifespan(application.app):
            pass

    asyncio.run(exercise_lifespan())
    assert create_calls == 0


def test_password_unification_is_not_part_of_web_startup():
    source = (PROJECT_ROOT / "backend" / "api" / "core" / "db.py").read_text(encoding="utf-8")
    assert "UNIFY_APP_USER_PASSWORD" not in source


def test_development_admin_fallback_is_disabled_in_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_DEV_ADMIN_PASSWORD_LOGIN", "1")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "shared-password")

    assert _development_admin_fallback() is None


def test_password_reset_link_is_not_logged_in_production(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("APP_ENV", "production")

    LogEmailSender().send_password_reset("user@example.com", "https://example.com/reset?token=secret")

    captured = capsys.readouterr().out
    assert "token=secret" not in captured
    assert "user@example.com" not in captured
