from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.services import hermes_assistant

_CONFIG_NAMES = (
    "HERMES_API_KEY",
    "HERMES_BASE_URL",
    "HERMES_FALLBACK_BASE_URL",
    "HERMES_FALLBACK_MODEL",
    "HERMES_MAX_TOKENS",
    "HERMES_MODEL",
    "PUBLIC_DEEPSEEK_API_KEY",
    "PUBLIC_DEEPSEEK_BASE_URL",
    "PUBLIC_DEEPSEEK_MODEL",
)


def _clear_config(monkeypatch) -> None:
    for name in _CONFIG_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("finish_reason", "saw_done_sentinel", "expected"),
    (
        ("stop", False, True),
        (None, True, True),
        (None, False, False),
        ("length", True, False),
        ("content_filter", True, False),
    ),
)
def test_stream_completion_requires_a_non_truncated_terminal_signal(
    finish_reason: str | None,
    saw_done_sentinel: bool,
    expected: bool,
) -> None:
    assert (
        hermes_assistant._stream_completion_is_terminal(
            finish_reason,
            saw_done_sentinel=saw_done_sentinel,
        )
        is expected
    )


def test_user_provider_precedes_global_environment(monkeypatch) -> None:
    _clear_config(monkeypatch)
    monkeypatch.setenv("HERMES_BASE_URL", "https://global.example/v1")
    monkeypatch.setenv("HERMES_MODEL", "global-model")
    user = SimpleNamespace(
        active_provider="hermes",
        api_keys='{"hermes":"user-key"}',
        base_url="https://user.example",
        default_model="user-model",
    )

    config = hermes_assistant.resolve_hermes_config(user)

    assert config == hermes_assistant.HermesConfig(
        base_url="https://user.example/v1",
        api_key="user-key",
        model="user-model",
        source="user",
    )


@pytest.mark.parametrize(
    "legacy_base_url",
    (
        "https://alice:secret@user.example/v1",
        "https://user.example/v1?token=secret",
        "https://user.example/v1#secret",
        "file:///tmp/provider.sock",
    ),
)
def test_legacy_unsafe_user_provider_base_url_fails_closed(
    monkeypatch,
    legacy_base_url: str,
) -> None:
    _clear_config(monkeypatch)
    user = SimpleNamespace(
        active_provider="hermes",
        api_keys='{"hermes":"user-key"}',
        base_url=legacy_base_url,
        default_model="user-model",
    )

    config = hermes_assistant.resolve_hermes_config(user)

    assert config.base_url == ""
    assert legacy_base_url not in repr(config)


def test_legacy_unsafe_user_base_does_not_override_environment_fallback(
    monkeypatch,
) -> None:
    _clear_config(monkeypatch)
    monkeypatch.setenv("HERMES_BASE_URL", "https://global.example/v1")
    user = SimpleNamespace(
        active_provider="hermes",
        api_keys='{"hermes":"user-key"}',
        base_url="https://alice:secret@user.example/v1",
        default_model="user-model",
    )

    config = hermes_assistant.resolve_hermes_config(user)

    assert config.base_url == "https://global.example/v1"
    assert config.source == "user"


@pytest.mark.parametrize(
    "legacy_api_keys",
    (
        '{"hermes":"first","hermes":"second"}',
        '{"hermes":"user-key","weight":NaN}',
        '{"hermes":"user-key","weight":Infinity}',
        '{"hermes":"user-key","weight":1e400}',
        '{"nested":' * 1_200 + 'null' + '}' * 1_200,
    ),
)
def test_legacy_ambiguous_or_unbounded_api_key_json_fails_closed(
    monkeypatch,
    legacy_api_keys: str,
) -> None:
    _clear_config(monkeypatch)
    user = SimpleNamespace(
        active_provider="hermes",
        api_keys=legacy_api_keys,
        base_url="https://user.example",
        default_model="user-model",
    )

    config = hermes_assistant.resolve_hermes_config(user)

    assert config.api_key == ""
    assert "first" not in repr(config)
    assert "second" not in repr(config)
    assert "user-key" not in repr(config)


def test_global_then_public_then_local_fallback_precedence(monkeypatch) -> None:
    _clear_config(monkeypatch)
    monkeypatch.setenv("HERMES_BASE_URL", "https://global.example")
    monkeypatch.setenv("HERMES_API_KEY", "global-key")
    monkeypatch.setenv("HERMES_MODEL", "global-model")
    assert hermes_assistant.resolve_hermes_config().source == "env"

    monkeypatch.delenv("HERMES_BASE_URL")
    monkeypatch.delenv("HERMES_MODEL")
    monkeypatch.setenv("PUBLIC_DEEPSEEK_API_KEY", "public-key")
    public = hermes_assistant.resolve_hermes_config()
    assert public.source == "public:deepseek"
    assert public.base_url == "https://api.deepseek.com"
    assert public.model == "deepseek-v4-flash"

    monkeypatch.delenv("PUBLIC_DEEPSEEK_API_KEY")
    local = hermes_assistant.resolve_hermes_config()
    assert local.source == "local-vllm-fallback"
    assert local.base_url == "http://127.0.0.1:8004/v1"
    assert local.model == "qwen2.5-7b-awq"


def test_invalid_numeric_setting_falls_back_without_import_or_request_failure(
    monkeypatch,
) -> None:
    _clear_config(monkeypatch)
    monkeypatch.setenv("HERMES_MAX_TOKENS", "not-an-integer")
    config = hermes_assistant.resolve_hermes_config()

    assert hermes_assistant._hermes_max_tokens(config, None) == 768

    remote = hermes_assistant.HermesConfig(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        source="env",
    )
    assert hermes_assistant._hermes_max_tokens(remote, None) == 4096
