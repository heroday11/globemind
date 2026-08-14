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
    CountryInstitutionCatalogResponse,
    CountryInstitutionSchemaDescriptor,
)
from api.routes import authoritative_data


NOW = datetime(2026, 8, 9, 20, 0, tzinfo=timezone.utc)


def _service() -> AuthoritativeDataService:
    def reject_network(_request: httpx.Request) -> httpx.Response:
        pytest.fail("institution schema catalog must not access the network")

    return AuthoritativeDataService(
        client=BoundedJsonClient(transport=httpx.MockTransport(reject_network)),
        now=lambda: NOW,
    )


def test_country_institution_catalog_is_empty_schema_with_field_evidence_gates() -> None:
    catalog = _service().country_institution_catalog()

    assert catalog.catalog_id == (
        "urn:globemind:country-institution-governance:catalog:v1"
    )
    assert catalog.schema_version == (
        "globemind.country-institution-governance.v1"
    )
    assert catalog.available is False
    assert catalog.operational_state == "not_configured"
    assert catalog.implementation_scope == "schema_catalog_only"
    assert catalog.live_checked is False
    assert catalog.live_data_status == "not_configured"
    assert catalog.owner_status == "not_configured"
    assert catalog.reviewer_status == "not_configured"
    assert catalog.license_status == "not_configured"
    assert catalog.country_scope_status == "not_configured"
    assert catalog.facts == ()

    schema = catalog.institution_schema
    section_ids = [section.section_id for section in schema.sections]
    field_ids = [field.field_id for field in schema.fields]
    assert section_ids == [
        "constitutional_order",
        "power_structure",
        "administrative_system",
        "evidence_governance",
    ]
    assert len(field_ids) == 27
    assert len(field_ids) == len(set(field_ids))
    assert set(field_ids) == {
        field_id
        for section in schema.sections
        for field_id in section.field_ids
    }
    assert {
        "constitutional_order.government_form",
        "constitutional_order.constitutional_basis",
        "power_structure.executive_authority",
        "power_structure.observed_power_claims",
        "power_structure.formal_observed_comparison",
        "administrative_system.administrative_levels",
        "administrative_system.civil_service_structure",
        "evidence_governance.claim_source_bindings",
        "evidence_governance.conflicting_evidence",
    } <= set(field_ids)

    assert all(field.evidence_required is True for field in schema.fields)
    assert all(field.citation_required is True for field in schema.fields)
    assert all(field.temporal_scope_required is True for field in schema.fields)
    assert all(field.license_evidence_required is True for field in schema.fields)
    assert all(field.owner_review_required is True for field in schema.fields)
    field_by_id = {field.field_id: field for field in schema.fields}
    assert (
        field_by_id["power_structure.observed_power_claims"].evidence_profile
        == "independent_observation_corroborated"
    )
    assert (
        field_by_id["power_structure.formal_observed_comparison"].evidence_profile
        == "separate_de_jure_and_de_facto_bindings"
    )

    evidence = schema.minimum_evidence
    assert evidence.claim_granularity == "one_fact_per_evidence_binding"
    assert evidence.source_locator == "absolute_https_url"
    assert evidence.source_authority == "required"
    assert evidence.source_language == "required_bcp47"
    assert evidence.legal_effective_period == "required_for_de_jure_claims"
    assert evidence.observation_period == "required_for_de_facto_claims"
    assert evidence.formal_actual_separation == "required"
    assert (
        evidence.independent_corroboration
        == "required_for_de_facto_claims"
    )
    assert evidence.license_state == "verified_or_restricted"
    assert evidence.owner_identifier == "required_stable_identifier"
    assert evidence.reviewer_identifier == "required_stable_identifier"
    assert evidence.expired_review_policy == "fail_closed"
    assert evidence.invalid_evidence_policy == "fail_closed"


def test_country_institution_contract_rejects_facts_and_assurance_inflation() -> None:
    payload = _service().country_institution_catalog().model_dump(mode="json")

    for field, value in (
        ("available", True),
        ("operational_state", "available"),
        ("live_checked", True),
        ("live_data_status", "live"),
        ("owner_status", "assigned"),
        ("reviewer_status", "approved"),
        ("license_status", "verified"),
        ("country_scope_status", "configured"),
        ("facts", [{"country_code": "ZZ", "government_form": "canary"}]),
        ("reason_codes", ["INSTITUTION_FACTS_NOT_CONFIGURED"]),
        ("generated_at", "2026-08-09T20:00:00"),
        ("country_fact", "canary"),
    ):
        mutated = {**payload, field: value}
        with pytest.raises(ValidationError):
            CountryInstitutionCatalogResponse.model_validate(mutated)

    wrong_observation_profile = (
        _service()
        .country_institution_catalog()
        .institution_schema.model_dump(mode="json")
    )
    observed = next(
        field
        for field in wrong_observation_profile["fields"]
        if field["field_id"] == "power_structure.observed_power_claims"
    )
    observed["evidence_profile"] = "official_legal_primary"
    with pytest.raises(ValidationError):
        CountryInstitutionSchemaDescriptor.model_validate(
            wrong_observation_profile
        )

    missing_evidence_gate = (
        _service()
        .country_institution_catalog()
        .institution_schema.model_dump(mode="json")
    )
    missing_evidence_gate["fields"][0]["citation_required"] = False
    with pytest.raises(ValidationError):
        CountryInstitutionSchemaDescriptor.model_validate(missing_evidence_gate)

    duplicated_section = (
        _service()
        .country_institution_catalog()
        .institution_schema.model_dump(mode="json")
    )
    duplicated_section["sections"].append(duplicated_section["sections"][0])
    with pytest.raises(ValidationError):
        CountryInstitutionSchemaDescriptor.model_validate(duplicated_section)


def test_country_institution_catalog_route_is_public_static_and_read_only() -> None:
    app = FastAPI()
    app.include_router(authoritative_data.router)
    app.dependency_overrides[
        authoritative_data.get_authoritative_data_service
    ] = _service

    with TestClient(app) as client:
        response = client.get(
            "/api/authoritative-data/country-profiles/institutions/catalog"
        )
        write_attempt = client.post(
            "/api/authoritative-data/country-profiles/institutions/catalog",
            json={"facts": []},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert write_attempt.status_code == 405
    body = response.json()
    assert body["available"] is False
    assert body["facts"] == []
    assert body["live_data_status"] == "not_configured"
    assert body["owner_status"] == "not_configured"
    assert body["reviewer_status"] == "not_configured"
    assert body["license_status"] == "not_configured"
    assert body["reason_codes"] == [
        "PILOT_COUNTRIES_NOT_SELECTED",
        "INSTITUTION_FACTS_NOT_CONFIGURED",
        "OFFICIAL_SOURCE_EVIDENCE_NOT_CONFIGURED",
        "DE_FACTO_METHOD_NOT_CONFIGURED",
        "LICENSE_EVIDENCE_NOT_CONFIGURED",
        "OWNER_NOT_CONFIGURED",
        "REVIEWER_NOT_CONFIGURED",
    ]
    encoded = json.dumps(body, ensure_ascii=False)
    assert '"country_code"' not in encoded
    assert '"fact_value"' not in encoded
