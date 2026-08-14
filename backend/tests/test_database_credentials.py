from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from api.core import database_credentials
from api.core.environment import discard_plaintext_database_environment


def _private_secret(path: Path, value: str = "unit-test-database-secret") -> Path:
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_test_environment_allows_explicit_passwordless_database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv(database_credentials.PASSWORD_FILE_ENV, raising=False)
    monkeypatch.setenv("DB_PASSWORD", "plaintext-value-must-be-ignored")
    monkeypatch.setenv("PG_PASSWORD", "another-value-that-must-be-ignored")

    assert database_credentials.database_password() == ""


def test_non_test_environment_requires_password_file(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv(database_credentials.PASSWORD_FILE_ENV, raising=False)
    monkeypatch.setenv("DB_PASSWORD", "legacy-plaintext-must-not-be-used")

    with pytest.raises(
        database_credentials.DatabaseCredentialError, match="required outside tests"
    ):
        database_credentials.database_password()


def test_canonical_web_identity_never_falls_back_to_postgres(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("DB_USER", raising=False)
    monkeypatch.setenv("PG_USER", "postgres")

    with pytest.raises(database_credentials.DatabaseCredentialError, match="DB_USER is required"):
        database_credentials.canonical_database_settings()


def test_database_password_file_must_be_exact_mode_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    secret = _private_secret(tmp_path / "db.password")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(database_credentials.PASSWORD_FILE_ENV, str(secret))

    secret.chmod(0o640)
    with pytest.raises(database_credentials.DatabaseCredentialError, match="mode 0600"):
        database_credentials.database_password()

    secret.chmod(0o600)
    assert database_credentials.database_password() == "unit-test-database-secret"


def test_database_password_file_rejects_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = _private_secret(tmp_path / "target.password")
    link = tmp_path / "database.password"
    link.symlink_to(target)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(database_credentials.PASSWORD_FILE_ENV, str(link))

    with pytest.raises(database_credentials.DatabaseCredentialError, match="must not be a symlink"):
        database_credentials.database_password()


def test_database_password_file_rejects_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    secret = _private_secret(tmp_path / "db.password")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(database_credentials.PASSWORD_FILE_ENV, str(secret))
    monkeypatch.setattr(database_credentials.os, "geteuid", lambda: os.stat(secret).st_uid + 1)

    with pytest.raises(database_credentials.DatabaseCredentialError, match="service user"):
        database_credentials.database_password()


def test_database_url_uses_file_secret_and_hides_it_when_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    secret = _private_secret(tmp_path / "db.password", "url-secret-that-must-stay-out-of-text")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(database_credentials.PASSWORD_FILE_ENV, str(secret))
    monkeypatch.setenv("DB_SSLMODE", "require")
    monkeypatch.delenv("GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT", raising=False)

    url = database_credentials.build_postgresql_url(
        host="db.internal",
        port="5432",
        user="web_runtime",
        database="news",
    )

    assert url.password == "url-secret-that-must-stay-out-of-text"
    assert "url-secret-that-must-stay-out-of-text" not in url.render_as_string()
    assert "***" in url.render_as_string()
    assert url.query["sslmode"] == "require"


def test_unencrypted_web_transport_requires_private_ip_and_explicit_switch(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DB_SSLMODE", "disable")
    monkeypatch.delenv("GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT", raising=False)
    with pytest.raises(database_credentials.DatabaseCredentialError, match="requires"):
        database_credentials._database_sslmode("192.168.1.10")

    monkeypatch.setenv("GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT", "1")
    with pytest.raises(database_credentials.DatabaseCredentialError, match="non-private"):
        database_credentials._database_sslmode("8.8.8.8")
    assert database_credentials._database_sslmode("192.168.1.10") == "disable"


def test_web_database_consumers_use_core_database_boundary():
    project_root = Path(__file__).resolve().parents[2]
    core = project_root / "backend/api/core/db.py"
    consumers = (
        project_root / "backend/api/routes/story_graph.py",
        project_root / "backend/api/routes/opinion_v2.py",
        project_root / "backend/api/services/financial_terminal.py",
        project_root / "backend/api/services/news_search_v2.py",
    )
    forbidden = ("DB_PASSWORD", "PG_PASSWORD", "L1_DB_PASSWORD", "OPINION_DB_PASSWORD")
    core_source = core.read_text(encoding="utf-8")
    assert "canonical_postgresql_url" in core_source
    assert core_source.count("create_engine(") == 1
    for target in consumers:
        source = target.read_text(encoding="utf-8")
        assert "from api.core.db import" in source, target
        assert "canonical_postgresql_url" not in source, target
        assert "create_engine" not in source, target
        assert "engine_pool_kwargs" not in source, target
        assert not any(name in source for name in forbidden), target
        assert "L1_DB_USER" not in source, target
        assert "OPINION_DB_USER" not in source, target


def test_candidate_overlay_controls_every_web_engine_identity(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[2]
    backend_root = project_root / "backend"
    secret = _private_secret(tmp_path / "db.password", "candidate-secret-value")
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    empty_env.chmod(0o600)
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "production",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(backend_root),
            "GLOBEMIND_ENV_FILES": str(empty_env),
            "GLOBEMIND_DB_PASSWORD_FILE": str(secret),
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
            "DB_NAME": "news",
            "DB_USER": "web_runtime",
            "DB_SSLMODE": "disable",
            "GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT": "1",
        }
    )
    for name in (
        "L1_DB_HOST",
        "L1_DB_PORT",
        "L1_DB_USER",
        "L1_DB_NAME",
        "OPINION_DB_HOST",
        "OPINION_DB_PORT",
        "OPINION_DB_USER",
        "OPINION_DB_NAME",
    ):
        env.pop(name, None)
    script = """
from api.core import db
from api.routes import opinion_v2, story_graph
from api.services import financial_terminal, news_search_v2
engines = (
    db.engine,
    story_graph._L1_ENGINE,
    news_search_v2.NEWS_ENGINE,
    financial_terminal._get_l1_engine(),
)
urls = tuple(item.url for item in engines) + (opinion_v2._make_news_database_url(),)
assert all(item is db.engine for item in engines)
assert story_graph._L1_SESSION_LOCAL is db.SessionLocal
assert opinion_v2._NEWS_SESSION_LOCAL is db.SessionLocal
assert all(url.username == 'web_runtime' for url in urls)
assert all(url.database == 'news' for url in urls)
"""

    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_production_rejects_legacy_database_target_aliases(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DB_USER", "web_runtime")
    monkeypatch.setenv("DB_NAME", "news")
    monkeypatch.setenv("L1_DB_USER", "postgres")

    with pytest.raises(database_credentials.DatabaseCredentialError, match="aliases are forbidden"):
        database_credentials.canonical_database_settings()


def test_web_role_overlay_explicitly_clears_legacy_database_targets():
    project_root = Path(__file__).resolve().parents[2]
    overlay = (
        project_root / "config/runtime/web-database-role.env.example"
    ).read_text(encoding="utf-8")

    assert "DB_USER=web_runtime" in overlay
    assert "DB_SSLMODE=disable" in overlay
    assert "GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT=1" in overlay
    for name in (
        "L1_DB_HOST",
        "L1_DB_PORT",
        "L1_DB_USER",
        "L1_DB_NAME",
        "OPINION_DB_HOST",
        "OPINION_DB_PORT",
        "OPINION_DB_USER",
        "OPINION_DB_NAME",
    ):
        assert f"{name}=\n" in overlay


def test_web_bootstrap_discards_legacy_database_secret_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    secret = _private_secret(tmp_path / "db.password")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(database_credentials.PASSWORD_FILE_ENV, str(secret))
    legacy_names = (
        "DB_PASSWORD",
        "PG_PASSWORD",
        "PGPASSWORD",
        "L1_DB_PASSWORD",
        "OPINION_DB_PASSWORD",
        "DATABASE_URL",
        "SQLALCHEMY_DATABASE_URL",
    )
    for name in legacy_names:
        monkeypatch.setenv(name, "legacy-secret")

    removed = discard_plaintext_database_environment()

    assert set(removed) == set(legacy_names)
    assert all(name not in os.environ for name in legacy_names)
