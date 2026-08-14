from __future__ import annotations

import os

from api.scripts.sanitize_legacy_auth_env import sanitize


def test_sanitize_legacy_auth_env_is_atomic_and_preserves_other_values(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KEEP=value\n"
        "ADMIN_PASSWORD=retired-secret\n"
        "export UNIFY_APP_USER_PASSWORD=retired-secret-2\n",
        encoding="utf-8",
    )
    os.chmod(env_file, 0o600)

    removed = sanitize(env_file)

    assert removed == ("ADMIN_PASSWORD", "UNIFY_APP_USER_PASSWORD")
    assert env_file.read_text(encoding="utf-8") == "KEEP=value\n"
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_sanitize_legacy_auth_env_is_idempotent(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=value\n", encoding="utf-8")
    os.chmod(env_file, 0o600)

    assert sanitize(env_file) == ()
