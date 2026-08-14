"""
Auth route module: login, register, forgot-password, reset-password, user profile, favorites, search history.
"""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import smtplib
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from api.core.db import get_db
from api.core.environment import bool_setting, int_setting, raw_setting, string_setting
from api.core.runtime_security import is_production
from api.core.secrets import SecretStoreConfigurationError, secret_store_configured
from api.features.assistant import configured_assistant_privacy_export_reader
from api.features.identity import (
    ApiConfigUpdateRequest,
    ChangePasswordRequest,
    FavoriteBatchRequest,
    FavoriteRemoveRequest,
    FavoriteToggleRequest,
    ForgotPasswordRequest,
    IdentityAssuranceConflict,
    IdentityAssuranceUnavailable,
    IdentityFactorRejected,
    LoginChallengeRejected,
    LoginMfaVerifyRequest,
    LoginRequest,
    MfaConfirmRequest,
    MfaDisableRequest,
    PersonalDataExportAdapterBinding,
    PrivacyDeletionRequestCreate,
    PrivacyDeletionRequestStore,
    PrivacyRightsConflict,
    PrivacyRightsNotFound,
    PrivacyRightsUnavailable,
    ProviderBaseUrlError,
    RegisterRequest,
    ResetPasswordRequest,
    SearchHistoryRequest,
    SessionRevokeRequest,
    UserProfileResponse,
    UserProfileUpdateRequest,
    authenticate_login,
    build_account_deletion_impact_plan,
    build_personal_data_export,
    configured_identity_assurance_store,
    hash_session_jti,
    normalize_provider_base_url,
    provider_base_url_or_none,
)
from api.features.research_workflow import (
    build_research_subject_export,
    configured_research_repository,
)
from api.orm import models
from api.services.assistant_user_defaults import ensure_assistant_user_defaults
from api.services.auth import (
    ACCESS_TOKEN_EXPIRE_HOURS,
    PASSWORD_MIN_LENGTH,
    PASSWORD_RESET_EXPIRE_MINUTES,
    _hash_password,
    _password_auth_version,
    _safe_commit_user_side_effect,
    create_access_token,
    get_current_user_required,
    verify_login_password,
)
from api.services.helpers import (
    _normalize_favorite_kind,
    _normalize_favorite_topic,
)

router = APIRouter(prefix="")


# ================== Helper functions ==================

_MAX_FAVORITES_MUTATION_JSON_BYTES = 64 * 1024
_MAX_FAVORITES_JSON_DEPTH = 5


def _reject_duplicate_favorite_json_keys(
    pairs: List[tuple[str, Any]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate favorite JSON key")
        payload[key] = value
    return payload


def _reject_non_finite_favorite_json_number(_value: str) -> None:
    raise ValueError("non-finite favorite JSON number")


def _finite_favorite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject_non_finite_favorite_json_number(value)
    return parsed


def _validate_favorite_json_depth(value: Any, depth: int = 0) -> None:
    if depth > _MAX_FAVORITES_JSON_DEPTH:
        raise ValueError("favorite JSON is too deeply nested")
    if isinstance(value, dict):
        for child in value.values():
            _validate_favorite_json_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_favorite_json_depth(child, depth + 1)


def _favorites_json_ambiguous(message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "FAVORITES_JSON_AMBIGUOUS",
            "message": message,
        },
    )


async def _require_unambiguous_favorite_json(request: Request) -> None:
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise _favorites_json_ambiguous("收藏写入正文必须是有界 JSON 对象")
    body = await request.body()
    if not body or len(body) > _MAX_FAVORITES_MUTATION_JSON_BYTES:
        raise _favorites_json_ambiguous("收藏写入正文为空或超出边界")
    try:
        payload = json.loads(
            body,
            object_pairs_hook=_reject_duplicate_favorite_json_keys,
            parse_constant=_reject_non_finite_favorite_json_number,
            parse_float=_finite_favorite_json_float,
        )
        if not isinstance(payload, dict):
            raise ValueError("favorite JSON root must be an object")
        _validate_favorite_json_depth(payload)
    except (TypeError, UnicodeError, ValueError, RecursionError) as exc:
        raise _favorites_json_ambiguous(
            "收藏正文必须是无重复键、有限且深度受限的 JSON 对象"
        ) from exc


class _StrictFavoriteRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def strict_route_handler(request: Request):
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                await _require_unambiguous_favorite_json(request)
            return await route_handler(request)

        return strict_route_handler


_MAX_IDENTITY_SECURITY_JSON_BYTES = 4 * 1024
_MAX_IDENTITY_SECURITY_JSON_DEPTH = 4
_MAX_IDENTITY_SECURITY_JSON_NODES = 32


def _identity_security_json_invalid() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "IDENTITY_SECURITY_JSON_INVALID",
            "message": "身份安全写入必须是受界、无歧义的 JSON 对象",
        },
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _reject_duplicate_identity_security_keys(
    pairs: List[tuple[str, Any]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate identity security JSON key")
        payload[key] = value
    return payload


def _finite_identity_security_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("identity security JSON number is not finite")
    return parsed


def _validate_identity_security_json(
    value: Any,
    *,
    depth: int = 0,
    nodes: Optional[List[int]] = None,
) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if (
        depth > _MAX_IDENTITY_SECURITY_JSON_DEPTH
        or nodes[0] > _MAX_IDENTITY_SECURITY_JSON_NODES
    ):
        raise ValueError("identity security JSON exceeds structural limits")
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                not isinstance(key, str)
                or len(key) > 80
                or any(ord(character) < 32 or ord(character) == 127 for character in key)
            ):
                raise ValueError("identity security JSON key is invalid")
            key.encode("utf-8")
            _validate_identity_security_json(
                child,
                depth=depth + 1,
                nodes=nodes,
            )
        return
    if isinstance(value, list):
        for child in value:
            _validate_identity_security_json(
                child,
                depth=depth + 1,
                nodes=nodes,
            )
        return
    if isinstance(value, str):
        value.encode("utf-8")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("identity security JSON string contains control characters")
        return
    if value is not None and not isinstance(value, (bool, int, float)):
        raise ValueError("identity security JSON contains an unsupported value")


async def _require_unambiguous_identity_security_json(
    request: Request,
    *,
    allow_empty: bool = False,
) -> None:
    body = await request.body()
    raw_content_length = request.headers.get("content-length")
    if raw_content_length is not None:
        if re.fullmatch(r"0|[1-9][0-9]*", raw_content_length) is None:
            raise _identity_security_json_invalid()
        declared_length = int(raw_content_length)
        if (
            declared_length != len(body)
            or declared_length > _MAX_IDENTITY_SECURITY_JSON_BYTES
        ):
            raise _identity_security_json_invalid()
    if not body:
        if allow_empty:
            return
        raise _identity_security_json_invalid()
    if allow_empty or len(body) > _MAX_IDENTITY_SECURITY_JSON_BYTES:
        raise _identity_security_json_invalid()
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise _identity_security_json_invalid()
    try:
        payload = json.loads(
            body,
            object_pairs_hook=_reject_duplicate_identity_security_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            parse_float=_finite_identity_security_float,
        )
        if not isinstance(payload, dict):
            raise ValueError("identity security JSON root must be an object")
        _validate_identity_security_json(payload)
    except (TypeError, UnicodeError, ValueError, RecursionError):
        raise _identity_security_json_invalid() from None


class _StrictIdentitySecurityRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def strict_route_handler(request: Request):
            response_headers = {
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            }
            try:
                if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                    await _require_unambiguous_identity_security_json(
                        request,
                        allow_empty=(
                            request.url.path == "/api/user/security/mfa/enroll"
                        ),
                    )
                response = await route_handler(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "IDENTITY_SECURITY_REQUEST_INVALID",
                        "message": "身份安全请求字段无效",
                    },
                    headers=response_headers,
                ) from exc
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), **response_headers}
                raise
            response.headers.update(response_headers)
            return response

        return strict_route_handler


def _favorite_user_id(user: Dict[str, Any]) -> int:
    try:
        user_id = int(user.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FAVORITES_ACCOUNT_UNAVAILABLE",
                "message": "当前身份不能访问账号收藏",
            },
        )
    return user_id

_SECRET_PATH_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){0,3}$")
_NON_SECRET_ROOT_FIELDS = {
    "image_backend",
    "image_base_url",
    "image_openai_base_url",
    "image_qwen_base_url",
    "image_model",
    "image_openai_model",
    "image_qwen_model",
}
_NON_SECRET_NESTED_FIELDS = {
    "backend",
    "base_url",
    "openai_base_url",
    "qwen_base_url",
    "model",
    "openai_model",
    "qwen_model",
}
_CLEAR_PATH_ALIASES = {
    "image.api_key": (
        "image.api_key",
        "image.openai_api_key",
        "image.qwen_api_key",
        "image_api_key",
        "image_openai_api_key",
        "image_qwen_api_key",
        "qwen_image",
    )
}


def _is_secret_path(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    key = path[-1].lower()
    if key in {"api_key", "openai_api_key", "qwen_api_key", "token", "secret", "password"}:
        return True
    if key.endswith(("_api_key", "_token", "_secret", "_password")):
        return True
    if len(path) == 1:
        return key not in _NON_SECRET_ROOT_FIELDS and not key.endswith(("_url", "_model", "_backend"))
    return key not in _NON_SECRET_NESTED_FIELDS and not key.endswith(("_url", "_model", "_backend"))


def _validate_api_key_node(value: Any, *, depth: int = 0, count: Optional[List[int]] = None) -> None:
    if count is None:
        count = [0]
    if depth > 4:
        raise HTTPException(status_code=422, detail="API Key JSON 嵌套层级过深")
    if isinstance(value, dict):
        for key, child in value.items():
            count[0] += 1
            if count[0] > 64:
                raise HTTPException(status_code=422, detail="API Key JSON 字段过多")
            if not isinstance(key, str) or not re.match(r"^[A-Za-z0-9_-]{1,64}$", key):
                raise HTTPException(status_code=422, detail="API Key JSON 包含非法字段名")
            _validate_api_key_node(child, depth=depth + 1, count=count)
        return
    if isinstance(value, str):
        if len(value) > 8192:
            raise HTTPException(status_code=422, detail="API Key JSON 字段过长")
        return
    if value is not None and not isinstance(value, (bool, int, float)):
        raise HTTPException(status_code=422, detail="API Key JSON 仅支持对象和基础值")


def _parse_api_key_object(raw: Optional[str], *, strict: bool) -> Dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_api_config_keys,
            parse_constant=_reject_non_finite_api_config_number,
        )
    except (TypeError, ValueError):
        if strict:
            raise HTTPException(status_code=422, detail="API Key 配置必须是合法 JSON 对象")
        return {}
    if not isinstance(parsed, dict):
        if strict:
            raise HTTPException(status_code=422, detail="API Key 配置必须是 JSON 对象")
        return {}
    if strict:
        _validate_api_key_node(parsed)
        parsed = _normalize_public_provider_urls(parsed)
    return parsed


def _reject_duplicate_api_config_keys(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate API config key")
        output[key] = value
    return output


def _reject_non_finite_api_config_number(value: str) -> None:
    raise ValueError(f"non-finite API config number: {value}")


def _is_public_provider_url_path(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    key = path[-1].lower()
    return key == "base_url" or key.endswith("_base_url")


def _normalize_public_provider_urls(
    value: Any,
    path: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_public_provider_urls(child, path + (str(key),))
            for key, child in value.items()
        }
    if not _is_public_provider_url_path(path):
        return value
    if value is None:
        return None
    try:
        return normalize_provider_base_url(value)
    except ProviderBaseUrlError as exc:
        raise HTTPException(
            status_code=422,
            detail="API 配置包含不安全的 provider URL",
        ) from exc


def _api_key_status(raw: Optional[str]) -> Dict[str, bool]:
    result: Dict[str, bool] = {}

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, path + (str(key),))
        elif _is_secret_path(path) and isinstance(value, str) and value.strip():
            result[".".join(path)] = True

    visit(_parse_api_key_object(raw, strict=False), ())
    if any(result.get(path) for path in _CLEAR_PATH_ALIASES["image.api_key"]):
        result["image.api_key"] = True
    return result


def _public_api_config(raw: Optional[str]) -> Dict[str, Any]:
    config = _parse_api_key_object(raw, strict=False)
    public: Dict[str, Any] = {}
    for key in _NON_SECRET_ROOT_FIELDS:
        value = config.get(key)
        if not isinstance(value, (str, int, float, bool)):
            continue
        if _is_public_provider_url_path((key,)):
            value = provider_base_url_or_none(value)
            if value is None:
                continue
        public[key] = value
    image = config.get("image")
    if isinstance(image, dict):
        public_image: Dict[str, Any] = {}
        for key in _NON_SECRET_NESTED_FIELDS:
            value = image.get(key)
            if not isinstance(value, (str, int, float, bool)):
                continue
            if _is_public_provider_url_path(("image", key)):
                value = provider_base_url_or_none(value)
                if value is None:
                    continue
            public_image[key] = value
        if public_image:
            public["image"] = public_image
    return public


def _merge_api_key_objects(
    existing: Dict[str, Any], incoming: Dict[str, Any], path: tuple[str, ...] = ()
) -> Dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        child_path = path + (key,)
        if isinstance(value, dict):
            previous = merged.get(key)
            merged[key] = _merge_api_key_objects(
                previous if isinstance(previous, dict) else {}, value, child_path
            )
        elif _is_secret_path(child_path) and (value is None or not str(value).strip()):
            # Empty secret inputs mean "keep existing". Removal is explicit via
            # clear_api_keys, preventing a model/base URL edit from erasing keys.
            continue
        else:
            merged[key] = value
    return merged


def _remove_api_key_path(config: Dict[str, Any], dotted_path: str) -> None:
    if not _SECRET_PATH_RE.fullmatch(dotted_path or ""):
        raise HTTPException(status_code=422, detail="clear_api_keys 包含非法路径")
    parts = dotted_path.split(".")
    cursor: Dict[str, Any] = config
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            return
        cursor = child
    cursor.pop(parts[-1], None)


def _remove_api_key_and_aliases(config: Dict[str, Any], dotted_path: str) -> None:
    for path in _CLEAR_PATH_ALIASES.get(dotted_path, (dotted_path,)):
        _remove_api_key_path(config, path)


def _trigger_user_akm(user_row: models.User) -> None:
    """Send AKM configuration over stdin so secrets never appear in argv."""
    setup_akm = raw_setting("SETUP_USER_AKM_PATH", "/usr/local/bin/setup-user-akm")
    if not (os.path.isfile(setup_akm) and os.access(setup_akm, os.X_OK)):
        return
    payload = {
        "user_id": int(user_row.id),
        "username": (user_row.username or "").strip(),
        "api_keys": _parse_api_key_object(user_row.api_keys, strict=False),
        "active_provider": (user_row.active_provider or "").strip(),
        "default_model": (user_row.default_model or "").strip(),
        "base_url": provider_base_url_or_none(user_row.base_url) or "",
    }

    def run() -> None:
        try:
            completed = subprocess.run(
                [setup_akm, "--stdin-json"],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            if completed.returncode:
                print(
                    f"[api-config] setup-user-akm exited with code {completed.returncode}",
                    flush=True,
                )
        except Exception as exc:
            print(f"[api-config] setup-user-akm failed: {type(exc).__name__}", flush=True)

    threading.Thread(target=run, name="setup-user-akm", daemon=True).start()

def _validate_password_rules(password: str, confirm_password: str):
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="两次输入密码不一致")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(status_code=400, detail=f"密码长度至少 {PASSWORD_MIN_LENGTH} 位")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="密码需包含字母和数字")


def _serialize_user_profile(user_row: models.User) -> Dict[str, Any]:
    api_keys_raw = getattr(user_row, "api_keys", None)
    return {
        "id": int(user_row.id),
        "username": user_row.username,
        "full_name": user_row.full_name or "",
        "email": user_row.email or "",
        "phone": user_row.phone or "",
        "created_at": user_row.created_at,
        "updated_at": user_row.updated_at,
        "is_active": bool(getattr(user_row, "is_active", True)),
        "last_login_at": getattr(user_row, "last_login_at", None),
        "role": (getattr(user_row, "role", None) or "user"),
        "avatar_url": getattr(user_row, "avatar_url", None) or "",
        "api_keys": None,
        "api_key_status": _api_key_status(api_keys_raw),
        "api_config_public": _public_api_config(api_keys_raw),
        "active_provider": getattr(user_row, "active_provider", None),
        "default_model": getattr(user_row, "default_model", None),
        "base_url": provider_base_url_or_none(getattr(user_row, "base_url", None)),
    }


def _privacy_request_store() -> PrivacyDeletionRequestStore:
    return PrivacyDeletionRequestStore(
        Path(string_setting("PRIVACY_RIGHTS_ROOT", "/root/data/web/privacy-rights"))
    )


def _raise_privacy_error(exc: Exception) -> None:
    if isinstance(exc, PrivacyRightsNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PrivacyRightsConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, PrivacyRightsUnavailable):
        raise HTTPException(
            status_code=503,
            detail="privacy rights service is unavailable",
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _build_password_reset_link(token: str) -> str:
    base = raw_setting(
        "FRONTEND_RESET_PASSWORD_URL",
        "http://localhost:5173/reset-password",
    )
    connector = "&" if "?" in base else "?"
    return f"{base}{connector}token={token}"


class BaseEmailSender:
    def send_password_reset(self, to_email: str, reset_link: str):
        raise NotImplementedError()


class LogEmailSender(BaseEmailSender):
    def send_password_reset(self, to_email: str, reset_link: str):
        if is_production():
            print(
                f"[PasswordReset][DELIVERY_DISABLED] recipient configured={bool(to_email)}",
                flush=True,
            )
            return
        print(f"[PasswordReset][DEV_ONLY] to={to_email}, reset_link={reset_link}")


class SMTPEmailSender(BaseEmailSender):
    def __init__(self):
        self.host = raw_setting("SMTP_HOST")
        self.port = int_setting("SMTP_PORT", 587)
        self.username = raw_setting("SMTP_USERNAME")
        self.password = raw_setting("SMTP_PASSWORD")
        self.sender = raw_setting("SMTP_SENDER", self.username)
        self.use_tls = bool_setting("SMTP_USE_TLS", True)

    def send_password_reset(self, to_email: str, reset_link: str):
        msg = EmailMessage()
        msg["Subject"] = "密码重置"
        msg["From"] = self.sender
        msg["To"] = to_email
        msg.set_content(f"请访问以下链接重置密码（{PASSWORD_RESET_EXPIRE_MINUTES}分钟内有效）：\n{reset_link}")
        with smtplib.SMTP(self.host, self.port) as server:
            if self.use_tls:
                server.starttls()
            if self.username:
                server.login(self.username, self.password)
            server.send_message(msg)


def get_email_sender() -> BaseEmailSender:
    enabled = bool_setting("SMTP_ENABLED")
    if enabled:
        return SMTPEmailSender()
    return LogEmailSender()


def _authenticate_user_for_login(
    db: Session,
    login_id: str,
    raw_password: str,
) -> Optional[models.User]:
    return authenticate_login(db, login_id, raw_password)


def get_user_for_login(db: Session, login_id: str, raw_password: str) -> Optional[Dict[str, Any]]:
    """Compatibility helper returning the public profile for valid DB credentials."""
    user_row = _authenticate_user_for_login(db, login_id, raw_password)
    return _serialize_user_profile(user_row) if user_row is not None else None


def _development_admin_fallback() -> Optional[tuple[str, str]]:
    """Return explicitly enabled development-only admin fallback credentials."""
    if is_production() or not bool_setting("ALLOW_DEV_ADMIN_PASSWORD_LOGIN"):
        return None
    username = string_setting("ADMIN_USER", "admin")
    password = raw_setting("ADMIN_PASSWORD")
    if not username or not password:
        return None
    return username, password


def _identity_assurance_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "IDENTITY_ASSURANCE_UNAVAILABLE",
            "message": "身份安全服务暂时不可用",
            "fallback": "none",
        },
    )


def _tracked_token_material(user_row: models.User) -> Dict[str, Any]:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return {
        "jti": secrets.token_urlsafe(32),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "auth_version": _password_auth_version(str(user_row.password_hash or "")),
    }


def _tracked_login_response(
    user_row: models.User,
    *,
    material: Dict[str, Any],
) -> Dict[str, Any]:
    token = create_access_token(
        int(user_row.id),
        user_row.username,
        password_hash=user_row.password_hash,
        jti=material["jti"],
        session_tracking="tracked",
        issued_at=material["issued_at"],
        expires_at=material["expires_at"],
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "session_tracking": "tracked",
        "user": _serialize_user_profile(user_row),
    }


# ================== Route: POST /api/auth/login ==================
@router.post("/api/auth/login", tags=["认证"])
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate against the database; production has no master password."""
    username = (body.username or "").strip()
    password = body.password or ""
    user_row = authenticate_login(
        db,
        username,
        password,
        development_admin=_development_admin_fallback(),
    )
    if user_row is not None:
        try:
            store = configured_identity_assurance_store()
            status_payload = store.status(int(user_row.id))
            auth_version = _password_auth_version(str(user_row.password_hash or ""))
            if status_payload["enabled"]:
                # Password verification has completed, but no access token is
                # minted until the one-time challenge consumes a second factor.
                return store.create_login_challenge(
                    int(user_row.id),
                    auth_version=auth_version,
                )
            material = _tracked_token_material(user_row)
            store.issue_session(
                int(user_row.id),
                jti=material["jti"],
                issued_at=material["issued_at"],
                expires_at=material["expires_at"],
                auth_version=material["auth_version"],
            )
            return _tracked_login_response(user_row, material=material)
        except IdentityAssuranceUnavailable as exc:
            raise _identity_assurance_unavailable(exc) from exc

    raise HTTPException(status_code=401, detail="用户名或密码错误")


@router.post("/api/auth/login/mfa", tags=["认证"])
def complete_mfa_login(body: LoginMfaVerifyRequest, db: Session = Depends(get_db)):
    """Consume one short-lived challenge before issuing a tracked JWT."""
    try:
        store = configured_identity_assurance_store()
        user_id = store.challenge_user_id(body.challenge)
    except LoginChallengeRejected as exc:
        raise HTTPException(status_code=401, detail="验证失败或挑战已失效") from exc
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc
    user_row = (
        db.query(models.User)
        .filter(models.User.id == user_id, models.User.is_active.is_(True))
        .first()
    )
    if user_row is None:
        raise HTTPException(status_code=401, detail="验证失败或挑战已失效")
    material = _tracked_token_material(user_row)
    try:
        store.complete_login_challenge(
            body.challenge,
            code=body.code,
            recovery_code=body.recovery_code,
            jti=material["jti"],
            issued_at=material["issued_at"],
            expires_at=material["expires_at"],
            auth_version=material["auth_version"],
        )
    except (LoginChallengeRejected, IdentityFactorRejected) as exc:
        raise HTTPException(status_code=401, detail="验证失败或挑战已失效") from exc
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc
    return _tracked_login_response(user_row, material=material)


# ================== Route: POST /api/auth/register ==================
@router.post("/api/auth/register", tags=["认证"])
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """新用户由数据库序列分配 id（与种子用户 id=1 无关）；若表曾清空，重启 API 会通过 ensure_default_app_user 补回 id=1。"""
    username = (body.username or "").strip()
    full_name = (body.full_name or "").strip()
    email = (body.email or "").strip().lower()
    phone = (body.phone or "").strip()
    password = body.password or ""
    if not username or not email:
        raise HTTPException(status_code=400, detail="用户名和邮箱不能为空")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if phone and not re.match(r"^1[3-9]\d{9}$", phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    _validate_password_rules(password, body.confirm_password or "")

    if db.query(models.User.id).filter(models.User.username == username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")
    if db.query(models.User.id).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="邮箱已存在")
    if phone and db.query(models.User.id).filter(models.User.phone == phone).first():
        raise HTTPException(status_code=409, detail="手机号已存在")

    now = datetime.now(timezone.utc)
    row = models.User(
        username=username,
        password_hash=_hash_password(password),
        full_name=full_name or None,
        email=email,
        phone=phone or None,
        created_at=now,
        updated_at=now,
        is_active=True,
        role="user",
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="注册冲突：用户名、邮箱或手机号可能已被使用")
    except SQLAlchemyError as exc:
        db.rollback()
        print(f"[auth] 注册入库失败: {type(exc).__name__}", flush=True)
        raise HTTPException(
            status_code=500,
            detail="注册暂时不可用，请稍后重试或联系支持人员",
        ) from exc
    try:
        ensure_assistant_user_defaults(username)
    except (OSError, ValueError) as exc:
        # Registration remains successful; the first assistant visit retries the
        # idempotent bootstrap if the filesystem is temporarily unavailable.
        print(f"[auth] 用户默认工作区初始化稍后重试: {type(exc).__name__}", flush=True)
    return {"user": _serialize_user_profile(row)}


# ================== Route: POST /api/auth/forgot-password ==================
@router.post("/api/auth/forgot-password", tags=["认证"])
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = (body.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")

    user_row = db.query(models.User).filter(models.User.email == email).first()
    if user_row:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES)
        token_row = models.PasswordResetToken(
            user_id=user_row.id,
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=None,
            created_at=now,
        )
        db.add(token_row)
        db.commit()
        reset_link = _build_password_reset_link(raw_token)
        get_email_sender().send_password_reset(email, reset_link)
    # 防止邮箱枚举，统一返回成功文案
    return {"ok": True, "message": "若邮箱已注册，将收到密码重置链接"}


# ================== Route: POST /api/auth/reset-password ==================
@router.post("/api/auth/reset-password", tags=["认证"])
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token 不能为空")
    _validate_password_rules(body.new_password or "", body.confirm_password or "")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    token_row = (
        db.query(models.PasswordResetToken)
        .filter(
            models.PasswordResetToken.token_hash == token_hash,
            models.PasswordResetToken.used_at.is_(None),
            models.PasswordResetToken.expires_at > now,
        )
        .order_by(desc(models.PasswordResetToken.id))
        .first()
    )
    if not token_row:
        raise HTTPException(status_code=400, detail="重置链接无效或已过期")
    user_row = db.query(models.User).filter(models.User.id == token_row.user_id).first()
    if not user_row:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        configured_identity_assurance_store().revoke_all_sessions(
            int(user_row.id),
            reason="password reset revoked tracked sessions",
        )
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc
    user_row.password_hash = _hash_password(body.new_password or "")
    user_row.updated_at = now
    token_row.used_at = now
    db.commit()
    return {"ok": True}


# ================== Route: GET /api/auth/me ==================
@router.get("/api/auth/me", tags=["认证"])
def auth_me(
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """获取当前用户信息，需携带 Authorization: Bearer <token>。"""
    user_id = int(user.get("user_id") or 0)
    row = db.query(models.User).filter(models.User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _serialize_user_profile(row)


def _current_identity_id(user: Dict[str, Any]) -> int:
    user_id = int(user.get("user_id") or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持身份安全设置")
    return user_id


def _tracked_session_claims(user: Dict[str, Any]) -> tuple[str, str]:
    if user.get("session_tracking") != "tracked":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CURRENT_SESSION_UNTRACKED",
                "message": "当前令牌不属于可管理会话，请重新登录后操作",
            },
        )
    jti = str(user.get("jti") or "")
    auth_version = str(user.get("auth_version") or "")
    if not jti or not auth_version:
        raise HTTPException(status_code=401, detail="未登录或 token 无效")
    return jti, auth_version


router.route_class = _StrictIdentitySecurityRoute


@router.get("/api/user/security/mfa", tags=["用户安全"])
def get_mfa_status(user: Dict[str, Any] = Depends(get_current_user_required)):
    try:
        return configured_identity_assurance_store().status(_current_identity_id(user))
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc


@router.post("/api/user/security/mfa/enroll", tags=["用户安全"])
def begin_mfa_enrollment(user: Dict[str, Any] = Depends(get_current_user_required)):
    _tracked_session_claims(user)
    try:
        return configured_identity_assurance_store().begin_enrollment(
            _current_identity_id(user),
            account_label=str(user.get("username") or "account"),
        )
    except IdentityAssuranceConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc


@router.post("/api/user/security/mfa/confirm", tags=["用户安全"])
def confirm_mfa_enrollment(
    body: MfaConfirmRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
):
    _tracked_session_claims(user)
    try:
        return configured_identity_assurance_store().confirm_enrollment(
            _current_identity_id(user),
            body.code,
        )
    except IdentityAssuranceConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except IdentityFactorRejected as exc:
        raise HTTPException(status_code=403, detail="验证码无效或已使用") from exc
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc


@router.post("/api/user/security/mfa/disable", tags=["用户安全"])
def disable_mfa(
    body: MfaDisableRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    _tracked_session_claims(user)
    user_id = _current_identity_id(user)
    if body.code is not None:
        row = db.query(models.User).filter(models.User.id == user_id).first()
        if row is None:
            raise HTTPException(status_code=403, detail="安全验证失败")
        password_ok, _needs_upgrade = verify_login_password(
            body.password or "",
            row.password_hash,
        )
        if not password_ok:
            raise HTTPException(status_code=403, detail="安全验证失败")
    try:
        configured_identity_assurance_store().disable_mfa(
            user_id,
            code=body.code,
            recovery_code=body.recovery_code,
        )
    except IdentityAssuranceConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except IdentityFactorRejected as exc:
        raise HTTPException(status_code=403, detail="安全验证失败") from exc
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc
    return {"ok": True, "status": "disabled"}


@router.get("/api/user/security/sessions", tags=["用户安全"])
def list_security_sessions(user: Dict[str, Any] = Depends(get_current_user_required)):
    jti, auth_version = _tracked_session_claims(user)
    try:
        return configured_identity_assurance_store().list_sessions(
            _current_identity_id(user),
            current_jti=jti,
            auth_version=auth_version,
        )
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc


@router.post("/api/user/security/sessions/{session_id}/revoke", tags=["用户安全"])
def revoke_security_session(
    session_id: str,
    body: SessionRevokeRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
):
    jti, _auth_version = _tracked_session_claims(user)
    try:
        store = configured_identity_assurance_store()
        store.revoke_session(
            _current_identity_id(user),
            session_id=session_id,
            reason=body.reason,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "current_session_revoked": hmac.compare_digest(
                session_id,
                hash_session_jti(jti),
            ),
        }
    except IdentityAssuranceConflict as exc:
        raise HTTPException(status_code=404, detail={"code": str(exc)}) from exc
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc


@router.post("/api/user/security/sessions/revoke-others", tags=["用户安全"])
def revoke_other_security_sessions(
    body: SessionRevokeRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
):
    jti, _auth_version = _tracked_session_claims(user)
    try:
        count = configured_identity_assurance_store().revoke_other_sessions(
            _current_identity_id(user),
            current_jti=jti,
            reason=body.reason,
        )
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc
    return {"ok": True, "revoked_count": count}


@router.get("/api/user/security/audit", tags=["用户安全"])
def get_security_audit(
    limit: int = Query(default=100, ge=1, le=200),
    user: Dict[str, Any] = Depends(get_current_user_required),
):
    try:
        return configured_identity_assurance_store().audit(
            _current_identity_id(user),
            limit=limit,
        )
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc


router.route_class = APIRoute


# ================== Route: GET /api/user/profile ==================
@router.get("/api/user/profile", response_model=UserProfileResponse, tags=["用户"])
def get_user_profile(
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    user_id = int(user.get("user_id") or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持个人资料")
    row = db.query(models.User).filter(models.User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _serialize_user_profile(row)


# ================== Route: PUT /api/user/profile ==================
@router.put("/api/user/profile", response_model=UserProfileResponse, tags=["用户"])
def update_user_profile(
    body: UserProfileUpdateRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    user_id = int(user.get("user_id") or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持个人资料修改")
    full_name = (body.full_name or "").strip()
    email = (body.email or "").strip().lower()
    phone = (body.phone or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if phone and not re.match(r"^1[3-9]\d{9}$", phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")

    row = db.query(models.User).filter(models.User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    email_conflict = (
        db.query(models.User.id)
        .filter(models.User.email == email, models.User.id != user_id)
        .first()
    )
    if email_conflict:
        raise HTTPException(status_code=409, detail="邮箱已被占用")
    phone_conflict = None
    if phone:
        phone_conflict = (
            db.query(models.User.id)
            .filter(models.User.phone == phone, models.User.id != user_id)
            .first()
        )
    if phone_conflict:
        raise HTTPException(status_code=409, detail="手机号已被占用")

    row.full_name = full_name or None
    row.email = email
    row.phone = phone or None
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _serialize_user_profile(row)


# ================== Personal data access / deletion request intake ==================
def _canonical_privacy_subject(
    user: Dict[str, Any],
    db: Session,
) -> tuple[int, str, models.User]:
    raw_user_id = user.get("user_id")
    if type(raw_user_id) is not int or raw_user_id <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持个人数据操作")
    user_id = raw_user_id
    row = db.query(models.User).filter(models.User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    raw_canonical_username = getattr(row, "username", None)
    claimed_username = user.get("username")
    if type(raw_canonical_username) is not str:
        raise HTTPException(status_code=503, detail="账号主体暂不可用")
    canonical_username = raw_canonical_username.strip()
    if (
        not canonical_username
        or raw_canonical_username != canonical_username
        or type(claimed_username) is not str
        or claimed_username != canonical_username
    ):
        raise HTTPException(status_code=403, detail="当前身份与账号主体不一致")
    return user_id, canonical_username, row


def _personal_data_export_adapters(
    user_id: int,
    canonical_username: str,
) -> tuple[PersonalDataExportAdapterBinding, ...]:
    return (
        PersonalDataExportAdapterBinding(
            scope="assistant_workspace_files",
            reader=lambda: configured_assistant_privacy_export_reader().export_workspaces(
                subject_id=user_id,
                username=canonical_username,
            ),
        ),
        PersonalDataExportAdapterBinding(
            scope="assistant_schedules_and_generated_reports",
            reader=lambda: (
                configured_assistant_privacy_export_reader().export_schedules_and_reports(
                    subject_id=user_id,
                    username=canonical_username,
                )
            ),
        ),
        PersonalDataExportAdapterBinding(
            scope="research_workflow_projects",
            reader=lambda: build_research_subject_export(
                configured_research_repository(),
                subject_id=user_id,
                username=canonical_username,
            ),
        ),
    )


def _build_canonical_personal_export(
    db: Session,
    *,
    user_id: int,
    canonical_username: str,
    row: models.User,
) -> Dict[str, Any]:
    return build_personal_data_export(
        db,
        subject_id=user_id,
        subject_username=canonical_username,
        account=_serialize_user_profile(row),
        adapters=_personal_data_export_adapters(user_id, canonical_username),
    )


@router.get("/api/user/privacy/export", tags=["用户"])
def export_personal_data(
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """Return a bounded self-service export and name every unavailable scope."""
    try:
        user_id, canonical_username, row = _canonical_privacy_subject(user, db)
        return _build_canonical_personal_export(
            db,
            user_id=user_id,
            canonical_username=canonical_username,
            row=row,
        )
    except Exception as exc:
        _raise_privacy_error(exc)


@router.get("/api/user/privacy/deletion-impact-plan", tags=["用户"])
def preview_account_deletion_impact(
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """Build a read-only impact plan; never register or perform deletion."""
    try:
        user_id, canonical_username, row = _canonical_privacy_subject(user, db)
        account = _serialize_user_profile(row)
        personal_export = _build_canonical_personal_export(
            db,
            user_id=user_id,
            canonical_username=canonical_username,
            row=row,
        )
        return build_account_deletion_impact_plan(
            subject_id=user_id,
            subject_username=canonical_username,
            account=account,
            personal_export=personal_export,
        )
    except Exception as exc:
        _raise_privacy_error(exc)


@router.get("/api/user/privacy/deletion-requests", tags=["用户"])
def list_personal_deletion_requests(
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    try:
        user_id, _canonical_username, _row = _canonical_privacy_subject(user, db)
        return _privacy_request_store().list(user_id)
    except Exception as exc:
        _raise_privacy_error(exc)


@router.post("/api/user/privacy/deletion-requests", status_code=201, tags=["用户"])
def create_personal_deletion_request(
    body: PrivacyDeletionRequestCreate,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """Record an authenticated request without pretending erasure has run."""
    try:
        user_id, _canonical_username, row = _canonical_privacy_subject(user, db)
        password_ok, _needs_upgrade = verify_login_password(
            body.password,
            row.password_hash,
        )
        if not password_ok:
            raise HTTPException(status_code=403, detail="密码确认失败")
        return _privacy_request_store().create(user_id)
    except Exception as exc:
        _raise_privacy_error(exc)


@router.post(
    "/api/user/privacy/deletion-requests/{request_id}/cancel",
    tags=["用户"],
)
def cancel_personal_deletion_request(
    request_id: str,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    try:
        user_id, _canonical_username, _row = _canonical_privacy_subject(user, db)
        return _privacy_request_store().cancel(
            user_id,
            request_id,
        )
    except Exception as exc:
        _raise_privacy_error(exc)


# ================== Route: POST /api/user/change-password ==================
@router.post("/api/user/change-password", tags=["用户"])
def change_password(
    body: ChangePasswordRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    user_id = int(user.get("user_id") or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持修改密码")
    _validate_password_rules(body.new_password or "", body.confirm_password or "")
    row = db.query(models.User).filter(models.User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")

    old_ok, _ = verify_login_password(body.old_password or "", row.password_hash)
    if not old_ok:
        raise HTTPException(status_code=400, detail="旧密码错误")
    try:
        configured_identity_assurance_store().revoke_all_sessions(
            user_id,
            reason="password change revoked tracked sessions",
        )
    except IdentityAssuranceUnavailable as exc:
        raise _identity_assurance_unavailable(exc) from exc
    row.password_hash = _hash_password(body.new_password or "")
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# ================== Route: PUT /api/user/api-config ==================
@router.put("/api/user/api-config", response_model=UserProfileResponse, tags=["用户"])
def update_user_api_config(
    body: ApiConfigUpdateRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    user_id = int(user.get("user_id") or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持此操作")
    row = db.query(models.User).filter(models.User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.api_keys is not None or body.clear_api_keys:
        if not secret_store_configured():
            raise HTTPException(
                status_code=503,
                detail="服务器密钥存储尚未配置，暂时无法保存 API Key",
            )
        existing = _parse_api_key_object(row.api_keys, strict=False)
        incoming = _parse_api_key_object(body.api_keys, strict=True)
        merged = _merge_api_key_objects(existing, incoming)
        for dotted_path in body.clear_api_keys:
            _remove_api_key_and_aliases(merged, dotted_path)
        row.api_keys = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    if body.active_provider is not None:
        row.active_provider = body.active_provider
    if body.default_model is not None:
        row.default_model = body.default_model
    if body.base_url is not None:
        row.base_url = body.base_url
    row.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except SecretStoreConfigurationError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="服务器密钥存储配置无效，暂时无法保存 API Key",
        )
    db.refresh(row)

    _trigger_user_akm(row)

    return _serialize_user_profile(row)


# ================== Route: GET /api/user/search-history ==================
@router.get("/api/user/search-history", tags=["用户"])
def get_user_search_history(
    limit: int = Query(50, ge=1, le=200),
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    if int(user.get("user_id") or 0) <= 0:
        return {"data": []}
    rows = (
        db.query(models.UserSearchHistory)
        .filter(models.UserSearchHistory.user_id == user["user_id"])
        .order_by(desc(models.UserSearchHistory.id))
        .limit(limit)
        .all()
    )
    return {
        "data": [
            {
                "id": r.id,
                "query": r.keyword,
                "time": (r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""),
            }
            for r in rows
        ]
    }


# ================== Route: POST /api/user/search-history ==================
@router.post("/api/user/search-history", tags=["用户"])
def add_user_search_history(
    body: SearchHistoryRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    if int(user.get("user_id") or 0) <= 0:
        return {"ok": False, "message": "当前账号未绑定用户表，无法保存搜索记录"}
    kw = (body.keyword or "").strip()
    if not kw:
        raise HTTPException(status_code=400, detail="keyword 不能为空")
    row = models.UserSearchHistory(
        user_id=user["user_id"],
        keyword=kw[:255],
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    _safe_commit_user_side_effect(db)
    return {"ok": True}


# ================== Route: GET /api/user/favorites ==================
# Earlier auth/account routes keep their existing route class. Favorites are a
# mutation-sensitive boundary and parse their raw body before FastAPI/Pydantic.
router.route_class = _StrictFavoriteRoute


@router.get("/api/user/favorites", tags=["用户"])
def get_user_favorites(
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    user_id = _favorite_user_id(user)
    rows = (
        db.query(models.UserFavorite)
        .filter(models.UserFavorite.user_id == user_id)
        .order_by(desc(models.UserFavorite.id))
        .all()
    )
    items: List[Dict[str, Any]] = []
    favorite_ids: set[int] = set()
    favorite_records = 0
    warning_records = 0
    invalid_records = 0
    for row in rows:
        kind = str(row.item_kind or "").strip().lower()
        if kind not in {"favorite", "warning"}:
            invalid_records += 1
            continue
        news_id = int(row.news_id)
        if kind == "favorite":
            favorite_records += 1
            favorite_ids.add(news_id)
        else:
            warning_records += 1
        items.append(
            {
                "news_id": news_id,
                "topic": row.topic or "",
                "kind": kind,
                "created_at": row.created_at,
            }
        )
    return {
        "schema_version": "user-favorites-v2",
        "items": items,
        # Compatibility projection: warnings are intentionally not favorites.
        "news_ids": sorted(favorite_ids),
        "counts": {
            "favorite_records": favorite_records,
            "warning_records": warning_records,
            "invalid_records": invalid_records,
            "distinct_favorite_news": len(favorite_ids),
        },
        "merge_semantics": "exact_topic_and_kind_records; news_ids_deduplicate_favorites_only",
    }


# ================== Route: POST /api/user/favorites/toggle ==================
@router.post(
    "/api/user/favorites/toggle",
    tags=["用户"],
)
def toggle_user_favorite(
    body: FavoriteToggleRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    user_id = _favorite_user_id(user)
    news_id = body.news_id
    topic = _normalize_favorite_topic(body.topic)
    kind = _normalize_favorite_kind(body.kind)
    row = (
        db.query(models.UserFavorite)
        .filter(
            models.UserFavorite.user_id == user_id,
            models.UserFavorite.news_id == news_id,
            models.UserFavorite.topic == topic,
            models.UserFavorite.item_kind == kind,
        )
        .first()
    )
    if row:
        db.delete(row)
        _safe_commit_user_side_effect(db)
        return {"favorited": False}
    new_row = models.UserFavorite(
        user_id=user_id,
        news_id=news_id,
        topic=topic,
        item_kind=kind,
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_row)
    _safe_commit_user_side_effect(db)
    return {"favorited": True}


# ================== Route: POST /api/user/favorites/remove ==================
@router.post(
    "/api/user/favorites/remove",
    tags=["用户"],
)
def remove_user_favorite(
    body: FavoriteRemoveRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """显式取消收藏/预警（不会误触发 toggle 新增）。"""
    user_id = _favorite_user_id(user)
    news_id = body.news_id
    topic = _normalize_favorite_topic(body.topic)
    kind = _normalize_favorite_kind(body.kind)
    row = (
        db.query(models.UserFavorite)
        .filter(
            models.UserFavorite.user_id == user_id,
            models.UserFavorite.news_id == news_id,
            models.UserFavorite.topic == topic,
            models.UserFavorite.item_kind == kind,
        )
        .first()
    )
    if row:
        db.delete(row)
        _safe_commit_user_side_effect(db)
    return {"ok": True}


# ================== Route: POST /api/user/favorites/batch ==================
@router.post(
    "/api/user/favorites/batch",
    tags=["用户"],
)
def batch_set_user_favorites(
    body: FavoriteBatchRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """Atomically and idempotently set up to 100 exact favorite keys."""
    user_id = _favorite_user_id(user)
    requested_news_ids = sorted({operation.news_id for operation in body.operations})
    rows = (
        db.query(models.UserFavorite)
        .filter(
            models.UserFavorite.user_id == user_id,
            models.UserFavorite.news_id.in_(requested_news_ids),
        )
        .with_for_update()
        .all()
    )
    existing: Dict[tuple[int, str, str], models.UserFavorite] = {}
    for row in rows:
        kind = str(row.item_kind or "").strip().lower()
        if kind not in {"favorite", "warning"}:
            continue
        key = (int(row.news_id), str(row.topic or ""), kind)
        existing.setdefault(key, row)

    for index, operation in enumerate(body.operations):
        key = (operation.news_id, operation.topic, operation.kind)
        current = key in existing
        if (
            operation.expected_favorited is not None
            and operation.expected_favorited != current
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "FAVORITES_STATE_CONFLICT",
                    "message": "收藏状态已变化，请刷新后重试",
                    "operation_index": index,
                },
            )

    applied = 0
    results: List[Dict[str, Any]] = []
    for operation in body.operations:
        key = (operation.news_id, operation.topic, operation.kind)
        row = existing.get(key)
        current = row is not None
        if operation.favorited and not current:
            row = models.UserFavorite(
                user_id=user_id,
                news_id=operation.news_id,
                topic=operation.topic,
                item_kind=operation.kind,
                created_at=datetime.now(timezone.utc),
            )
            db.add(row)
            existing[key] = row
            applied += 1
        elif not operation.favorited and current:
            db.delete(row)
            existing.pop(key, None)
            applied += 1
        results.append(
            {
                "news_id": operation.news_id,
                "topic": operation.topic,
                "kind": operation.kind,
                "favorited": operation.favorited,
            }
        )

    if applied:
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "FAVORITES_CONCURRENT_UPDATE",
                    "message": "收藏状态被并发更新，请刷新后重试",
                },
            ) from exc
        except SQLAlchemyError as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "FAVORITES_STORAGE_UNAVAILABLE",
                    "message": "收藏存储暂时不可用",
                },
            ) from exc

    return {
        "schema_version": "favorite-batch-v1",
        "mutation_semantics": "atomic_explicit_set",
        "requested": len(body.operations),
        "applied": applied,
        "unchanged": len(body.operations) - applied,
        "results": results,
    }
