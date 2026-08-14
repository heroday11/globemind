"""Strict schema-v2 loading and trust-boundary validation."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .constants import (
    DATA_ROOT,
    DEFAULT_MANIFEST,
    LIFECYCLE_COMMANDS,
    PROJECT_ROOT,
    SAFE_COMMANDS,
    SAFE_COMPLETE_VALUES,
    SCHEMA_VERSION,
)


class InventoryError(RuntimeError):
    pass


class Inventory(dict[str, Any]):
    """A normal mapping with non-serialised filesystem trust roots."""

    def __init__(
        self,
        value: Mapping[str, Any],
        trusted_roots: Sequence[Path],
        *,
        manifest_path: Path | None = None,
        manifest_sha256: str | None = None,
    ) -> None:
        super().__init__(value)
        self.trusted_roots = tuple(root.resolve() for root in trusted_roots)
        self.manifest_path = manifest_path
        self.manifest_sha256 = manifest_sha256


TOP_LEVEL_KEYS = {
    "schema_version",
    "inventory_version",
    "project",
    "description",
    "variables",
    "control_policy",
    "probes",
    "services",
}
CONTROL_POLICY_KEYS = {
    "mode",
    "destructive_commands_enabled",
    "allowed_commands",
    "adoption_note",
}
SERVICE_KEYS = {
    "id",
    "name",
    "kind",
    "owner",
    "criticality",
    "check_interval_seconds",
    "dependencies",
    "external_dependencies",
    "controller",
    "pid",
    "port",
    "log",
    "health",
    "health_policy",
    "state",
    "output",
    "secret_policy",
    "secret_refs",
    "checkpoint",
    "replay",
    "lifecycle_authorization",
    "runbook",
}
CONTROLLER_KEYS = {"type", "path", "entrypoint", "interface", "adoption", "lifecycle"}
LIFECYCLE_KEYS = {
    "enabled",
    "argv",
    "controller_artifacts",
    "checkpoint",
    "rollback",
    "audit_directory",
    "timeout_seconds",
}
LIFECYCLE_EVIDENCE_KEYS = {"path", "sha256"}
PID_KEYS = {
    "kind",
    "path",
    "glob",
    "meta_path",
    "meta",
    "cmdline_contains",
    "expected",
    "minimum_running",
    "port_from_filename",
    "pidfile_start_tolerance_seconds",
}
PID_META_KEYS = {
    "format",
    "schema_version",
    "pid_index",
    "starttime_ticks_index",
    "pid_path",
    "starttime_ticks_path",
}
FILE_KEYS = {
    "path",
    "glob",
    "required",
    "format",
    "select",
    "timestamp_field",
    "max_age_seconds",
    "stale_severity",
    "summary_fields",
    "status_field",
    "complete_values",
    "authoritative",
}
PORT_KEYS = {"id", "host", "number", "required", "timeout_seconds"}
HEALTH_KEYS = {
    "type",
    "port_ref",
    "path",
    "expect_status",
    "timeout_seconds",
    "required",
    "host",
}
SECRET_POLICY_KEYS = {"argv", "environment", "files", "redact_diagnostics"}
SECRET_FILE_KEYS = {"path", "required", "max_permissions"}
HEALTH_POLICY_KEYS = {"mode", "signals"}
HEALTH_SIGNAL_KEYS = {"source", "index", "required"}
CHECKPOINT_KEYS = {"mode", "state_refs", "takeover_ready"}
REPLAY_KEYS = {"mode", "assurance", "evidence"}
REPLAY_EVIDENCE_KEYS = {"path", "selector"}
SECRET_REF_KEYS = {"name", "file_index"}
LIFECYCLE_AUTHORIZATION_KEYS = {
    "state",
    "authorized_operations",
    "change_request_required",
    "maintenance_window_required",
    "required_approvals",
}
RUNBOOK_KEYS = {"path", "section"}
EXTERNAL_DEPENDENCY_KEYS = {
    "name",
    "required",
    "verification",
    "via_health",
    "via_probe",
    "reason",
}
PROBE_KEYS = {
    "id",
    "type",
    "host",
    "port",
    "path",
    "timeout_seconds",
    "evidence_ttl_seconds",
    "bind_service",
}
PROBE_TYPES = {
    "postgres-tcp",
    "postgres-application-readiness",
    "cloudflare-tunnel-ready",
    "model-http-health",
}
PROBE_CANONICAL_PATHS = {
    "postgres-application-readiness": "/api/health/ready",
    "cloudflare-tunnel-ready": "/ready",
    "model-http-health": "/health",
}

HEALTH_SIGNAL_SOURCES = frozenset({"pid", "port", "health", "log", "state", "output"})
HEALTH_POLICY_MODES = frozenset(
    {"active-probe", "state-freshness", "log-freshness", "process-only", "composite"}
)
CHECKPOINT_MODES = frozenset(
    {"not-applicable", "durable", "progress-only", "not-evidenced"}
)
REPLAY_MODES = frozenset(
    {"not-applicable", "checkpoint-resume", "idempotent-rerun", "manual", "unknown"}
)
REPLAY_ASSURANCE = frozenset(
    {"not-applicable", "verified", "documented", "not-evidenced"}
)


def _fail(location: str, message: str) -> None:
    raise InventoryError(f"{location}: {message}")


def _object(
    value: Any, location: str, *, keys: set[str], required: set[str] = frozenset()
) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail(location, "must be an object")
    unknown = sorted(set(value) - keys)
    if unknown:
        _fail(location, f"unknown field {unknown[0]!r}")
    missing = sorted(required - set(value))
    if missing:
        _fail(location, f"missing required field {missing[0]!r}")
    return value


def _string(value: Any, location: str, *, pattern: str | None = None) -> str:
    if not isinstance(value, str) or not value:
        _fail(location, "must be a non-empty string")
    if pattern and re.fullmatch(pattern, value) is None:
        _fail(location, "has an invalid value")
    return value


def _bool(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        _fail(location, "must be a boolean")
    return value


def _positive_number(
    value: Any,
    location: str,
    *,
    integer: bool = False,
    maximum: float | None = None,
) -> float | int:
    expected = int if integer else (int, float)
    try:
        finite = (
            math.isfinite(value)
            if isinstance(value, expected) and not isinstance(value, bool)
            else False
        )
    except OverflowError:
        finite = False
    if isinstance(value, bool) or not isinstance(value, expected) or value <= 0 or not finite:
        _fail(location, "must be a positive number")
    if maximum is not None and value > maximum:
        _fail(location, f"must not exceed {maximum:g}")
    return value


def _string_list(value: Any, location: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        _fail(location, "must be a list" + (" with at least one item" if nonempty else ""))
    for index, item in enumerate(value):
        _string(item, f"{location}[{index}]")
    return value


def _is_within(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def ensure_trusted_path(value: str, roots: Sequence[Path], *, allow_glob: bool = False) -> Path:
    """Validate a configured or selected path against canonical trusted roots."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise InventoryError("filesystem path must be a non-empty string")
    if not Path(value).is_absolute():
        raise InventoryError(f"filesystem path must be absolute: {value!r}")
    if any(part == ".." for part in Path(value).parts):
        raise InventoryError(f"filesystem path may not contain '..': {value!r}")

    candidate = value
    if allow_glob:
        if "**" in value:
            raise InventoryError(f"recursive filesystem globs are forbidden: {value!r}")
        magic = [
            index
            for index in (candidate.find("*"), candidate.find("?"), candidate.find("["))
            if index >= 0
        ]
        if magic:
            prefix = candidate[: min(magic)]
            candidate = prefix.rsplit("/", 1)[0] or "/"
    resolved = Path(candidate).resolve(strict=False)
    canonical_roots = tuple(root.resolve() for root in roots)
    if not _is_within(resolved, canonical_roots):
        raise InventoryError(f"filesystem path escapes trusted roots: {value!r}")
    return Path(value)


def _expand(value: Any, variables: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _expand(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, variables) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for name, replacement in variables.items():
        result = result.replace("${" + name + "}", replacement)
    unresolved = re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", result)
    if unresolved:
        raise InventoryError(f"unresolved inventory variable: {unresolved[0]}")
    return result


def _validate_path(value: Any, location: str, roots: Sequence[Path], *, glob: bool = False) -> None:
    _string(value, location)
    try:
        ensure_trusted_path(value, roots, allow_glob=glob)
    except InventoryError as exc:
        _fail(location, str(exc))


def _validate_file_spec(value: Any, location: str, roots: Sequence[Path]) -> None:
    spec = _object(value, location, keys=FILE_KEYS)
    has_path = "path" in spec
    has_glob = "glob" in spec
    if has_path == has_glob:
        _fail(location, "must define exactly one of path or glob")
    _validate_path(
        spec.get("path") if has_path else spec.get("glob"),
        f"{location}.{'path' if has_path else 'glob'}",
        roots,
        glob=has_glob,
    )
    if "required" in spec:
        _bool(spec["required"], f"{location}.required")
    if "format" in spec and spec["format"] != "json":
        _fail(f"{location}.format", "only 'json' is supported")
    if "select" in spec and spec["select"] != "newest":
        _fail(f"{location}.select", "only 'newest' is supported")
    if "timestamp_field" in spec:
        _string(spec["timestamp_field"], f"{location}.timestamp_field")
    if "max_age_seconds" in spec:
        _positive_number(
            spec["max_age_seconds"], f"{location}.max_age_seconds", maximum=315_360_000
        )
    if "stale_severity" in spec and spec["stale_severity"] not in {"warning", "error", "critical"}:
        _fail(f"{location}.stale_severity", "must be warning, error, or critical")
    if "summary_fields" in spec:
        _string_list(spec["summary_fields"], f"{location}.summary_fields")
    if "status_field" in spec:
        _string(spec["status_field"], f"{location}.status_field")
    if "complete_values" in spec:
        values = _string_list(spec["complete_values"], f"{location}.complete_values", nonempty=True)
        unsafe = sorted({item.lower() for item in values} - SAFE_COMPLETE_VALUES)
        if unsafe:
            _fail(f"{location}.complete_values", f"unsafe terminal state {unsafe[0]!r}")
        if "status_field" not in spec:
            _fail(location, "complete_values requires status_field")
    if "authoritative" in spec:
        _bool(spec["authoritative"], f"{location}.authoritative")
    json_only_fields = {"timestamp_field", "summary_fields", "status_field", "complete_values"}
    if json_only_fields & set(spec) and spec.get("format") != "json":
        _fail(location, "timestamp, summary, and status fields require format 'json'")


def _validate_controller(value: Any, location: str, roots: Sequence[Path]) -> None:
    spec = _object(
        value,
        location,
        keys=CONTROLLER_KEYS,
        required={"type", "path", "interface", "adoption"},
    )
    if spec["type"] not in {"shell-script", "python-script", "shell-loop"}:
        _fail(f"{location}.type", "has an unsupported controller type")
    _validate_path(spec["path"], f"{location}.path", roots)
    if "entrypoint" in spec:
        _validate_path(spec["entrypoint"], f"{location}.entrypoint", roots)
    interface = _string(spec["interface"], f"{location}.interface")
    adoption = spec["adoption"]
    if adoption not in {"observe-only", "managed"}:
        _fail(f"{location}.adoption", "must be 'observe-only' or 'managed'")

    lifecycle = spec.get("lifecycle")
    if lifecycle is None:
        if adoption != "observe-only":
            _fail(
                f"{location}.adoption",
                "managed adoption requires enabled lifecycle; otherwise must remain observe-only",
            )
        return

    policy = _object(
        lifecycle,
        f"{location}.lifecycle",
        keys=LIFECYCLE_KEYS,
        required=LIFECYCLE_KEYS,
    )
    _bool(policy["enabled"], f"{location}.lifecycle.enabled")
    argv = _object(
        policy["argv"],
        f"{location}.lifecycle.argv",
        keys=set(LIFECYCLE_COMMANDS),
        required=set(LIFECYCLE_COMMANDS),
    )
    for operation in LIFECYCLE_COMMANDS:
        operation_argv = argv[operation]
        if not isinstance(operation_argv, list) or operation_argv != [spec["path"], operation]:
            _fail(
                f"{location}.lifecycle.argv.{operation}",
                "must be exactly [controller path, operation]",
            )
    for field in ("checkpoint", "rollback"):
        evidence = _object(
            policy[field],
            f"{location}.lifecycle.{field}",
            keys=LIFECYCLE_EVIDENCE_KEYS,
            required=LIFECYCLE_EVIDENCE_KEYS,
        )
        _validate_path(evidence["path"], f"{location}.lifecycle.{field}.path", roots)
        _string(
            evidence["sha256"],
            f"{location}.lifecycle.{field}.sha256",
            pattern=r"[0-9a-f]{64}",
        )
    artifacts = policy["controller_artifacts"]
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 32:
        _fail(
            f"{location}.lifecycle.controller_artifacts",
            "must contain between 1 and 32 attested files",
        )
    artifact_paths: set[str] = set()
    for index, item in enumerate(artifacts):
        artifact = _object(
            item,
            f"{location}.lifecycle.controller_artifacts[{index}]",
            keys=LIFECYCLE_EVIDENCE_KEYS,
            required=LIFECYCLE_EVIDENCE_KEYS,
        )
        _validate_path(
            artifact["path"],
            f"{location}.lifecycle.controller_artifacts[{index}].path",
            roots,
        )
        _string(
            artifact["sha256"],
            f"{location}.lifecycle.controller_artifacts[{index}].sha256",
            pattern=r"[0-9a-f]{64}",
        )
        if artifact["path"] in artifact_paths:
            _fail(
                f"{location}.lifecycle.controller_artifacts[{index}].path",
                "duplicates an attested controller artifact",
            )
        artifact_paths.add(artifact["path"])
    _validate_path(
        policy["audit_directory"],
        f"{location}.lifecycle.audit_directory",
        roots,
    )
    _positive_number(
        policy["timeout_seconds"],
        f"{location}.lifecycle.timeout_seconds",
        integer=True,
        maximum=300,
    )
    enabled = policy["enabled"] is True
    if enabled and adoption != "managed":
        _fail(f"{location}.adoption", "enabled lifecycle requires managed adoption")
    if not enabled and adoption != "observe-only":
        _fail(f"{location}.adoption", "disabled lifecycle must remain observe-only")
    if enabled:
        if spec["path"] not in artifact_paths:
            _fail(
                f"{location}.lifecycle.controller_artifacts",
                "must attest the dispatched controller path",
            )
        if spec["type"] != "shell-script":
            _fail(f"{location}.type", "enabled lifecycle requires a shell-script controller")
        declared = set(interface.split("|"))
        if declared - (LIFECYCLE_COMMANDS | {"logs", "follow"}):
            _fail(f"{location}.interface", "enabled lifecycle declares an unsafe operation")
        if not LIFECYCLE_COMMANDS <= declared:
            _fail(
                f"{location}.interface",
                "enabled lifecycle must declare status, start, stop, and restart",
            )


def _validate_pid(value: Any, location: str, roots: Sequence[Path]) -> None:
    spec = _object(
        value,
        location,
        keys=PID_KEYS,
        required={"kind", "cmdline_contains", "expected"},
    )
    if spec["kind"] not in {"single", "directory"}:
        _fail(f"{location}.kind", "must be single or directory")
    path_key = "path" if spec["kind"] == "single" else "glob"
    if path_key not in spec or ({"path", "glob"} - {path_key}) & set(spec):
        _fail(location, f"{spec['kind']} PID spec has invalid path/glob fields")
    _validate_path(spec[path_key], f"{location}.{path_key}", roots, glob=path_key == "glob")
    _string_list(spec["cmdline_contains"], f"{location}.cmdline_contains", nonempty=True)
    if spec["expected"] not in {"running", "running-or-complete"}:
        _fail(f"{location}.expected", "must be running or running-or-complete")
    if "meta_path" in spec:
        if spec["kind"] != "single":
            _fail(f"{location}.meta_path", "is only valid for single PID specs")
        _validate_path(spec["meta_path"], f"{location}.meta_path", roots)
        meta = _object(
            spec.get("meta"),
            f"{location}.meta",
            keys=PID_META_KEYS,
        )
        meta_format = meta.get("format", "tokens")
        if meta_format == "tokens":
            required = {"pid_index", "starttime_ticks_index"}
            missing = sorted(required - set(meta))
            if missing:
                _fail(f"{location}.meta", f"missing required field {missing[0]!r}")
            forbidden = {"schema_version", "pid_path", "starttime_ticks_path"} & set(meta)
            if forbidden:
                _fail(f"{location}.meta", f"token metadata does not allow {sorted(forbidden)[0]}")
            for name in ("pid_index", "starttime_ticks_index"):
                if (
                    isinstance(meta[name], bool)
                    or not isinstance(meta[name], int)
                    or meta[name] < 0
                ):
                    _fail(f"{location}.meta.{name}", "must be a non-negative integer")
            if meta["pid_index"] == meta["starttime_ticks_index"]:
                _fail(f"{location}.meta", "PID and start-ticks indices must differ")
        elif meta_format == "json":
            required = {"schema_version", "pid_path", "starttime_ticks_path"}
            missing = sorted(required - set(meta))
            if missing:
                _fail(f"{location}.meta", f"missing required field {missing[0]!r}")
            forbidden = {"pid_index", "starttime_ticks_index"} & set(meta)
            if forbidden:
                _fail(f"{location}.meta", f"JSON metadata does not allow {sorted(forbidden)[0]}")
            if meta["schema_version"] != 2:
                _fail(f"{location}.meta.schema_version", "must be 2")
            for name in ("pid_path", "starttime_ticks_path"):
                _string(
                    meta[name],
                    f"{location}.meta.{name}",
                    pattern=r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
                )
            if meta["pid_path"] == meta["starttime_ticks_path"]:
                _fail(f"{location}.meta", "PID and start-ticks paths must differ")
        else:
            _fail(f"{location}.meta.format", "must be tokens or json")
    elif "meta" in spec:
        _fail(f"{location}.meta", "requires meta_path")
    if "minimum_running" in spec:
        if spec["kind"] != "directory":
            _fail(f"{location}.minimum_running", "is only valid for directory PID specs")
        _positive_number(
            spec["minimum_running"],
            f"{location}.minimum_running",
            integer=True,
            maximum=10_000,
        )
    if "port_from_filename" in spec:
        if spec["kind"] != "directory":
            _fail(f"{location}.port_from_filename", "is only valid for directory PID specs")
        _bool(spec["port_from_filename"], f"{location}.port_from_filename")
    if "pidfile_start_tolerance_seconds" in spec:
        _positive_number(
            spec["pidfile_start_tolerance_seconds"],
            f"{location}.pidfile_start_tolerance_seconds",
            maximum=300,
        )


def _validate_port(value: Any, location: str, roots: Sequence[Path]) -> None:
    spec = _object(value, location, keys=PORT_KEYS, required={"id", "host", "number", "required"})
    _string(spec["id"], f"{location}.id", pattern=r"[a-z][a-z0-9_-]*")
    host = _string(spec["host"], f"{location}.host")
    try:
        if not ipaddress.ip_address(host).is_loopback:
            _fail(f"{location}.host", "must be a loopback IP literal")
    except ValueError:
        _fail(f"{location}.host", "must be a loopback IP literal")
    number = spec["number"]
    if isinstance(number, bool):
        _fail(f"{location}.number", "must be a port or dynamic port object")
    if isinstance(number, int):
        if not 1 <= number <= 65535:
            _fail(f"{location}.number", "must be between 1 and 65535")
    else:
        dynamic = _object(
            number,
            f"{location}.number",
            keys={"pid_meta", "fallback"},
            required={"pid_meta", "fallback"},
        )
        meta = _object(
            dynamic["pid_meta"],
            f"{location}.number.pid_meta",
            keys={"path", "token_index"},
            required={"path", "token_index"},
        )
        _validate_path(meta["path"], f"{location}.number.pid_meta.path", roots)
        if (
            isinstance(meta["token_index"], bool)
            or not isinstance(meta["token_index"], int)
            or meta["token_index"] < 0
        ):
            _fail(f"{location}.number.pid_meta.token_index", "must be a non-negative integer")
        if (
            isinstance(dynamic["fallback"], bool)
            or not isinstance(dynamic["fallback"], int)
            or not 1 <= dynamic["fallback"] <= 65535
        ):
            _fail(f"{location}.number.fallback", "must be between 1 and 65535")
    _bool(spec["required"], f"{location}.required")
    if "timeout_seconds" in spec:
        _positive_number(spec["timeout_seconds"], f"{location}.timeout_seconds", maximum=30)


def _validate_health(value: Any, location: str, roots: Sequence[Path]) -> None:
    spec = _object(value, location, keys=HEALTH_KEYS, required={"type", "required"})
    _bool(spec["required"], f"{location}.required")
    check_type = spec["type"]
    if check_type == "http":
        for field in ("port_ref", "path", "expect_status", "timeout_seconds"):
            if field not in spec:
                _fail(location, f"HTTP check requires {field}")
        _string(spec["port_ref"], f"{location}.port_ref")
        path = _string(spec["path"], f"{location}.path")
        if (
            not path.startswith("/")
            or path.startswith("//")
            or any(char in path for char in "\r\n#")
        ):
            _fail(f"{location}.path", "must be a local absolute HTTP path")
        statuses = spec["expect_status"]
        if not isinstance(statuses, list) or not statuses:
            _fail(f"{location}.expect_status", "must be a non-empty list")
        if any(
            isinstance(code, bool) or not isinstance(code, int) or not 100 <= code <= 599
            for code in statuses
        ):
            _fail(f"{location}.expect_status", "contains an invalid HTTP status")
        _positive_number(spec["timeout_seconds"], f"{location}.timeout_seconds", maximum=30)
        if "host" in spec:
            _fail(f"{location}.host", "HTTP host must be inherited from a declared loopback port")
    elif check_type == "tcp-members":
        host = _string(spec.get("host"), f"{location}.host")
        try:
            if not ipaddress.ip_address(host).is_loopback:
                _fail(f"{location}.host", "must be a loopback IP literal")
        except ValueError:
            _fail(f"{location}.host", "must be a loopback IP literal")
        _positive_number(spec.get("timeout_seconds"), f"{location}.timeout_seconds", maximum=30)
        forbidden = {"port_ref", "path", "expect_status"} & set(spec)
        if forbidden:
            _fail(location, f"tcp-members does not allow {sorted(forbidden)[0]}")
    elif check_type == "unix-control-status":
        for field in ("path", "expect_status", "timeout_seconds"):
            if field not in spec:
                _fail(location, f"Unix control status check requires {field}")
        _validate_path(spec["path"], f"{location}.path", roots)
        statuses = spec["expect_status"]
        if statuses != ["running"]:
            _fail(f"{location}.expect_status", "must be exactly ['running']")
        _positive_number(spec["timeout_seconds"], f"{location}.timeout_seconds", maximum=10)
        forbidden = {"port_ref", "host"} & set(spec)
        if forbidden:
            _fail(location, f"unix-control-status does not allow {sorted(forbidden)[0]}")
    else:
        _fail(f"{location}.type", "must be http, tcp-members, or unix-control-status")


def _validate_secret_policy(value: Any, location: str, roots: Sequence[Path]) -> None:
    spec = _object(
        value,
        location,
        keys=SECRET_POLICY_KEYS,
        required={"argv", "environment", "files", "redact_diagnostics"},
    )
    if spec["argv"] != "forbid-sensitive-values":
        _fail(f"{location}.argv", "must be 'forbid-sensitive-values'")
    if spec["environment"] not in {
        "config-path-reference-only",
        "credential-file-only",
        "process-environment-only",
        "secret-file-or-process-environment-only",
    }:
        _fail(f"{location}.environment", "has an unsupported secret transport policy")
    _bool(spec["redact_diagnostics"], f"{location}.redact_diagnostics")
    if spec["redact_diagnostics"] is not True:
        _fail(f"{location}.redact_diagnostics", "must be true")
    if not isinstance(spec["files"], list):
        _fail(f"{location}.files", "must be a list")
    for index, item in enumerate(spec["files"]):
        file_spec = _object(
            item,
            f"{location}.files[{index}]",
            keys=SECRET_FILE_KEYS,
            required={"path", "required", "max_permissions"},
        )
        _validate_path(file_spec["path"], f"{location}.files[{index}].path", roots)
        _bool(file_spec["required"], f"{location}.files[{index}].required")
        mode = _string(file_spec["max_permissions"], f"{location}.files[{index}].max_permissions")
        if re.fullmatch(r"0[0-7]{3}", mode) is None:
            _fail(
                f"{location}.files[{index}].max_permissions", "must be an octal mode such as 0600"
            )


def _index(value: Any, location: str, *, size: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(location, "must be a non-negative integer")
    if value >= size:
        _fail(location, "references an entry outside the declared collection")
    return value


def _validate_health_policy(service: Mapping[str, Any], location: str) -> None:
    policy = _object(
        service["health_policy"],
        location,
        keys=HEALTH_POLICY_KEYS,
        required=HEALTH_POLICY_KEYS,
    )
    mode = policy["mode"]
    if mode not in HEALTH_POLICY_MODES:
        _fail(f"{location}.mode", "has an unsupported health policy mode")
    signals = policy["signals"]
    if not isinstance(signals, list) or not signals:
        _fail(f"{location}.signals", "must contain at least one health signal")

    seen: set[tuple[str, int | None]] = set()
    sources: set[str] = set()
    required_health: set[int] = {
        index for index, item in enumerate(service["health"]) if item.get("required") is True
    }
    declared_required_health: set[int] = set()
    has_required = False
    for index, value in enumerate(signals):
        item_location = f"{location}.signals[{index}]"
        signal = _object(
            value,
            item_location,
            keys=HEALTH_SIGNAL_KEYS,
            required={"source", "required"},
        )
        source = signal["source"]
        if source not in HEALTH_SIGNAL_SOURCES:
            _fail(f"{item_location}.source", "has an unsupported health signal source")
        required = _bool(signal["required"], f"{item_location}.required")
        has_required = has_required or required
        sources.add(source)

        if source == "pid":
            if "index" in signal:
                _fail(f"{item_location}.index", "is not allowed for the singleton PID contract")
            reference = (source, None)
            if required is not True:
                _fail(f"{item_location}.required", "the PID identity signal must be required")
        else:
            if "index" not in signal:
                _fail(f"{item_location}.index", "is required for collection health signals")
            collection = service.get(source) or []
            signal_index = _index(
                signal["index"], f"{item_location}.index", size=len(collection)
            )
            reference = (source, signal_index)
            configured_required = collection[signal_index].get("required") is True
            if required is not configured_required:
                _fail(
                    f"{item_location}.required",
                    "must match the underlying observation requirement",
                )
            if source == "health" and required:
                declared_required_health.add(signal_index)
        if reference in seen:
            _fail(item_location, "duplicates a health signal reference")
        seen.add(reference)

    if not has_required:
        _fail(f"{location}.signals", "must include at least one required signal")
    if ("pid", None) not in seen:
        _fail(f"{location}.signals", "must explicitly include the PID identity contract")
    if required_health != declared_required_health:
        _fail(
            f"{location}.signals",
            "must include every required active health check as a required signal",
        )
    if mode == "active-probe" and "health" not in sources:
        _fail(f"{location}.mode", "active-probe requires a health signal")
    if mode == "state-freshness" and "state" not in sources:
        _fail(f"{location}.mode", "state-freshness requires a state signal")
    if mode == "log-freshness" and not ({"log", "output"} & sources):
        _fail(f"{location}.mode", "log-freshness requires a log or output signal")
    if mode == "process-only" and sources != {"pid"}:
        _fail(f"{location}.mode", "process-only may declare only the PID signal")
    if mode == "composite" and len(sources) < 2:
        _fail(f"{location}.mode", "composite requires at least two signal sources")


def _validate_checkpoint(service: Mapping[str, Any], location: str) -> None:
    checkpoint = _object(
        service["checkpoint"],
        location,
        keys=CHECKPOINT_KEYS,
        required=CHECKPOINT_KEYS,
    )
    mode = checkpoint["mode"]
    if mode not in CHECKPOINT_MODES:
        _fail(f"{location}.mode", "has an unsupported checkpoint mode")
    takeover_ready = _bool(checkpoint["takeover_ready"], f"{location}.takeover_ready")
    refs = checkpoint["state_refs"]
    if not isinstance(refs, list):
        _fail(f"{location}.state_refs", "must be a list")
    seen: set[int] = set()
    for index, value in enumerate(refs):
        state_index = _index(value, f"{location}.state_refs[{index}]", size=len(service["state"]))
        if state_index in seen:
            _fail(f"{location}.state_refs[{index}]", "duplicates a state reference")
        seen.add(state_index)

    is_pipeline = service["kind"] == "pipeline"
    if not is_pipeline and (mode != "not-applicable" or refs or takeover_ready):
        _fail(location, "non-pipeline entries must use a non-applicable checkpoint contract")
    if is_pipeline and mode == "not-applicable":
        _fail(f"{location}.mode", "pipelines must declare checkpoint evidence or its absence")
    if mode in {"not-applicable", "not-evidenced"} and refs:
        _fail(f"{location}.state_refs", f"{mode} cannot reference state evidence")
    if mode in {"durable", "progress-only"} and not refs:
        _fail(f"{location}.state_refs", f"{mode} requires state evidence")
    if mode == "durable" and any(
        service["state"][state_index].get("authoritative") is not True for state_index in refs
    ):
        _fail(f"{location}.state_refs", "durable checkpoints require authoritative state evidence")
    if takeover_ready and mode != "durable":
        _fail(f"{location}.takeover_ready", "requires a durable checkpoint")


def _validate_replay(service: Mapping[str, Any], location: str, roots: Sequence[Path]) -> None:
    replay = _object(
        service["replay"], location, keys=REPLAY_KEYS, required=REPLAY_KEYS
    )
    mode = replay["mode"]
    assurance = replay["assurance"]
    if mode not in REPLAY_MODES:
        _fail(f"{location}.mode", "has an unsupported replay mode")
    if assurance not in REPLAY_ASSURANCE:
        _fail(f"{location}.assurance", "has an unsupported replay assurance")
    evidence = replay["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 16:
        _fail(f"{location}.evidence", "must be a list with at most 16 entries")
    seen: set[tuple[str, str]] = set()
    for index, value in enumerate(evidence):
        item_location = f"{location}.evidence[{index}]"
        item = _object(
            value,
            item_location,
            keys=REPLAY_EVIDENCE_KEYS,
            required=REPLAY_EVIDENCE_KEYS,
        )
        _validate_path(item["path"], f"{item_location}.path", roots)
        selector = _string(
            item["selector"],
            f"{item_location}.selector",
            pattern=r"[A-Za-z_][A-Za-z0-9_]{0,127}",
        )
        identity = (item["path"], selector)
        if identity in seen:
            _fail(item_location, "duplicates replay evidence")
        seen.add(identity)

    is_pipeline = service["kind"] == "pipeline"
    if not is_pipeline and (mode != "not-applicable" or assurance != "not-applicable" or evidence):
        _fail(location, "non-pipeline entries must use a non-applicable replay contract")
    if is_pipeline and mode == "not-applicable":
        _fail(f"{location}.mode", "pipelines must declare replay evidence or its absence")
    if (mode == "not-applicable") != (assurance == "not-applicable"):
        _fail(location, "replay mode and assurance must agree on non-applicability")
    if assurance in {"verified", "documented"} and not evidence:
        _fail(f"{location}.evidence", f"{assurance} replay assurance requires evidence")
    if assurance in {"not-applicable", "not-evidenced"} and evidence:
        _fail(f"{location}.evidence", f"{assurance} replay assurance cannot cite evidence")


def _validate_secret_refs(service: Mapping[str, Any], location: str) -> None:
    refs = service["secret_refs"]
    if not isinstance(refs, list):
        _fail(location, "must be a list")
    names: set[str] = set()
    indexes: set[int] = set()
    files = service["secret_policy"]["files"]
    for index, value in enumerate(refs):
        item_location = f"{location}[{index}]"
        item = _object(
            value,
            item_location,
            keys=SECRET_REF_KEYS,
            required=SECRET_REF_KEYS,
        )
        name = _string(item["name"], f"{item_location}.name", pattern=r"[a-z][a-z0-9_-]*")
        file_index = _index(
            item["file_index"], f"{item_location}.file_index", size=len(files)
        )
        if name in names:
            _fail(f"{item_location}.name", "duplicates a secret reference name")
        if file_index in indexes:
            _fail(f"{item_location}.file_index", "duplicates a secret policy file reference")
        names.add(name)
        indexes.add(file_index)
    if indexes != set(range(len(files))):
        _fail(location, "must reference every declared secret policy file exactly once")


def _validate_lifecycle_authorization(service: Mapping[str, Any], location: str) -> None:
    authorization = _object(
        service["lifecycle_authorization"],
        location,
        keys=LIFECYCLE_AUTHORIZATION_KEYS,
        required=LIFECYCLE_AUTHORIZATION_KEYS,
    )
    state = authorization["state"]
    if state not in {"not-authorized", "authorized"}:
        _fail(f"{location}.state", "must be not-authorized or authorized")
    operations = _string_list(
        authorization["authorized_operations"], f"{location}.authorized_operations"
    )
    if len(operations) != len(set(operations)) or set(operations) - LIFECYCLE_COMMANDS:
        _fail(f"{location}.authorized_operations", "contains duplicate or unsafe operations")
    for field in ("change_request_required", "maintenance_window_required"):
        _bool(authorization[field], f"{location}.{field}")
        if authorization[field] is not True:
            _fail(f"{location}.{field}", "must remain true")
    approvals = _string_list(
        authorization["required_approvals"],
        f"{location}.required_approvals",
        nonempty=True,
    )
    if len(approvals) != 2 or set(approvals) != {
        "service-owner",
        "platform-operations",
    }:
        _fail(
            f"{location}.required_approvals",
            "must require exactly service-owner and platform-operations",
        )

    controller = service["controller"]
    lifecycle = controller.get("lifecycle") or {}
    if state == "not-authorized":
        if operations:
            _fail(f"{location}.authorized_operations", "must be empty when not authorized")
        if controller["adoption"] != "observe-only" or lifecycle.get("enabled") is True:
            _fail(location, "not-authorized requires observe-only controller adoption")
    else:
        if set(operations) != set(LIFECYCLE_COMMANDS):
            _fail(
                f"{location}.authorized_operations",
                "authorized lifecycle must explicitly grant all fixed lifecycle operations",
            )
        if controller["adoption"] != "managed" or lifecycle.get("enabled") is not True:
            _fail(location, "authorized lifecycle requires enabled managed adoption")


def _validate_runbook(value: Any, location: str, roots: Sequence[Path]) -> None:
    runbook = _object(value, location, keys=RUNBOOK_KEYS, required=RUNBOOK_KEYS)
    _validate_path(runbook["path"], f"{location}.path", roots)
    _string(runbook["section"], f"{location}.section", pattern=r"[a-z][a-z0-9_-]*")


def _validate_management_contract(
    service: Mapping[str, Any], location: str, roots: Sequence[Path]
) -> None:
    _validate_health_policy(service, f"{location}.health_policy")
    _validate_checkpoint(service, f"{location}.checkpoint")
    _validate_replay(service, f"{location}.replay", roots)
    _validate_secret_refs(service, f"{location}.secret_refs")
    _validate_lifecycle_authorization(service, f"{location}.lifecycle_authorization")
    _validate_runbook(service["runbook"], f"{location}.runbook", roots)
    if service["checkpoint"]["takeover_ready"] is True:
        if service["replay"]["assurance"] != "verified":
            _fail(
                f"{location}.checkpoint.takeover_ready",
                "requires verified replay assurance",
            )
        if service["lifecycle_authorization"]["state"] != "authorized":
            _fail(
                f"{location}.checkpoint.takeover_ready",
                "requires explicit lifecycle authorization",
            )


def _validate_probe_path(value: Any, location: str, expected: str) -> None:
    path = _string(value, location)
    if path != expected:
        _fail(location, f"must be the fixed path {expected!r}")
    if (
        not path.startswith("/")
        or path.startswith("//")
        or any(char in path for char in "\\\r\n?#")
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        _fail(location, "must be a fixed local absolute HTTP path")


def _validate_probes(value: Any, location: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        _fail(location, "must be a list")
    probes: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        probe = _object(
            item,
            item_location,
            keys=PROBE_KEYS,
            required={
                "id",
                "type",
                "host",
                "port",
                "timeout_seconds",
                "evidence_ttl_seconds",
            },
        )
        identifier = _string(probe["id"], f"{item_location}.id", pattern=r"[a-z][a-z0-9_-]*")
        if identifier in probes:
            _fail(f"{item_location}.id", f"duplicate probe id {identifier!r}")
        probe_type = probe["type"]
        if probe_type not in PROBE_TYPES:
            _fail(f"{item_location}.type", "has an unsupported fixed probe type")
        host = _string(probe["host"], f"{item_location}.host")
        try:
            if not ipaddress.ip_address(host).is_loopback:
                _fail(f"{item_location}.host", "must be a loopback IP literal")
        except ValueError:
            _fail(f"{item_location}.host", "must be a loopback IP literal")
        port = probe["port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            _fail(f"{item_location}.port", "must be a fixed port between 1 and 65535")
        _positive_number(
            probe["timeout_seconds"], f"{item_location}.timeout_seconds", maximum=5
        )
        _positive_number(
            probe["evidence_ttl_seconds"],
            f"{item_location}.evidence_ttl_seconds",
            maximum=300,
        )

        expected_path = PROBE_CANONICAL_PATHS.get(str(probe_type))
        if expected_path is None:
            forbidden = {"path", "bind_service"} & set(probe)
            if forbidden:
                _fail(item_location, f"postgres-tcp does not allow {sorted(forbidden)[0]}")
        else:
            for field in ("path", "bind_service"):
                if field not in probe:
                    _fail(item_location, f"{probe_type} requires {field}")
            _validate_probe_path(probe["path"], f"{item_location}.path", expected_path)
            _string(
                probe["bind_service"],
                f"{item_location}.bind_service",
                pattern=r"[a-z][a-z0-9_]*",
            )
        probes[identifier] = probe
    return probes


def _validate_external_dependencies(value: Any, location: str) -> None:
    if not isinstance(value, list):
        _fail(location, "must be a list")
    names: set[str] = set()
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        if isinstance(item, str):
            name = _string(item, item_location)
        else:
            spec = _object(
                item,
                item_location,
                keys=EXTERNAL_DEPENDENCY_KEYS,
                required={"name", "required", "verification"},
            )
            name = _string(spec["name"], f"{item_location}.name")
            _bool(spec["required"], f"{item_location}.required")
            if spec["verification"] not in {
                "external-monitor",
                "local-health",
                "manual",
                "probe",
                "unverified",
            }:
                _fail(f"{item_location}.verification", "has an unsupported verification mode")
            via_health = spec.get("via_health")
            if via_health is not None:
                _string(via_health, f"{item_location}.via_health")
            if spec["verification"] == "local-health" and via_health is None:
                _fail(
                    f"{item_location}.via_health",
                    "is required when verification is local-health",
                )
            via_probe = spec.get("via_probe")
            if via_probe is not None:
                _string(via_probe, f"{item_location}.via_probe", pattern=r"[a-z][a-z0-9_-]*")
            if spec["verification"] == "probe" and via_probe is None:
                _fail(f"{item_location}.via_probe", "is required when verification is probe")
            if spec["verification"] != "probe" and via_probe is not None:
                _fail(f"{item_location}.via_probe", "is only allowed when verification is probe")
            if spec["verification"] != "local-health" and via_health is not None:
                _fail(
                    f"{item_location}.via_health",
                    "is only allowed when verification is local-health",
                )
            reason = spec.get("reason")
            if reason is not None:
                _string(reason, f"{item_location}.reason")
                if spec["verification"] not in {"manual", "unverified"}:
                    _fail(
                        f"{item_location}.reason",
                        "is only allowed for manual or unverified dependencies",
                    )
        if name in names:
            _fail(item_location, f"duplicates external dependency {name!r}")
        names.add(name)


def validate_inventory(
    inventory: Mapping[str, Any], trusted_roots: Sequence[Path] | None = None
) -> None:
    roots = tuple(trusted_roots or (PROJECT_ROOT, DATA_ROOT))
    top = _object(
        inventory,
        "inventory",
        keys=TOP_LEVEL_KEYS,
        required={
            "schema_version",
            "inventory_version",
            "project",
            "variables",
            "control_policy",
            "services",
        },
    )
    if top["schema_version"] != SCHEMA_VERSION:
        _fail("inventory.schema_version", f"must be {SCHEMA_VERSION}")
    _string(top["inventory_version"], "inventory.inventory_version")
    _string(top["project"], "inventory.project")
    if "description" in top:
        _string(top["description"], "inventory.description")
    variables = _object(
        top["variables"],
        "inventory.variables",
        keys={"PROJECT_ROOT", "DATA_ROOT"},
        required={"PROJECT_ROOT", "DATA_ROOT"},
    )
    for name in ("PROJECT_ROOT", "DATA_ROOT"):
        _string(variables[name], f"inventory.variables.{name}")

    policy = _object(
        top["control_policy"],
        "inventory.control_policy",
        keys=CONTROL_POLICY_KEYS,
        required={"mode", "destructive_commands_enabled", "allowed_commands"},
    )
    if policy["mode"] != "read_only" or policy["destructive_commands_enabled"] is not False:
        _fail("inventory.control_policy", "must explicitly enforce read_only mode")
    allowed = _string_list(policy["allowed_commands"], "inventory.control_policy.allowed_commands")
    if set(allowed) != set(SAFE_COMMANDS) or len(allowed) != len(SAFE_COMMANDS):
        _fail(
            "inventory.control_policy.allowed_commands",
            "must be exactly catalog, doctor, list, status",
        )
    if "adoption_note" in policy:
        _string(policy["adoption_note"], "inventory.control_policy.adoption_note")

    probes = _validate_probes(top.get("probes", []), "inventory.probes")

    services = top["services"]
    if not isinstance(services, list) or not services:
        _fail("inventory.services", "must be a non-empty list")
    identifiers: set[str] = set()
    dependency_graph: dict[str, list[str]] = {}
    interval_limits = {"critical": 60, "high": 300, "medium": 900}
    for index, value in enumerate(services):
        location = f"inventory.services[{index}]"
        service = _object(
            value,
            location,
            keys=SERVICE_KEYS,
            required={
                "id",
                "name",
                "kind",
                "owner",
                "criticality",
                "check_interval_seconds",
                "dependencies",
                "external_dependencies",
                "controller",
                "pid",
                "port",
                "log",
                "health",
                "health_policy",
                "state",
                "secret_policy",
                "secret_refs",
                "checkpoint",
                "replay",
                "lifecycle_authorization",
                "runbook",
            },
        )
        identifier = _string(service["id"], f"{location}.id", pattern=r"[a-z][a-z0-9_]*")
        if identifier in identifiers:
            _fail(f"{location}.id", f"duplicate service id {identifier!r}")
        identifiers.add(identifier)
        _string(service["name"], f"{location}.name")
        if service["kind"] not in {"service", "service-pool", "pipeline"}:
            _fail(f"{location}.kind", "must be service, service-pool, or pipeline")
        _string(service["owner"], f"{location}.owner")
        criticality = service["criticality"]
        if criticality not in interval_limits:
            _fail(f"{location}.criticality", "must be critical, high, or medium")
        interval = _positive_number(
            service["check_interval_seconds"], f"{location}.check_interval_seconds", integer=True
        )
        if interval > interval_limits[criticality]:
            _fail(f"{location}.check_interval_seconds", "is too slow for service criticality")

        dependencies = service["dependencies"]
        if not isinstance(dependencies, list):
            _fail(f"{location}.dependencies", "must be a list")
        dependency_graph[identifier] = []
        seen_dependencies: set[str] = set()
        for dep_index, value in enumerate(dependencies):
            dep = _object(
                value,
                f"{location}.dependencies[{dep_index}]",
                keys={"service", "required"},
                required={"service", "required"},
            )
            dep_id = _string(
                dep["service"],
                f"{location}.dependencies[{dep_index}].service",
                pattern=r"[a-z][a-z0-9_]*",
            )
            _bool(dep["required"], f"{location}.dependencies[{dep_index}].required")
            if dep_id in seen_dependencies:
                _fail(f"{location}.dependencies[{dep_index}]", f"duplicates dependency {dep_id!r}")
            seen_dependencies.add(dep_id)
            dependency_graph[identifier].append(dep_id)
        _validate_external_dependencies(
            service["external_dependencies"], f"{location}.external_dependencies"
        )
        for dep_index, dependency in enumerate(service["external_dependencies"]):
            if not isinstance(dependency, Mapping) or dependency.get("verification") != "probe":
                continue
            probe_id = str(dependency["via_probe"])
            probe = probes.get(probe_id)
            dep_location = f"{location}.external_dependencies[{dep_index}]"
            if probe is None:
                _fail(f"{dep_location}.via_probe", f"references unknown probe {probe_id!r}")
            bound_service = probe.get("bind_service")
            if bound_service is not None and bound_service != identifier:
                _fail(
                    f"{dep_location}.via_probe",
                    "may only use an HTTP probe bound to the same service",
                )
            probe_type = probe["type"]
            dependency_name = str(dependency["name"])
            if probe_type.startswith("postgres-") and not dependency_name.startswith("postgres-"):
                _fail(f"{dep_location}.via_probe", "PostgreSQL probes require a postgres dependency")
            if probe_type == "cloudflare-tunnel-ready" and dependency_name != "cloudflare-edge":
                _fail(
                    f"{dep_location}.via_probe",
                    "Cloudflare readiness may only verify cloudflare-edge",
                )
        _validate_controller(service["controller"], f"{location}.controller", roots)
        _validate_pid(service["pid"], f"{location}.pid", roots)
        for field, validator in (("port", _validate_port),):
            if not isinstance(service[field], list):
                _fail(f"{location}.{field}", "must be a list")
            for item_index, item in enumerate(service[field]):
                validator(item, f"{location}.{field}[{item_index}]", roots)
        port_ids = [item["id"] for item in service["port"]]
        if len(port_ids) != len(set(port_ids)):
            _fail(f"{location}.port", "contains duplicate port ids")
        for field in ("log", "state", "output"):
            values = service.get(field, [])
            if not isinstance(values, list):
                _fail(f"{location}.{field}", "must be a list")
            for item_index, item in enumerate(values):
                _validate_file_spec(item, f"{location}.{field}[{item_index}]", roots)
        if service["pid"]["expected"] == "running-or-complete" and not any(
            item.get("complete_values") for item in service["state"]
        ):
            _fail(
                f"{location}.pid.expected",
                "running-or-complete requires a state entry with explicit successful complete_values",
            )
        if not isinstance(service["health"], list):
            _fail(f"{location}.health", "must be a list")
        for item_index, item in enumerate(service["health"]):
            _validate_health(item, f"{location}.health[{item_index}]", roots)
            if item["type"] == "http" and item["port_ref"] not in set(port_ids):
                _fail(
                    f"{location}.health[{item_index}].port_ref",
                    f"references unknown port {item['port_ref']!r}",
                )
            if item["type"] == "tcp-members" and not service["pid"].get(
                "port_from_filename", False
            ):
                _fail(
                    f"{location}.health[{item_index}]",
                    "tcp-members requires pid.port_from_filename=true",
                )
            if item["type"] == "unix-control-status":
                meta = service["pid"].get("meta") or {}
                if (
                    service["pid"].get("kind") != "single"
                    or not service["pid"].get("meta_path")
                    or meta.get("format") != "json"
                    or meta.get("pid_path") != "identity.pid"
                    or meta.get("starttime_ticks_path") != "identity.start_ticks"
                ):
                    _fail(
                        f"{location}.health[{item_index}]",
                        "unix-control-status requires schema-v2 JSON PID identity metadata",
                    )
        _validate_secret_policy(service["secret_policy"], f"{location}.secret_policy", roots)
        _validate_management_contract(service, location, roots)
        lifecycle = service["controller"].get("lifecycle")
        if lifecycle and lifecycle["enabled"] is True:
            pid = service["pid"]
            if pid["kind"] != "single" or not pid.get("meta_path") or not pid.get("meta"):
                _fail(
                    f"{location}.pid",
                    "enabled lifecycle requires a single PID with start-ticks metadata",
                )
            if not any(item.get("required") is True for item in service["health"]):
                _fail(
                    f"{location}.health",
                    "enabled lifecycle requires at least one required health check",
                )

    services_by_id = {service["id"]: service for service in services}
    for probe_id, probe in probes.items():
        bound_service = probe.get("bind_service")
        if bound_service is None:
            continue
        service = services_by_id.get(str(bound_service))
        if service is None:
            _fail(
                f"inventory.probes[{probe_id!r}].bind_service",
                f"references unknown service {bound_service!r}",
            )
        target_matches = False
        for port_spec in service["port"]:
            number = port_spec["number"]
            fixed_port = number if isinstance(number, int) else number["fallback"]
            if port_spec["host"] == probe["host"] and fixed_port == probe["port"]:
                target_matches = True
                break
        if not target_matches:
            _fail(
                f"inventory.probes[{probe_id!r}]",
                "target must match a declared loopback port of bind_service",
            )

    for identifier, dependencies in dependency_graph.items():
        for dep_id in dependencies:
            if dep_id not in identifiers:
                _fail(f"service {identifier!r}", f"references unknown dependency {dep_id!r}")
            if dep_id == identifier:
                _fail(f"service {identifier!r}", "may not depend on itself")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            _fail("inventory.services", f"dependency cycle includes {identifier!r}")
        if identifier in visited:
            return
        visiting.add(identifier)
        for dependency in dependency_graph[identifier]:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in dependency_graph:
        visit(identifier)


def load_inventory(
    path: Path = DEFAULT_MANIFEST,
    *,
    trusted_roots: Sequence[Path] | None = None,
) -> Inventory:
    if trusted_roots is None:
        roots = (PROJECT_ROOT.resolve(), DATA_ROOT.resolve())
    else:
        roots = tuple(Path(root).resolve() for root in trusted_roots)
        if not roots or len(roots) > 2:
            raise InventoryError("trusted_roots must explicitly contain one or two roots")
    try:
        manifest_path = ensure_trusted_path(str(Path(path).absolute()), roots)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(manifest_path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise InventoryError("inventory must be a non-symlink regular file")
            if metadata.st_uid != os.geteuid():
                raise InventoryError("inventory must be owned by the caller")
            if stat.S_IMODE(metadata.st_mode) & 0o022:
                raise InventoryError("inventory must not be group/world writable")
            content = handle.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise InventoryError("inventory exceeds the 5 MiB size limit")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON number {value!r}")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON field {key!r}")
                result[key] = value
            return result

        raw = json.loads(
            content.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except InventoryError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise InventoryError(f"cannot load inventory {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise InventoryError("inventory root must be an object")
    raw_variables = _object(
        raw.get("variables"),
        "inventory.variables",
        keys={"PROJECT_ROOT", "DATA_ROOT"},
        required={"PROJECT_ROOT", "DATA_ROOT"},
    )
    for name in ("PROJECT_ROOT", "DATA_ROOT"):
        _string(raw_variables[name], f"inventory.variables.{name}")

    trusted_project = roots[0]
    trusted_data = roots[1] if len(roots) > 1 else roots[0]
    variables = {"PROJECT_ROOT": str(trusted_project), "DATA_ROOT": str(trusted_data)}
    expanded = _expand(raw, variables)
    # Manifest-provided values can never redefine the built-in trust anchors.
    expanded["variables"] = variables
    validate_inventory(expanded, roots)
    return Inventory(
        expanded,
        roots,
        manifest_path=manifest_path,
        manifest_sha256=hashlib.sha256(content).hexdigest(),
    )


def service_dependency_closure(
    inventory: Mapping[str, Any], service_ids: Sequence[str]
) -> set[str]:
    by_id = {service["id"]: service for service in inventory["services"]}
    selected = set(service_ids)
    unknown = sorted(selected - set(by_id))
    if unknown:
        raise InventoryError(f"unknown service id: {unknown[0]}")
    if not selected:
        return set(by_id)

    pending = list(selected)
    while pending:
        identifier = pending.pop()
        for dependency in by_id[identifier]["dependencies"]:
            dep_id = dependency["service"]
            if dep_id not in selected:
                selected.add(dep_id)
                pending.append(dep_id)
    return selected


def service_dependency_order(inventory: Mapping[str, Any], service_ids: Sequence[str]) -> list[str]:
    """Return dependencies before dependents, independent of manifest ordering."""

    by_id = {service["id"]: service for service in inventory["services"]}
    selected = service_dependency_closure(inventory, service_ids)
    visiting: set[str] = set()
    visited: set[str] = set()
    result: list[str] = []

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise InventoryError(f"dependency cycle includes {identifier}")
        if identifier in visited:
            return
        visiting.add(identifier)
        dependencies = sorted(
            dependency["service"]
            for dependency in by_id[identifier]["dependencies"]
            if dependency["service"] in selected
        )
        for dependency in dependencies:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)
        result.append(identifier)

    for identifier in sorted(selected):
        visit(identifier)
    return result
