#!/usr/bin/env python3
"""One-time migration from the legacy environment admin password to DB auth."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from api.core.environment import load_environment


def _write_backup(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.exists():
        raise RuntimeError(f"backup already exists: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="perform the migration")
    parser.add_argument(
        "--backup-file",
        default="/root/data/web/security/admin_password_hash_pre_v091.json",
    )
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required")

    load_environment()
    legacy_password = os.getenv("ADMIN_PASSWORD") or ""
    admin_username = (os.getenv("ADMIN_USER") or "admin").strip()
    if not legacy_password:
        raise RuntimeError("ADMIN_PASSWORD is not configured; nothing to migrate")

    from api.core.db import SessionLocal
    from api.orm.models import User
    from api.services.auth import _hash_password, verify_login_password

    with SessionLocal() as db:
        row = db.query(User).filter(User.username == admin_username).with_for_update().first()
        if row is None or row.is_active is not True or str(row.role or "").lower() != "admin":
            raise RuntimeError("active database administrator was not found")
        matches, _ = verify_login_password(legacy_password, row.password_hash)
        if matches:
            print(json.dumps({"status": "already_migrated", "user_id": int(row.id)}))
            return 0

        backup_path = Path(args.backup_file).expanduser().resolve()
        _write_backup(
            backup_path,
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "user_id": int(row.id),
                "username": row.username,
                "password_hash": row.password_hash,
            },
        )
        row.password_hash = _hash_password(legacy_password)
        row.updated_at = datetime.now(timezone.utc)
        db.commit()

        verified, _ = verify_login_password(legacy_password, row.password_hash)
        if not verified:
            raise RuntimeError("database password verification failed after migration")
        print(
            json.dumps(
                {
                    "status": "migrated",
                    "user_id": int(row.id),
                    "backup_file": str(backup_path),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
