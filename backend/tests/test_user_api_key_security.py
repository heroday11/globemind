from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import ValidationError

from api.core.secrets import (
    FALLBACK_KEYS_ENV,
    PRIMARY_KEY_ENV,
    EncryptedSecretsText,
    SecretDecryptionError,
    decrypt_secret_text,
    encrypt_secret_text,
    is_encrypted_secret_text,
    reencrypt_secret_text,
)
from api.features.identity import ApiConfigUpdateRequest
from api.routes.auth import (
    _merge_api_key_objects,
    _parse_api_key_object,
    _remove_api_key_and_aliases,
    _serialize_user_profile,
)


def _set_primary(monkeypatch: pytest.MonkeyPatch, key: bytes) -> None:
    monkeypatch.setenv(PRIMARY_KEY_ENV, key.decode("ascii"))
    monkeypatch.delenv(FALLBACK_KEYS_ENV, raising=False)


def test_encrypted_type_never_stores_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_primary(monkeypatch, Fernet.generate_key())
    raw = '{"deepseek":"sk-private-value"}'

    column_type = EncryptedSecretsText()
    stored = column_type.process_bind_param(raw, None)

    assert is_encrypted_secret_text(stored)
    assert "sk-private-value" not in stored
    assert column_type.process_result_value(stored, None) == raw


def test_legacy_plaintext_is_readable_until_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PRIMARY_KEY_ENV, raising=False)
    monkeypatch.delenv(FALLBACK_KEYS_ENV, raising=False)
    assert decrypt_secret_text('{"openai":"legacy"}') == '{"openai":"legacy"}'


def test_rotation_uses_fallback_then_reencrypts_with_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_key = Fernet.generate_key()
    new_key = Fernet.generate_key()
    _set_primary(monkeypatch, old_key)
    old_ciphertext = encrypt_secret_text("secret")

    monkeypatch.setenv(PRIMARY_KEY_ENV, new_key.decode("ascii"))
    monkeypatch.setenv(FALLBACK_KEYS_ENV, old_key.decode("ascii"))
    assert decrypt_secret_text(old_ciphertext) == "secret"

    new_ciphertext = reencrypt_secret_text(old_ciphertext)
    monkeypatch.delenv(FALLBACK_KEYS_ENV)
    assert decrypt_secret_text(new_ciphertext) == "secret"
    with pytest.raises(SecretDecryptionError):
        decrypt_secret_text(old_ciphertext)


def test_profile_response_contains_status_and_public_metadata_only() -> None:
    raw = json.dumps(
        {
            "deepseek": "sk-do-not-return",
            "image": {
                "backend": "openai",
                "api_key": "image-do-not-return",
                "base_url": "https://images.example/v1",
                "model": "image-model",
            },
        }
    )
    row = SimpleNamespace(
        id=7,
        username="alice",
        full_name="Alice",
        email="alice@example.com",
        phone="13800000000",
        created_at=None,
        updated_at=None,
        is_active=True,
        last_login_at=None,
        role="user",
        avatar_url="",
        api_keys=raw,
        active_provider="deepseek",
        default_model="model",
        base_url="https://api.example/v1",
    )

    response = _serialize_user_profile(row)
    serialized = json.dumps(response)

    assert response["api_keys"] is None
    assert response["api_key_status"] == {"deepseek": True, "image.api_key": True}
    assert response["api_config_public"]["image"] == {
        "backend": "openai",
        "base_url": "https://images.example/v1",
        "model": "image-model",
    }
    assert "sk-do-not-return" not in serialized
    assert "image-do-not-return" not in serialized


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///etc/passwd",
        "https://alice:provider-secret@example.test/v1",
        "https://example.test/v1?token=provider-secret",
        "https://example.test/v1#provider-secret",
        "https://example.test\\@127.0.0.1/v1",
        "https://example.test/v1\nX-Injected: yes",
    ],
)
def test_api_config_contract_rejects_unsafe_provider_base_urls(base_url: str) -> None:
    with pytest.raises(ValidationError):
        ApiConfigUpdateRequest(base_url=base_url)


def test_api_config_contract_strips_safe_provider_base_url() -> None:
    request = ApiConfigUpdateRequest(base_url="  https://gateway.example.test/v1/  ")

    assert request.base_url == "https://gateway.example.test/v1/"


def test_api_config_contract_keeps_explicit_local_provider_compatible() -> None:
    request = ApiConfigUpdateRequest(base_url="http://127.0.0.1:8004/v1")

    assert request.base_url == "http://127.0.0.1:8004/v1"


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://images.example.test/v1",
        "https://alice:provider-secret@images.example.test/v1",
        "https://images.example.test/v1?token=provider-secret",
    ],
)
def test_api_key_json_rejects_unsafe_public_provider_urls(base_url: str) -> None:
    raw = json.dumps({"image": {"base_url": base_url}})

    with pytest.raises(HTTPException) as caught:
        _parse_api_key_object(raw, strict=True)

    assert caught.value.status_code == 422


@pytest.mark.parametrize(
    "raw",
    [
        '{"deepseek":"first","deepseek":"second"}',
        '{"image":{"model":NaN}}',
        '{"image":{"model":Infinity}}',
    ],
)
def test_api_key_json_rejects_ambiguous_or_non_finite_json(raw: str) -> None:
    with pytest.raises(HTTPException) as caught:
        _parse_api_key_object(raw, strict=True)

    assert caught.value.status_code == 422


def test_legacy_ambiguous_api_key_json_fails_closed() -> None:
    raw = '{"deepseek":"first","deepseek":"second"}'

    assert _parse_api_key_object(raw, strict=False) == {}


def test_profile_omits_unsafe_legacy_provider_urls() -> None:
    canary = "provider-secret-must-not-be-returned"
    row = SimpleNamespace(
        id=7,
        username="alice",
        full_name="Alice",
        email="alice@example.com",
        phone="",
        created_at=None,
        updated_at=None,
        is_active=True,
        last_login_at=None,
        role="user",
        avatar_url="",
        api_keys=json.dumps(
            {
                "image": {
                    "backend": "openai",
                    "base_url": f"https://alice:{canary}@images.example.test/v1",
                    "model": "image-model",
                }
            }
        ),
        active_provider="custom",
        default_model="model",
        base_url=f"https://alice:{canary}@api.example.test/v1",
    )

    response = _serialize_user_profile(row)
    serialized = json.dumps(response)

    assert response["base_url"] is None
    assert response["api_config_public"]["image"] == {
        "backend": "openai",
        "model": "image-model",
    }
    assert canary not in serialized


def test_incremental_update_preserves_blank_secrets_and_explicit_clear_removes_aliases() -> None:
    existing = {
        "deepseek": "keep-me",
        "image_api_key": "legacy-image-key",
        "image": {"api_key": "new-image-key", "model": "old-model"},
    }
    incoming = {
        "deepseek": "",
        "image": {"api_key": "", "model": "new-model"},
    }

    merged = _merge_api_key_objects(existing, incoming)
    assert merged["deepseek"] == "keep-me"
    assert merged["image"]["api_key"] == "new-image-key"
    assert merged["image"]["model"] == "new-model"

    _remove_api_key_and_aliases(merged, "image.api_key")
    assert "api_key" not in merged["image"]
    assert "image_api_key" not in merged
