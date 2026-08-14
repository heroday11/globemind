#!/usr/bin/env python3
"""Validate the V0.10 PostgreSQL consumer inventory without opening a connection."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = REPOSITORY_ROOT / "ops" / "runtime" / "database-consumers.json"
DEFAULT_SERVICES = REPOSITORY_ROOT / "ops" / "runtime" / "services.json"

EXPECTED_SERVICE_IDS = (
    "web",
    "wave1_loader",
    "l1_prep",
    "l1_extract",
    "daily_ingest",
    "quality_labels",
    "ground_refresh",
    "ground_images",
)
EXPECTED_ENTRYPOINTS = {
    "web": ("python_cli", "backend/serve_prod.py"),
    "wave1_loader": ("python_cli", "scripts/stream_load_news_to_postgres.py"),
    "l1_prep": ("python_cli", "scripts/stream_l1_event_features.py"),
    "l1_extract": ("python_cli", "scripts/stream_l1_event_features.py"),
    "daily_ingest": ("shell_loop", "deploy/daily_news_ingest_loop.sh"),
    "quality_labels": ("shell_loop", "deploy/news_quality_labels_loop.sh"),
    "ground_refresh": ("shell_loop", "deploy/ground_news_realtime_refresh_loop.sh"),
    "ground_images": ("shell_loop", "deploy/ground_news_image_backfill_loop.sh"),
}
CURRENT_ROLES = {
    "web": ("web_runtime", "assigned_runtime"),
    "wave1_loader": ("wave1_loader", "assigned_runtime"),
    "daily_ingest": (None, "legacy_runtime_unverified"),
    "l1_prep": ("postgres", "legacy_owner_default"),
    "l1_extract": ("postgres", "legacy_owner_default"),
    "quality_labels": ("postgres", "legacy_owner_default"),
    "ground_refresh": ("postgres", "legacy_owner_default"),
    "ground_images": ("postgres", "legacy_owner_default"),
}
ASSIGNED_TARGET_ROLES = {
    "web": "web_runtime",
    "wave1_loader": "wave1_loader",
    "daily_ingest": "wave1_loader",
}
EXPECTED_CAPABILITIES = {
    "web": {"web_allowlist"},
    "wave1_loader": {"wave1_loader_allowlist"},
    "daily_ingest": {"wave1_loader_allowlist"},
    "l1_prep": {"read_news_quality_inputs", "write_l1_prep"},
    "l1_extract": {"read_l1_prep", "write_l1_event_extractions"},
    "quality_labels": {"read_news_quality_inputs", "write_news_quality_labels"},
    "ground_refresh": {"read_news_story_inputs", "write_l1_l15_l2_story_data"},
    "ground_images": {"read_news_story_inputs", "write_news_and_story_image_assets"},
}

SECRET_FILE_ENV = "secret_file_environment"
CONTROLLER_SECRET_SOURCE_ENV = "controller_source_secret_file_environment"
MATERIALIZED_SECRET_FILE_ENV = "materialized_secret_file_environment"
LEGACY_PLAINTEXT_ENV = "legacy_plaintext_environment_or_dotenv"
EXPECTED_CREDENTIAL_REFERENCES = {
    "web": {("GLOBEMIND_DB_PASSWORD_FILE", SECRET_FILE_ENV)},
    "wave1_loader": {
        ("WAVE1_LOADER_DB_PASSWORD_SOURCE_FILE", CONTROLLER_SECRET_SOURCE_ENV),
        ("GLOBEMIND_DB_PASSWORD_FILE", MATERIALIZED_SECRET_FILE_ENV),
    },
    "daily_ingest": {
        (
            "DAILY_INGEST_DB_PASSWORD_SOURCE_FILE",
            "target_controller_source_secret_file_environment",
        ),
        ("GLOBEMIND_DB_PASSWORD_FILE", "target_materialized_secret_file_environment"),
    },
}
LEGACY_RESOLVER_REFERENCES = {
    ("GLOBEMIND_DB_PASSWORD_FILE", SECRET_FILE_ENV),
    ("L1_DB_PASSWORD", LEGACY_PLAINTEXT_ENV),
    ("PG_WRITE_PASSWORD", LEGACY_PLAINTEXT_ENV),
    ("DB_PASSWORD", LEGACY_PLAINTEXT_ENV),
    ("PG_PASSWORD", LEGACY_PLAINTEXT_ENV),
}
for _service_id in EXPECTED_SERVICE_IDS:
    if _service_id not in EXPECTED_CREDENTIAL_REFERENCES:
        EXPECTED_CREDENTIAL_REFERENCES[_service_id] = LEGACY_RESOLVER_REFERENCES

EXPECTED_MAINTENANCE = {
    "v093-database-schema": {
        "class": "schema_migration",
        "path": "deploy/v093_database_schema.py",
        "credential_references": {("--admin-password-file", "command_line_secret_file_reference")},
    },
    "database-runtime-roles": {
        "class": "role_administration",
        "path": "deploy/db_runtime_roles.py",
        "credential_references": {
            ("--admin-password-file", "command_line_secret_file_reference"),
            ("--web-password-file", "command_line_secret_file_reference"),
            ("--loader-password-file", "command_line_secret_file_reference"),
        },
    },
}

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key)\s*[:=]", re.IGNORECASE
)
POSTGRES_URL_RE = re.compile(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?://", re.IGNORECASE)
FORBIDDEN_SECRET_KEYS = {
    "credential_value",
    "database_url",
    "dsn",
    "password",
    "password_value",
    "secret",
    "secret_value",
    "token",
    "value",
}
EXTERNAL_DEPENDENCY_KEYS = {
    "name",
    "required",
    "verification",
    "via_health",
    "via_probe",
    "reason",
}
EXTERNAL_VERIFICATION_MODES = {
    "external-monitor",
    "local-health",
    "manual",
    "probe",
    "unverified",
}


class InventoryError(RuntimeError):
    """Raised when either inventory input violates the offline contract."""


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
    except InventoryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read JSON document {path}: {exc}") from exc


def _require_object(value: object, location: str) -> dict:
    if not isinstance(value, dict):
        raise InventoryError(f"{location} must be an object")
    return value


def _require_exact_keys(value: dict, expected: set[str], location: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise InventoryError(f"{location} schema mismatch: missing={missing}, unknown={unknown}")


def _require_nonempty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InventoryError(f"{location} must be a trimmed non-empty string")
    return value


def _validate_no_secret_material(value: object, location: str = "inventory") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_SECRET_KEYS:
                raise InventoryError(
                    f"embedded secret material field is forbidden at {location}.{key}"
                )
            _validate_no_secret_material(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_no_secret_material(child, f"{location}[{index}]")
    elif isinstance(value, str):
        if POSTGRES_URL_RE.search(value) or SECRET_ASSIGNMENT_RE.search(value):
            raise InventoryError(f"possible embedded secret material at {location}")
        if "-----BEGIN " in value and "PRIVATE KEY-----" in value:
            raise InventoryError(f"possible embedded private key at {location}")


def _validate_repo_file(root: Path, raw_path: object, location: str) -> str:
    path = _require_nonempty_string(raw_path, location)
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or "\\" in path:
        raise InventoryError(f"{location} must be a repository-relative POSIX path")
    resolved_root = root.resolve()
    resolved = (resolved_root / pure).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise InventoryError(f"{location} escapes the repository") from exc
    if not resolved.is_file():
        raise InventoryError(f"{location} does not name an existing file: {path}")
    return path


def _external_dependency_names(value: object, location: str) -> set[str]:
    if not isinstance(value, list):
        raise InventoryError(f"{location} must be an array")
    names: list[str] = []
    for index, raw_dependency in enumerate(value):
        item_location = f"{location}[{index}]"
        if isinstance(raw_dependency, str):
            name = _require_nonempty_string(raw_dependency, item_location)
        else:
            dependency = _require_object(raw_dependency, item_location)
            unknown = sorted(set(dependency) - EXTERNAL_DEPENDENCY_KEYS)
            missing = sorted({"name", "required", "verification"} - set(dependency))
            if missing or unknown:
                raise InventoryError(
                    f"{item_location} schema mismatch: missing={missing}, unknown={unknown}"
                )
            name = _require_nonempty_string(
                dependency["name"], f"{item_location}.name"
            )
            if not isinstance(dependency["required"], bool):
                raise InventoryError(f"{item_location}.required must be a boolean")
            verification = _require_nonempty_string(
                dependency["verification"], f"{item_location}.verification"
            )
            if verification not in EXTERNAL_VERIFICATION_MODES:
                raise InventoryError(
                    f"{item_location}.verification has an unsupported mode"
                )
            via_health = dependency.get("via_health")
            via_probe = dependency.get("via_probe")
            if via_health is not None:
                _require_nonempty_string(via_health, f"{item_location}.via_health")
            if via_probe is not None:
                _require_nonempty_string(via_probe, f"{item_location}.via_probe")
            if verification == "local-health" and via_health is None:
                raise InventoryError(
                    f"{item_location}.via_health is required for local-health"
                )
            if verification == "probe" and via_probe is None:
                raise InventoryError(f"{item_location}.via_probe is required for probe")
            if verification != "local-health" and via_health is not None:
                raise InventoryError(
                    f"{item_location}.via_health is only valid for local-health"
                )
            if verification != "probe" and via_probe is not None:
                raise InventoryError(f"{item_location}.via_probe is only valid for probe")
            reason = dependency.get("reason")
            if reason is not None:
                _require_nonempty_string(reason, f"{item_location}.reason")
        names.append(name)
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise InventoryError(f"{location} contains duplicate names: {duplicates}")
    return set(names)


def _manifest_services(payload: object) -> dict[str, dict]:
    manifest = _require_object(payload, "services manifest")
    services = manifest.get("services")
    if not isinstance(services, list) or not services:
        raise InventoryError("services manifest.services must be a non-empty array")

    ids: list[str] = []
    by_id: dict[str, dict] = {}
    external_names_by_id: dict[str, set[str]] = {}
    for index, raw_service in enumerate(services):
        location = f"services manifest.services[{index}]"
        service = _require_object(raw_service, location)
        service_id = _require_nonempty_string(service.get("id"), f"{location}.id")
        _require_nonempty_string(service.get("owner"), f"{location}.owner")
        external_names_by_id[service_id] = _external_dependency_names(
            service.get("external_dependencies"),
            f"{location}.external_dependencies",
        )
        controller = _require_object(service.get("controller"), f"{location}.controller")
        _require_nonempty_string(controller.get("type"), f"{location}.controller.type")
        _require_nonempty_string(controller.get("path"), f"{location}.controller.path")
        ids.append(service_id)
        by_id[service_id] = service

    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise InventoryError(f"services manifest contains duplicate service ids: {duplicates}")

    postgres_services = {
        service_id
        for service_id in by_id
        if "postgres-news" in external_names_by_id[service_id]
    }
    expected = set(EXPECTED_SERVICE_IDS)
    if postgres_services != expected:
        raise InventoryError(
            "services manifest postgres-news set differs from the V0.10 contract: "
            f"missing={sorted(expected - postgres_services)}, "
            f"unexpected={sorted(postgres_services - expected)}"
        )
    return by_id


def _controller_repo_path(raw_path: str) -> str:
    prefix = "${PROJECT_ROOT}/"
    if not raw_path.startswith(prefix):
        raise InventoryError("postgres-news controller paths must be rooted at ${PROJECT_ROOT}")
    return raw_path[len(prefix) :]


def _validate_database(value: object, location: str) -> None:
    database = _require_object(value, location)
    _require_exact_keys(database, {"name", "schema"}, location)
    if database != {"name": "news", "schema": "public"}:
        raise InventoryError(f"{location} must be fixed to news/public")


def _reference_pairs(
    value: object,
    location: str,
    *,
    environment_names: bool,
) -> set[tuple[str, str]]:
    if not isinstance(value, list) or not value:
        raise InventoryError(f"{location} must be a non-empty array")
    references: list[tuple[str, str]] = []
    for index, raw_reference in enumerate(value):
        item_location = f"{location}[{index}]"
        reference = _require_object(raw_reference, item_location)
        _require_exact_keys(reference, {"name", "type"}, item_location)
        name = _require_nonempty_string(reference["name"], f"{item_location}.name")
        reference_type = _require_nonempty_string(reference["type"], f"{item_location}.type")
        if environment_names and not ENV_NAME_RE.fullmatch(name):
            raise InventoryError(f"{item_location}.name is not an environment variable name")
        if not environment_names and not re.fullmatch(r"--[a-z][a-z0-9-]*", name):
            raise InventoryError(f"{item_location}.name is not a command-line option name")
        references.append((name, reference_type))
    duplicates = sorted(key for key, count in Counter(references).items() if count > 1)
    if duplicates:
        raise InventoryError(f"{location} contains duplicate references: {duplicates}")
    return set(references)


def _validate_entrypoint(
    value: object,
    location: str,
    root: Path,
    *,
    expected: tuple[str, str],
) -> None:
    entrypoint = _require_object(value, location)
    _require_exact_keys(entrypoint, {"type", "path"}, location)
    entrypoint_type = _require_nonempty_string(entrypoint["type"], f"{location}.type")
    path = _validate_repo_file(root, entrypoint["path"], f"{location}.path")
    if (entrypoint_type, path) != expected:
        raise InventoryError(
            f"{location} differs from the verified entrypoint: "
            f"expected={expected!r}, actual={(entrypoint_type, path)!r}"
        )


def _validate_role_contract(consumer: dict, service_id: str, location: str) -> None:
    current = _require_object(consumer["current_role"], f"{location}.current_role")
    _require_exact_keys(current, {"name", "status"}, f"{location}.current_role")
    actual_current = (current.get("name"), current.get("status"))
    if actual_current != CURRENT_ROLES[service_id]:
        raise InventoryError(
            f"{location}.current_role differs from the source-verified role: "
            f"expected={CURRENT_ROLES[service_id]!r}, actual={actual_current!r}"
        )

    target = _require_object(consumer["target_role"], f"{location}.target_role")
    _require_exact_keys(
        target, {"name", "status", "required_capabilities"}, f"{location}.target_role"
    )
    capabilities = target["required_capabilities"]
    if not isinstance(capabilities, list) or not capabilities:
        raise InventoryError(f"{location}.target_role.required_capabilities must be non-empty")
    if not all(isinstance(item, str) and CAPABILITY_RE.fullmatch(item) for item in capabilities):
        raise InventoryError(
            f"{location}.target_role.required_capabilities contains an invalid capability"
        )
    if len(set(capabilities)) != len(capabilities):
        raise InventoryError(f"{location}.target_role.required_capabilities contains duplicates")
    if set(capabilities) != EXPECTED_CAPABILITIES[service_id]:
        raise InventoryError(
            f"{location}.target_role.required_capabilities differs from the reviewed contract"
        )

    assigned_role = ASSIGNED_TARGET_ROLES.get(service_id)
    if assigned_role is not None:
        if target.get("status") != "assigned" or target.get("name") != assigned_role:
            raise InventoryError(f"{location}.target_role must remain assigned to {assigned_role}")
    elif target.get("status") != "unassigned" or target.get("name") is not None:
        raise InventoryError(
            f"{location}.target_role must remain explicitly unassigned; do not invent a role"
        )


def _validate_transport(value: object, service_id: str, location: str) -> None:
    transport = _require_object(value, location)
    _require_exact_keys(
        transport,
        {"network_scope", "current_tls", "target_tls", "status"},
        location,
    )
    if service_id == "daily_ingest":
        expected = {
            "network_scope": "legacy_runtime_unverified",
            "current_tls": "legacy_runtime_unverified",
            "target_tls": "disabled_private_scram_exception",
            "status": "managed_takeover_pending",
        }
    elif service_id in ASSIGNED_TARGET_ROLES:
        expected = {
            "network_scope": "private_network",
            "current_tls": "disabled_private_scram_exception",
            "target_tls": "verify_full",
            "status": "exception_active",
        }
    else:
        expected = {
            "network_scope": "private_network",
            "current_tls": "driver_default_unverified",
            "target_tls": "verify_full",
            "status": "unverified",
        }
    if transport != expected:
        raise InventoryError(f"{location} differs from the reviewed TLS status")


def _validate_migration(consumer: dict, service_id: str, location: str) -> None:
    window = _require_object(consumer["maintenance_window"], f"{location}.maintenance_window")
    _require_exact_keys(window, {"required", "status"}, f"{location}.maintenance_window")
    if window != {"required": True, "status": "not_scheduled"}:
        raise InventoryError(
            f"{location}.maintenance_window must remain required and not_scheduled"
        )

    migration = _require_object(consumer["migration"], f"{location}.migration")
    _require_exact_keys(migration, {"status", "blockers"}, f"{location}.migration")
    blockers = migration["blockers"]
    if not isinstance(blockers, list) or not all(
        isinstance(item, str) and CAPABILITY_RE.fullmatch(item) for item in blockers
    ):
        raise InventoryError(f"{location}.migration.blockers must be a code array")
    if len(set(blockers)) != len(blockers):
        raise InventoryError(f"{location}.migration.blockers contains duplicates")
    if service_id == "daily_ingest":
        expected_status = "managed_role_takeover_pending"
        expected_blockers = {
            "checkpointed_takeover_not_completed",
            "current_runtime_role_unverified",
            "current_runtime_tls_unverified",
            "tls_not_verified",
        }
    elif service_id in ASSIGNED_TARGET_ROLES:
        expected_status = "role_assigned_transport_pending"
        expected_blockers = {"tls_not_verified"}
    else:
        expected_status = "blocked"
        expected_blockers = {
            "credential_source_allows_plaintext",
            "runtime_ddl_not_separated",
            "target_role_unassigned",
            "tls_not_verified",
        }
    if migration.get("status") != expected_status or set(blockers) != expected_blockers:
        raise InventoryError(f"{location}.migration differs from the reviewed status")


def _validate_consumer(
    raw_consumer: object,
    index: int,
    services: dict[str, dict],
    root: Path,
) -> str:
    location = f"consumers[{index}]"
    consumer = _require_object(raw_consumer, location)
    _require_exact_keys(
        consumer,
        {
            "id",
            "service_id",
            "owner",
            "entrypoint",
            "controller",
            "database",
            "current_role",
            "target_role",
            "credential_references",
            "transport",
            "long_running",
            "maintenance_window",
            "migration",
        },
        location,
    )
    service_id = _require_nonempty_string(consumer["service_id"], f"{location}.service_id")
    if service_id not in services:
        raise InventoryError(f"{location}.service_id names an unknown service: {service_id}")
    if service_id not in EXPECTED_SERVICE_IDS:
        raise InventoryError(f"{location}.service_id is not a postgres-news service: {service_id}")
    expected_id = f"postgres-news-{service_id.replace('_', '-')}"
    if consumer["id"] != expected_id:
        raise InventoryError(f"{location}.id must be the stable id {expected_id!r}")

    service = services[service_id]
    if consumer["owner"] != service["owner"]:
        raise InventoryError(f"{location}.owner differs from services.json")
    _validate_entrypoint(
        consumer["entrypoint"],
        f"{location}.entrypoint",
        root,
        expected=EXPECTED_ENTRYPOINTS[service_id],
    )

    controller = _require_object(consumer["controller"], f"{location}.controller")
    _require_exact_keys(controller, {"type", "path"}, f"{location}.controller")
    controller_type = _require_nonempty_string(controller["type"], f"{location}.controller.type")
    controller_path = _validate_repo_file(root, controller["path"], f"{location}.controller.path")
    manifest_controller = service["controller"]
    expected_controller = (
        manifest_controller["type"],
        _controller_repo_path(manifest_controller["path"]),
    )
    if (controller_type, controller_path) != expected_controller:
        raise InventoryError(f"{location}.controller differs from services.json")

    _validate_database(consumer["database"], f"{location}.database")
    _validate_role_contract(consumer, service_id, location)
    references = _reference_pairs(
        consumer["credential_references"],
        f"{location}.credential_references",
        environment_names=True,
    )
    if references != EXPECTED_CREDENTIAL_REFERENCES[service_id]:
        raise InventoryError(
            f"{location}.credential_references differs from the verified name/type set"
        )
    _validate_transport(consumer["transport"], service_id, f"{location}.transport")
    if consumer["long_running"] is not True:
        raise InventoryError(f"{location}.long_running must be true")
    _validate_migration(consumer, service_id, location)
    return service_id


def _validate_maintenance(value: object, root: Path) -> int:
    if not isinstance(value, list):
        raise InventoryError("maintenance_entrypoints must be an array")
    ids: list[str] = []
    for index, raw_entry in enumerate(value):
        location = f"maintenance_entrypoints[{index}]"
        entry = _require_object(raw_entry, location)
        _require_exact_keys(
            entry,
            {
                "id",
                "class",
                "entrypoint",
                "database",
                "execution_role",
                "credential_references",
                "transport",
                "long_running",
                "approval",
            },
            location,
        )
        entry_id = _require_nonempty_string(entry["id"], f"{location}.id")
        ids.append(entry_id)
        expected = EXPECTED_MAINTENANCE.get(entry_id)
        if expected is None:
            raise InventoryError(f"{location}.id is not an approved maintenance entrypoint")
        if entry["class"] != expected["class"]:
            raise InventoryError(f"{location}.class differs from the approved class")
        _validate_entrypoint(
            entry["entrypoint"],
            f"{location}.entrypoint",
            root,
            expected=("python_cli", expected["path"]),
        )
        _validate_database(entry["database"], f"{location}.database")
        role = _require_object(entry["execution_role"], f"{location}.execution_role")
        _require_exact_keys(role, {"name", "status"}, f"{location}.execution_role")
        if role != {"name": "postgres", "status": "maintenance_only"}:
            raise InventoryError(f"{location}.execution_role must be maintenance-only postgres")
        references = _reference_pairs(
            entry["credential_references"],
            f"{location}.credential_references",
            environment_names=False,
        )
        if references != expected["credential_references"]:
            raise InventoryError(
                f"{location}.credential_references differs from the approved name/type set"
            )
        transport = _require_object(entry["transport"], f"{location}.transport")
        _require_exact_keys(
            transport,
            {"network_scope", "current_tls", "target_tls", "status"},
            f"{location}.transport",
        )
        if transport != {
            "network_scope": "operator_selected",
            "current_tls": "explicit_cli_selection",
            "target_tls": "verify_full",
            "status": "maintenance_only",
        }:
            raise InventoryError(f"{location}.transport differs from the approved contract")
        if entry["long_running"] is not False:
            raise InventoryError(f"{location}.long_running must be false")
        if entry["approval"] != "approved_on_demand":
            raise InventoryError(f"{location}.approval must be approved_on_demand")

    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise InventoryError(f"maintenance_entrypoints contains duplicate ids: {duplicates}")
    if set(ids) != set(EXPECTED_MAINTENANCE):
        raise InventoryError(
            "maintenance_entrypoints must contain exactly the two approved CLIs: "
            f"missing={sorted(set(EXPECTED_MAINTENANCE) - set(ids))}, "
            f"unexpected={sorted(set(ids) - set(EXPECTED_MAINTENANCE))}"
        )
    return len(ids)


def validate_inventory(
    payload: object,
    services_payload: object,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, object]:
    _validate_no_secret_material(payload)
    inventory = _require_object(payload, "inventory")
    _require_exact_keys(
        inventory,
        {
            "schema_version",
            "inventory_version",
            "service_manifest",
            "consumers",
            "maintenance_entrypoints",
        },
        "inventory",
    )
    if inventory["schema_version"] != 1:
        raise InventoryError("schema_version must be 1")
    if inventory["inventory_version"] != "0.10.0":
        raise InventoryError("inventory_version must be 0.10.0")
    if inventory["service_manifest"] != "ops/runtime/services.json":
        raise InventoryError("service_manifest must be ops/runtime/services.json")

    services = _manifest_services(services_payload)
    consumers = inventory["consumers"]
    if not isinstance(consumers, list):
        raise InventoryError("consumers must be an array")

    raw_ids = [
        item.get("id")
        for item in consumers
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    duplicate_ids = sorted(key for key, count in Counter(raw_ids).items() if count > 1)
    if duplicate_ids:
        raise InventoryError(f"consumers contains duplicate ids: {duplicate_ids}")

    service_ids = [
        _validate_consumer(item, index, services, repository_root)
        for index, item in enumerate(consumers)
    ]
    duplicate_services = sorted(key for key, count in Counter(service_ids).items() if count > 1)
    if duplicate_services:
        raise InventoryError(f"consumers contains duplicate service coverage: {duplicate_services}")
    expected = set(EXPECTED_SERVICE_IDS)
    actual = set(service_ids)
    if actual != expected:
        raise InventoryError(
            "consumer coverage differs from services.json postgres-news dependencies: "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )

    maintenance_count = _validate_maintenance(inventory["maintenance_entrypoints"], repository_root)
    return {
        "consumers": len(service_ids),
        "maintenance_entrypoints": maintenance_count,
        "service_ids": sorted(service_ids),
    }


def load_and_validate(
    inventory_path: Path = DEFAULT_INVENTORY,
    services_path: Path = DEFAULT_SERVICES,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, object]:
    return validate_inventory(
        load_json(inventory_path),
        load_json(services_path),
        repository_root=repository_root,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--services", type=Path, default=DEFAULT_SERVICES)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = load_and_validate(args.inventory, args.services)
    except InventoryError as exc:
        print(f"database consumer inventory error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps({"status": "passed", **summary}, indent=2, sort_keys=True))
    else:
        print(
            "database consumer inventory: PASS; "
            f"consumers={summary['consumers']}; "
            f"maintenance_entrypoints={summary['maintenance_entrypoints']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
