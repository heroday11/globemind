from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.http_security import RequestRateLimitMiddleware
from api.features import operations
from api.features.operations.asset_inventory import (
    PROCESSING_ACTIVITIES,
    build_asset_inventory,
    build_dependency_inventory,
    build_environment_inventory,
)
from api.routes import governance_inventory
from api.services.auth import get_current_user_required

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_asset_inventory_covers_current_routes_locks_environment_and_processing_without_values(
    monkeypatch,
) -> None:
    app = FastAPI()
    app.include_router(governance_inventory.router)
    monkeypatch.setenv("V1_INVENTORY_SECRET_MARKER", "must-never-appear-in-inventory")

    payload = build_asset_inventory(
        app,
        repository_root=PROJECT_ROOT,
        generated_at=datetime(2026, 8, 9, 9, tzinfo=timezone.utc),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["schema_version"] == "globemind.asset-inventory.v1"
    assert payload["api"]["route_count"] >= 1
    route = next(
        item
        for item in payload["api"]["routes"]
        if item["path"] == "/api/governance/asset-inventory"
    )
    assert route["access"] == {
        "level": "administrator",
        "evidence": "fastapi_dependency",
    }
    assert len(payload["dependencies"]["manifests"]) == 2
    assert all(
        item["status"] == "available"
        for item in payload["dependencies"]["manifests"]
    )
    assert all(
        item["dependency_count"] > 0
        for item in payload["dependencies"]["manifests"]
    )
    assert payload["environment"]["status"] == "available"
    assert payload["environment"]["variables"]
    assert len(payload["processing_activities"]) == len(PROCESSING_ACTIVITIES)
    assert payload["processing_inventory_status"].endswith("privacy_and_legal_approval")
    assert "must-never-appear-in-inventory" not in serialized
    assert "database rows" in payload["exclusions"]


def test_processing_inventory_never_claims_unapproved_owner_retention_or_training_use() -> None:
    ids = {item["id"] for item in PROCESSING_ACTIVITIES}

    assert len(ids) == len(PROCESSING_ACTIVITIES)
    assert "account-identity" in ids
    assert "workspace-files-and-reports" in ids
    assert "assistant-conversations-and-memory" in ids
    for activity in PROCESSING_ACTIVITIES:
        assert activity["owner"] == "待指定"
        assert activity["retention_status"] == "not_approved"
        assert activity["legal_basis_status"] == "not_approved"
        assert activity["processor_inventory_status"] == "not_complete"
        assert activity["training_use_status"] in {"not_assessed", "not_applicable"}
        assert activity["rights_workflow_status"] == "manual_intake_only"


def test_dependency_inventory_fails_closed_when_manifests_are_absent(tmp_path: Path) -> None:
    manifests = build_dependency_inventory(tmp_path)

    assert len(manifests) == 2
    assert all(item["status"] == "missing" for item in manifests)
    assert all(item["sha256"] is None for item in manifests)
    assert all(item["dependencies"] == [] for item in manifests)


def test_dependency_inventory_rejects_symlinks_and_duplicate_json_keys(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    python_lock = repository / "requirements" / "roles" / "web.lock"
    python_lock.parent.mkdir(parents=True)
    outside_lock = tmp_path / "outside.lock"
    outside_lock.write_text("private-package==9.9.9\n", encoding="utf-8")
    python_lock.symlink_to(outside_lock)

    npm_lock = repository / "package-lock.json"
    npm_lock.parent.mkdir(parents=True, exist_ok=True)
    npm_lock.write_text(
        '{"packages": {}, "packages": {"node_modules/private": {"version": "1"}}}',
        encoding="utf-8",
    )

    manifests = {
        item["id"]: item for item in build_dependency_inventory(repository)
    }

    assert manifests["python-web"]["status"] == "invalid_path"
    assert manifests["python-web"]["sha256"] is None
    assert manifests["python-web"]["dependencies"] == []
    assert manifests["frontend-workspaces"]["status"] == "invalid_or_empty"
    assert manifests["frontend-workspaces"]["sha256"] is None
    assert manifests["frontend-workspaces"]["dependencies"] == []


def test_dependency_inventory_rejects_partially_parseable_python_lock(
    tmp_path: Path,
) -> None:
    python_lock = tmp_path / "requirements" / "roles" / "web.lock"
    python_lock.parent.mkdir(parents=True)
    python_lock.write_text(
        "safe-package==1.0.0\nnot a locked requirement\n",
        encoding="utf-8",
    )

    manifest = build_dependency_inventory(tmp_path)[0]

    assert manifest["status"] == "invalid_or_empty"
    assert manifest["sha256"] is None
    assert manifest["dependencies"] == []


def test_environment_inventory_rejects_corrupt_manifest_without_values(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "config" / "runtime" / "env-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"variables": [{"name": "SECRET"}],', encoding="utf-8")

    inventory = build_environment_inventory(tmp_path)

    assert inventory == {
        "path": "config/runtime/env-manifest.json",
        "status": "invalid_or_empty",
        "sha256": None,
        "services": [],
        "variables": [],
    }


def test_application_mounts_admin_governance_inventory_routes() -> None:
    from api.application import app as application

    paths = {getattr(route, "path", "") for route in application.routes}

    assert "/api/governance/asset-inventory" in paths
    assert "/api/governance/api-contract" in paths
    assert "/api/governance/openapi.json" in paths

    contract = operations.build_api_documentation_contract(application)
    assert contract["running_schema"]["path_count"] >= 100
    assert contract["running_schema"]["operation_count"] >= 100
    assert contract["running_schema"]["application_version"] != "unavailable"

    schema = application.openapi()
    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method.lower()
        in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert {
        method: operation["operationId"]
        for method, operation in schema["paths"]["/llm/{path}"].items()
        if method in {"get", "post", "put", "patch", "delete", "head", "options"}
    } == {
        method: f"authenticated_llm_proxy_{method}"
        for method in {"get", "post", "put", "patch", "delete", "head", "options"}
    }


def test_authenticated_api_contract_is_versioned_bounded_and_honest() -> None:
    app = FastAPI(
        title="Contract fixture",
        version="1.2.3",
        description="provider-secret-canary must never be copied into metadata",
    )

    @app.get("/api/example")
    def example() -> dict[str, bool]:
        return {"ok": True}

    payload = operations.build_api_documentation_contract(
        app,
        generated_at=datetime(2026, 8, 9, 19, tzinfo=timezone.utc),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["schema_version"] == "globemind.authenticated-api-documentation.v1"
    assert payload["access"] == {
        "catalog_endpoint": "/api/governance/api-contract",
        "openapi_endpoint": "/api/governance/openapi.json",
        "required_role": "administrator",
        "authentication_scheme": "bearer",
        "route_access_policy": "mixed_route_specific",
    }
    assert payload["running_schema"]["application_version"] == "1.2.3"
    assert payload["running_schema"]["openapi_version"].startswith("3.")
    assert payload["running_schema"]["path_count"] == 1
    assert payload["running_schema"]["operation_count"] == 1
    assert len(payload["running_schema"]["sha256"]) == 64
    assert payload["running_schema"]["hash_scope"] == "canonical_running_openapi"
    assert payload["versioning"]["stability_claim"] == "not_established"
    assert payload["versioning"]["compatibility_policy"] == "not_approved"
    assert payload["rate_limits"]["effective_runtime_attestation"] == "not_available"
    assert payload["rate_limits"]["multi_instance_coordination"] == "not_configured"
    assert payload["rate_limits"]["persistence"] == "process_memory_only"
    assert {rule["id"] for rule in payload["rate_limits"]["source_defaults"]} == {
        "auth",
        "registration",
        "ai",
        "upload",
        "heartbeat",
        "mutation",
    }
    assert payload["examples"][0]["authorization"] == "Bearer <access-token>"
    assert "provider-secret-canary" not in serialized
    assert "runtime_rate_limit_values_not_attested" in payload["limitations"]


def test_api_documentation_rejects_ambiguous_or_unbounded_schema_shapes() -> None:
    class DuplicateKeyDict(dict[str, object]):
        def items(self):
            return [("repeated", "first"), ("repeated", "second")]

    class LyingPathDict(dict[str, object]):
        def __len__(self) -> int:
            return 0

        def items(self):
            return [(f"/path-{index}", {}) for index in range(5001)]

    def schema_with(paths: dict[str, object], **extensions: object) -> dict[str, object]:
        return {
            "openapi": "3.1.0",
            "info": {"title": "fixture", "version": "1"},
            "paths": paths,
            **extensions,
        }

    deep: object = "leaf"
    for _ in range(80):
        deep = {"next": deep}

    cases = {
        "duplicate-operation-id": schema_with(
            {
                "/one": {"get": {"operationId": "shared", "responses": {}}},
                "/two": {"post": {"operationId": "shared", "responses": {}}},
            }
        ),
        "missing-operation-id": schema_with(
            {"/one": {"get": {"responses": {}}}}
        ),
        "non-object-operation": schema_with({"/one": {"get": []}}),
        "non-string-method-key": schema_with(
            {"/one": {1: {"operationId": "integer-method"}}}  # type: ignore[dict-item]
        ),
        "non-string-json-key": schema_with(
            {"/one": {"get": {"operationId": "one", "x-data": {1: "value"}}}}
        ),
        "duplicate-json-key": schema_with(
            {"/one": {"get": {"operationId": "one"}}},
            **{"x-duplicate": DuplicateKeyDict(repeated="stored")},
        ),
        "path-count-bypass": schema_with(LyingPathDict()),
        "non-finite-number": schema_with(
            {"/one": {"get": {"operationId": "one"}}},
            **{"x-score": math.nan},
        ),
        "excessive-depth": schema_with(
            {"/one": {"get": {"operationId": "one"}}},
            **{"x-deep": deep},
        ),
        "oversized": schema_with(
            {"/one": {"get": {"operationId": "one"}}},
            **{"x-padding": "x" * (4 * 1024 * 1024 + 1)},
        ),
    }

    for case_id, schema in cases.items():
        app = FastAPI()
        app.openapi = lambda schema=schema: schema  # type: ignore[method-assign]
        with pytest.raises(
            operations.ApiDocumentationUnavailable,
            match="OpenAPI|openapi|operation|schema",
        ) as failed:
            operations.build_api_documentation_contract(app)
        assert failed.value.args, case_id


def test_api_documentation_wraps_generation_errors_without_secret_detail() -> None:
    app = FastAPI()

    def broken_openapi() -> dict[str, object]:
        raise RuntimeError("provider-secret-canary")

    app.openapi = broken_openapi  # type: ignore[method-assign]
    with pytest.raises(operations.ApiDocumentationUnavailable) as failed:
        operations.build_api_documentation_contract(app)
    assert "canary" not in str(failed.value)


def test_api_documentation_hash_is_independent_of_mapping_insertion_order() -> None:
    first_schema = {
        "openapi": "3.1.0",
        "info": {"title": "fixture", "version": "1.0.0"},
        "paths": {
            "/z": {"get": {"operationId": "z_get", "responses": {}}},
            "/a": {"post": {"responses": {}, "operationId": "a_post"}},
        },
    }
    second_schema = {
        "paths": {
            "/a": {"post": {"operationId": "a_post", "responses": {}}},
            "/z": {"get": {"responses": {}, "operationId": "z_get"}},
        },
        "info": {"version": "1.0.0", "title": "fixture"},
        "openapi": "3.1.0",
    }
    contracts = []
    for schema in (first_schema, second_schema):
        app = FastAPI(version="1.0.0")
        app.openapi = lambda schema=schema: schema  # type: ignore[method-assign]
        contracts.append(
            operations.build_api_documentation_contract(
                app,
                generated_at=datetime(2026, 8, 9, 19, tzinfo=timezone.utc),
            )
        )

    assert contracts[0]["running_schema"] == contracts[1]["running_schema"]
    assert contracts[0]["running_schema"]["path_count"] == 2
    assert contracts[0]["running_schema"]["operation_count"] == 2


def test_authenticated_openapi_bytes_match_contract_hash_and_are_not_cached() -> None:
    app = FastAPI(title="Canonical fixture", version="1.2.3")

    @app.get("/api/example", operation_id="canonical_example")
    def example() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(governance_inventory.router)
    app.dependency_overrides[governance_inventory.get_current_admin_user] = lambda: {
        "user_id": 1,
        "role": "admin",
    }
    with TestClient(app) as client:
        contract_response = client.get("/api/governance/api-contract")
        openapi_response = client.get("/api/governance/openapi.json")

    assert contract_response.status_code == 200
    assert openapi_response.status_code == 200
    assert openapi_response.headers["cache-control"] == "no-store"
    assert openapi_response.headers["x-content-type-options"] == "nosniff"
    assert len(openapi_response.content) <= 4 * 1024 * 1024
    assert contract_response.json()["running_schema"]["sha256"] == hashlib.sha256(
        openapi_response.content
    ).hexdigest()


def test_api_documentation_routes_fail_closed_without_error_or_request_leak() -> None:
    app = FastAPI()
    app.include_router(governance_inventory.router)
    app.dependency_overrides[governance_inventory.get_current_admin_user] = lambda: {
        "user_id": 1,
        "role": "admin",
    }

    def broken_openapi() -> dict[str, object]:
        raise RuntimeError("provider-secret-canary")

    app.openapi = broken_openapi  # type: ignore[method-assign]
    with TestClient(app, raise_server_exceptions=False) as client:
        responses = [
            client.get(
                endpoint,
                headers={"Authorization": "Bearer request-secret-canary"},
            )
            for endpoint in (
                "/api/governance/api-contract",
                "/api/governance/openapi.json",
            )
        ]

    for response in responses:
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.json() == {
            "detail": {"code": "API_DOCUMENTATION_UNAVAILABLE"}
        }
        assert "canary" not in response.text


def test_rate_limit_source_defaults_never_claim_runtime_effective_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_RATE_LIMIT_REQUESTS", "777")
    monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "888")

    async def downstream(_scope, _receive, _send) -> None:
        return None

    runtime = RequestRateLimitMiddleware(downstream)
    assert runtime.auth_rule.requests == 777
    assert runtime.auth_rule.window_seconds == 888

    app = FastAPI(version="1.2.3")

    @app.get("/api/example", operation_id="source_default_example")
    def example() -> dict[str, bool]:
        return {"ok": True}

    payload = operations.build_api_documentation_contract(app)
    auth_default = next(
        item for item in payload["rate_limits"]["source_defaults"]
        if item["id"] == "auth"
    )
    assert auth_default["default_requests"] == 10
    assert auth_default["default_window_seconds"] == 60
    assert "effective_requests" not in auth_default
    assert payload["rate_limits"]["effective_runtime_attestation"] == "not_available"

    example_payload = json.dumps(payload["examples"], ensure_ascii=False).lower()
    assert payload["examples"][0]["authorization"] == "Bearer <access-token>"
    assert "localhost" not in example_payload
    assert "127.0.0.1" not in example_payload
    assert "http://" not in example_payload
    assert "https://" not in example_payload


def test_governance_documentation_rejects_non_admin_roles_and_has_no_write_method() -> None:
    app = FastAPI()
    app.include_router(governance_inventory.router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 2,
        "role": "user",
    }
    with TestClient(app) as client:
        denied = [
            client.get(endpoint)
            for endpoint in (
                "/api/governance/api-contract",
                "/api/governance/openapi.json",
            )
        ]
    assert all(response.status_code == 403 for response in denied)

    documentation_paths = {
        "/api/governance/api-contract",
        "/api/governance/openapi.json",
    }
    routes = [
        route for route in governance_inventory.router.routes
        if getattr(route, "path", "") in documentation_paths
    ]
    assert len(routes) == 2
    assert all(getattr(route, "methods", set()) == {"GET"} for route in routes)


def test_asset_inventory_endpoint_requires_an_administrator() -> None:
    app = FastAPI()
    app.include_router(governance_inventory.router)

    with TestClient(app) as client:
        unauthenticated = client.get("/api/governance/asset-inventory")
        unauthenticated_contract = client.get("/api/governance/api-contract")
        unauthenticated_openapi = client.get("/api/governance/openapi.json")

    assert unauthenticated.status_code == 401
    assert unauthenticated_contract.status_code == 401
    assert unauthenticated_openapi.status_code == 401

    app.dependency_overrides[governance_inventory.get_current_admin_user] = lambda: {
        "user_id": 1,
        "username": "admin",
        "role": "admin",
    }
    with TestClient(app) as client:
        response = client.get("/api/governance/asset-inventory")
        contract_response = client.get("/api/governance/api-contract")
        openapi_response = client.get("/api/governance/openapi.json")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "globemind.asset-inventory.v1"
    assert contract_response.status_code == 200
    assert contract_response.json()["schema_version"] == (
        "globemind.authenticated-api-documentation.v1"
    )
    assert openapi_response.status_code == 200
    assert openapi_response.json()["openapi"].startswith("3.")
    assert "/api/governance/asset-inventory" in openapi_response.json()["paths"]
