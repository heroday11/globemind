from __future__ import annotations

from pathlib import Path

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.features.entity_governance import (
    EntityGovernanceLedger,
    EntityGovernanceService,
    load_search_seed_entities,
)
from api.routes import entity_governance as entity_routes
from api.services.auth import get_current_user_required


CN = "urn:globemind:entity:country:CN"
DIGEST = "a" * 64


def _client(
    root: Path,
    user: dict[str, object],
) -> tuple[TestClient, EntityGovernanceService]:
    app = FastAPI()
    app.include_router(entity_routes.router)
    service = EntityGovernanceService(
        EntityGovernanceLedger(
            root,
            b"entity-governance-route-hmac-key-001",
        ),
        load_search_seed_entities(),
        evidence_reader=None,
    )
    app.dependency_overrides[get_current_user_required] = lambda: user
    app.dependency_overrides[entity_routes.get_entity_governance_service] = lambda: service
    return TestClient(app), service


def test_route_namespace_exposes_required_read_and_mutation_boundaries() -> None:
    paths = {
        (route.path, method)
        for route in entity_routes.router.routes
        for method in route.methods
    }
    assert {
        ("/api/entity-governance/status", "GET"),
        ("/api/entity-governance/catalog", "GET"),
        ("/api/entity-governance/entities/{entity_id}", "GET"),
        ("/api/entity-governance/relations", "GET"),
        ("/api/entity-governance/history", "GET"),
        ("/api/entity-governance/entities/{entity_id}/decisions", "POST"),
        ("/api/entity-governance/aliases/reviews", "POST"),
        ("/api/entity-governance/relations", "POST"),
        (
            "/api/entity-governance/relations/{relation_id}/retractions",
            "POST",
        ),
        ("/api/entity-governance/merges", "POST"),
        ("/api/entity-governance/splits", "POST"),
    }.issubset(paths)


def test_main_application_mounts_entity_governance_namespace() -> None:
    from api.application import app as application

    paths = {route.path for route in application.routes}
    assert {
        "/api/entity-governance/status",
        "/api/entity-governance/catalog",
        "/api/entity-governance/entities/{entity_id}",
        "/api/entity-governance/relations",
        "/api/entity-governance/history",
    }.issubset(paths)


def test_entity_governance_namespace_rejects_anonymous_reads() -> None:
    app = FastAPI()
    app.include_router(entity_routes.router)

    response = TestClient(app).get("/api/entity-governance/status")

    assert response.status_code == 401
    assert response.json() == {"detail": "未登录或 token 无效"}


def test_logged_in_user_can_read_but_non_admin_cannot_mutate(tmp_path: Path) -> None:
    root = tmp_path / "governance"
    client, _ = _client(root, {"user_id": 2, "role": "user"})

    status = client.get("/api/entity-governance/status")
    catalog = client.get("/api/entity-governance/catalog")
    denied = client.post(
        f"/api/entity-governance/entities/{CN}/decisions",
        json={
            "expected_previous_event_id": None,
            "reason": "A non administrator cannot approve this entity",
            "evidence": {
                "article_id": 1,
                "snapshot_id": f"article-1-{'a' * 64}",
                "content_sha256": "a" * 64,
                "parser_version": "article-display-v1",
            },
            "decision": "approve",
            "valid_from": None,
            "valid_to": None,
        },
    )

    assert status.status_code == 200
    assert catalog.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "ENTITY_GOVERNANCE_ADMIN_REQUIRED"
    assert not root.exists()


def test_id_only_identity_is_rejected_and_strict_partial_evidence_is_422(
    tmp_path: Path,
) -> None:
    id_only_client, _ = _client(
        tmp_path / "id-only",
        {"id": 2, "role": "admin"},
    )
    assert id_only_client.get("/api/entity-governance/status").status_code == 403

    admin_client, _ = _client(
        tmp_path / "partial",
        {"user_id": 2, "role": "admin"},
    )
    response = admin_client.post(
        f"/api/entity-governance/entities/{CN}/decisions",
        json={
            "expected_previous_event_id": None,
            "reason": "This request has only a partial evidence reference",
            "evidence": {
                "article_id": 1,
                "snapshot_id": f"article-1-{'a' * 64}",
                "parser_version": "article-display-v1",
            },
            "decision": "approve",
            "valid_from": None,
            "valid_to": None,
        },
    )
    assert response.status_code == 422
    assert not (tmp_path / "partial").exists()


def test_admin_mutation_without_safe_evidence_reader_returns_503_no_fallback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "governance"
    client, _ = _client(root, {"user_id": 2, "role": "admin"})
    response = client.post(
        f"/api/entity-governance/entities/{CN}/decisions",
        json={
            "expected_previous_event_id": None,
            "reason": "This mutation cannot verify the referenced evidence",
            "evidence": {
                "article_id": 1,
                "snapshot_id": f"article-1-{'a' * 64}",
                "content_sha256": "a" * 64,
                "parser_version": "article-display-v1",
            },
            "decision": "approve",
            "valid_from": None,
            "valid_to": None,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "ENTITY_GOVERNANCE_UNAVAILABLE",
        "reason_code": "ENTITY_EVIDENCE_LEDGER_READER_UNAVAILABLE",
        "fallback": "none",
    }
    assert not root.exists()


@pytest.mark.parametrize(
    "ambiguous_body",
    [
        (
            '{"expected_previous_event_id":null,'
            '"expected_previous_event_id":"egv-0000000001-20260809T120000000000Z-aaaaaaaaaaaaaaaa",'
            '"reason":"Duplicate optimistic head must not be resolved by last-key-wins",'
            f'"evidence":{{"article_id":1,"snapshot_id":"article-1-{DIGEST}",'
            f'"content_sha256":"{DIGEST}","parser_version":"article-display-v1"}},'
            '"decision":"approve","valid_from":null,"valid_to":null}'
        ),
        (
            '{"expected_previous_event_id":null,'
            '"reason":"Non finite numbers must not survive an otherwise unused field",'
            f'"evidence":{{"article_id":1,"snapshot_id":"article-1-{DIGEST}",'
            f'"content_sha256":"{DIGEST}","parser_version":"article-display-v1"}},'
            '"decision":"approve","valid_from":null,"valid_to":null,"ignored":1e400}'
        ),
        '{"ignored":NaN}',
        '{"ignored":Infinity}',
        '{"ignored":-Infinity}',
        '{"nested":' + ("[" * 1_100) + "0" + ("]" * 1_100) + "}",
    ],
)
def test_mutation_routes_reject_ambiguous_json_before_service_use(
    tmp_path: Path,
    ambiguous_body: str,
) -> None:
    root = tmp_path / "ambiguous-json"
    client, _ = _client(root, {"user_id": 2, "role": "admin"})

    response = client.post(
        f"/api/entity-governance/entities/{CN}/decisions",
        content=ambiguous_body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ENTITY_GOVERNANCE_JSON_AMBIGUOUS"
    assert not root.exists()


def test_service_factory_missing_hmac_key_is_honest_and_zero_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "configured-but-disabled"
    monkeypatch.setenv("ENTITY_GOVERNANCE_ROOT", str(root))
    monkeypatch.delenv("ENTITY_GOVERNANCE_HMAC_KEY", raising=False)

    service = entity_routes.get_entity_governance_service()
    status = service.status({"user_id": 3, "role": "user"})

    assert status["storage_status"] == "unavailable"
    assert status["integrity_status"] == "unavailable"
    assert status["reason"] == "ENTITY_GOVERNANCE_LEDGER_CONFIGURATION_UNAVAILABLE"
    assert status["mutation_status"] == "blocked"
    assert status["mutation_blocker"] == (
        "ENTITY_GOVERNANCE_LEDGER_CONFIGURATION_UNAVAILABLE"
    )
    assert status["accuracy_claim"] == "not_measured"
    assert not root.exists()


def test_service_factory_rejects_relative_root_without_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENTITY_GOVERNANCE_ROOT", "relative-governance")
    monkeypatch.setenv(
        "ENTITY_GOVERNANCE_HMAC_KEY",
        "entity-governance-route-hmac-key-001",
    )

    service = entity_routes.get_entity_governance_service()
    status = service.status({"user_id": 3, "role": "user"})

    assert status["storage_status"] == "unavailable"
    assert status["reason"] == "ENTITY_GOVERNANCE_LEDGER_CONFIGURATION_UNAVAILABLE"
    assert not (tmp_path / "relative-governance").exists()
