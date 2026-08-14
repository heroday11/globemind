from __future__ import annotations

import atexit
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="globemind-pytest-"))
atexit.register(shutil.rmtree, TEST_RUNTIME_ROOT, ignore_errors=True)
(TEST_RUNTIME_ROOT / "frontend").mkdir(parents=True, exist_ok=True)
(TEST_RUNTIME_ROOT / "frontend" / "assets").mkdir()
(TEST_RUNTIME_ROOT / "frontend" / "index.html").write_text(
    "<!doctype html><html><body>test</body></html>",
    encoding="utf-8",
)

# Establish isolation before pytest imports any application module. Test mode does
# not scan normal .env files, and every database target is an unused local port.
os.environ["APP_ENV"] = "test"
os.environ["GLOBEMIND_TESTING"] = "1"
os.environ["GLOBEMIND_TEST_ROOT"] = str(TEST_RUNTIME_ROOT)
os.environ.pop("GLOBEMIND_ENV_FILE", None)
os.environ.pop("GLOBEMIND_ENV_FILES", None)
os.environ.pop("GLOBEMIND_TEST_ENV_FILE", None)
os.environ.pop("DATABASE_URL", None)
os.environ.pop("SQLALCHEMY_DATABASE_URL", None)

_SAFE_ENV = {
    "JWT_SECRET_KEY": "globemind-pytest-secret-not-for-production-000000000000",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "1",
    "DB_USER": "globemind_test",
    "DB_PASSWORD": "",
    "DB_NAME": "globemind_test",
    "PG_HOST": "127.0.0.1",
    "PG_PORT": "1",
    "PG_USER": "globemind_test",
    "PG_PASSWORD": "",
    "PG_DATABASE": "globemind_test",
    "L1_DB_HOST": "127.0.0.1",
    "L1_DB_PORT": "1",
    "L1_DB_USER": "globemind_test",
    "L1_DB_PASSWORD": "",
    "L1_DB_NAME": "globemind_test",
    "OPINION_DB_HOST": "127.0.0.1",
    "OPINION_DB_PORT": "1",
    "OPINION_DB_USER": "globemind_test",
    "OPINION_DB_PASSWORD": "",
    "OPINION_DB_NAME": "globemind_test",
    "FRONTEND_DIST": str(TEST_RUNTIME_ROOT / "frontend"),
    "GLOBEMIND_ROOT": str(TEST_RUNTIME_ROOT / "project"),
    "GLOBEMIND_WORKSPACE_ROOT": str(TEST_RUNTIME_ROOT / "workspace"),
    "ASSISTANT_SCHEDULE_RUNNER_LOCK": str(TEST_RUNTIME_ROOT / "schedule.lock"),
    "ASSISTANT_SCHEDULE_RUNNER_STATUS": str(TEST_RUNTIME_ROOT / "schedule.status.json"),
    "ASSISTANT_SCHEDULE_DISABLE": "1",
    "FINANCIAL_ALERT_RULES_STORE": str(TEST_RUNTIME_ROOT / "financial_rules.json"),
    "FINANCIAL_ALERT_HISTORY_STORE": str(TEST_RUNTIME_ROOT / "financial_history.json"),
    "FINANCIAL_ALERT_TRIAGE_ROOT": str(
        TEST_RUNTIME_ROOT / "financial_alert_triage"
    ),
    "FINANCIAL_TERMINAL_SHARED_CACHE": str(TEST_RUNTIME_ROOT / "financial_dashboard.json"),
    "EVIDENCE_SNAPSHOT_ROOT": str(TEST_RUNTIME_ROOT / "evidence_snapshots"),
    "SEARCH_SNAPSHOT_ROOT": str(TEST_RUNTIME_ROOT / "search_snapshots"),
    "MODEL_ASSURANCE_ROOT": str(TEST_RUNTIME_ROOT / "model_assurance"),
    "PRIVACY_RIGHTS_ROOT": str(TEST_RUNTIME_ROOT / "privacy_rights"),
    "IDENTITY_ASSURANCE_ROOT": str(TEST_RUNTIME_ROOT / "identity_assurance"),
    "SERVICE_LEVEL_ROOT": str(TEST_RUNTIME_ROOT / "service_level"),
    "ENTITY_GOVERNANCE_ROOT": str(TEST_RUNTIME_ROOT / "entity_governance"),
    "ENTITY_GOVERNANCE_HMAC_KEY": "entity-governance-pytest-hmac-key-0001",
    "USER_API_KEYS_ENCRYPTION_KEY": "6oOtGYpo5fg5qWAezxmioR11yWJTpTp4O5TnfwhpebA=",
    "ALLOW_RUNTIME_SCHEMA_MUTATIONS": "0",
}
os.environ.update(_SAFE_ENV)


def pytest_configure(config: pytest.Config) -> None:
    for marker, description in (
        ("integration", "integration test requiring multiple application components"),
        ("live_db", "test that explicitly requires a disposable live database"),
        ("gpu", "test that explicitly requires a GPU"),
        ("slow", "test intentionally excluded from the fast offline gate"),
    ):
        config.addinivalue_line("markers", f"{marker}: {description}")


def _deny_network(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("External network access is disabled during unit tests")


@pytest.fixture(autouse=True)
def isolate_external_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", _deny_network)
    monkeypatch.setattr(socket.socket, "connect", _deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny_network)

    runtime_root = Path(tempfile.mkdtemp(prefix="case-", dir=TEST_RUNTIME_ROOT))
    monkeypatch.setenv(
        "IDENTITY_ASSURANCE_ROOT",
        str(runtime_root / "identity_assurance"),
    )
    monkeypatch.setenv(
        "ENTITY_GOVERNANCE_ROOT",
        str(runtime_root / "entity_governance"),
    )
    monkeypatch.setenv(
        "FINANCIAL_ALERT_TRIAGE_ROOT",
        str(runtime_root / "financial_alert_triage"),
    )
    overrides = {
        "api.routes.assistant_data": {"WORKSPACE_ROOT": runtime_root / "workspace"},
        "api.services.assistant_schedule": {
            "WORKSPACE_ROOT": runtime_root / "workspace",
            "RUNNER_LOCK_PATH": runtime_root / "schedule.lock",
            "RUNNER_STATUS_PATH": runtime_root / "schedule.status.json",
        },
        "api.services.file_store": {"_DATA_DIR": runtime_root / "file_store"},
        "api.routes.financial": {
            "ALERT_RULES_STORE": runtime_root / "financial_rules.json",
            "ALERT_HISTORY_STORE": runtime_root / "financial_history.json",
            "FINANCIAL_ALERT_TRIAGE_ROOT": runtime_root / "financial_alert_triage",
        },
        "api.services.financial_terminal": {
            "SHARED_DASHBOARD_CACHE": runtime_root / "financial_dashboard.json"
        },
    }
    for module_name, attributes in overrides.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attribute, value in attributes.items():
            monkeypatch.setattr(module, attribute, value)

    try:
        yield
    finally:
        shutil.rmtree(runtime_root, ignore_errors=True)
