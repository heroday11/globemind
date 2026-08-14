"""Lazy public compatibility surface for the GlobeMind runtime control plane.

Importing the package must not load lifecycle capabilities into read-only Web
consumers. Public names retain compatibility and resolve their owning module only
when explicitly requested.
"""

from importlib import import_module

_EXPORTS = {
    "AtomicAuditWriter": ("lifecycle", "AtomicAuditWriter"),
    "DATA_ROOT": ("constants", "DATA_ROOT"),
    "DEFAULT_MANIFEST": ("constants", "DEFAULT_MANIFEST"),
    "DESTRUCTIVE_COMMANDS": ("constants", "DESTRUCTIVE_COMMANDS"),
    "Inventory": ("manifest", "Inventory"),
    "InventoryError": ("manifest", "InventoryError"),
    "LIFECYCLE_COMMANDS": ("constants", "LIFECYCLE_COMMANDS"),
    "LifecycleDispatcher": ("lifecycle", "LifecycleDispatcher"),
    "LifecycleError": ("lifecycle", "LifecycleError"),
    "PROJECT_ROOT": ("constants", "PROJECT_ROOT"),
    "REDACTED": ("constants", "REDACTED"),
    "RuntimeInspector": ("inspection", "RuntimeInspector"),
    "SAFE_COMMANDS": ("constants", "SAFE_COMMANDS"),
    "SCHEMA_VERSION": ("constants", "SCHEMA_VERSION"),
    "SEVERITY_ORDER": ("constants", "SEVERITY_ORDER"),
    "catalog_drift_issues": ("catalog", "catalog_drift_issues"),
    "catalog_payload": ("catalog", "catalog_payload"),
    "list_payload": ("inspection", "list_payload"),
    "load_inventory": ("manifest", "load_inventory"),
    "main": ("cli", "main"),
    "public_catalog_definition": ("catalog", "public_catalog_definition"),
    "redact_argv": ("redaction", "redact_argv"),
    "redact_text": ("redaction", "redact_text"),
    "sanitize": ("redaction", "sanitize"),
    "service_dependency_closure": ("manifest", "service_dependency_closure"),
    "service_dependency_order": ("manifest", "service_dependency_order"),
    "validate_inventory": ("manifest", "validate_inventory"),
}

__all__ = tuple(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTS))
