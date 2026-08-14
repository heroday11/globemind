#!/usr/bin/env python3
"""Audit claim-level citation coverage without reading generated output bodies.

The checked-in inventory names concrete HTTP surfaces, UI entry points, and
symbol-scoped AST probes.  A probe establishes only that a contract field or
gate is present in code; it does not establish source truth, semantic
entailment, runtime wiring, or human approval.  Known gaps remain findings so
EV-01 cannot be promoted by the existence of this checker.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = REPOSITORY_ROOT / "config" / "claim-output-inventory.json"

SCHEMA_VERSION = "globemind.claim-output-inventory.v1"
REQUIRED_CAPABILITIES = (
    "claim_id",
    "citation_locator",
    "reason_code",
    "unknown_gate",
)
_ALLOWED_SUFFIXES = frozenset({".py", ".vue", ".js", ".ts", ".tsx"})
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_ALLOWED_PROBE_KINDS = frozenset(
    {"python_class_fields", "python_class_literals", "python_function_literals"}
)
_MAX_CONFIG_BYTES = 1_048_576
_MAX_JSON_DEPTH = 24
_HARD_MAX_ENTRIES = 64
_HARD_MAX_LOCATORS = 256
_HARD_MAX_FILE_BYTES = 1_048_576
_HARD_MAX_TOTAL_BYTES = 16_777_216
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,95}$")


class ClaimCoverageError(RuntimeError):
    """The inventory or its bounded source scope cannot be trusted."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ClaimCoverageError(f"inventory contains duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_non_finite(value: str) -> None:
    raise ClaimCoverageError(f"inventory contains non-finite JSON number: {value}")


def _check_json_depth(value: object, depth: int = 0) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise ClaimCoverageError("inventory exceeds the JSON nesting depth limit")
    if isinstance(value, dict):
        for child in value.values():
            _check_json_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_json_depth(child, depth + 1)


def _path_has_symlink_component(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    probe = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        probe /= part
        if probe.is_symlink():
            return True
    return False


def _nonempty_string(value: object, field: str, maximum: int = 2_048) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClaimCoverageError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum or any(ord(char) < 32 for char in normalized):
        raise ClaimCoverageError(f"{field} is invalid or exceeds its length limit")
    return normalized


def _bounded_int(value: object, field: str, hard_limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ClaimCoverageError(f"{field} must be a positive integer")
    if value > hard_limit:
        raise ClaimCoverageError(f"{field} exceeds its hard safety limit")
    return value


def _relative_locator(value: object, field: str) -> str:
    locator = _nonempty_string(value, field).replace("\\", "/")
    path = PurePosixPath(locator)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ClaimCoverageError(
            f"{field} must be a normalized repository-relative locator"
        )
    if any(part in {"releases", "current", "previous", "rejected"} for part in path.parts):
        raise ClaimCoverageError(f"{field} crosses the production release boundary")
    return path.as_posix()


def _resolve_regular_file(root: Path, locator: str, field: str) -> Path:
    root_resolved = root.resolve()
    path = root_resolved / locator
    if _path_has_symlink_component(path):
        raise ClaimCoverageError(f"{field} must not contain a symlink")
    resolved = path.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise ClaimCoverageError(f"{field} escapes the repository")
    if not resolved.exists():
        raise ClaimCoverageError(f"{field} does not exist")
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ClaimCoverageError(f"{field} must be a regular file")
    if metadata.st_nlink != 1:
        raise ClaimCoverageError(f"{field} must not be hard-linked")
    return resolved


@dataclass(frozen=True, order=True)
class Finding:
    surface_id: str
    capability: str
    rule_code: str
    locator: str

    def public_payload(self) -> dict[str, str]:
        return {
            "surface_id": self.surface_id,
            "capability": self.capability,
            "rule_code": self.rule_code,
            "locator": self.locator,
        }


@dataclass(frozen=True)
class RouteProbe:
    method: str
    public_path: str
    locator: str
    function: str
    mount_prefix: str
    mount_locator: str | None
    mount_router: str | None


@dataclass(frozen=True)
class ContractProbe:
    kind: str
    locator: str
    symbol: str
    literals: tuple[str, ...]
    fields: tuple[str, ...]


@dataclass(frozen=True)
class Capability:
    name: str
    state: str
    reason_code: str | None
    probes: tuple[ContractProbe, ...]


@dataclass(frozen=True)
class InventoryEntry:
    id: str
    output_kind: str
    public_routes: tuple[RouteProbe, ...]
    public_pages: tuple[str, ...]
    capabilities: tuple[Capability, ...]


@dataclass(frozen=True)
class ClaimOutputInventory:
    automation_state: str
    allowed_roots: tuple[str, ...]
    allowed_suffixes: frozenset[str]
    max_entries: int
    max_locators: int
    max_file_bytes: int
    max_total_bytes: int
    entries: tuple[InventoryEntry, ...]


@dataclass(frozen=True)
class CoverageReport:
    automation_state: str
    coverage_state: str
    entry_total: int
    capability_total: int
    finding_total: int
    findings: tuple[Finding, ...]

    def public_payload(self) -> dict[str, object]:
        return {
            "schema_version": "globemind.claim-output-coverage-report.v1",
            "automation_state": self.automation_state,
            "coverage_state": self.coverage_state,
            "entry_total": self.entry_total,
            "capability_total": self.capability_total,
            "finding_total": self.finding_total,
            "findings": [item.public_payload() for item in self.findings],
            "assurance_boundaries": {
                "output_bodies_read": False,
                "runtime_routes_called": False,
                "source_truth_verified": False,
                "semantic_entailment_verified": False,
                "human_review_performed": False,
            },
        }


def _path_in_allowed_root(locator: str, roots: Sequence[str]) -> bool:
    path = PurePosixPath(locator)
    return any(path == PurePosixPath(root) or path.is_relative_to(root) for root in roots)


def _parse_contract_probe(raw: object, field: str) -> ContractProbe:
    if not isinstance(raw, dict):
        raise ClaimCoverageError(f"{field} must be an object")
    kind = _nonempty_string(raw.get("kind"), f"{field}.kind", 80)
    if kind not in _ALLOWED_PROBE_KINDS:
        raise ClaimCoverageError(f"{field}.kind is unsupported")
    locator = _relative_locator(raw.get("locator"), f"{field}.locator")
    symbol = _nonempty_string(raw.get("symbol"), f"{field}.symbol", 128)
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ClaimCoverageError(f"{field}.symbol is invalid")
    raw_literals = raw.get("literals", [])
    raw_fields = raw.get("fields", [])
    if not isinstance(raw_literals, list) or not isinstance(raw_fields, list):
        raise ClaimCoverageError(f"{field} literals and fields must be arrays")
    literals = tuple(
        _nonempty_string(value, f"{field}.literals[{index}]", 256)
        for index, value in enumerate(raw_literals)
    )
    fields = tuple(
        _nonempty_string(value, f"{field}.fields[{index}]", 128)
        for index, value in enumerate(raw_fields)
    )
    if kind == "python_class_fields" and (not fields or literals):
        raise ClaimCoverageError(f"{field} requires fields only")
    if kind != "python_class_fields" and (not literals or fields):
        raise ClaimCoverageError(f"{field} requires literals only")
    if len(set((*literals, *fields))) != len((*literals, *fields)):
        raise ClaimCoverageError(f"{field} contains duplicate expectations")
    return ContractProbe(kind, locator, symbol, literals, fields)


def validate_inventory(payload: object, repository_root: Path) -> ClaimOutputInventory:
    if not isinstance(payload, dict):
        raise ClaimCoverageError("inventory root must be an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ClaimCoverageError(f"schema_version must be {SCHEMA_VERSION}")

    automation = payload.get("automation")
    if not isinstance(automation, dict):
        raise ClaimCoverageError("automation must be an object")
    automation_state = automation.get("state")
    if automation_state not in {"configured", "not_configured"}:
        raise ClaimCoverageError("automation.state must be configured or not_configured")
    scheduler_locator = automation.get("scheduler_locator")
    retention_locator = automation.get("artifact_retention_locator")
    if automation_state == "configured":
        scheduler = _relative_locator(
            scheduler_locator,
            "automation.scheduler_locator",
        )
        retention = _relative_locator(
            retention_locator,
            "automation.artifact_retention_locator",
        )
        _resolve_regular_file(repository_root, scheduler, "automation.scheduler_locator")
        _resolve_regular_file(
            repository_root,
            retention,
            "automation.artifact_retention_locator",
        )
    elif scheduler_locator is not None or retention_locator is not None:
        raise ClaimCoverageError(
            "not_configured automation must not declare automation locators"
        )
    reason = _nonempty_string(automation.get("reason_code"), "automation.reason_code", 96)
    if not _REASON_RE.fullmatch(reason):
        raise ClaimCoverageError("automation.reason_code is invalid")

    scope = payload.get("scope")
    if not isinstance(scope, dict):
        raise ClaimCoverageError("scope must be an object")
    raw_roots = scope.get("allowed_roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        raise ClaimCoverageError("scope.allowed_roots must be a non-empty array")
    allowed_roots = tuple(
        _relative_locator(value, f"scope.allowed_roots[{index}]")
        for index, value in enumerate(raw_roots)
    )
    if len(set(allowed_roots)) != len(allowed_roots):
        raise ClaimCoverageError("scope.allowed_roots contains duplicates")
    root_resolved = repository_root.resolve()
    for index, locator in enumerate(allowed_roots):
        path = root_resolved / locator
        if _path_has_symlink_component(path):
            raise ClaimCoverageError(f"scope.allowed_roots[{index}] must not contain a symlink")
        resolved = path.resolve()
        if not resolved.is_relative_to(root_resolved) or not resolved.is_dir():
            raise ClaimCoverageError(f"scope.allowed_roots[{index}] must be an existing directory")

    raw_suffixes = scope.get("allowed_suffixes")
    if not isinstance(raw_suffixes, list) or not raw_suffixes:
        raise ClaimCoverageError("scope.allowed_suffixes must be a non-empty array")
    allowed_suffixes = frozenset(
        _nonempty_string(value, f"scope.allowed_suffixes[{index}]", 16)
        for index, value in enumerate(raw_suffixes)
    )
    if not allowed_suffixes <= _ALLOWED_SUFFIXES:
        raise ClaimCoverageError("scope.allowed_suffixes contains an unsupported suffix")
    max_entries = _bounded_int(scope.get("max_entries"), "scope.max_entries", _HARD_MAX_ENTRIES)
    max_locators = _bounded_int(scope.get("max_locators"), "scope.max_locators", _HARD_MAX_LOCATORS)
    max_file_bytes = _bounded_int(scope.get("max_file_bytes"), "scope.max_file_bytes", _HARD_MAX_FILE_BYTES)
    max_total_bytes = _bounded_int(scope.get("max_total_bytes"), "scope.max_total_bytes", _HARD_MAX_TOTAL_BYTES)

    required = payload.get("required_capabilities")
    if required != list(REQUIRED_CAPABILITIES):
        raise ClaimCoverageError("required_capabilities must contain the canonical ordered set")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ClaimCoverageError("entries must be a non-empty array")
    if len(raw_entries) > max_entries:
        raise ClaimCoverageError("inventory exceeds its entry limit")

    entries: list[InventoryEntry] = []
    entry_ids: set[str] = set()
    all_locators: list[str] = []
    for entry_index, raw_entry in enumerate(raw_entries):
        prefix = f"entries[{entry_index}]"
        if not isinstance(raw_entry, dict):
            raise ClaimCoverageError(f"{prefix} must be an object")
        entry_id = _nonempty_string(raw_entry.get("id"), f"{prefix}.id", 80)
        if not _ID_RE.fullmatch(entry_id) or entry_id in entry_ids:
            raise ClaimCoverageError(f"{prefix}.id is invalid or duplicated")
        entry_ids.add(entry_id)
        output_kind = _nonempty_string(raw_entry.get("output_kind"), f"{prefix}.output_kind", 80)

        raw_routes = raw_entry.get("public_routes")
        if not isinstance(raw_routes, list) or not raw_routes:
            raise ClaimCoverageError(f"{prefix}.public_routes must be a non-empty array")
        routes: list[RouteProbe] = []
        for route_index, raw_route in enumerate(raw_routes):
            field = f"{prefix}.public_routes[{route_index}]"
            if not isinstance(raw_route, dict):
                raise ClaimCoverageError(f"{field} must be an object")
            method = _nonempty_string(raw_route.get("method"), f"{field}.method", 10).upper()
            if method not in _ALLOWED_METHODS:
                raise ClaimCoverageError(f"{field}.method is unsupported")
            public_path = _nonempty_string(raw_route.get("public_path"), f"{field}.public_path", 300)
            if not public_path.startswith("/") or "//" in public_path or "?" in public_path:
                raise ClaimCoverageError(f"{field}.public_path is invalid")
            locator = _relative_locator(raw_route.get("locator"), f"{field}.locator")
            function = _nonempty_string(raw_route.get("function"), f"{field}.function", 128)
            if not _SYMBOL_RE.fullmatch(function):
                raise ClaimCoverageError(f"{field}.function is invalid")
            raw_mount_prefix = raw_route.get("mount_prefix")
            raw_mount_locator = raw_route.get("mount_locator")
            raw_mount_router = raw_route.get("mount_router")
            if raw_mount_prefix is None:
                if raw_mount_locator is not None or raw_mount_router is not None:
                    raise ClaimCoverageError(f"{field} mount metadata is incomplete")
                mount_prefix = ""
                mount_locator = None
                mount_router = None
            else:
                mount_prefix = _nonempty_string(raw_mount_prefix, f"{field}.mount_prefix", 120)
                if not mount_prefix.startswith("/") or mount_prefix.endswith("/"):
                    raise ClaimCoverageError(f"{field}.mount_prefix is invalid")
                mount_locator = _relative_locator(raw_mount_locator, f"{field}.mount_locator")
                mount_router = _nonempty_string(raw_mount_router, f"{field}.mount_router", 128)
                if not _SYMBOL_RE.fullmatch(mount_router):
                    raise ClaimCoverageError(f"{field}.mount_router is invalid")
                all_locators.append(mount_locator)
            routes.append(
                RouteProbe(
                    method,
                    public_path,
                    locator,
                    function,
                    mount_prefix,
                    mount_locator,
                    mount_router,
                )
            )
            all_locators.append(locator)

        raw_pages = raw_entry.get("public_pages")
        if not isinstance(raw_pages, list) or not raw_pages:
            raise ClaimCoverageError(f"{prefix}.public_pages must be a non-empty array")
        pages = tuple(
            _relative_locator(value, f"{prefix}.public_pages[{index}]")
            for index, value in enumerate(raw_pages)
        )
        all_locators.extend(pages)

        raw_capabilities = raw_entry.get("capabilities")
        if not isinstance(raw_capabilities, dict) or set(raw_capabilities) != set(REQUIRED_CAPABILITIES):
            raise ClaimCoverageError(f"{prefix}.capabilities must contain exactly the required capabilities")
        capabilities: list[Capability] = []
        for name in REQUIRED_CAPABILITIES:
            raw_capability = raw_capabilities[name]
            field = f"{prefix}.capabilities.{name}"
            if not isinstance(raw_capability, dict):
                raise ClaimCoverageError(f"{field} must be an object")
            state = raw_capability.get("state")
            if state not in {"present", "missing"}:
                raise ClaimCoverageError(f"{field}.state must be present or missing")
            if state == "missing":
                reason_code = _nonempty_string(raw_capability.get("reason_code"), f"{field}.reason_code", 96)
                if not _REASON_RE.fullmatch(reason_code) or raw_capability.get("probes") not in (None, []):
                    raise ClaimCoverageError(f"{field} missing state is invalid")
                probes: tuple[ContractProbe, ...] = ()
            else:
                if raw_capability.get("reason_code") is not None:
                    raise ClaimCoverageError(f"{field} present state cannot declare a reason")
                raw_probes = raw_capability.get("probes")
                if not isinstance(raw_probes, list) or not raw_probes:
                    raise ClaimCoverageError(f"{field}.probes must be a non-empty array")
                probes = tuple(
                    _parse_contract_probe(value, f"{field}.probes[{index}]")
                    for index, value in enumerate(raw_probes)
                )
                reason_code = None
                all_locators.extend(probe.locator for probe in probes)
            capabilities.append(Capability(name, state, reason_code, probes))
        entries.append(InventoryEntry(entry_id, output_kind, tuple(routes), pages, tuple(capabilities)))

    unique_locators = tuple(dict.fromkeys(all_locators))
    if len(unique_locators) > max_locators:
        raise ClaimCoverageError("inventory exceeds its locator limit")
    total_bytes = 0
    for locator in unique_locators:
        if not _path_in_allowed_root(locator, allowed_roots):
            raise ClaimCoverageError(f"source locator is outside configured roots: {locator}")
        if Path(locator).suffix not in allowed_suffixes:
            raise ClaimCoverageError(f"source locator suffix is not allowed: {locator}")
        path = _resolve_regular_file(repository_root, locator, f"source locator {locator}")
        size = path.stat().st_size
        if size > max_file_bytes:
            raise ClaimCoverageError(f"source locator exceeds the file byte limit: {locator}")
        total_bytes += size
        if total_bytes > max_total_bytes:
            raise ClaimCoverageError("source locators exceed the aggregate byte limit")

    return ClaimOutputInventory(
        automation_state=automation_state,
        allowed_roots=allowed_roots,
        allowed_suffixes=allowed_suffixes,
        max_entries=max_entries,
        max_locators=max_locators,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        entries=tuple(entries),
    )


def load_inventory(path: Path, repository_root: Path) -> ClaimOutputInventory:
    if not path.exists():
        raise ClaimCoverageError("inventory file is missing")
    if _path_has_symlink_component(path):
        raise ClaimCoverageError("inventory path must not contain a symlink")
    root_resolved = repository_root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(root_resolved):
        raise ClaimCoverageError("inventory path escapes the repository")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ClaimCoverageError("inventory must be a single-link regular file")
    if metadata.st_size > _MAX_CONFIG_BYTES:
        raise ClaimCoverageError("inventory exceeds the configuration byte limit")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except ClaimCoverageError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ClaimCoverageError("inventory is not strict bounded UTF-8 JSON") from exc
    _check_json_depth(payload)
    return validate_inventory(payload, repository_root)


def _symbol(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return node
    return None


def _router_prefix(tree: ast.Module) -> str:
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "router" for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        for keyword in node.value.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                return keyword.value.value
    return ""


def _route_matches(tree: ast.Module, route: RouteProbe) -> bool:
    node = _symbol(tree, route.function)
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    prefix = _router_prefix(tree).rstrip("/")
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        if decorator.func.attr.upper() != route.method or not decorator.args:
            continue
        value = decorator.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            declared = value.value if value.value.startswith("/") else f"/{value.value}"
            if f"{route.mount_prefix}{prefix}{declared}" == route.public_path:
                return True
    return False


def _mount_matches(tree: ast.Module, route: RouteProbe) -> bool:
    if not route.mount_prefix:
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "include_router" or not node.args:
            continue
        if not isinstance(node.args[0], ast.Name) or node.args[0].id != route.mount_router:
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "prefix"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == route.mount_prefix
            ):
                return True
    return False


def _probe_matches(tree: ast.Module, probe: ContractProbe) -> bool:
    node = _symbol(tree, probe.symbol)
    if node is None:
        return False
    if probe.kind.startswith("python_class") and not isinstance(node, ast.ClassDef):
        return False
    if probe.kind == "python_function_literals" and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if probe.kind == "python_class_fields":
        fields: set[str] = set()
        for child in node.body:  # type: ignore[union-attr]
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                fields.add(child.target.id)
            elif isinstance(child, ast.Assign):
                fields.update(target.id for target in child.targets if isinstance(target, ast.Name))
        return set(probe.fields) <= fields
    literals = {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }
    return set(probe.literals) <= literals


def audit_inventory(repository_root: Path, inventory: ClaimOutputInventory) -> CoverageReport:
    parsed: dict[str, ast.Module | None] = {}

    def tree(locator: str) -> ast.Module | None:
        if locator not in parsed:
            path = repository_root.resolve() / locator
            try:
                parsed[locator] = ast.parse(path.read_text(encoding="utf-8"), filename=locator)
            except (OSError, UnicodeError, SyntaxError, ValueError):
                parsed[locator] = None
        return parsed[locator]

    findings: list[Finding] = []
    for entry in inventory.entries:
        for route in entry.public_routes:
            parsed_tree = tree(route.locator)
            mount_tree = tree(route.mount_locator) if route.mount_locator else parsed_tree
            if (
                parsed_tree is None
                or not _route_matches(parsed_tree, route)
                or mount_tree is None
                or not _mount_matches(mount_tree, route)
            ):
                findings.append(
                    Finding(entry.id, "surface", "COV_ROUTE_PROBE_FAILED", f"{route.locator}#{route.function}")
                )
        primary_locator = f"{entry.public_routes[0].locator}#{entry.public_routes[0].function}"
        for capability in entry.capabilities:
            if capability.state == "missing":
                findings.append(
                    Finding(
                        entry.id,
                        capability.name,
                        f"COV_{capability.name.upper()}_MISSING",
                        primary_locator,
                    )
                )
                continue
            for probe in capability.probes:
                parsed_tree = tree(probe.locator)
                if parsed_tree is None or not _probe_matches(parsed_tree, probe):
                    findings.append(
                        Finding(
                            entry.id,
                            capability.name,
                            f"COV_{capability.name.upper()}_PROBE_FAILED",
                            f"{probe.locator}#{probe.symbol}",
                        )
                    )
    ordered = tuple(sorted(set(findings)))
    return CoverageReport(
        automation_state=inventory.automation_state,
        coverage_state="partial" if ordered else "inventory_probes_passed_not_fact_verified",
        entry_total=len(inventory.entries),
        capability_total=len(inventory.entries) * len(REQUIRED_CAPABILITIES),
        finding_total=len(ordered),
        findings=ordered,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--json", action="store_true", help="emit metadata-only JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inventory = load_inventory(args.inventory, args.repository_root)
        report = audit_inventory(args.repository_root, inventory)
    except ClaimCoverageError as exc:
        code = "COV_CONFIG_MISSING" if "missing" in str(exc) else "COV_CONFIG_INVALID"
        print(f"claim-output-coverage:config:{code}")
        return 2
    if args.json:
        print(json.dumps(report.public_payload(), ensure_ascii=False, sort_keys=True))
    else:
        print(
            "claim-output-coverage:"
            f"coverage_state={report.coverage_state}:"
            f"entries={report.entry_total}:findings={report.finding_total}:"
            f"automation_state={report.automation_state}"
        )
        for finding in report.findings:
            print(
                f"{finding.surface_id}:{finding.capability}:"
                f"{finding.rule_code}:{finding.locator}"
            )
    return 1 if report.findings else 0


if __name__ == "__main__":
    sys.exit(main())
