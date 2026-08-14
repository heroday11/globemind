"""Fail-closed PostgreSQL credential loading for Web runtime connections."""

from __future__ import annotations

import ipaddress
import os
import stat
from pathlib import Path

from sqlalchemy.engine import URL

from api.core.environment import is_test_environment, string_setting

PASSWORD_FILE_ENV = "GLOBEMIND_DB_PASSWORD_FILE"
MAX_SECRET_BYTES = 4096


class DatabaseCredentialError(RuntimeError):
    """Raised when a database credential cannot be loaded without weakening policy."""


def _read_private_secret_file(path: Path) -> str:
    if not path.is_absolute():
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} must be an absolute path")

    try:
        before = os.lstat(path)
    except OSError as exc:
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} is unavailable") from exc
    if stat.S_ISLNK(before.st_mode):
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} must not be a symlink")
    if not stat.S_ISREG(before.st_mode):
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} must be a regular file")
    if stat.S_IMODE(before.st_mode) != 0o600:
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} must have mode 0600")
    if before.st_uid != os.geteuid():
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} must be owned by the service user")
    if before.st_size <= 0 or before.st_size > MAX_SECRET_BYTES:
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} has an invalid size")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} could not be opened safely") from exc
    try:
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or not stat.S_ISREG(after.st_mode)
            or stat.S_IMODE(after.st_mode) != 0o600
            or after.st_uid != os.geteuid()
            or after.st_size <= 0
            or after.st_size > MAX_SECRET_BYTES
        ):
            raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} changed while opening")
        chunks: list[bytes] = []
        remaining = MAX_SECRET_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(payload) != after.st_size
        ):
            raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} changed while reading")
    finally:
        os.close(descriptor)

    if len(payload) > MAX_SECRET_BYTES:
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} is too large")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} must contain UTF-8 text") from exc

    if text.endswith("\r\n"):
        value = text[:-2]
    elif text.endswith("\n"):
        value = text[:-1]
    else:
        value = text
    if not value or value != value.strip() or "\n" in value or "\r" in value or "\x00" in value:
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} contains an invalid secret")
    return value


def database_password() -> str:
    """Return the shared Web DB password without consulting plaintext env values."""
    configured_path = string_setting(PASSWORD_FILE_ENV)
    if not configured_path:
        if is_test_environment():
            return ""
        raise DatabaseCredentialError(f"{PASSWORD_FILE_ENV} is required outside tests")
    return _read_private_secret_file(Path(configured_path).expanduser())


def build_postgresql_url(
    *,
    host: str,
    port: str | int,
    user: str,
    database: str,
) -> URL:
    """Build a SQLAlchemy URL without interpolating the password into a string."""
    clean_host = str(host or "").strip()
    clean_user = str(user or "").strip()
    clean_database = str(database or "").strip()
    try:
        clean_port = int(port)
    except (TypeError, ValueError) as exc:
        raise DatabaseCredentialError("database port must be an integer") from exc
    if not clean_host or not clean_user or not clean_database:
        raise DatabaseCredentialError("database host, user, and name are required")
    if clean_port < 1 or clean_port > 65535:
        raise DatabaseCredentialError("database port is outside the valid range")
    clean_sslmode = _database_sslmode(clean_host)
    return URL.create(
        "postgresql+psycopg2",
        username=clean_user,
        password=database_password(),
        host=clean_host,
        port=clean_port,
        database=clean_database,
        query={"sslmode": clean_sslmode},
    )


def _database_sslmode(host: str) -> str:
    configured = string_setting("DB_SSLMODE").lower()
    if not configured and is_test_environment():
        return "disable"
    if configured not in {"verify-full", "require", "disable"}:
        raise DatabaseCredentialError(
            "DB_SSLMODE must be explicitly set to verify-full, require, or disable"
        )
    allow_private = string_setting("GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT")
    if configured == "disable":
        if allow_private != "1":
            raise DatabaseCredentialError(
                "DB_SSLMODE=disable requires GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT=1"
            )
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise DatabaseCredentialError(
                "unencrypted database transport requires a literal private IP"
            ) from exc
        if not address.is_private:
            raise DatabaseCredentialError(
                "unencrypted database transport is forbidden for non-private addresses"
            )
    elif allow_private not in {"", "0"}:
        raise DatabaseCredentialError(
            "GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT is valid only with DB_SSLMODE=disable"
        )
    return configured


def canonical_database_settings() -> dict[str, str]:
    """Return one canonical endpoint and identity for every Web DB engine."""
    user = string_setting("DB_USER")
    if not user:
        raise DatabaseCredentialError("DB_USER is required for every Web database engine")
    host = string_setting("DB_HOST", string_setting("PG_HOST", "127.0.0.1"))
    database = string_setting(
        "DB_NAME",
        string_setting("PG_DATABASE", string_setting("PG_DBNAME", "news")),
    )
    if not is_test_environment():
        if user != "web_runtime" or database != "news":
            raise DatabaseCredentialError(
                "production Web database identity must be web_runtime on news"
            )
        legacy_aliases = (
            "L1_DB_HOST",
            "L1_DB_PORT",
            "L1_DB_USER",
            "L1_DB_NAME",
            "OPINION_DB_HOST",
            "OPINION_DB_PORT",
            "OPINION_DB_USER",
            "OPINION_DB_NAME",
        )
        if any(string_setting(name) for name in legacy_aliases):
            raise DatabaseCredentialError(
                "legacy L1/OPINION database target aliases are forbidden for Web"
            )
    return {
        "host": host,
        "port": string_setting("DB_PORT", string_setting("PG_PORT", "5432")),
        "user": user,
        "database": database,
    }


def canonical_postgresql_url() -> URL:
    return build_postgresql_url(**canonical_database_settings())
