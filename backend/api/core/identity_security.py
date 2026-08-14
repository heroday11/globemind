"""Stateless password and signed-identity security primitives."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional

import bcrypt
import jwt

from api.core.environment import raw_setting

SECRET_KEY = raw_setting("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
_JTI_RE = re.compile(r"^[A-Za-z0-9_-]{20,256}$")


class ActiveIdentity(dict[str, Any]):
    """Marker for an identity resolved against the active user store."""


def password_auth_version(password_hash: str) -> str:
    """Derive a non-reversible token version from the stored password hash."""
    return hmac.new(
        SECRET_KEY.encode("utf-8"),
        str(password_hash or "").encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def auth_versions_match(candidate: str, current: str) -> bool:
    return hmac.compare_digest(candidate, current)


def create_access_token(
    user_id: int,
    username: str,
    *,
    password_hash: Optional[str] = None,
    jti: Optional[str] = None,
    session_tracking: Literal["tracked", "untracked"] = "untracked",
    issued_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
) -> str:
    now = issued_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("access-token issuance requires a timezone")
    now = now.astimezone(timezone.utc)
    expire = expires_at or (now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    if expire.tzinfo is None or expire <= now:
        raise ValueError("access-token expiry must follow issuance")
    expire = expire.astimezone(timezone.utc)
    token_jti = str(jti or secrets.token_urlsafe(32)).strip()
    if _JTI_RE.fullmatch(token_jti) is None:
        raise ValueError("access-token jti is invalid")
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": expire,
        "jti": token_jti,
        "st": session_tracking,
    }
    if password_hash:
        payload["av"] = password_auth_version(password_hash)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_user_from_access_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve an application JWT without FastAPI or database dependencies."""
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "username", "exp"]},
        )
        user_id = int(payload.get("sub"))
        username = str(payload.get("username") or "").strip()
        jti = payload.get("jti")
        session_tracking = payload.get("st", "untracked_legacy")
        if (
            user_id > 0
            and username
            and session_tracking in {"tracked", "untracked", "untracked_legacy"}
            and (
                jti is None
                or (
                    isinstance(jti, str)
                    and _JTI_RE.fullmatch(jti) is not None
                )
            )
            and not (session_tracking == "tracked" and jti is None)
        ):
            return {
                "user_id": user_id,
                "username": username,
                "auth_version": payload.get("av"),
                "issued_at": payload.get("iat"),
                "expires_at": payload.get("exp"),
                "jti": jti,
                "session_tracking": session_tracking,
            }
    except Exception:
        pass
    return None


def hash_password(raw_password: str) -> str:
    """Hash a password using the existing bcrypt compatibility policy."""
    secret = str(raw_password or "").encode("utf-8")
    if len(secret) > 72:
        secret = secret[:72]
    return bcrypt.hashpw(secret, bcrypt.gensalt()).decode("utf-8")


def verify_login_password(
    raw_password: str,
    stored_password: Optional[str],
) -> tuple[bool, bool]:
    """Return whether the password matches and whether legacy storage needs upgrade."""
    if stored_password is None:
        return False, False
    raw_password = str(raw_password or "")
    stored = str(stored_password or "")

    if stored.startswith("$2"):
        secret = raw_password.encode("utf-8")
        if len(secret) > 72:
            return False, False
        try:
            return bcrypt.checkpw(secret, stored.encode("utf-8")), False
        except (TypeError, ValueError):
            return False, False

    return raw_password == stored, True
