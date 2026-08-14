from __future__ import annotations

from pathlib import Path

import pytest

from config import db_runtime_config as shared_db_runtime_config
from scripts import db_runtime_config


@pytest.fixture(autouse=True)
def isolated_database_password_environment(monkeypatch: pytest.MonkeyPatch):
    for name in (*db_runtime_config.PASSWORD_NAMES, "GLOBEMIND_DB_PASSWORD_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(db_runtime_config, "DEFAULT_ENV_FILES", ())
    db_runtime_config._file_values.cache_clear()
    yield
    db_runtime_config._file_values.cache_clear()


def test_scripts_entry_point_is_the_shared_configuration_module():
    assert db_runtime_config is shared_db_runtime_config


def test_database_password_role_order_is_explicit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PG_WRITE_PASSWORD", "unit-test-write-password")
    monkeypatch.setenv("PG_PASSWORD", "unit-test-read-password")

    assert db_runtime_config.require_database_password() == "unit-test-write-password"
    assert (
        db_runtime_config.require_database_password("PG_PASSWORD", "DB_PASSWORD")
        == "unit-test-read-password"
    )


def test_database_password_file_must_be_private(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    password_file = tmp_path / "database-password"
    password_file.write_text("unit-test-file-password\n", encoding="utf-8")
    password_file.chmod(0o640)
    monkeypatch.setenv("GLOBEMIND_DB_PASSWORD_FILE", str(password_file))

    with pytest.raises(RuntimeError, match="group/world accessible"):
        db_runtime_config.require_database_password()

    password_file.chmod(0o600)
    assert (
        db_runtime_config.require_database_password()
        == "unit-test-file-password"
    )


def test_database_password_file_rejects_symlink_and_owner_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    password_file = tmp_path / "database-password"
    password_file.write_text("unit-test-file-password\n", encoding="utf-8")
    password_file.chmod(0o600)
    link = tmp_path / "database-password-link"
    link.symlink_to(password_file)
    monkeypatch.setenv("GLOBEMIND_DB_PASSWORD_FILE", str(link))
    with pytest.raises(RuntimeError, match="non-symlink"):
        db_runtime_config.require_database_password()

    monkeypatch.setenv("GLOBEMIND_DB_PASSWORD_FILE", str(password_file))
    monkeypatch.setattr(db_runtime_config.os, "geteuid", lambda: password_file.stat().st_uid + 1)
    with pytest.raises(RuntimeError, match="owner mismatch"):
        db_runtime_config.require_database_password()


def test_database_password_can_come_from_environment_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "PG_PASSWORD=unit-test-read-password\n"
        "PG_WRITE_PASSWORD=unit-test-write-password\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db_runtime_config, "DEFAULT_ENV_FILES", (env_file,))
    db_runtime_config._file_values.cache_clear()

    assert db_runtime_config.require_database_password() == "unit-test-write-password"
    assert (
        db_runtime_config.require_database_password("PG_PASSWORD")
        == "unit-test-read-password"
    )


def test_database_transport_never_implicitly_downgrades():
    with pytest.raises(RuntimeError, match="must be"):
        db_runtime_config.validate_database_transport(
            "192.168.1.10", "", allow_private_scram_transport=False
        )
    with pytest.raises(RuntimeError, match="explicit"):
        db_runtime_config.validate_database_transport(
            "192.168.1.10", "disable", allow_private_scram_transport=False
        )
    with pytest.raises(RuntimeError, match="non-private"):
        db_runtime_config.validate_database_transport(
            "8.8.8.8", "disable", allow_private_scram_transport=True
        )
    assert (
        db_runtime_config.validate_database_transport(
            "192.168.1.10", "disable", allow_private_scram_transport=True
        )
        == "disable"
    )


def test_managed_database_role_requires_secret_file(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("PG_WRITE_PASSWORD", "legacy-environment-secret")

    with pytest.raises(RuntimeError, match="PASSWORD_FILE is required"):
        db_runtime_config.require_database_password(require_file=True)


def test_loader_role_allows_only_managed_or_explicit_postgres_rollback(
    capsys: pytest.CaptureFixture[str],
):
    assert (
        db_runtime_config.validate_loader_database_role(
            "wave1_loader", allow_legacy_postgres_role=False
        )
        == "wave1_loader"
    )
    with pytest.raises(RuntimeError, match="must be wave1_loader"):
        db_runtime_config.validate_loader_database_role(
            "another_role", allow_legacy_postgres_role=True
        )
    with pytest.raises(RuntimeError, match="valid only with user=postgres"):
        db_runtime_config.validate_loader_database_role(
            "wave1_loader", allow_legacy_postgres_role=True
        )

    assert (
        db_runtime_config.validate_loader_database_role(
            "postgres", allow_legacy_postgres_role=True
        )
        == "postgres"
    )
    assert "legacy postgres loader role for rollback" in capsys.readouterr().err
