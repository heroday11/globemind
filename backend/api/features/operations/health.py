"""Read-only health probe for the operations control-plane catalog."""

from __future__ import annotations

from api.features import FeatureHealthCheck, run_feature_probe
from api.features.operations.runtime_catalog import load_runtime_catalog


def probe_operations_health() -> FeatureHealthCheck:
    def operation() -> dict[str, int | bool]:
        catalog = load_runtime_catalog()
        summary = catalog.get("summary") or {}
        return {
            "catalog_available": catalog.get("available") is True,
            "services_registered": int(summary.get("service_count") or 0),
            "services_takeover_ready": int(summary.get("takeover_ready") or 0),
            "services_takeover_blocked": int(summary.get("takeover_blocked") or 0),
        }

    return run_feature_probe(
        "operations",
        ("runtime:service-catalog",),
        operation,
    )


__all__ = ("probe_operations_health",)
