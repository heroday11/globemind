"""Stable request and response contracts for identity and account routes."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .provider_urls import normalize_provider_base_url


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginMfaVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    challenge: str = Field(min_length=40, max_length=512)
    code: Optional[str] = Field(default=None, pattern=r"^[0-9]{6}$")
    recovery_code: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$",
    )

    @model_validator(mode="after")
    def require_one_factor(self) -> "LoginMfaVerifyRequest":
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("provide exactly one MFA factor")
        return self


class MfaConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(pattern=r"^[0-9]{6}$")


class MfaDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    password: Optional[str] = Field(default=None, min_length=1, max_length=1024)
    code: Optional[str] = Field(default=None, pattern=r"^[0-9]{6}$")
    recovery_code: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$",
    )

    @model_validator(mode="after")
    def require_disable_proof(self) -> "MfaDisableRequest":
        password_and_totp = self.password is not None and self.code is not None
        recovery_only = (
            self.password is None
            and self.code is None
            and self.recovery_code is not None
        )
        if not (password_and_totp or recovery_only):
            raise ValueError("disable requires password plus TOTP, or one recovery code")
        if self.recovery_code is not None and not recovery_only:
            raise ValueError("recovery-code disable cannot be combined with other factors")
        return self


class SessionRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(default="user requested session revocation", min_length=2, max_length=500)


class RegisterRequest(BaseModel):
    username: str
    password: str
    confirm_password: str
    full_name: str = Field(default="", max_length=128)
    email: str
    phone: str = Field(default="", max_length=32)


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
    confirm_password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
    confirm_password: str


class UserProfileUpdateRequest(BaseModel):
    full_name: str = Field(default="", max_length=128)
    email: str
    phone: str = Field(default="", max_length=32)


class ApiConfigUpdateRequest(BaseModel):
    api_keys: Optional[str] = Field(default=None, max_length=32768)
    clear_api_keys: List[str] = Field(default_factory=list, max_length=32)
    active_provider: Optional[str] = Field(default=None, max_length=32)
    default_model: Optional[str] = Field(default=None, max_length=128)
    base_url: Optional[str] = Field(default=None, max_length=512)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_provider_base_url(value)


class UserProfileResponse(BaseModel):
    id: int
    username: str
    full_name: str = ""
    email: str = ""
    phone: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_active: bool = True
    last_login_at: Optional[datetime] = None
    role: str = "user"
    avatar_url: str = ""
    api_keys: Optional[str] = None
    api_key_status: Dict[str, bool] = Field(default_factory=dict)
    api_config_public: Dict[str, Any] = Field(default_factory=dict)
    active_provider: Optional[str] = None
    default_model: Optional[str] = None
    base_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PrivacyDeletionRequestCreate(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    acknowledgement: Literal["REQUEST ACCOUNT DELETION"]


class SearchHistoryRequest(BaseModel):
    keyword: str


class _FavoriteItemRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    news_id: int = Field(gt=0, le=2_147_483_647, strict=True)
    topic: str = Field(default="", max_length=255)
    kind: Literal["favorite", "warning"] = "favorite"

    @field_validator("topic")
    @classmethod
    def reject_ambiguous_topic(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("topic contains control characters")
        return value


class FavoriteToggleRequest(_FavoriteItemRequest):
    """Legacy non-idempotent toggle request retained for compatible clients."""


class FavoriteRemoveRequest(_FavoriteItemRequest):
    """Idempotently remove one exact favorite key."""


class FavoriteBatchOperation(_FavoriteItemRequest):
    favorited: bool
    expected_favorited: Optional[bool] = None


class FavoriteBatchRequest(BaseModel):
    """Atomically set at most 100 exact favorite keys to explicit states."""

    model_config = ConfigDict(extra="forbid", strict=True)

    operations: List[FavoriteBatchOperation] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reject_duplicate_operations(self) -> "FavoriteBatchRequest":
        seen: set[tuple[int, str, str]] = set()
        for operation in self.operations:
            key = (operation.news_id, operation.topic, operation.kind)
            if key in seen:
                raise ValueError("duplicate favorite operation")
            seen.add(key)
        return self


__all__ = (
    "ApiConfigUpdateRequest",
    "ChangePasswordRequest",
    "FavoriteBatchOperation",
    "FavoriteBatchRequest",
    "FavoriteRemoveRequest",
    "FavoriteToggleRequest",
    "ForgotPasswordRequest",
    "LoginMfaVerifyRequest",
    "LoginRequest",
    "MfaConfirmRequest",
    "MfaDisableRequest",
    "PrivacyDeletionRequestCreate",
    "RegisterRequest",
    "ResetPasswordRequest",
    "SearchHistoryRequest",
    "SessionRevokeRequest",
    "UserProfileResponse",
    "UserProfileUpdateRequest",
)
