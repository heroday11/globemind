from __future__ import annotations

import importlib


def test_legacy_runtime_control_modules_are_identity_aliases() -> None:
    for name in (
        "catalog",
        "manifest",
        "constants",
        "redaction",
        "json_safety",
        "dependency_probes",
        "process_identity",
        "inspection",
        "lifecycle",
        "cli",
    ):
        implementation = importlib.import_module(f"runtime_control.{name}")
        compatibility = importlib.import_module(f"scripts.runtime_control.{name}")
        assert compatibility is implementation


def test_legacy_public_exports_resolve_to_backend_implementation() -> None:
    implementation = importlib.import_module("runtime_control")
    compatibility = importlib.import_module("scripts.runtime_control")

    assert compatibility.catalog_payload is implementation.catalog_payload
    assert compatibility.Inventory is implementation.Inventory
    assert compatibility.redact_text is implementation.redact_text
