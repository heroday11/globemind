"""Read-only API projection of the authoritative runtime service catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# Runtime catalog is a read-only backend capability.  Keep its implementation
# in the backend package so the API does not depend on executable ``scripts``
# (the CLI keeps its historical import path for operators).
from runtime_control.catalog import catalog_payload
from runtime_control.manifest import InventoryError, load_inventory

SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MANIFEST = SOURCE_PROJECT_ROOT / "ops" / "runtime" / "services.json"
DEFAULT_DATA_ROOT = Path("/root/data")

PIPELINE_CATALOG_IDS = {
    "wave1_extract": "wave1_extractor",
    "wave1_loader": "wave1_loader",
    "daily_ingest": "daily_ingest",
    "quality_labels": "quality_labels",
    "l1_prep": "l1_prep",
    "l1_extract": "l1_extract",
    "ground_realtime": "ground_refresh",
    "story_images": "ground_images",
    "vllm": "vllm",
    "web": "web",
}


class RuntimeCatalogUnavailable(RuntimeError):
    """Raised when the authoritative catalog cannot be safely projected."""


def _process_identity_contract(service: Mapping[str, Any]) -> dict[str, Any]:
    pid = service["pid"]
    strong = bool(
        pid["kind"] == "single"
        and pid.get("meta_path")
        and isinstance(pid.get("meta"), Mapping)
    )
    return {
        "kind": pid["kind"],
        "assurance": "strong" if strong else "weak",
        "expected": pid.get("expected"),
        "source": "runtime-catalog",
    }


def _public_service(
    definition: Mapping[str, Any], declared: Mapping[str, Any]
) -> dict[str, Any]:
    """Allowlist management facts; secret policy and references never cross the API."""

    return {
        "id": definition["id"],
        "name": definition["name"],
        "kind": definition["kind"],
        "owner": definition["owner"],
        "criticality": definition["criticality"],
        "dependencies": definition["dependencies"],
        "controller": definition["controller"],
        "health_policy": definition["health_policy"],
        "checkpoint": definition["checkpoint"],
        "replay": definition["replay"],
        "lifecycle_authorization": definition["lifecycle_authorization"],
        "identity_contract": _process_identity_contract(declared),
        "catalog_status": definition["catalog_status"],
        "catalog_drift": definition["catalog_drift"],
        "takeover_ready": definition["takeover_ready"],
        "management_blockers": definition["management_blockers"],
        "evidence": {
            "source": "runtime-catalog",
            "quality": "authoritative-management",
            "process_inspection": False,
        },
    }


def project_runtime_catalog(
    payload: Mapping[str, Any], inventory: Mapping[str, Any]
) -> dict[str, Any]:
    declared = {service["id"]: service for service in inventory["services"]}
    services = [
        _public_service(service, declared[service["id"]])
        for service in payload["services"]
    ]
    return {
        "schema_version": payload["schema_version"],
        "inventory_version": payload.get("inventory_version"),
        "operation": "runtime-catalog",
        "available": True,
        "read_only": True,
        "process_inspection": False,
        "control": {"enabled": False, "actions": []},
        "summary": payload["summary"],
        "services": services,
    }


def load_runtime_catalog(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    project_root: Path = SOURCE_PROJECT_ROOT,
    data_root: Path = DEFAULT_DATA_ROOT,
    service_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Load and project catalog facts without inspecting or controlling processes."""

    try:
        inventory = load_inventory(
            manifest_path,
            trusted_roots=(project_root.resolve(), data_root.resolve()),
        )
        return project_runtime_catalog(catalog_payload(inventory, service_ids), inventory)
    except (InventoryError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeCatalogUnavailable("runtime catalog is unavailable") from exc


def unavailable_runtime_catalog() -> dict[str, Any]:
    return {
        "operation": "runtime-catalog",
        "available": False,
        "read_only": True,
        "process_inspection": False,
        "control": {"enabled": False, "actions": []},
        "summary": {
            "service_count": 0,
            "catalog_current": 0,
            "catalog_drifted": 0,
            "lifecycle_authorized": 0,
            "takeover_ready": 0,
            "takeover_blocked": 0,
        },
        "services": [],
        "error": {"code": "runtime-catalog-unavailable"},
    }


def attach_catalog_management(
    pipelines: Sequence[Mapping[str, Any]], catalog: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Overlay authoritative management facts while retaining labelled telemetry."""

    definitions = {
        service["id"]: service
        for service in catalog.get("services", [])
        if isinstance(service, Mapping) and isinstance(service.get("id"), str)
    }
    catalog_available = catalog.get("available") is True
    enriched: list[dict[str, Any]] = []
    for item in pipelines:
        pipeline = dict(item)
        catalog_id = PIPELINE_CATALOG_IDS.get(str(pipeline.get("id")))
        definition = definitions.get(catalog_id) if catalog_id else None
        if definition is None:
            blocker = (
                "service-not-in-runtime-catalog"
                if catalog_available
                else "runtime-catalog-unavailable"
            )
            pipeline["management"] = {
                "catalog_service_id": catalog_id,
                "registered": False,
                "source": "runtime-catalog",
                "evidence_quality": "unavailable",
                "effective_lifecycle_state": "not-authorized",
                "management_blockers": [blocker],
            }
        else:
            lifecycle = definition["lifecycle_authorization"]
            pipeline["management"] = {
                "catalog_service_id": definition["id"],
                "registered": True,
                "source": "runtime-catalog",
                "evidence_quality": "authoritative-management",
                "name": definition["name"],
                "kind": definition["kind"],
                "owner": definition["owner"],
                "criticality": definition["criticality"],
                "identity_contract": definition["identity_contract"],
                "controller": definition["controller"],
                "health_policy": definition["health_policy"],
                "lifecycle_authorization": lifecycle,
                "effective_lifecycle_state": lifecycle["state"],
                "catalog_status": definition["catalog_status"],
                "takeover_ready": definition["takeover_ready"],
                "management_blockers": definition["management_blockers"],
            }
        pipeline.setdefault(
            "telemetry_evidence",
            {
                "quality": "heuristic",
                "source": "legacy-pid-or-process-observation",
                "authoritative_for_management": False,
            },
        )
        enriched.append(pipeline)
    return enriched
