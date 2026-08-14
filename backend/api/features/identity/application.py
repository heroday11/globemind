"""Identity login application service preserving the existing auth semantics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from api.features.identity.repository import IdentityRepository
from api.services.auth import _hash_password, verify_login_password


def authenticate_login(
    db: Session,
    login_id: str,
    raw_password: str,
    *,
    development_admin: tuple[str, str] | None = None,
) -> Any | None:
    repository = IdentityRepository(db)
    user_row = repository.find_for_login(login_id)
    if user_row is not None and getattr(user_row, "is_active", True) is not False:
        is_valid, needs_upgrade = verify_login_password(
            raw_password,
            user_row.password_hash,
        )
        if is_valid:
            return repository.record_successful_login(
                user_row,
                now=datetime.now(timezone.utc),
                upgraded_password_hash=(
                    _hash_password(raw_password) if needs_upgrade else None
                ),
            )

    if development_admin is None or (login_id, raw_password) != development_admin:
        return None
    admin_row = repository.find_by_username(development_admin[0])
    if (
        admin_row is None
        or admin_row.is_active is not True
        or str(admin_row.role or "").lower() != "admin"
    ):
        return None
    return admin_row


__all__ = ("authenticate_login",)
