from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "cppt"))

from cppt import cc_bridge  # noqa: E402


def test_cc_standalone_is_disabled_in_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CC_STANDALONE_ENABLE", "1")

    with pytest.raises(RuntimeError, match="disabled"):
        cc_bridge.create_standalone_app()


def test_cc_standalone_requires_explicit_development_opt_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("CC_STANDALONE_ENABLE", raising=False)

    with pytest.raises(RuntimeError, match="CC_STANDALONE_ENABLE"):
        cc_bridge.create_standalone_app()


def test_cc_standalone_chat_still_requires_authentication(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CC_STANDALONE_ENABLE", "1")

    with TestClient(cc_bridge.create_standalone_app()) as client:
        response = client.post("/cc/chat", json={"message": "test"})

    assert response.status_code == 401


def test_cc_standalone_config_requires_authentication(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CC_STANDALONE_ENABLE", "1")

    with TestClient(cc_bridge.create_standalone_app()) as client:
        response = client.get("/cc/config")

    assert response.status_code == 401


def test_code_execution_tool_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("CC_ENABLE_RUN_CODE", raising=False)

    names = {tool["name"] for tool in cc_bridge._active_tools()}

    assert "run_code" not in names


@pytest.mark.parametrize("username", ["../../..", "/root", "name/other"])
def test_workspace_sandbox_rejects_invalid_username(username: str):
    with pytest.raises(ValueError):
        cc_bridge._sandbox_root(username)
