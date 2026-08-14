#!/usr/bin/env python3
"""Compatibility wrapper for the modular runtime control plane."""

from __future__ import annotations

try:
    from scripts.runtime_control import (
        DATA_ROOT,
        DEFAULT_MANIFEST,
        DESTRUCTIVE_COMMANDS,
        LIFECYCLE_COMMANDS,
        PROJECT_ROOT,
        REDACTED,
        SAFE_COMMANDS,
        SCHEMA_VERSION,
        SEVERITY_ORDER,
        Inventory,
        InventoryError,
        LifecycleDispatcher,
        LifecycleError,
        RuntimeInspector,
        list_payload,
        load_inventory,
        main,
        redact_argv,
        redact_text,
        sanitize,
        service_dependency_closure,
        service_dependency_order,
        validate_inventory,
    )
except ModuleNotFoundError:  # Direct execution: python scripts/globemind_runtime.py
    from runtime_control import (  # type: ignore[no-redef]
        DATA_ROOT,
        DEFAULT_MANIFEST,
        DESTRUCTIVE_COMMANDS,
        LIFECYCLE_COMMANDS,
        PROJECT_ROOT,
        REDACTED,
        SAFE_COMMANDS,
        SCHEMA_VERSION,
        SEVERITY_ORDER,
        Inventory,
        InventoryError,
        LifecycleDispatcher,
        LifecycleError,
        RuntimeInspector,
        list_payload,
        load_inventory,
        main,
        redact_argv,
        redact_text,
        sanitize,
        service_dependency_closure,
        service_dependency_order,
        validate_inventory,
    )

# Private aliases retained for callers that used the original single-file API.
_list_payload = list_payload

__all__ = [
    "DATA_ROOT",
    "DEFAULT_MANIFEST",
    "DESTRUCTIVE_COMMANDS",
    "Inventory",
    "InventoryError",
    "LIFECYCLE_COMMANDS",
    "LifecycleDispatcher",
    "LifecycleError",
    "PROJECT_ROOT",
    "REDACTED",
    "RuntimeInspector",
    "SAFE_COMMANDS",
    "SCHEMA_VERSION",
    "SEVERITY_ORDER",
    "load_inventory",
    "main",
    "redact_argv",
    "redact_text",
    "sanitize",
    "service_dependency_closure",
    "service_dependency_order",
    "validate_inventory",
]


if __name__ == "__main__":
    raise SystemExit(main())
