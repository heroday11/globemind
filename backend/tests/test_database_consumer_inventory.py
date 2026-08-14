from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = PROJECT_ROOT / "ops" / "runtime" / "database-consumers.json"
SERVICES_PATH = PROJECT_ROOT / "ops" / "runtime" / "services.json"
CHECKER_PATH = PROJECT_ROOT / "scripts" / "ci" / "check_database_consumers.py"

SPEC = importlib.util.spec_from_file_location("check_database_consumers", CHECKER_PATH)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def _payload() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _services_payload() -> dict:
    return json.loads(SERVICES_PATH.read_text(encoding="utf-8"))


def _validate(payload: dict) -> dict[str, object]:
    return checker.validate_inventory(
        payload,
        _services_payload(),
        repository_root=PROJECT_ROOT,
    )


def test_checked_in_database_consumer_inventory_is_exact_and_offline() -> None:
    summary = checker.load_and_validate(
        INVENTORY_PATH,
        SERVICES_PATH,
        repository_root=PROJECT_ROOT,
    )

    assert summary == {
        "consumers": 8,
        "maintenance_entrypoints": 2,
        "service_ids": [
            "daily_ingest",
            "ground_images",
            "ground_refresh",
            "l1_extract",
            "l1_prep",
            "quality_labels",
            "wave1_loader",
            "web",
        ],
    }


def test_daily_ingest_does_not_claim_managed_role_before_checkpointed_takeover() -> None:
    payload = _payload()
    daily = next(item for item in payload["consumers"] if item["service_id"] == "daily_ingest")

    assert daily["current_role"] == {
        "name": None,
        "status": "legacy_runtime_unverified",
    }
    assert daily["target_role"]["name"] == "wave1_loader"
    assert daily["transport"] == {
        "network_scope": "legacy_runtime_unverified",
        "current_tls": "legacy_runtime_unverified",
        "target_tls": "disabled_private_scram_exception",
        "status": "managed_takeover_pending",
    }
    assert daily["migration"]["status"] == "managed_role_takeover_pending"
    assert "checkpointed_takeover_not_completed" in daily["migration"]["blockers"]

    daily["current_role"] = {
        "name": "wave1_loader",
        "status": "assigned_runtime",
    }
    with pytest.raises(checker.InventoryError, match="source-verified role"):
        _validate(payload)


def test_database_consumer_checker_cli_reports_machine_readable_success() -> None:
    result = subprocess.run(
        [sys.executable, "-B", str(CHECKER_PATH), "--format", "json"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "passed",
        "consumers": 8,
        "maintenance_entrypoints": 2,
        "service_ids": [
            "daily_ingest",
            "ground_images",
            "ground_refresh",
            "l1_extract",
            "l1_prep",
            "quality_labels",
            "wave1_loader",
            "web",
        ],
    }


def test_checker_accepts_structured_probe_dependency_for_postgres() -> None:
    services = _services_payload()
    web = next(item for item in services["services"] if item["id"] == "web")
    assert web["external_dependencies"] == [
        {
            "name": "postgres-news",
            "required": True,
            "verification": "probe",
            "via_probe": "web-postgres-readiness",
        }
    ]

    summary = checker.validate_inventory(
        _payload(),
        services,
        repository_root=PROJECT_ROOT,
    )

    assert summary["consumers"] == 8


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda dependency: dependency.update(required="yes"), "must be a boolean"),
        (lambda dependency: dependency.pop("via_probe"), "via_probe is required"),
        (lambda dependency: dependency.update(command="curl"), "unknown=\\['command'\\]"),
    ],
)
def test_checker_rejects_invalid_structured_external_dependency(
    mutation,
    message: str,
) -> None:
    services = _services_payload()
    web = next(item for item in services["services"] if item["id"] == "web")
    mutation(web["external_dependencies"][0])

    with pytest.raises(checker.InventoryError, match=message):
        checker.validate_inventory(
            _payload(),
            services,
            repository_root=PROJECT_ROOT,
        )


def test_checker_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    raw = INVENTORY_PATH.read_text(encoding="utf-8").replace(
        '"schema_version": 1,',
        '"schema_version": 1,\n  "schema_version": 1,',
        1,
    )
    duplicate = tmp_path / "database-consumers.json"
    duplicate.write_text(raw, encoding="utf-8")

    with pytest.raises(checker.InventoryError, match="duplicate JSON key"):
        checker.load_json(duplicate)


def test_checker_rejects_missing_postgres_service_coverage() -> None:
    payload = _payload()
    payload["consumers"].pop()

    with pytest.raises(checker.InventoryError, match="consumer coverage"):
        _validate(payload)


def test_checker_rejects_unknown_services() -> None:
    payload = _payload()
    payload["consumers"][0]["service_id"] = "not_registered"

    with pytest.raises(checker.InventoryError, match="unknown service"):
        _validate(payload)


def test_checker_rejects_entrypoints_outside_the_repository() -> None:
    payload = _payload()
    payload["consumers"][0]["entrypoint"]["path"] = "../serve_prod.py"

    with pytest.raises(checker.InventoryError, match="repository-relative"):
        _validate(payload)


def test_checker_rejects_unverified_or_invented_roles() -> None:
    payload = _payload()
    l1_prep = next(item for item in payload["consumers"] if item["service_id"] == "l1_prep")
    l1_prep["target_role"] = {
        "name": "invented_pipeline_role",
        "status": "assigned",
        "required_capabilities": copy.deepcopy(l1_prep["target_role"]["required_capabilities"]),
    }

    with pytest.raises(checker.InventoryError, match="explicitly unassigned"):
        _validate(payload)


def test_checker_rejects_embedded_secret_fields_before_schema_validation() -> None:
    payload = _payload()
    payload["consumers"][0]["credential_references"][0]["value"] = "not-a-reference"

    with pytest.raises(checker.InventoryError, match="embedded secret material"):
        _validate(payload)


def test_checker_rejects_unapproved_maintenance_entrypoints() -> None:
    payload = _payload()
    payload["maintenance_entrypoints"][0]["entrypoint"]["path"] = "deploy/db_role_policy.py"

    with pytest.raises(checker.InventoryError, match="verified entrypoint"):
        _validate(payload)
