from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.features.authoritative_data import (
    AuthoritativeDataService,
    BoundedJsonClient,
    CountryPrimaryDocumentCatalogResponse,
    CountryPrimaryDocumentSchemaDescriptor,
)
from api.routes import authoritative_data


NOW = datetime(2026, 8, 10, 5, 30, tzinfo=timezone.utc)


def _service() -> AuthoritativeDataService:
    def reject_network(_request: httpx.Request) -> httpx.Response:
        pytest.fail("primary-document catalog must not access the network")

    return AuthoritativeDataService(
        client=BoundedJsonClient(transport=httpx.MockTransport(reject_network)),
        now=lambda: NOW,
    )


def test_primary_document_catalog_is_empty_and_fail_closed() -> None:
    catalog = _service().country_primary_document_catalog()

    assert catalog.schema_version == "globemind.country-primary-document.v1"
    assert catalog.available is False
    assert catalog.operational_state == "not_configured"
    assert catalog.implementation_scope == "schema_catalog_only"
    assert catalog.live_checked is False
    assert catalog.documents == ()
    assert len(catalog.document_schema.required_fields) == 25
    assert catalog.document_schema.document_kinds == (
        "constitution",
        "statute",
        "regulation",
        "official_gazette",
        "judicial_decision",
        "policy_document",
        "treaty",
    )
    assert {
        "text.section_anchor",
        "temporal.effective_from",
        "version.amended_by",
        "governance.license_state",
        "governance.reviewer_identifier",
    } <= set(catalog.document_schema.required_fields)
    evidence = catalog.document_schema.minimum_evidence
    assert evidence.section_anchor == "required_for_claim_citation"
    assert evidence.legal_effective_period == "required_and_not_inferred"
    assert evidence.version_relationships == "explicit_or_unknown"
    assert evidence.invalid_or_expired_policy == "fail_closed"


def test_primary_document_contract_rejects_documents_and_claim_inflation() -> None:
    payload = _service().country_primary_document_catalog().model_dump(mode="json")

    for field, value in (
        ("available", True),
        ("operational_state", "available"),
        ("live_checked", True),
        ("source_status", "configured"),
        ("license_status", "verified"),
        ("documents", [{"country_code": "ZZ", "title": "canary"}]),
        ("country_fact", "canary"),
        ("generated_at", "2026-08-10T05:30:00"),
    ):
        with pytest.raises(ValidationError):
            CountryPrimaryDocumentCatalogResponse.model_validate(
                {**payload, field: value}
            )

    schema = payload["document_schema"]
    schema["required_fields"] = list(reversed(schema["required_fields"]))
    with pytest.raises(ValidationError):
        CountryPrimaryDocumentSchemaDescriptor.model_validate(schema)


def test_primary_document_route_is_public_static_and_read_only() -> None:
    app = FastAPI()
    app.include_router(authoritative_data.router)
    app.dependency_overrides[
        authoritative_data.get_authoritative_data_service
    ] = _service

    with TestClient(app) as client:
        response = client.get(
            "/api/authoritative-data/country-profiles/primary-documents/catalog"
        )
        write_attempt = client.post(
            "/api/authoritative-data/country-profiles/primary-documents/catalog",
            json={"documents": []},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert write_attempt.status_code == 405
    body = response.json()
    assert body["available"] is False
    assert body["documents"] == []
    assert body["reason_codes"] == [
        "PILOT_COUNTRIES_NOT_SELECTED",
        "PRIMARY_DOCUMENTS_NOT_CONFIGURED",
        "OFFICIAL_SOURCE_EVIDENCE_NOT_CONFIGURED",
        "LICENSE_EVIDENCE_NOT_CONFIGURED",
        "OWNER_NOT_CONFIGURED",
        "REVIEWER_NOT_CONFIGURED",
    ]
    encoded = json.dumps(body, ensure_ascii=False)
    assert '"document_value"' not in encoded
    assert '"country_fact"' not in encoded
