from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.core import identity_security as _identity_security
from api.core.environment import raw_setting
from api.core.runtime_security import is_production

ACCESS_TOKEN_EXPIRE_HOURS = _identity_security.ACCESS_TOKEN_EXPIRE_HOURS
ALGORITHM = _identity_security.ALGORITHM
SECRET_KEY = _identity_security.SECRET_KEY
_ActiveIdentity = _identity_security.ActiveIdentity
auth_versions_match = _identity_security.auth_versions_match
create_access_token = _identity_security.create_access_token
get_user_from_access_token = _identity_security.get_user_from_access_token
_hash_password = _identity_security.hash_password
_password_auth_version = _identity_security.password_auth_version
verify_login_password = _identity_security.verify_login_password

# ================== 认证配置 ==================
PASSWORD_RESET_EXPIRE_MINUTES = int(raw_setting("PASSWORD_RESET_EXPIRE_MINUTES", "30"))
PASSWORD_MIN_LENGTH = int(raw_setting("PASSWORD_MIN_LENGTH", "6"))
AUTH_USER = raw_setting("ADMIN_USER", "admin")
AUTH_PASSWORD = raw_setting("ADMIN_PASSWORD", "")
security = HTTPBearer(auto_error=False)


def validate_active_user_identity(
    user: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Resolve a token identity against the live database, failing closed.

    Tokens issued before the password-derived ``av`` claim was introduced are
    rejected. Development can temporarily opt in with
    ``ALLOW_DEV_LEGACY_AUTH_TOKENS=1``; production never accepts them.
    """
    if not user:
        return None
    try:
        user_id = int(user.get("user_id") or 0)
        token_username = str(user.get("username") or "").strip()
        if user_id <= 0 or not token_username:
            return None

        # Keep token parsing independent of ORM initialization.
        from api.core.db import SessionLocal
        from api.orm.models import User

        db = SessionLocal()
        try:
            row = (
                db.query(
                    User.id,
                    User.username,
                    User.password_hash,
                    User.is_active,
                    User.role,
                )
                .filter(User.id == user_id)
                .first()
            )
        finally:
            db.close()
    except Exception:
        return None

    if row is None:
        return None
    if str(row.username or "") != token_username or row.is_active is not True:
        return None

    token_version = str(user.get("auth_version") or "")
    current_version = _password_auth_version(str(row.password_hash or ""))
    if token_version:
        if not auth_versions_match(token_version, current_version):
            return None
    elif is_production() or raw_setting("ALLOW_DEV_LEGACY_AUTH_TOKENS").strip() != "1":
        return None

    session_tracking = str(user.get("session_tracking") or "untracked_legacy")
    if session_tracking == "tracked":
        jti = str(user.get("jti") or "")
        if not jti or not token_version:
            return None
        try:
            from api.features.identity import (
                configured_identity_assurance_store,
            )

            if not configured_identity_assurance_store().session_valid(
                user_id,
                jti=jti,
                auth_version=token_version,
            ):
                return None
        except Exception:
            return None
    elif session_tracking not in {"untracked", "untracked_legacy"}:
        return None

    identity = _ActiveIdentity(user)
    identity["role"] = str(row.role or "user").lower()
    return identity


def get_active_user_from_access_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Callable active-identity check for non-FastAPI ASGI wrappers/proxies."""
    return validate_active_user_identity(get_user_from_access_token(token))


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[Dict[str, Any]]:
    if not credentials or not credentials.credentials:
        return None
    return get_active_user_from_access_token(credentials.credentials)


def get_current_user_required(
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
) -> Dict[str, Any]:
    active_user = user if isinstance(user, _ActiveIdentity) else validate_active_user_identity(user)
    if not active_user:
        raise HTTPException(status_code=401, detail="未登录或 token 无效")
    return active_user


def is_admin_user(user: Optional[Dict[str, Any]]) -> bool:
    """Resolve administrator status and fail closed on lookup errors."""
    active_user = user if isinstance(user, _ActiveIdentity) else validate_active_user_identity(user)
    return bool(active_user and active_user.get("role") == "admin")


def get_current_admin_user(
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> Dict[str, Any]:
    """Authorize global administration operations and fail closed on lookup errors."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _safe_commit_user_side_effect(db: Session) -> None:
    """提交与用户相关写入；外键失败时返回可读错误而非 500。"""
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        orig = str(getattr(e, "orig", e) or e).lower()
        if "foreign key" in orig or "fk_" in orig or "user_id" in orig:
            raise HTTPException(
                status_code=409,
                detail=(
                    "当前账号在数据库中不存在（可能刚重置过库）。请退出登录后重新登录；"
                    "默认种子用户为 id=1，启动 API 后会自动创建。"
                ),
            )
        raise
