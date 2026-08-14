"""Read-only assurance projection for saved-search monitoring capabilities.

This module intentionally does not schedule, replay, or inspect a search.  It
turns absent monitoring infrastructure into a bounded machine-readable state
instead of letting callers infer that a saved query is being watched.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

SAVED_SEARCH_MONITORING_SCHEMA_VERSION = "research-saved-search-monitoring-v1"
MAX_SAVED_SEARCH_MONITORING_ITEMS = 200

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEARCH_SNAPSHOT_ID_RE = re.compile(
    r"^search-snap-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$"
)


class SavedSearchMonitoringUnavailable(RuntimeError):
    """The persisted records cannot support a safe monitoring projection."""


def _safe_identifier(value: Any) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        raise SavedSearchMonitoringUnavailable(
            "SAVED_SEARCH_MONITORING_RECORD_INVALID"
        )
    return value


def build_saved_search_monitoring_status(
    project: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a content-free, fail-closed status without mutating storage."""

    if not isinstance(project, Mapping):
        raise SavedSearchMonitoringUnavailable(
            "SAVED_SEARCH_MONITORING_PROJECT_INVALID"
        )
    project_id = _safe_identifier(project.get("id"))
    records = project.get("saved_searches")
    if (
        not isinstance(records, list)
        or len(records) > MAX_SAVED_SEARCH_MONITORING_ITEMS
    ):
        raise SavedSearchMonitoringUnavailable(
            "SAVED_SEARCH_MONITORING_INVENTORY_INVALID"
        )

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise SavedSearchMonitoringUnavailable(
                "SAVED_SEARCH_MONITORING_RECORD_INVALID"
            )
        saved_search_id = _safe_identifier(record.get("id"))
        if saved_search_id in seen:
            raise SavedSearchMonitoringUnavailable(
                "SAVED_SEARCH_MONITORING_RECORD_DUPLICATE"
            )
        seen.add(saved_search_id)
        query_contract_sha256 = record.get("query_sha256")
        if (
            not isinstance(query_contract_sha256, str)
            or _SHA256_RE.fullmatch(query_contract_sha256) is None
        ):
            raise SavedSearchMonitoringUnavailable(
                "SAVED_SEARCH_MONITORING_RECORD_INVALID"
            )
        linked_snapshot_state = record.get("snapshot_status")
        linked_snapshot_id = record.get("search_snapshot_id")
        if linked_snapshot_state == "verified":
            if (
                not isinstance(linked_snapshot_id, str)
                or _SEARCH_SNAPSHOT_ID_RE.fullmatch(linked_snapshot_id) is None
            ):
                raise SavedSearchMonitoringUnavailable(
                    "SAVED_SEARCH_MONITORING_RECORD_INVALID"
                )
        elif linked_snapshot_state == "unavailable":
            if linked_snapshot_id is not None:
                raise SavedSearchMonitoringUnavailable(
                    "SAVED_SEARCH_MONITORING_RECORD_INVALID"
                )
        else:
            raise SavedSearchMonitoringUnavailable(
                "SAVED_SEARCH_MONITORING_RECORD_INVALID"
            )
        items.append(
            {
                "saved_search_id": saved_search_id,
                "query_contract_sha256": query_contract_sha256,
                "linked_snapshot_state": linked_snapshot_state,
                "monitor_run_state": "never_run",
                "last_monitor_run_at": None,
                "checkpoint_snapshot_id": None,
                "delta_state": "not_computable",
                "added_result_count": None,
                "new_only_available": False,
                "notification_delivery_state": "not_configured",
                "reason_code": "SCHEDULER_CHECKPOINT_AND_DELTA_NOT_CONFIGURED",
            }
        )

    return {
        "schema_version": SAVED_SEARCH_MONITORING_SCHEMA_VERSION,
        "project_id": project_id,
        "evidence_scope": "project_saved_search_records_only",
        "read_side_effects": "none",
        "scheduler_state": "not_configured",
        "checkpoint_state": "not_established",
        "delta_semantics_state": "not_established",
        "new_only_state": "not_available",
        "notification_state": "not_configured",
        "items": items,
    }


__all__ = (
    "MAX_SAVED_SEARCH_MONITORING_ITEMS",
    "SAVED_SEARCH_MONITORING_SCHEMA_VERSION",
    "SavedSearchMonitoringUnavailable",
    "build_saved_search_monitoring_status",
)
