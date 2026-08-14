from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api.application import app
from api.features.operations.runtime_catalog import (
    attach_catalog_management,
    load_runtime_catalog,
)
from api.routes import ops_monitor
from api.services.auth import get_current_user_required

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set(value) | {key for nested in value.values() for key in _nested_keys(nested)}
    if isinstance(value, list):
        return {key for nested in value for key in _nested_keys(nested)}
    return set()


def _catalog_fixture() -> dict[str, Any]:
    return {
        "operation": "runtime-catalog",
        "available": True,
        "read_only": True,
        "process_inspection": False,
        "control": {"enabled": False, "actions": []},
        "summary": {"service_count": 1},
        "services": [
            {
                "id": "wave1_loader",
                "name": "Catalog loader",
                "kind": "pipeline",
                "owner": "data-ingestion",
                "criticality": "high",
                "identity_contract": {
                    "kind": "single",
                    "assurance": "strong",
                    "expected": "running",
                    "source": "runtime-catalog",
                },
                "controller": {
                    "type": "shell-script",
                    "path": "/project/deploy/wave1_loader_ctl.sh",
                    "interface": "start|stop|restart|status|logs",
                    "adoption": "observe-only",
                },
                "health_policy": {"mode": "composite", "signals": []},
                "lifecycle_authorization": {
                    "state": "not-authorized",
                    "authorized_operations": [],
                },
                "catalog_status": "current",
                "takeover_ready": False,
                "management_blockers": ["lifecycle-not-authorized"],
            }
        ],
    }


def test_runtime_catalog_projection_is_read_only_allowlisted_and_secret_free() -> None:
    payload = load_runtime_catalog(
        manifest_path=PROJECT_ROOT / "ops/runtime/services.json",
        project_root=PROJECT_ROOT,
        data_root=PROJECT_ROOT.parent,
    )
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["available"] is True
    assert payload["read_only"] is True
    assert payload["process_inspection"] is False
    assert payload["control"] == {"enabled": False, "actions": []}
    assert payload["summary"]["service_count"] == 12
    assert all(
        service["evidence"]
        == {
            "source": "runtime-catalog",
            "quality": "authoritative-management",
            "process_inspection": False,
        }
        for service in payload["services"]
    )
    quality_labels = next(
        service for service in payload["services"] if service["id"] == "quality_labels"
    )
    assert quality_labels["identity_contract"] == {
        "kind": "single",
        "assurance": "strong",
        "expected": "running",
        "source": "runtime-catalog",
    }
    assert "strong-process-identity-not-evidenced" not in quality_labels["management_blockers"]
    assert not {
        "secret_refs",
        "secret_policy",
        "secret_transport",
        "argv",
        "cmdline",
        "command",
    }.intersection(_nested_keys(payload))
    assert "credentials.json" not in serialized
    assert ".db-secret" not in serialized
    assert "/backend/api/.env" not in serialized


def test_read_only_catalog_import_does_not_load_lifecycle_capabilities() -> None:
    command = (
        "import sys; "
        "import api.features.operations.runtime_catalog; "
        "assert 'scripts.runtime_control.lifecycle' not in sys.modules; "
        "assert 'scripts.runtime_control.cli' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", command],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": f"{PROJECT_ROOT / 'backend'}:{PROJECT_ROOT}",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_runtime_catalog_endpoint_requires_auth_and_exposes_no_control_method(
    monkeypatch,
) -> None:
    fixture = _catalog_fixture()
    monkeypatch.setattr(ops_monitor, "load_runtime_catalog", lambda: fixture)
    previous = dict(app.dependency_overrides)
    try:
        with TestClient(app) as client:
            assert client.get("/api/ops/runtime-catalog").status_code == 401

            app.dependency_overrides[get_current_user_required] = lambda: {"user_id": 1}
            response = client.get("/api/ops/runtime-catalog")
            assert response.status_code == 200
            assert response.json() == fixture
            assert client.post("/api/ops/runtime-catalog").status_code == 405
            assert client.delete("/api/ops/runtime-catalog").status_code == 405
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_catalog_management_overrides_legacy_identity_and_denies_unregistered() -> None:
    pipelines = [
        {
            "id": "wave1_loader",
            "name": "Legacy loader label",
            "pid": 123,
            "process": {"evidence_quality": "heuristic"},
        },
        {"id": "translation", "name": "Unregistered translation"},
    ]

    enriched = attach_catalog_management(pipelines, _catalog_fixture())

    loader = enriched[0]
    assert loader["pid"] == 123
    assert loader["management"]["catalog_service_id"] == "wave1_loader"
    assert loader["management"]["owner"] == "data-ingestion"
    assert loader["management"]["identity_contract"]["assurance"] == "strong"
    assert loader["management"]["effective_lifecycle_state"] == "not-authorized"
    assert loader["management"]["evidence_quality"] == "authoritative-management"
    assert loader["telemetry_evidence"]["authoritative_for_management"] is False

    translation = enriched[1]["management"]
    assert translation["registered"] is False
    assert translation["effective_lifecycle_state"] == "not-authorized"
    assert translation["management_blockers"] == ["service-not-in-runtime-catalog"]


def test_wave1_loader_uses_catalog_declared_state_not_retired_pid_file(monkeypatch) -> None:
    def fake_read_json(path: Path) -> dict[str, Any]:
        if path.name.endswith("heartbeat"):
            return {
                "status": "running",
                "heartbeat_at": "2026-07-11T00:00:00Z",
                "offset": 42,
                "_age_sec": 2.0,
                "_path": str(path),
            }
        return {
            "seen": 10,
            "inserted": 9,
            "updated_at": 1_752_192_000,
            "_age_sec": 1.0,
            "_path": str(path),
        }

    monkeypatch.setattr(ops_monitor, "_read_json", fake_read_json)
    monkeypatch.setattr(
        ops_monitor,
        "_process_from_pid_file",
        lambda _path: (_ for _ in ()).throw(AssertionError("legacy PID inspected")),
    )

    pipeline = ops_monitor._loader_pipeline({"news": {"total": 20}})

    assert pipeline["status"] == "running"
    assert pipeline["pid"] is None
    assert pipeline["process"]["evidence_quality"] == "not-inspected"
    assert pipeline["telemetry_evidence"]["quality"] == "authoritative-state"
    assert pipeline["details"]["heartbeat"]["offset"] == 42
    assert "wave1_loader.pid" not in str(pipeline)
