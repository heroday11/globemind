from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import globemind_runtime as runtime
from scripts.runtime_control.catalog import catalog_payload
from scripts.runtime_control.manifest import Inventory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "ops" / "runtime" / "services.json"


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value} | {
            nested for item in value.values() for nested in _keys(item)
        }
    if isinstance(value, list):
        return {nested for item in value for nested in _keys(item)}
    return set()


def test_real_catalog_is_current_read_only_and_explicitly_not_adopted() -> None:
    inventory = runtime.load_inventory(MANIFEST)

    payload = catalog_payload(inventory, [])

    assert payload["operation"] == "catalog"
    assert payload["read_only"] is True
    assert payload["process_inspection"] is False
    assert payload["summary"] == {
        "service_count": 12,
        "catalog_current": 12,
        "catalog_drifted": 0,
        "lifecycle_authorized": 0,
        "takeover_ready": 0,
        "takeover_blocked": 12,
    }
    assert all(item["catalog_status"] == "current" for item in payload["services"])
    assert all(item["takeover_ready"] is False for item in payload["services"])
    assert all(
        item["lifecycle_authorization"]["state"] == "not-authorized"
        for item in payload["services"]
    )
    assert "lifecycle-not-authorized" in {
        blocker for item in payload["services"] for blocker in item["management_blockers"]
    }

    serialized = json.dumps(payload)
    assert "/root/data/cloudflared/credentials.json" not in serialized
    assert "/root/data/globemind/backend/api/.env" not in serialized
    assert not ({"argv", "cmdline", "command"} & _keys(payload))


def test_catalog_selection_includes_dependencies_without_process_inspection() -> None:
    inventory = runtime.load_inventory(MANIFEST)

    payload = catalog_payload(inventory, ["tunnel"])

    assert payload["dependency_closure"] == ["vllm", "web", "tunnel"]
    assert [item["id"] for item in payload["services"]] == ["vllm", "web", "tunnel"]
    assert payload["process_inspection"] is False


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("runbook", {"path": str(MANIFEST), "section": "missing_section"}, "runbook-section-missing"),
        (
            "controller",
            {
                "type": "shell-script",
                "path": str(PROJECT_ROOT / "deploy" / "missing-controller.sh"),
                "interface": "status",
                "adoption": "observe-only",
            },
            "artifact-unavailable",
        ),
    ],
)
def test_catalog_reports_artifact_drift_without_raising_or_accessing_processes(
    field: str, value: dict[str, Any], code: str
) -> None:
    loaded = runtime.load_inventory(MANIFEST)
    service = copy.deepcopy(next(item for item in loaded["services"] if item["id"] == "web"))
    service[field] = value
    service["dependencies"] = []
    inventory = Inventory(
        {"schema_version": 2, "inventory_version": "test", "services": [service]},
        loaded.trusted_roots,
    )

    payload = catalog_payload(inventory, ["web"])

    assert payload["summary"]["catalog_drifted"] == 1
    assert payload["services"][0]["catalog_status"] == "drifted"
    assert code in {item["code"] for item in payload["services"][0]["catalog_drift"]}


def test_catalog_reports_replay_evidence_selector_drift() -> None:
    loaded = runtime.load_inventory(MANIFEST)
    service = copy.deepcopy(
        next(item for item in loaded["services"] if item["id"] == "wave1_loader")
    )
    service["dependencies"] = []
    service["replay"]["evidence"][0]["selector"] = "missing_replay_test_selector"
    inventory = Inventory(
        {"schema_version": 2, "inventory_version": "test", "services": [service]},
        loaded.trusted_roots,
    )

    payload = catalog_payload(inventory, ["wave1_loader"])

    drift = payload["services"][0]["catalog_drift"]
    assert {item["code"] for item in drift} == {"replay-selector-missing"}
    assert payload["services"][0]["takeover_ready"] is False


def test_catalog_cli_json_is_a_safe_summary(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = runtime.main(["catalog", "wave1_loader", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["operation"] == "catalog"
    assert payload["process_inspection"] is False
    assert payload["services"][-1]["id"] == "wave1_loader"
    assert payload["services"][-1]["checkpoint"]["mode"] == "durable"
    assert payload["services"][-1]["replay"]["assurance"] == "verified"
    assert payload["services"][-1]["takeover_ready"] is False
