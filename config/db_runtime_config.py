"""Resolve database credentials without command-line arguments or source defaults."""
from __future__ import annotations

import ipaddress
import os
import stat
import sys
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import dotenv_values as _dotenv_values
except ImportError:  # Keep operational scripts usable in lean virtualenvs.

    def _dotenv_values(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or not key.isidentifier():
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[key] = value
        return values


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILES = (
    PROJECT_ROOT / "backend" / "api" / ".env",
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / "backend" / "agentic_rag" / ".env",
)
PASSWORD_NAMES = (
    "L1_DB_PASSWORD",
    "PG_WRITE_PASSWORD",
    "DB_PASSWORD",
    "PG_PASSWORD",
)
MAX_PASSWORD_FILE_BYTES = 4096


def _read_private_password_file(path: Path) -> str:
    path = path.expanduser()
    if not path.is_absolute():
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE must be an absolute path")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE does not exist") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE must be a non-symlink regular file")
    if stat.S_IMODE(before.st_mode) != 0o600:
        raise RuntimeError(
            "GLOBEMIND_DB_PASSWORD_FILE must have mode 0600 and not be group/world accessible"
        )
    if before.st_uid != os.geteuid():
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE owner mismatch")
    if before.st_size <= 0 or before.st_size > MAX_PASSWORD_FILE_BYTES:
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE has an invalid size")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_uid != os.geteuid()
            or opened.st_size <= 0
            or opened.st_size > MAX_PASSWORD_FILE_BYTES
        ):
            raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE changed while opening")
        chunks: list[bytes] = []
        remaining = MAX_PASSWORD_FILE_BYTES + 1
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
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            or len(payload) != opened.st_size
        ):
            raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE changed while reading")
    finally:
        os.close(descriptor)
    if len(payload) > MAX_PASSWORD_FILE_BYTES:
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE is too large")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE must contain UTF-8 text") from exc
    value = text[:-2] if text.endswith("\r\n") else text[:-1] if text.endswith("\n") else text
    if not value or value != value.strip() or any(char in value for char in "\r\n\x00"):
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE contains an invalid secret")
    return value


def validate_database_transport(
    host: str,
    sslmode: str,
    *,
    allow_private_scram_transport: bool,
) -> str:
    mode = str(sslmode or "").strip().lower()
    if mode not in {"verify-full", "require", "disable"}:
        raise RuntimeError("database sslmode must be verify-full, require, or disable")
    if mode == "disable":
        if not allow_private_scram_transport:
            raise RuntimeError("sslmode=disable requires explicit private SCRAM transport approval")
        try:
            address = ipaddress.ip_address(str(host or "").strip())
        except ValueError as exc:
            raise RuntimeError("unencrypted database transport requires a literal private IP") from exc
        if not address.is_private:
            raise RuntimeError("unencrypted database transport is forbidden for non-private addresses")
    elif allow_private_scram_transport:
        raise RuntimeError("private SCRAM transport approval is valid only with sslmode=disable")
    return mode


def validate_loader_database_role(
    user: str,
    *,
    allow_legacy_postgres_role: bool,
) -> str:
    role = str(user or "").strip()
    if role == "wave1_loader":
        if allow_legacy_postgres_role:
            raise RuntimeError("legacy postgres role approval is valid only with user=postgres")
        return role
    if role == "postgres" and allow_legacy_postgres_role:
        print(
            "warning: explicitly using the legacy postgres loader role for rollback",
            file=sys.stderr,
        )
        return role
    raise RuntimeError(
        "loader database user must be wave1_loader; postgres requires explicit legacy rollback approval"
    )


@lru_cache(maxsize=1)
def _file_values() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in DEFAULT_ENV_FILES:
        if path.is_file():
            for key, value in _dotenv_values(path).items():
                if value not in (None, "") and key not in merged:
                    merged[str(key)] = str(value)
    return merged


def require_database_password(*password_names: str, require_file: bool = False) -> str:
    names = password_names or PASSWORD_NAMES
    password_file = (os.getenv("GLOBEMIND_DB_PASSWORD_FILE") or "").strip()
    if password_file:
        return _read_private_password_file(Path(password_file))
    if require_file:
        raise RuntimeError("GLOBEMIND_DB_PASSWORD_FILE is required for the managed database role")

    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    configured = _file_values()
    for name in names:
        value = (configured.get(name) or "").strip()
        if value:
            return value
    raise RuntimeError(
        "Database password is required via environment or GLOBEMIND_DB_PASSWORD_FILE"
    )
