#!/usr/bin/env python3
"""Validate the machine-readable GlobeMind runtime configuration catalog."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "config" / "runtime" / "env-manifest.json"
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
SENSITIVITIES = {"public", "internal", "secret"}
ACTIVATIONS = {"process_restart", "service_restart", "checkpointed_restart", "next_run"}
DEFAULT_MODES = {
    "required",
    "required_in_production",
    "secret_required",
    "safe_default",
    "derived",
    "disabled_by_default",
    "optional",
    "legacy_contextual",
}


class ManifestError(RuntimeError):
    """Raised when the configuration catalog violates its contract."""


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")
    return value


def validate_manifest(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ManifestError("manifest root must be an object")
    if payload.get("schema_version") != 1:
        raise ManifestError("schema_version must be 1")

    raw_services = payload.get("services")
    if not isinstance(raw_services, dict) or not raw_services:
        raise ManifestError("services must be a non-empty object")
    services: set[str] = set()
    for name, metadata in raw_services.items():
        _nonempty_string(name, "service name")
        if not isinstance(metadata, dict):
            raise ManifestError(f"service {name} metadata must be an object")
        _nonempty_string(metadata.get("owner"), f"service {name}.owner")
        _nonempty_string(metadata.get("description"), f"service {name}.description")
        services.add(name)

    variables = payload.get("variables")
    if not isinstance(variables, list) or not variables:
        raise ManifestError("variables must be a non-empty array")

    names: list[str] = []
    scope_counts: Counter[str] = Counter()
    for index, item in enumerate(variables):
        prefix = f"variables[{index}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{prefix} must be an object")
        name = _nonempty_string(item.get("name"), f"{prefix}.name")
        if not ENV_NAME_RE.fullmatch(name):
            raise ManifestError(f"{prefix}.name is not an environment variable: {name!r}")
        names.append(name)

        _nonempty_string(item.get("owner"), f"{prefix}.owner")
        _nonempty_string(item.get("description"), f"{prefix}.description")
        scope = _nonempty_string(item.get("scope"), f"{prefix}.scope")
        scope_counts[scope] += 1

        sensitivity = item.get("sensitivity")
        if sensitivity not in SENSITIVITIES:
            raise ManifestError(
                f"{prefix}.sensitivity must be one of {sorted(SENSITIVITIES)}"
            )

        item_services = item.get("services")
        if not isinstance(item_services, list) or not item_services:
            raise ManifestError(f"{prefix}.services must be a non-empty array")
        unknown_services = sorted(
            service for service in item_services if service not in services
        )
        if unknown_services:
            raise ManifestError(f"{prefix}.services contains unknown values: {unknown_services}")
        if len(set(item_services)) != len(item_services):
            raise ManifestError(f"{prefix}.services contains duplicates")

        if not isinstance(item.get("restart_required"), bool):
            raise ManifestError(f"{prefix}.restart_required must be boolean")
        activation = item.get("activation")
        if activation not in ACTIVATIONS:
            raise ManifestError(f"{prefix}.activation must be one of {sorted(ACTIVATIONS)}")

        default_policy = item.get("default_policy")
        if not isinstance(default_policy, dict):
            raise ManifestError(f"{prefix}.default_policy must be an object")
        mode = default_policy.get("mode")
        if mode not in DEFAULT_MODES:
            raise ManifestError(
                f"{prefix}.default_policy.mode must be one of {sorted(DEFAULT_MODES)}"
            )
        has_value = "value" in default_policy
        if mode == "safe_default" and not has_value:
            raise ManifestError(f"{prefix} safe_default must declare value")
        if sensitivity == "secret" and has_value:
            raise ManifestError(f"{prefix} secret variables must never embed a default value")
        if mode in {"secret_required", "required", "required_in_production"} and has_value:
            raise ManifestError(f"{prefix} required variables must not embed a default value")

    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise ManifestError(f"duplicate variable names: {duplicates}")

    required_scopes = {"web", "database", "security", "ai", "pipeline"}
    missing_scopes = sorted(required_scopes - set(scope_counts))
    if missing_scopes:
        raise ManifestError(f"manifest does not cover required scopes: {missing_scopes}")

    return {
        "services": len(services),
        "variables": len(variables),
        "scope_counts": dict(sorted(scope_counts.items())),
    }


def load_and_validate(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    return validate_manifest(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = load_and_validate(args.manifest)
    except ManifestError as exc:
        print(f"runtime config manifest error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps({"status": "passed", **summary}, indent=2, sort_keys=True))
    else:
        scopes = ", ".join(f"{key}={value}" for key, value in summary["scope_counts"].items())
        print(
            "runtime config manifest: PASS; "
            f"services={summary['services']}; variables={summary['variables']}; {scopes}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
