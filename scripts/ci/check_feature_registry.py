#!/usr/bin/env python3
"""Validate the GlobeMind V1 feature ownership and public-entry registry."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPOSITORY_ROOT / "ops" / "features" / "registry.json"
BOUNDARY_CHECKER_PATH = REPOSITORY_ROOT / "scripts" / "ci" / "check_import_boundaries.py"

ROOT_KEYS = {
    "schema_version",
    "registry_id",
    "scope",
    "owners",
    "managed_facades",
    "coverage_gaps",
    "features",
}
OWNER_KEYS = {"id", "name", "kind", "scope"}
MANAGED_FACADES_KEYS = {"backend", "frontend"}
FACADE_INVENTORY_KEYS = {"root", "entrypoint"}
FEATURE_KEYS = {
    "id",
    "title",
    "owner_id",
    "boundary_status",
    "public_entries",
    "routes",
    "pages",
    "dependencies",
    "contract_tests",
    "health_signal",
    "candidate_smoke",
    "rollback",
    "blockers",
}
ENTRY_KEYS = {"surface", "kind", "path"}
ROUTE_KEYS = {"namespace", "kind", "references"}
PAGE_KEYS = {"route", "component", "references"}
DEPENDENCY_KEYS = {"feature_id", "evidence"}
TEST_KEYS = {"runner", "path"}
EVIDENCE_KEYS = {"path", "locator"}
SIGNAL_KEYS = {"status", "description", "references", "blockers"}
ROLLBACK_KEYS = {"status", "strategy", "procedure", "references", "blockers"}
GAP_KEYS = {"id", "title", "owner_id", "status", "evidence", "blockers"}

FEATURE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
OWNER_ID_RE = FEATURE_ID_RE
BLOCKER_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
BACKEND_FACADE_RE = re.compile(
    r"^backend/api/features/[a-z][a-z0-9_]*/__init__\.py$"
)
FRONTEND_FACADE_RE = re.compile(
    r"^frontend/vue_project/src/features/[a-z][a-z0-9-]*/index\.js$"
)
BACKEND_ROUTE_RE = re.compile(r"^backend/api/routes/[a-z][a-z0-9_]*\.py$")
FRONTEND_PAGE_RE = re.compile(r"^frontend/vue_project/src/views/.+\.vue$")
FRONTEND_ROUTER_RE = re.compile(r"^frontend/vue_project/src/router/.+\.js$")
PYTEST_PATH_RE = re.compile(r"^backend/tests/test_[a-z0-9_]+\.py$")
NODE_TEST_PATH_RE = re.compile(
    r"^frontend/vue_project/tests/[a-z0-9-]+-feature\.test\.mjs$"
)

EXPECTED_MANAGED_FACADES = {
    "backend": ("backend/api/features", "__init__.py"),
    "frontend": ("frontend/vue_project/src/features", "index.js"),
}
BACKEND_ROUTE_ROOT = "backend/api/routes"
BOUNDARY_STATUSES = {"verified", "partial", "blocked"}
RECORD_STATUSES = {"verified", "pending", "blocked"}
ENTRY_KINDS = {"backend_facade", "frontend_facade"}
SURFACES = set(EXPECTED_MANAGED_FACADES)
TEST_RUNNERS = {"pytest", "node_test"}
ROUTE_KINDS = {"exact", "prefix"}
ROLLBACK_STRATEGIES = {"whole_release", "feature_flag", "data_restore", "not_defined"}
OWNER_KINDS = {"accountability_role"}

_BOUNDARY_MODULE: ModuleType | None = None


class FeatureRegistryError(RuntimeError):
    """Raised when the feature registry violates its offline contract."""


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FeatureRegistryError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> object:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except FeatureRegistryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FeatureRegistryError(f"cannot read feature registry {path}: {exc}") from exc


def _require_object(value: object, location: str) -> dict:
    if not isinstance(value, dict):
        raise FeatureRegistryError(f"{location} must be an object")
    return value


def _require_exact_keys(value: dict, expected: set[str], location: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise FeatureRegistryError(
            f"{location} schema mismatch: missing={missing}, unknown={unknown}"
        )


def _require_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise FeatureRegistryError(f"{location} must be a trimmed non-empty string")
    return value


def _require_string_array(
    value: object,
    location: str,
    *,
    allow_empty: bool,
    pattern: re.Pattern[str] | None = None,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        suffix = "" if allow_empty else " non-empty"
        raise FeatureRegistryError(f"{location} must be a{suffix} string array")
    result: list[str] = []
    for index, raw_item in enumerate(value):
        item = _require_string(raw_item, f"{location}[{index}]")
        if pattern is not None and pattern.fullmatch(item) is None:
            raise FeatureRegistryError(f"{location}[{index}] has an invalid identifier: {item!r}")
        result.append(item)
    duplicates = sorted(name for name, count in Counter(result).items() if count > 1)
    if duplicates:
        raise FeatureRegistryError(f"{location} contains duplicates: {duplicates}")
    return result


def _safe_repo_path(
    root: Path,
    raw_path: object,
    location: str,
    *,
    require_file: bool = False,
    require_dir: bool = False,
) -> tuple[str, Path]:
    path = _require_string(raw_path, location)
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or "\\" in path:
        raise FeatureRegistryError(f"{location} must be a repository-relative POSIX path")
    root = root.resolve()
    resolved = (root / pure).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise FeatureRegistryError(f"{location} escapes the repository") from exc
    if require_file and not resolved.is_file():
        raise FeatureRegistryError(f"{location} does not name an existing file: {path}")
    if require_dir and not resolved.is_dir():
        raise FeatureRegistryError(f"{location} does not name an existing directory: {path}")
    return path, resolved


def _repo_file(root: Path, raw_path: object, location: str) -> tuple[str, Path]:
    return _safe_repo_path(root, raw_path, location, require_file=True)


def _validate_evidence(value: object, location: str, *, root: Path) -> dict[str, str]:
    evidence = _require_object(value, location)
    _require_exact_keys(evidence, EVIDENCE_KEYS, location)
    path, resolved = _repo_file(root, evidence["path"], f"{location}.path")
    locator = _require_string(evidence["locator"], f"{location}.locator")
    if "\n" in locator or "\r" in locator or len(locator) > 240:
        raise FeatureRegistryError(f"{location}.locator must be a single line of at most 240 chars")
    try:
        content = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise FeatureRegistryError(f"cannot inspect evidence file {path}: {exc}") from exc
    if locator not in content:
        raise FeatureRegistryError(
            f"{location}.locator was not found in {path}: {locator!r}"
        )
    return {"path": path, "locator": locator}


def _validate_references(value: object, location: str, *, root: Path) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise FeatureRegistryError(f"{location} must be an array")
    references = [
        _validate_evidence(item, f"{location}[{index}]", root=root)
        for index, item in enumerate(value)
    ]
    fingerprints = [(item["path"], item["locator"]) for item in references]
    duplicates = sorted(name for name, count in Counter(fingerprints).items() if count > 1)
    if duplicates:
        raise FeatureRegistryError(f"{location} contains duplicate references: {duplicates}")
    return references


def _validate_status_record(value: object, location: str, *, root: Path) -> str:
    record = _require_object(value, location)
    _require_exact_keys(record, SIGNAL_KEYS, location)
    status = _require_string(record["status"], f"{location}.status")
    if status not in RECORD_STATUSES:
        raise FeatureRegistryError(
            f"{location}.status must be one of {sorted(RECORD_STATUSES)}"
        )
    _require_string(record["description"], f"{location}.description")
    references = _validate_references(record["references"], f"{location}.references", root=root)
    blockers = _require_string_array(
        record["blockers"],
        f"{location}.blockers",
        allow_empty=True,
        pattern=BLOCKER_ID_RE,
    )
    if status == "verified":
        if not references:
            raise FeatureRegistryError(f"{location} verified records require evidence")
        if blockers:
            raise FeatureRegistryError(f"{location} verified records cannot have blockers")
    elif not blockers:
        raise FeatureRegistryError(f"{location} {status} records require blockers")
    return status


def _validate_entry(value: object, location: str, *, root: Path) -> tuple[str, str, str]:
    entry = _require_object(value, location)
    _require_exact_keys(entry, ENTRY_KEYS, location)
    surface = _require_string(entry["surface"], f"{location}.surface")
    kind = _require_string(entry["kind"], f"{location}.kind")
    if surface not in SURFACES:
        raise FeatureRegistryError(f"{location}.surface must be one of {sorted(SURFACES)}")
    if kind not in ENTRY_KINDS:
        raise FeatureRegistryError(f"{location}.kind must be one of {sorted(ENTRY_KINDS)}")
    path, resolved = _repo_file(root, entry["path"], f"{location}.path")
    if kind == "backend_facade":
        if surface != "backend" or BACKEND_FACADE_RE.fullmatch(path) is None:
            raise FeatureRegistryError(
                f"{location} backend public entries must use a feature package __init__.py facade"
            )
        marker = "__all__"
    else:
        if surface != "frontend" or FRONTEND_FACADE_RE.fullmatch(path) is None:
            raise FeatureRegistryError(
                f"{location} frontend public entries must use a feature index.js facade"
            )
        marker = "export "
    if marker not in resolved.read_text(encoding="utf-8"):
        raise FeatureRegistryError(f"{location}.path does not expose the expected facade marker")
    return surface, kind, path


def _validate_url_path(value: object, location: str) -> str:
    path = _require_string(value, location)
    if (
        not path.startswith("/")
        or (path != "/" and path.endswith("/"))
        or "//" in path
        or any(character.isspace() for character in path)
        or any(character in path for character in ("?", "#", "\\"))
    ):
        raise FeatureRegistryError(f"{location} must be a canonical absolute URL path")
    return path


def _validate_route(
    value: object, location: str, *, root: Path
) -> tuple[str, str, set[str]]:
    route = _require_object(value, location)
    _require_exact_keys(route, ROUTE_KEYS, location)
    namespace = _validate_url_path(route["namespace"], f"{location}.namespace")
    kind = _require_string(route["kind"], f"{location}.kind")
    if kind not in ROUTE_KINDS:
        raise FeatureRegistryError(f"{location}.kind must be one of {sorted(ROUTE_KINDS)}")
    references = _validate_references(route["references"], f"{location}.references", root=root)
    if not references:
        raise FeatureRegistryError(f"{location}.references must not be empty")
    invalid = sorted({item["path"] for item in references if BACKEND_ROUTE_RE.fullmatch(item["path"]) is None})
    if invalid:
        raise FeatureRegistryError(
            f"{location}.references must point to backend route modules: {invalid}"
        )
    return namespace, kind, {item["path"] for item in references}


def _validate_page(value: object, location: str, *, root: Path) -> tuple[str, str]:
    page = _require_object(value, location)
    _require_exact_keys(page, PAGE_KEYS, location)
    route = _validate_url_path(page["route"], f"{location}.route")
    component, _ = _repo_file(root, page["component"], f"{location}.component")
    if FRONTEND_PAGE_RE.fullmatch(component) is None:
        raise FeatureRegistryError(f"{location}.component must be a route-level Vue page")
    references = _validate_references(page["references"], f"{location}.references", root=root)
    if not references:
        raise FeatureRegistryError(f"{location}.references must not be empty")
    invalid = sorted({
        item["path"] for item in references if FRONTEND_ROUTER_RE.fullmatch(item["path"]) is None
    })
    if invalid:
        raise FeatureRegistryError(
            f"{location}.references must point to frontend router modules: {invalid}"
        )
    return route, component


def _validate_contract_test(value: object, location: str, *, root: Path) -> tuple[str, str]:
    contract = _require_object(value, location)
    _require_exact_keys(contract, TEST_KEYS, location)
    runner = _require_string(contract["runner"], f"{location}.runner")
    if runner not in TEST_RUNNERS:
        raise FeatureRegistryError(f"{location}.runner must be one of {sorted(TEST_RUNNERS)}")
    path, resolved = _repo_file(root, contract["path"], f"{location}.path")
    expected_path = PYTEST_PATH_RE if runner == "pytest" else NODE_TEST_PATH_RE
    if expected_path.fullmatch(path) is None:
        raise FeatureRegistryError(
            f"{location}.path is not an approved {runner} contract test entry: {path}"
        )
    content = resolved.read_text(encoding="utf-8")
    marker = "def test_" if runner == "pytest" else "test("
    if marker not in content:
        raise FeatureRegistryError(f"{location}.path contains no recognizable tests")
    return runner, path


def _validate_rollback(value: object, location: str, *, root: Path) -> str:
    rollback = _require_object(value, location)
    _require_exact_keys(rollback, ROLLBACK_KEYS, location)
    status = _require_string(rollback["status"], f"{location}.status")
    if status not in RECORD_STATUSES:
        raise FeatureRegistryError(
            f"{location}.status must be one of {sorted(RECORD_STATUSES)}"
        )
    strategy = _require_string(rollback["strategy"], f"{location}.strategy")
    if strategy not in ROLLBACK_STRATEGIES:
        raise FeatureRegistryError(
            f"{location}.strategy must be one of {sorted(ROLLBACK_STRATEGIES)}"
        )
    _require_string(rollback["procedure"], f"{location}.procedure")
    references = _validate_references(rollback["references"], f"{location}.references", root=root)
    if not references:
        raise FeatureRegistryError(f"{location}.references must not be empty")
    blockers = _require_string_array(
        rollback["blockers"],
        f"{location}.blockers",
        allow_empty=True,
        pattern=BLOCKER_ID_RE,
    )
    if status == "verified":
        if blockers:
            raise FeatureRegistryError(f"{location} verified rollback cannot have blockers")
        if strategy == "not_defined":
            raise FeatureRegistryError(f"{location} verified rollback needs a concrete strategy")
    elif not blockers:
        raise FeatureRegistryError(f"{location} {status} rollback requires blockers")
    return status


def _validate_managed_facades(value: object, *, root: Path) -> dict[str, tuple[Path, str]]:
    managed = _require_object(value, "registry.managed_facades")
    _require_exact_keys(managed, MANAGED_FACADES_KEYS, "registry.managed_facades")
    result: dict[str, tuple[Path, str]] = {}
    for surface, expected in EXPECTED_MANAGED_FACADES.items():
        location = f"registry.managed_facades.{surface}"
        record = _require_object(managed[surface], location)
        _require_exact_keys(record, FACADE_INVENTORY_KEYS, location)
        root_path = _require_string(record["root"], f"{location}.root")
        entrypoint = _require_string(record["entrypoint"], f"{location}.entrypoint")
        if (root_path, entrypoint) != expected:
            raise FeatureRegistryError(
                f"{location} must remain fixed at root={expected[0]!r}, entrypoint={expected[1]!r}"
            )
        _, resolved = _safe_repo_path(root, root_path, f"{location}.root", require_dir=True)
        result[surface] = (resolved, entrypoint)
    return result


def _validate_facade_inventory(
    managed: dict[str, tuple[Path, str]],
    declared: dict[str, set[str]],
    *,
    root: Path,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    resolved_root = root.resolve()
    for surface, (facade_root, entrypoint) in managed.items():
        actual = {
            (child / entrypoint).resolve().relative_to(resolved_root).as_posix()
            for child in facade_root.iterdir()
            if child.is_dir() and (child / entrypoint).is_file()
        }
        undeclared = sorted(actual - declared[surface])
        stale = sorted(declared[surface] - actual)
        if undeclared or stale:
            raise FeatureRegistryError(
                f"facade manifest drift for {surface}: undeclared={undeclared}, stale={stale}"
            )
        counts[surface] = len(actual)
    return counts


def _validate_route_module_inventory(
    declared: set[str], *, root: Path
) -> int:
    _, route_root = _safe_repo_path(
        root,
        BACKEND_ROUTE_ROOT,
        "backend route inventory root",
        require_dir=True,
    )
    resolved_root = root.resolve()
    actual = {
        path.resolve().relative_to(resolved_root).as_posix()
        for path in route_root.glob("*.py")
        if path.name != "__init__.py"
    }
    undeclared = sorted(actual - declared)
    stale = sorted(declared - actual)
    if undeclared or stale:
        raise FeatureRegistryError(
            f"backend route module manifest drift: undeclared={undeclared}, stale={stale}"
        )
    return len(actual)


def _reject_overlapping_route_ownership(
    claims: list[tuple[str, str, str]],
) -> None:
    for index, (left_path, left_kind, left_owner) in enumerate(claims):
        for right_path, right_kind, right_owner in claims[index + 1 :]:
            if left_owner == right_owner:
                continue
            left_contains_right = (
                left_kind == "prefix" and right_path.startswith(left_path + "/")
            )
            right_contains_left = (
                right_kind == "prefix" and left_path.startswith(right_path + "/")
            )
            if left_contains_right or right_contains_left:
                raise FeatureRegistryError(
                    "overlapping route ownership: "
                    f"{left_owner}={left_path} ({left_kind}), "
                    f"{right_owner}={right_path} ({right_kind})"
                )


def _load_boundary_checker() -> ModuleType:
    global _BOUNDARY_MODULE
    if _BOUNDARY_MODULE is not None:
        return _BOUNDARY_MODULE
    module_name = "_globemind_feature_registry_import_boundaries"
    spec = importlib.util.spec_from_file_location(module_name, BOUNDARY_CHECKER_PATH)
    if spec is None or spec.loader is None:
        raise FeatureRegistryError(f"cannot load import boundary checker: {BOUNDARY_CHECKER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise FeatureRegistryError(f"cannot load import boundary checker: {exc}") from exc
    _BOUNDARY_MODULE = module
    return module


def feature_boundary_violations(root: Path) -> list[dict[str, object]]:
    """Return cross-feature imports that bypass a registered public facade."""
    checker = _load_boundary_checker()
    try:
        violations = checker.scan_repository(root)
    except Exception as exc:
        raise FeatureRegistryError(f"cannot scan feature import boundaries: {exc}") from exc
    governed_rules = {
        checker.RULE_BACKEND_FEATURE_PUBLIC_API,
        checker.RULE_FRONTEND_FEATURE_PUBLIC_API,
    }
    return [
        {
            "rule": item.rule,
            "path": item.path,
            "line": item.line,
            "detail": item.detail,
        }
        for item in violations
        if item.rule in governed_rules
    ]


def _reject_dependency_cycles(graph: dict[str, list[str]]) -> None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        status = state.get(node, 0)
        if status == 2:
            return
        if status == 1:
            start = stack.index(node)
            cycle = stack[start:] + [node]
            raise FeatureRegistryError(
                f"feature dependency graph contains a cycle: {' -> '.join(cycle)}"
            )
        state[node] = 1
        stack.append(node)
        for dependency in graph[node]:
            visit(dependency)
        stack.pop()
        state[node] = 2

    for feature_id in sorted(graph):
        visit(feature_id)


def validate_registry(
    payload: object,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    release_ready: bool = False,
) -> dict[str, object]:
    registry = _require_object(payload, "registry")
    _require_exact_keys(registry, ROOT_KEYS, "registry")
    if registry["schema_version"] != 2:
        raise FeatureRegistryError("registry.schema_version must be 2")
    _require_string(registry["registry_id"], "registry.registry_id")
    _require_string(registry["scope"], "registry.scope")

    raw_owners = registry["owners"]
    if not isinstance(raw_owners, list) or not raw_owners:
        raise FeatureRegistryError("registry.owners must be a non-empty array")
    owner_ids: list[str] = []
    for index, raw_owner in enumerate(raw_owners):
        location = f"registry.owners[{index}]"
        owner = _require_object(raw_owner, location)
        _require_exact_keys(owner, OWNER_KEYS, location)
        owner_id = _require_string(owner["id"], f"{location}.id")
        if OWNER_ID_RE.fullmatch(owner_id) is None:
            raise FeatureRegistryError(f"{location}.id is invalid: {owner_id!r}")
        _require_string(owner["name"], f"{location}.name")
        kind = _require_string(owner["kind"], f"{location}.kind")
        if kind not in OWNER_KINDS:
            raise FeatureRegistryError(f"{location}.kind must be one of {sorted(OWNER_KINDS)}")
        _require_string(owner["scope"], f"{location}.scope")
        owner_ids.append(owner_id)
    duplicate_owners = sorted(name for name, count in Counter(owner_ids).items() if count > 1)
    if duplicate_owners:
        raise FeatureRegistryError(f"registry contains duplicate owner ids: {duplicate_owners}")
    known_owners = set(owner_ids)
    used_owners: set[str] = set()

    managed = _validate_managed_facades(registry["managed_facades"], root=repository_root)
    features = registry["features"]
    if not isinstance(features, list) or not features:
        raise FeatureRegistryError("registry.features must be a non-empty array")

    feature_objects: list[dict] = []
    feature_ids: list[str] = []
    for index, raw_feature in enumerate(features):
        location = f"registry.features[{index}]"
        feature = _require_object(raw_feature, location)
        _require_exact_keys(feature, FEATURE_KEYS, location)
        feature_id = _require_string(feature["id"], f"{location}.id")
        if FEATURE_ID_RE.fullmatch(feature_id) is None:
            raise FeatureRegistryError(f"{location}.id is invalid: {feature_id!r}")
        _require_string(feature["title"], f"{location}.title")
        feature_ids.append(feature_id)
        feature_objects.append(feature)
    duplicate_features = sorted(name for name, count in Counter(feature_ids).items() if count > 1)
    if duplicate_features:
        raise FeatureRegistryError(f"registry contains duplicate feature ids: {duplicate_features}")
    known_ids = set(feature_ids)

    graph: dict[str, list[str]] = {}
    entry_owners: dict[str, str] = {}
    declared_facades: dict[str, set[str]] = {surface: set() for surface in SURFACES}
    route_owners: dict[str, str] = {}
    route_claims: list[tuple[str, str, str]] = []
    declared_route_modules: set[str] = set()
    page_owners: dict[str, str] = {}
    component_owners: dict[str, str] = {}
    boundary_counts: Counter[str] = Counter()
    record_counts: Counter[str] = Counter()
    dependency_edges = 0
    unresolved: list[str] = []

    for index, feature in enumerate(feature_objects):
        feature_id = feature_ids[index]
        location = f"registry.features[{index}]({feature_id})"
        owner_id = _require_string(feature["owner_id"], f"{location}.owner_id")
        if owner_id not in known_owners:
            raise FeatureRegistryError(f"{location}.owner_id references unknown owner: {owner_id}")
        used_owners.add(owner_id)

        boundary_status = _require_string(feature["boundary_status"], f"{location}.boundary_status")
        if boundary_status not in BOUNDARY_STATUSES:
            raise FeatureRegistryError(
                f"{location}.boundary_status must be one of {sorted(BOUNDARY_STATUSES)}"
            )
        blockers = _require_string_array(
            feature["blockers"], f"{location}.blockers", allow_empty=True, pattern=BLOCKER_ID_RE
        )
        if boundary_status == "verified" and blockers:
            raise FeatureRegistryError(f"{location} verified boundaries cannot have blockers")
        if boundary_status != "verified" and not blockers:
            raise FeatureRegistryError(f"{location} {boundary_status} boundaries require blockers")
        boundary_counts[boundary_status] += 1
        if boundary_status != "verified":
            unresolved.append(f"{feature_id}.boundary_status={boundary_status}")

        entries = feature["public_entries"]
        if not isinstance(entries, list) or not entries:
            raise FeatureRegistryError(f"{location}.public_entries must be a non-empty array")
        local_entries: list[str] = []
        for entry_index, entry in enumerate(entries):
            surface, _, path = _validate_entry(
                entry, f"{location}.public_entries[{entry_index}]", root=repository_root
            )
            local_entries.append(path)
            previous = entry_owners.get(path)
            if previous is not None:
                raise FeatureRegistryError(
                    f"public entry {path} is claimed by both {previous} and {feature_id}"
                )
            entry_owners[path] = feature_id
            declared_facades[surface].add(path)
        if len(local_entries) != len(set(local_entries)):
            raise FeatureRegistryError(f"{location}.public_entries contains duplicates")

        raw_routes = feature["routes"]
        if not isinstance(raw_routes, list):
            raise FeatureRegistryError(f"{location}.routes must be an array")
        for route_index, raw_route in enumerate(raw_routes):
            namespace, route_kind, route_modules = _validate_route(
                raw_route, f"{location}.routes[{route_index}]", root=repository_root
            )
            previous = route_owners.get(namespace)
            if previous is not None:
                raise FeatureRegistryError(
                    f"route namespace {namespace} is claimed by both {previous} and {feature_id}"
                )
            route_owners[namespace] = feature_id
            route_claims.append((namespace, route_kind, feature_id))
            declared_route_modules.update(route_modules)

        raw_pages = feature["pages"]
        if not isinstance(raw_pages, list):
            raise FeatureRegistryError(f"{location}.pages must be an array")
        for page_index, raw_page in enumerate(raw_pages):
            route, component = _validate_page(
                raw_page, f"{location}.pages[{page_index}]", root=repository_root
            )
            previous = page_owners.get(route)
            if previous is not None:
                raise FeatureRegistryError(
                    f"page route {route} is claimed by both {previous} and {feature_id}"
                )
            page_owners[route] = feature_id
            component_owner = component_owners.get(component)
            if component_owner is not None and component_owner != feature_id:
                raise FeatureRegistryError(
                    f"page component {component} is claimed by both {component_owner} and {feature_id}"
                )
            component_owners[component] = feature_id

        raw_dependencies = feature["dependencies"]
        if not isinstance(raw_dependencies, list):
            raise FeatureRegistryError(f"{location}.dependencies must be an array")
        dependencies: list[str] = []
        for dependency_index, raw_dependency in enumerate(raw_dependencies):
            dependency_location = f"{location}.dependencies[{dependency_index}]"
            dependency = _require_object(raw_dependency, dependency_location)
            _require_exact_keys(dependency, DEPENDENCY_KEYS, dependency_location)
            target = _require_string(dependency["feature_id"], f"{dependency_location}.feature_id")
            if target not in known_ids:
                raise FeatureRegistryError(
                    f"{dependency_location}.feature_id references unknown feature: {target}"
                )
            if target == feature_id:
                raise FeatureRegistryError(f"{dependency_location} cannot depend on itself")
            _validate_evidence(
                dependency["evidence"], f"{dependency_location}.evidence", root=repository_root
            )
            dependencies.append(target)
        duplicate_dependencies = sorted(
            name for name, count in Counter(dependencies).items() if count > 1
        )
        if duplicate_dependencies:
            raise FeatureRegistryError(
                f"{location}.dependencies contains duplicates: {duplicate_dependencies}"
            )
        graph[feature_id] = dependencies
        dependency_edges += len(dependencies)

        raw_tests = feature["contract_tests"]
        if not isinstance(raw_tests, list) or not raw_tests:
            raise FeatureRegistryError(f"{location}.contract_tests must be a non-empty array")
        tests = [
            _validate_contract_test(
                item, f"{location}.contract_tests[{test_index}]", root=repository_root
            )
            for test_index, item in enumerate(raw_tests)
        ]
        if len(tests) != len(set(tests)):
            raise FeatureRegistryError(f"{location}.contract_tests contains duplicates")

        health_status = _validate_status_record(
            feature["health_signal"], f"{location}.health_signal", root=repository_root
        )
        smoke_status = _validate_status_record(
            feature["candidate_smoke"], f"{location}.candidate_smoke", root=repository_root
        )
        rollback_status = _validate_rollback(
            feature["rollback"], f"{location}.rollback", root=repository_root
        )
        for record_name, status in (
            ("health_signal", health_status),
            ("candidate_smoke", smoke_status),
            ("rollback", rollback_status),
        ):
            record_counts[status] += 1
            if status != "verified":
                unresolved.append(f"{feature_id}.{record_name}={status}")

    coverage_gaps = registry["coverage_gaps"]
    if not isinstance(coverage_gaps, list):
        raise FeatureRegistryError("registry.coverage_gaps must be an array")
    gap_ids: list[str] = []
    gap_counts: Counter[str] = Counter()
    for index, raw_gap in enumerate(coverage_gaps):
        location = f"registry.coverage_gaps[{index}]"
        gap = _require_object(raw_gap, location)
        _require_exact_keys(gap, GAP_KEYS, location)
        gap_id = _require_string(gap["id"], f"{location}.id")
        if FEATURE_ID_RE.fullmatch(gap_id) is None:
            raise FeatureRegistryError(f"{location}.id is invalid: {gap_id!r}")
        if gap_id in known_ids:
            raise FeatureRegistryError(f"{location}.id is already a registered feature: {gap_id}")
        _require_string(gap["title"], f"{location}.title")
        owner_id = _require_string(gap["owner_id"], f"{location}.owner_id")
        if owner_id not in known_owners:
            raise FeatureRegistryError(f"{location}.owner_id references unknown owner: {owner_id}")
        used_owners.add(owner_id)
        status = _require_string(gap["status"], f"{location}.status")
        if status not in {"pending", "blocked"}:
            raise FeatureRegistryError(f"{location}.status must be pending or blocked")
        references = _validate_references(
            gap["evidence"], f"{location}.evidence", root=repository_root
        )
        if not references:
            raise FeatureRegistryError(f"{location}.evidence must not be empty")
        declared_route_modules.update(
            item["path"]
            for item in references
            if BACKEND_ROUTE_RE.fullmatch(item["path"]) is not None
        )
        _require_string_array(
            gap["blockers"], f"{location}.blockers", allow_empty=False, pattern=BLOCKER_ID_RE
        )
        gap_ids.append(gap_id)
        gap_counts[status] += 1
        unresolved.append(f"coverage-gap:{gap_id}={status}")
    duplicate_gaps = sorted(name for name, count in Counter(gap_ids).items() if count > 1)
    if duplicate_gaps:
        raise FeatureRegistryError(f"registry.coverage_gaps contains duplicate ids: {duplicate_gaps}")

    unused_owners = sorted(known_owners - used_owners)
    if unused_owners:
        raise FeatureRegistryError(f"registry contains unused owner ids: {unused_owners}")

    _reject_dependency_cycles(graph)
    _reject_overlapping_route_ownership(route_claims)
    facade_counts = _validate_facade_inventory(
        managed, declared_facades, root=repository_root
    )
    route_module_count = _validate_route_module_inventory(
        declared_route_modules, root=repository_root
    )
    boundary_violations = feature_boundary_violations(repository_root)
    if boundary_violations:
        examples = ", ".join(
            f"{item['path']}:{item['line']} ({item['detail']})"
            for item in boundary_violations[:8]
        )
        raise FeatureRegistryError(
            f"cross-feature deep imports bypass public entries: {examples}"
        )
    if release_ready and unresolved:
        raise FeatureRegistryError(
            "release-ready feature registry has unresolved records: " + ", ".join(unresolved)
        )

    return {
        "owners": len(owner_ids),
        "features": len(feature_ids),
        "feature_ids": sorted(feature_ids),
        "coverage_gaps": len(gap_ids),
        "coverage_gap_status": dict(sorted(gap_counts.items())),
        "public_entries": len(entry_owners),
        "facade_inventory": dict(sorted(facade_counts.items())),
        "routes": len(route_owners),
        "route_modules": route_module_count,
        "pages": len(page_owners),
        "dependency_edges": dependency_edges,
        "boundary_status": dict(sorted(boundary_counts.items())),
        "boundary_violations": 0,
        "record_status": dict(sorted(record_counts.items())),
        "release_ready": not unresolved,
        "unresolved_records": len(unresolved),
    }


def load_and_validate(
    path: Path = DEFAULT_REGISTRY,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    release_ready: bool = False,
) -> dict[str, object]:
    return validate_registry(
        load_json(path), repository_root=repository_root, release_ready=release_ready
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--release-ready",
        action="store_true",
        help="fail unless every feature readiness record is verified",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = load_and_validate(args.registry, release_ready=args.release_ready)
    except FeatureRegistryError as exc:
        if args.format == "json":
            print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
        else:
            print(f"feature registry error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps({"status": "passed", **summary}, indent=2, sort_keys=True))
    else:
        print(
            "feature registry: PASS; "
            f"features={summary['features']}; owners={summary['owners']}; "
            f"facades={summary['public_entries']}; routes={summary['routes']}; "
            f"pages={summary['pages']}; release_ready={str(summary['release_ready']).lower()}; "
            f"unresolved={summary['unresolved_records']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
