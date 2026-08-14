from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "ops" / "features" / "registry.json"
CHECKER_PATH = PROJECT_ROOT / "scripts" / "ci" / "check_feature_registry.py"

SPEC = importlib.util.spec_from_file_location("check_feature_registry", CHECKER_PATH)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def _payload() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _feature(payload: dict, feature_id: str) -> dict:
    return next(item for item in payload["features"] if item["id"] == feature_id)


def _validate(payload: dict, *, release_ready: bool = False) -> dict[str, object]:
    return checker.validate_registry(
        payload,
        repository_root=PROJECT_ROOT,
        release_ready=release_ready,
    )


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_checked_in_feature_registry_is_valid_and_inventory_complete() -> None:
    summary = checker.load_and_validate(REGISTRY_PATH, repository_root=PROJECT_ROOT)

    assert summary == {
        "owners": 10,
        "features": 18,
        "feature_ids": [
            "assistant",
            "authoritative-data",
            "dashboard",
            "data-governance",
            "entity-governance",
            "evidence-chain",
            "financial-alerts",
            "graph-briefing",
            "ground-news",
            "identity",
            "legacy-endpoint-retirement",
            "model-assurance",
            "operations",
            "opinion-analysis",
            "research-workflow",
            "search",
            "service-level",
            "story-graph",
        ],
        "coverage_gaps": 0,
        "coverage_gap_status": {},
        "public_entries": 31,
        "facade_inventory": {"backend": 18, "frontend": 13},
        "routes": 37,
        "route_modules": 20,
        "pages": 22,
        "dependency_edges": 19,
        "boundary_status": {"verified": 18},
        "boundary_violations": 0,
        "record_status": {"verified": 54},
        "release_ready": True,
        "unresolved_records": 0,
    }


def test_default_cli_reports_machine_readable_inventory() -> None:
    result = subprocess.run(
        [sys.executable, "-B", str(CHECKER_PATH), "--format", "json"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["features"] == 18
    assert payload["owners"] == 10
    assert payload["public_entries"] == 31
    assert payload["facade_inventory"] == {"backend": 18, "frontend": 13}
    assert payload["route_modules"] == 20
    assert payload["boundary_violations"] == 0
    assert payload["release_ready"] is True


def test_release_ready_mode_passes_without_unresolved_records() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(CHECKER_PATH),
            "--release-ready",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["release_ready"] is True
    assert payload["unresolved_records"] == 0


def test_checker_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    raw = REGISTRY_PATH.read_text(encoding="utf-8").replace(
        '"schema_version": 2,',
        '"schema_version": 2,\n  "schema_version": 2,',
        1,
    )
    duplicate = tmp_path / "registry.json"
    duplicate.write_text(raw, encoding="utf-8")

    with pytest.raises(checker.FeatureRegistryError, match="duplicate JSON key"):
        checker.load_json(duplicate)


def test_checker_rejects_unknown_schema_fields() -> None:
    payload = _payload()
    payload["features"][0]["invented_state"] = True

    with pytest.raises(checker.FeatureRegistryError, match="schema mismatch"):
        _validate(payload)


def test_checker_rejects_coverage_gap_without_valid_evidence() -> None:
    payload = _payload()
    payload["coverage_gaps"] = [
        {
            "id": "unmigrated",
            "title": "Unmigrated Feature",
            "owner_id": "identity-security",
            "status": "pending",
            "evidence": [
                {
                    "path": "backend/api/routes/not-present.py",
                    "locator": "missing",
                }
            ],
            "blockers": ["feature_facade_not_available"],
        }
    ]

    with pytest.raises(checker.FeatureRegistryError, match="does not name an existing file"):
        _validate(payload)


def test_checker_rejects_duplicate_feature_ids() -> None:
    payload = _payload()
    payload["features"][1]["id"] = payload["features"][0]["id"]

    with pytest.raises(checker.FeatureRegistryError, match="duplicate feature ids"):
        _validate(payload)


def test_checker_rejects_missing_public_entry_file() -> None:
    payload = _payload()
    _feature(payload, "search")["public_entries"][0]["path"] = (
        "backend/api/features/search/not-present.py"
    )

    with pytest.raises(checker.FeatureRegistryError, match="does not name an existing file"):
        _validate(payload)


def test_checker_rejects_facade_manifest_drift() -> None:
    payload = _payload()
    assistant = _feature(payload, "assistant")
    assistant["public_entries"] = [assistant["public_entries"][0]]

    with pytest.raises(checker.FeatureRegistryError, match="facade manifest drift.*undeclared"):
        _validate(payload)


def test_checker_rejects_deep_internal_module_as_public_entry() -> None:
    payload = _payload()
    _feature(payload, "search")["public_entries"][0]["path"] = (
        "backend/api/features/search/application.py"
    )

    with pytest.raises(checker.FeatureRegistryError, match="__init__.*facade"):
        _validate(payload)


def test_checker_rejects_non_contract_file_as_test_entry() -> None:
    payload = _payload()
    _feature(payload, "search")["contract_tests"][0]["path"] = (
        "backend/api/features/search/application.py"
    )

    with pytest.raises(checker.FeatureRegistryError, match="approved pytest contract test"):
        _validate(payload)


def test_checker_rejects_invalid_rollback_reference() -> None:
    payload = _payload()
    _feature(payload, "assistant")["rollback"]["references"][0]["locator"] = (
        "rollback evidence that is not present"
    )

    with pytest.raises(checker.FeatureRegistryError, match="locator was not found"):
        _validate(payload)


def test_checker_rejects_unknown_feature_dependency() -> None:
    payload = _payload()
    _feature(payload, "search")["dependencies"][0]["feature_id"] = "not-registered"

    with pytest.raises(checker.FeatureRegistryError, match="unknown feature"):
        _validate(payload)


def test_checker_rejects_feature_dependency_cycle() -> None:
    payload = _payload()
    assistant = _feature(payload, "assistant")
    assistant["dependencies"] = [
        copy.deepcopy(_feature(payload, "search")["dependencies"][0])
    ]
    assistant["dependencies"][0]["feature_id"] = "search"

    with pytest.raises(checker.FeatureRegistryError, match="contains a cycle"):
        _validate(payload)


def test_checker_rejects_pending_record_without_blocker() -> None:
    payload = _payload()
    health = _feature(payload, "assistant")["health_signal"]
    health["status"] = "pending"
    health["references"] = []
    health["blockers"] = []

    with pytest.raises(checker.FeatureRegistryError, match="pending records require blockers"):
        _validate(payload)


def test_checker_rejects_unknown_owner() -> None:
    payload = _payload()
    _feature(payload, "assistant")["owner_id"] = "invented-owner"

    with pytest.raises(checker.FeatureRegistryError, match="references unknown owner"):
        _validate(payload)


def test_checker_rejects_unused_owner() -> None:
    payload = _payload()
    payload["owners"].append(
        {
            "id": "unused-owner",
            "name": "Unused Owner",
            "kind": "accountability_role",
            "scope": "No registered responsibility.",
        }
    )

    with pytest.raises(checker.FeatureRegistryError, match="unused owner ids"):
        _validate(payload)


def test_checker_rejects_duplicate_route_namespace() -> None:
    payload = _payload()
    _feature(payload, "operations")["routes"][0]["namespace"] = (
        _feature(payload, "assistant")["routes"][0]["namespace"]
    )

    with pytest.raises(checker.FeatureRegistryError, match="route namespace.*claimed by both"):
        _validate(payload)


def test_checker_rejects_overlapping_route_ownership() -> None:
    payload = _payload()
    _feature(payload, "financial-alerts")["routes"][0]["namespace"] = (
        "/api/assistant/financial"
    )

    with pytest.raises(checker.FeatureRegistryError, match="overlapping route ownership"):
        _validate(payload)


def test_checker_rejects_unowned_backend_route_module() -> None:
    payload = _payload()
    _feature(payload, "identity")["routes"] = []

    with pytest.raises(
        checker.FeatureRegistryError,
        match="backend route module manifest drift.*auth.py",
    ):
        _validate(payload)


def test_checker_rejects_duplicate_page_route() -> None:
    payload = _payload()
    _feature(payload, "search")["pages"][0]["route"] = (
        _feature(payload, "assistant")["pages"][0]["route"]
    )

    with pytest.raises(checker.FeatureRegistryError, match="page route.*claimed by both"):
        _validate(payload)


def test_checker_rejects_page_router_locator_drift() -> None:
    payload = _payload()
    _feature(payload, "search")["pages"][0]["references"][0]["locator"] = (
        "path: 'renamed-without-registry-update'"
    )

    with pytest.raises(checker.FeatureRegistryError, match="locator was not found"):
        _validate(payload)


def test_checker_rejects_repository_path_escape() -> None:
    payload = _payload()
    _feature(payload, "assistant")["public_entries"][0]["path"] = "../outside.py"

    with pytest.raises(checker.FeatureRegistryError, match="repository-relative POSIX path"):
        _validate(payload)


def test_feature_boundary_scan_detects_backend_and_frontend_deep_imports(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "backend/api/routes/bad.py",
        "from api.features.story_graph.contracts import StoryNode\n",
    )
    _write(
        tmp_path,
        "backend/api/features/story_graph/contracts.py",
        "class StoryNode:\n    pass\n",
    )
    _write(
        tmp_path,
        "frontend/vue_project/src/views/Bad.vue",
        "<script setup>\nimport { normalize } from '@/features/search/model.js'\n</script>\n",
    )
    _write(
        tmp_path,
        "frontend/vue_project/src/features/search/model.js",
        "export const normalize = value => value\n",
    )

    violations = checker.feature_boundary_violations(tmp_path)

    assert {(item["rule"], item["path"]) for item in violations} == {
        ("backend-feature-public-api", "backend/api/routes/bad.py"),
        ("frontend-feature-public-api", "frontend/vue_project/src/views/Bad.vue"),
    }
