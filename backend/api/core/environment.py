from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

_API_DIR = Path(__file__).resolve().parents[1]
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEST_ENVIRONMENTS = frozenset({"test", "testing"})
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_LEGACY_DATABASE_SECRET_ENV = (
    "DB_PASSWORD",
    "PG_PASSWORD",
    "PGPASSWORD",
    "L1_DB_PASSWORD",
    "OPINION_DB_PASSWORD",
    "DATABASE_URL",
    "SQLALCHEMY_DATABASE_URL",
)


def is_test_environment() -> bool:
    return (os.getenv("APP_ENV") or "").strip().lower() in _TEST_ENVIRONMENTS


def int_setting(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, value) if minimum is not None else value


def float_setting(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = raw_setting(name).strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, value) if minimum is not None else value


def bool_setting(name: str, default: bool = False) -> bool:
    raw = raw_setting(name).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def string_setting(name: str, default: str = "") -> str:
    value = (os.getenv(name) or "").strip()
    return value or default


def raw_setting(name: str, default: str = "") -> str:
    """Read a setting without trimming or replacing an explicitly empty value."""
    value = os.getenv(name)
    return default if value is None else value


def source_version(default: str = "development") -> str:
    """Return the repository/release VERSION without relying on the working directory."""
    try:
        value = (_REPO_ROOT / "VERSION").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return default
    return value if _VERSION_PATTERN.fullmatch(value) else default


def app_version(default: str = "development") -> str:
    value = (os.getenv("APP_VERSION") or "").strip()
    return value or source_version(default)


def default_env_paths() -> tuple[Path, ...]:
    if is_test_environment():
        explicit_test = (os.getenv("GLOBEMIND_TEST_ENV_FILE") or "").strip()
        return (Path(explicit_test).expanduser(),) if explicit_test else ()

    explicit_files = (os.getenv("GLOBEMIND_ENV_FILES") or "").strip()
    if explicit_files:
        return tuple(
            Path(value.strip()).expanduser()
            for value in explicit_files.split(os.pathsep)
            if value.strip()
        )

    explicit = (os.getenv("GLOBEMIND_ENV_FILE") or "").strip()
    if explicit:
        return (Path(explicit).expanduser(),)

    return (
        _API_DIR / ".env",
        _BACKEND_DIR / ".env",
        _BACKEND_DIR / "agentic_rag" / ".env",
        _REPO_ROOT / ".env",
    )


def load_environment(paths: Iterable[Path] | None = None, *, override: bool = False) -> tuple[Path, ...]:
    """Load configuration in deterministic, most-specific-first order."""
    loaded: list[Path] = []
    seen: set[Path] = set()
    for raw_path in default_env_paths() if paths is None else paths:
        path = Path(raw_path).expanduser().resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        load_dotenv(path, override=override)
        loaded.append(path)
    return tuple(loaded)


def discard_plaintext_database_environment() -> tuple[str, ...]:
    """Remove legacy DB secret variables once the shared secret file is configured."""
    if is_test_environment() or not (os.getenv("GLOBEMIND_DB_PASSWORD_FILE") or "").strip():
        return ()
    removed: list[str] = []
    for name in _LEGACY_DATABASE_SECRET_ENV:
        if name in os.environ:
            os.environ.pop(name, None)
            removed.append(name)
    return tuple(removed)
