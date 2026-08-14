"""Database adapter for the identity login use case."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.orm import models


class IdentityRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def find_for_login(self, login_id: str) -> Any | None:
        normalized = (login_id or "").strip()
        if not normalized:
            return None
        row = (
            self._db.query(models.User)
            .filter(models.User.username == normalized)
            .first()
        )
        if row is not None:
            return row
        return (
            self._db.query(models.User)
            .filter(func.lower(models.User.email) == normalized.lower())
            .first()
        )

    def find_by_username(self, username: str) -> Any | None:
        return (
            self._db.query(models.User)
            .filter(models.User.username == username)
            .first()
        )

    def record_successful_login(
        self,
        user_row: Any,
        *,
        now: datetime,
        upgraded_password_hash: str | None,
    ) -> Any:
        if upgraded_password_hash is not None:
            user_row.password_hash = upgraded_password_hash
        user_row.updated_at = now
        user_row.last_login_at = now
        self._db.commit()
        self._db.refresh(user_row)
        return user_row


__all__ = ("IdentityRepository",)
