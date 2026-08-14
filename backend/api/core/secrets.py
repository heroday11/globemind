"""Encryption helpers for application-managed secrets stored in PostgreSQL."""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import Text, TypeDecorator

from api.core.environment import string_setting

ENCRYPTED_VALUE_PREFIX = "gms1:"
PRIMARY_KEY_ENV = "USER_API_KEYS_ENCRYPTION_KEY"
FALLBACK_KEYS_ENV = "USER_API_KEYS_DECRYPTION_KEYS"


class SecretStoreConfigurationError(RuntimeError):
    """Raised when the server cannot safely encrypt application secrets."""


class SecretDecryptionError(RuntimeError):
    """Raised when an encrypted value cannot be authenticated or decrypted."""


def _configured_key_strings() -> tuple[str, ...]:
    primary = string_setting(PRIMARY_KEY_ENV)
    fallbacks = tuple(
        item.strip()
        for item in string_setting(FALLBACK_KEYS_ENV).split(",")
        if item.strip()
    )
    return ((primary,) if primary else ()) + fallbacks


@lru_cache(maxsize=16)
def _build_fernets(keys: tuple[str, ...]) -> tuple[Fernet, ...]:
    result: list[Fernet] = []
    for key in keys:
        try:
            result.append(Fernet(key.encode("ascii")))
        except (UnicodeEncodeError, ValueError) as exc:
            raise SecretStoreConfigurationError(
                f"{PRIMARY_KEY_ENV} and {FALLBACK_KEYS_ENV} must contain Fernet keys"
            ) from exc
    return tuple(result)


def _fernets() -> tuple[Fernet, ...]:
    return _build_fernets(_configured_key_strings())


def _primary_fernet() -> Fernet:
    primary = string_setting(PRIMARY_KEY_ENV)
    if not primary:
        raise SecretStoreConfigurationError(
            f"{PRIMARY_KEY_ENV} is required before API keys can be saved"
        )
    return _build_fernets((primary,))[0]


def secret_store_configured() -> bool:
    try:
        _primary_fernet()
    except SecretStoreConfigurationError:
        return False
    return True


def is_encrypted_secret_text(value: object) -> bool:
    return isinstance(value, str) and value.startswith(ENCRYPTED_VALUE_PREFIX)


def encrypt_secret_text(value: str | None) -> str | None:
    if value is None or value == "" or is_encrypted_secret_text(value):
        return value
    token = _primary_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_VALUE_PREFIX}{token}"


def decrypt_secret_text(value: str | None) -> str | None:
    if value is None or value == "" or not is_encrypted_secret_text(value):
        # Legacy rows remain readable until the explicit migration is applied.
        return value
    token = value[len(ENCRYPTED_VALUE_PREFIX) :].encode("ascii")
    fernets = _fernets()
    if not fernets:
        raise SecretStoreConfigurationError(
            f"{PRIMARY_KEY_ENV} or {FALLBACK_KEYS_ENV} is required to decrypt API keys"
        )
    for fernet in fernets:
        try:
            return fernet.decrypt(token).decode("utf-8")
        except InvalidToken:
            continue
    raise SecretDecryptionError("Stored API key data could not be authenticated")


def reencrypt_secret_text(value: str) -> str:
    plaintext = decrypt_secret_text(value)
    if plaintext is None:
        return ""
    token = _primary_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_VALUE_PREFIX}{token}"


class EncryptedSecretsText(TypeDecorator):
    """Encrypt Text values on writes while accepting legacy plaintext rows."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> str | None:
        return encrypt_secret_text(value)

    def process_result_value(self, value: str | None, dialect: object) -> str | None:
        return decrypt_secret_text(value)
