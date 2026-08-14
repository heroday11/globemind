from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.routes import assistant
from api.services.hermes_assistant import HermesConfig


def test_platform_tool_toggle_uses_strict_boolean_setting(monkeypatch) -> None:
    request = assistant.AssistantCCStreamRequest(
        message="分析中国芯片出口管制的最新影响",
    )

    monkeypatch.setenv("ASSISTANT_PLATFORM_TOOLS", "off")
    assert assistant._assistant_build_platform_tool_plans(request) == []

    monkeypatch.setenv("ASSISTANT_PLATFORM_TOOLS", "invalid")
    assert assistant._assistant_build_platform_tool_plans(request)


def test_web_search_config_preserves_provider_and_environment_precedence(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        assistant,
        "resolve_hermes_config",
        lambda _user: HermesConfig(
            base_url="https://api.deepseek.com/v1",
            api_key="user-key",
            model="user-model",
            source="user:deepseek",
        ),
    )
    monkeypatch.setenv(
        "HERMES_WEB_SEARCH_ANTHROPIC_BASE_URL",
        "https://search.example/anthropic",
    )
    monkeypatch.setenv("HERMES_WEB_SEARCH_API_KEY", "explicit-key")
    monkeypatch.setenv("HERMES_WEB_SEARCH_MODEL", "explicit-model")

    assert assistant._assistant_deepseek_web_config(None) == (
        "https://search.example/anthropic",
        "explicit-key",
        "explicit-model",
    )


def test_vllm_base_and_invalid_numeric_settings_fail_safe(monkeypatch) -> None:
    monkeypatch.setenv("VLLM_BASE_URL", "http://127.0.0.1:9000/v1/v1")
    monkeypatch.setenv("HERMES_WEB_SEARCH_SUMMARY_CHARS", "invalid")

    assert assistant._openai_compat_v1_base() == "http://127.0.0.1:9000/v1"
    payload = assistant._assistant_extract_deepseek_web_response(
        {"content": [{"type": "text", "text": "summary"}]},
        5,
    )
    assert payload["summary"] == "summary"


def test_platform_tool_intent_respects_explicit_no_tool_instruction() -> None:
    assert not assistant._assistant_should_use_platform_tools(
        "不要调用任何工具，请直接整理一份十二部分的分析提纲"
    )
    assert not assistant._assistant_should_use_platform_tools(
        "请把下面这段文字改写得更简洁、更专业"
    )
    assert assistant._assistant_should_use_platform_tools(
        "检索最近的台海新闻并分析风险"
    )


def test_assistant_modes_reserve_enough_completion_budget() -> None:
    assert assistant._assistant_mode_config("fast").max_tokens == 3200
    assert assistant._assistant_mode_config("pro").max_tokens == 6400
    assert assistant._assistant_mode_config("expert").max_tokens == 8192
    assert assistant._assistant_mode_config("pro").max_tool_calls == 4
    assert assistant._assistant_mode_config("expert").max_tool_calls == 5


@pytest.mark.parametrize(
    "legacy_base_url",
    (
        "https://alice:secret@images.example/v1",
        "https://images.example/v1?token=secret",
    ),
)
def test_legacy_unsafe_user_image_base_url_does_not_enter_image_env(
    monkeypatch,
    tmp_path: Path,
    legacy_base_url: str,
) -> None:
    monkeypatch.setattr(assistant, "HERMES_IMAGE_ENV_FILE", tmp_path / "missing.env")
    for name in (
        "HERMES_IMAGE_BACKEND",
        "HERMES_IMAGE_BASE_URL",
        "HERMES_IMAGE_OPENAI_BASE_URL",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    user = SimpleNamespace(
        api_keys=json.dumps(
            {
                "image": {
                    "backend": "openai",
                    "api_key": "user-key",
                    "base_url": legacy_base_url,
                }
            }
        )
    )

    env = assistant._assistant_prepare_image_env(user, use_user_config=True)

    assert "OPENAI_BASE_URL" not in env
    assert legacy_base_url not in env.values()


def test_legacy_unsafe_user_image_base_preserves_environment_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(assistant, "HERMES_IMAGE_ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("HERMES_IMAGE_BASE_URL", raising=False)
    monkeypatch.setenv("HERMES_IMAGE_BACKEND", "openai")
    monkeypatch.setenv(
        "HERMES_IMAGE_OPENAI_BASE_URL",
        "https://global-images.example/v1",
    )
    user = SimpleNamespace(
        api_keys=json.dumps(
            {
                "image": {
                    "backend": "openai",
                    "api_key": "user-key",
                    "base_url": "https://images.example/v1?token=secret",
                }
            }
        )
    )

    env = assistant._assistant_prepare_image_env(user, use_user_config=True)

    assert env["OPENAI_BASE_URL"] == "https://global-images.example/v1"
    assert "token=secret" not in env["OPENAI_BASE_URL"]


def test_assistant_tool_schemas_only_expose_available_context() -> None:
    body = assistant.AssistantCCStreamRequest(
        message="请基于页面快照研判，并给出后续检索提纲",
        tool_mode="context_only",
    )
    names = {
        schema["function"]["name"]
        for schema in assistant._assistant_hermes_tool_schemas(body)
    }
    assert "news_search" in names
    assert "workspace_list_files" not in names
    assert "selected_skill_list" not in names
    assert "web_search" not in names

    web_body = assistant.AssistantCCStreamRequest(
        message="请联网搜索官网最新政策",
        pinned_workspace="policy",
        knowledge_context={"skills": [{"name": "policy"}]},
    )
    web_names = {
        schema["function"]["name"]
        for schema in assistant._assistant_hermes_tool_schemas(web_body)
    }
    assert "web_search" in web_names
    assert "workspace_list_files" in web_names
    assert "selected_skill_list" in web_names
