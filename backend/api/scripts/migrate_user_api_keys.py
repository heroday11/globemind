#!/usr/bin/env python3
"""Audit, encrypt, or re-encrypt app_user.api_keys without exposing values."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from sqlalchemy import text


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

load_dotenv(BACKEND_ROOT / "api" / ".env", override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

from api.core.db import engine  # noqa: E402
from api.core.secrets import (  # noqa: E402
    decrypt_secret_text,
    is_encrypted_secret_text,
    reencrypt_secret_text,
    secret_store_configured,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit or encrypt public.app_user.api_keys. Defaults to read-only audit."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Encrypt plaintext rows and re-encrypt ciphertext with the primary key.",
    )
    parser.add_argument(
        "--generate-key",
        action="store_true",
        help="Print a new Fernet key and exit; this never connects to PostgreSQL.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.generate_key:
        print(Fernet.generate_key().decode("ascii"))
        return 0
    if args.apply and not secret_store_configured():
        print("USER_API_KEYS_ENCRYPTION_KEY must contain a valid Fernet key", file=sys.stderr)
        return 2

    select_sql = (
        "SELECT id, api_keys FROM public.app_user "
        "WHERE api_keys IS NOT NULL AND btrim(api_keys) <> '' ORDER BY id"
    )
    if not args.apply:
        with engine.connect() as conn:
            rows = conn.execute(text(select_sql)).all()
        plaintext, encrypted = _audit_rows(rows)
        print(f"rows={len(rows)} plaintext={plaintext} encrypted={encrypted}")
        print("audit only; run with --apply after backing up the table")
        return 0

    with engine.begin() as conn:
        # Lock selected rows so a concurrent profile update cannot be overwritten
        # by a stale value read before the migration transaction.
        rows = conn.execute(text(f"{select_sql} FOR UPDATE")).all()
        plaintext, encrypted = _audit_rows(rows)
        print(f"rows={len(rows)} plaintext={plaintext} encrypted={encrypted}")
        for user_id, stored in rows:
            conn.execute(
                text("UPDATE public.app_user SET api_keys = :value WHERE id = :user_id"),
                {"value": reencrypt_secret_text(stored), "user_id": user_id},
            )
    print(f"updated={len(rows)}; no secret values were logged")
    return 0


def _audit_rows(rows: list[tuple[int, str]]) -> tuple[int, int]:
    plaintext = 0
    encrypted = 0
    for _user_id, stored in rows:
        if is_encrypted_secret_text(stored):
            decrypt_secret_text(stored)
            encrypted += 1
        else:
            plaintext += 1
    return plaintext, encrypted


if __name__ == "__main__":
    raise SystemExit(main())
