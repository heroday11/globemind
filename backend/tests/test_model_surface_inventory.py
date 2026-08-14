from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.features.model_assurance import (
    MODEL_OUTPUT_SURFACE_SCHEMA_VERSION,
    audit_model_output_surface_sources,
    build_model_output_surface_inventory,
)
from api.routes import model_assurance
from api.services.auth import get_current_user_required


ROOT = Path(__file__).resolve().parents[2]


def test_bounded_inventory_is_content_free_and_fail_closed() -> None:
    inventory = build_model_output_surface_inventory()

    assert inventory.schema_version == MODEL_OUTPUT_SURFACE_SCHEMA_VERSION
    assert inventory.scope == "bounded_public_model_output_surfaces"
    assert inventory.coverage_state == "source_located"
    assert inventory.complete_runtime_deployment_claim is False
    assert inventory.runtime_attestation_state == "not_available"
    assert [surface.surface_id for surface in inventory.surfaces] == [
        "article.opinion-detail",
        "assistant.interactive",
        "assistant.scheduled-report",
        "financial.derived-indicators",
        "opinion.aggregate",
        "story-graph.derived-relations",
    ]

    version_status = {
        "article.opinion-detail": "not_available",
        "assistant.interactive": "not_available",
        "assistant.scheduled-report": "not_available",
        "financial.derived-indicators": "unknown",
        "opinion.aggregate": "unknown",
        "story-graph.derived-relations": "not_available",
    }
    contract_fields = {
        "article.opinion-detail": (),
        "assistant.interactive": (),
        "assistant.scheduled-report": (),
        "financial.derived-indicators": ("model_version", "method_version"),
        "opinion.aggregate": ("model_version", "method_version"),
        "story-graph.derived-relations": (),
    }
    for surface in inventory.surfaces:
        assert surface.runtime_attestation.status == "not_available"
        assert surface.runtime_attestation.attestation_id is None
        assert surface.runtime_attestation.observed_at is None
        assert surface.identity.model_id.status == "not_available"
        assert surface.identity.model_id.value is None
        assert surface.identity.model_version.status == version_status[surface.surface_id]
        assert surface.identity.model_version.value is None
        assert surface.identity_contract_fields == contract_fields[surface.surface_id]
        assert surface.identity.deployed_at.status == "not_available"
        assert surface.identity.deployed_at.value is None
        assert surface.identity.change_notes.status == "not_available"
        assert surface.identity.change_notes.value is None
        assert surface.route_patterns
        assert surface.source_locators
        assert {
            "RUNTIME_MODEL_ATTESTATION_NOT_AVAILABLE",
            "DEPLOYMENT_TIME_NOT_AVAILABLE",
            "CHANGE_NOTES_NOT_AVAILABLE",
        }.issubset(surface.reason_codes)

    payload = inventory.model_dump(mode="json")
    serialized = str(payload).lower()
    for forbidden in (
        "prompt_text",
        "response_body",
        "article_body",
        "secret",
        "token",
        "database_url",
    ):
        assert forbidden not in serialized


def test_repository_source_coverage_gate_is_green_and_detects_removed_locator(
    tmp_path: Path,
) -> None:
    inventory = build_model_output_surface_inventory()
    assert audit_model_output_surface_sources(ROOT, inventory) == ()

    sources: dict[str, list[str]] = defaultdict(list)
    for surface in inventory.surfaces:
        for source in surface.source_locators:
            sources[source.path].append(source.locator)
    for relative_path, locators in sources.items():
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(locators), encoding="utf-8")

    first = inventory.surfaces[0].source_locators[0]
    target = tmp_path / first.path
    target.write_text(
        target.read_text(encoding="utf-8").replace(first.locator, "REMOVED", 1),
        encoding="utf-8",
    )
    issues = audit_model_output_surface_sources(tmp_path, inventory)
    assert len(issues) == 1
    assert issues[0].code == "SOURCE_LOCATOR_MISSING"
    assert issues[0].surface_id == inventory.surfaces[0].surface_id
    assert issues[0].path == first.path
    assert first.locator not in str(issues[0])


def test_authenticated_read_only_route_returns_no_store_inventory() -> None:
    app = FastAPI()
    app.include_router(model_assurance.router)

    with TestClient(app) as anonymous:
        denied = anonymous.get("/api/model-assurance/surfaces")
    assert denied.status_code == 401

    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 8,
        "role": "user",
    }
    with TestClient(app) as client:
        response = client.get("/api/model-assurance/surfaces")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    payload = response.json()
    assert payload["schema_version"] == MODEL_OUTPUT_SURFACE_SCHEMA_VERSION
    assert payload["complete_runtime_deployment_claim"] is False
    assert payload["runtime_attestation_state"] == "not_available"
    assert len(payload["surfaces"]) == 6
