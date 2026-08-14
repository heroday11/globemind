from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.features.authoritative_data import (
    AuthoritativeDataService,
    BoundedJsonClient,
    CountryProfileCatalogResponse,
    CountryProfileSchemaDescriptor,
    CrossrefQuery,
    ImfQuery,
    UnSdgQuery,
    UpstreamFailure,
    WorldBankQuery,
)
from api.routes import authoritative_data
from api.services.auth import get_current_user_required


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _service(handler, *, now=lambda: NOW, maximum_response_bytes=1_048_576):
    transport = httpx.MockTransport(handler)
    client = BoundedJsonClient(
        transport=transport,
        maximum_response_bytes=maximum_response_bytes,
    )
    return AuthoritativeDataService(client=client, now=now), client


def _world_bank_payload():
    return [
        {"page": 1, "pages": 1, "per_page": 2, "total": 3},
        [
            {
                "indicator": {"id": "SP.POP.TOTL", "value": "Population"},
                "country": {"id": "CN", "value": "China"},
                "countryiso3code": "CHN",
                "date": "2025",
                "value": 1_400_000_000,
                "unit": "",
                "obs_status": "",
                "decimal": 0,
            },
            {
                "indicator": {"id": "SP.POP.TOTL", "value": "Population"},
                "country": {"id": "CN", "value": "China"},
                "countryiso3code": "CHN",
                "date": "2024",
                "value": 1_401_000_000,
                "unit": "",
                "obs_status": "",
                "decimal": 0,
            },
        ],
    ]


def test_world_bank_normalizes_and_reuses_a_provenance_complete_cache() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_world_bank_payload())

    service, client = _service(handler)

    async def scenario():
        query = WorldBankQuery(
            country="CHN",
            indicator="SP.POP.TOTL",
            limit=2,
        )
        first = await service.world_bank(query)
        second = await service.world_bank(query)
        return first, second

    first, second = asyncio.run(scenario())

    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "api.worldbank.org"
    assert request.url.path == "/v2/country/CHN/indicator/SP.POP.TOTL"
    assert request.url.params["source"] == "2"
    assert request.url.params["per_page"] == "2"
    assert request.url.params["mrnev"] == "2"
    assert first.available is True
    assert first.state == "available"
    assert [record.period for record in first.records] == ["2025", "2024"]
    assert first.cache.state == "refreshed"
    assert first.cache.available is True
    assert first.cache.cutoff == "2025"
    assert first.cache.last_success == NOW
    assert first.cache.license.state == "restricted"
    assert first.cache.coverage.returned_records == 2
    assert first.cache.coverage.upstream_total == 3
    assert first.cache.source.source_id == "world-bank"
    assert "Indicators API v2" in first.cache.version
    assert first.cache.payload_sha256 is not None
    assert second.state == "cached"
    assert second.cache.state == "hit"
    assert client.network_policy["trust_env"] is False
    assert client.network_policy["follow_redirects"] is False
    assert client.network_policy["https_only"] is True


def test_imf_reapplies_requested_entities_and_periods_to_an_overbroad_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "api": {"version": "2", "output-method": "json"},
                "indicators": {
                    "NGDP_RPCH": {
                        "label": "Real GDP growth",
                        "unit": "Percent change",
                    }
                },
                "values": {
                    "NGDP_RPCH": {
                        "CHN": {"2023": 5.2, "2024": 4.8, "2025": 4.5},
                        "USA": {"2024": 2.7},
                        "SDN": {"2024": -23.4},
                    },
                    "": {"ignored": True},
                },
            },
        )

    service, _ = _service(handler)
    response = asyncio.run(
        service.imf(
            ImfQuery(
                indicator="NGDP_RPCH",
                entities=["CHN", "USA"],
                periods=[2024, 2025],
                limit=10,
            )
        )
    )

    assert seen[0].url.path.endswith("/v2/NGDP_RPCH/CHN/USA")
    assert seen[0].url.params["periods"] == "2024,2025"
    assert {
        (record.entity_id, record.period)
        for record in response.records
    } == {("CHN", "2024"), ("CHN", "2025"), ("USA", "2024")}
    assert all(record.entity_id != "SDN" for record in response.records)
    assert response.cache.cutoff == "2025"
    assert response.cache.cutoff_kind == "data_period"
    assert response.cache.license.state == "unknown"
    assert response.cache.coverage.upstream_total == 3
    assert response.cache.coverage.requested_dimensions["entities"] == [
        "CHN",
        "USA",
    ]
    assert all(
        record.metadata["estimate_status"] == "unknown"
        for record in response.records
    )


def test_un_sdg_uses_one_bounded_page_and_discards_wrong_dimensions() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "size": 3,
                "totalElements": 9,
                "totalPages": 3,
                "pageNumber": 1,
                "data": [
                    {
                        "series": "SI_POV_DAY1",
                        "seriesDescription": "Population below poverty line",
                        "geoAreaCode": "156",
                        "geoAreaName": "China",
                        "timePeriodStart": 2024.0,
                        "value": "0.1",
                        "source": "Custodian agency",
                        "dimensions": {"Sex": "ALL"},
                        "attributes": {"Nature": "C"},
                    },
                    {
                        "series": "SI_POV_DAY1",
                        "geoAreaCode": "840",
                        "timePeriodStart": 2024,
                        "value": "0.2",
                    },
                    {
                        "series": "WRONG_SERIES",
                        "geoAreaCode": "156",
                        "timePeriodStart": 2024,
                        "value": "0.3",
                    },
                ],
            },
        )

    service, _ = _service(handler)
    response = asyncio.run(
        service.un_sdg(
            UnSdgQuery(
                series_code="SI_POV_DAY1",
                area_code=156,
                start_year=2020,
                end_year=2025,
                limit=3,
            )
        )
    )

    params = seen[0].url.params
    assert seen[0].url.path == "/SDGAPI/v1/sdg/Series/Data"
    assert params["seriesCode"] == "SI_POV_DAY1"
    assert params["areaCode"] == "156"
    assert params["page"] == "1"
    assert params["pageSize"] == "3"
    assert params["timePeriodStart"] == "2020"
    assert params["timePeriodEnd"] == "2025"
    assert len(response.records) == 1
    assert response.records[0].entity_id == "156"
    assert response.records[0].metadata["dimension.Sex"] == "ALL"
    assert response.cache.cutoff == "2024"
    assert response.cache.coverage.truncated is True
    assert response.cache.license.state == "unknown"


def test_crossref_keeps_only_safe_metadata_and_a_bounded_first_page() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "message-type": "work-list",
                "message-version": "1.0.0",
                "message": {
                    "total-results": 500,
                    "items": [
                        {
                            "DOI": "10.5555/SAFE-1",
                            "title": ["Climate policy evidence"],
                            "type": "journal-article",
                            "publisher": "Example Publisher",
                            "published": {"date-parts": [[2025, 7, 1]]},
                            "indexed": {"date-time": "2026-08-08T10:00:00Z"},
                            "abstract": "COPYRIGHTED ABSTRACT MUST NOT ESCAPE",
                            "reference": [{"unstructured": "PRIVATE REFERENCE"}],
                            "link": [{"URL": "https://publisher.invalid/fulltext"}],
                        }
                    ],
                },
            },
        )

    service, _ = _service(handler)
    response = asyncio.run(
        service.crossref(
            CrossrefQuery(
                query="climate policy",
                from_index_date=date(2026, 8, 1),
                until_index_date=date(2026, 8, 9),
                limit=1,
            )
        )
    )

    request = seen[0]
    assert request.url.path == "/v1/works"
    assert request.url.params["rows"] == "1"
    assert request.url.params["query.title"] == "climate policy"
    assert request.url.params["filter"] == (
        "from-index-date:2026-08-01,until-index-date:2026-08-09"
    )
    assert "abstract" not in request.url.params["select"].lower()
    serialized = json.dumps(response.model_dump(mode="json"))
    assert "COPYRIGHTED" not in serialized
    assert "PRIVATE REFERENCE" not in serialized
    assert "publisher.invalid" not in serialized
    assert response.records[0].entity_id == "10.5555/safe-1"
    assert response.cache.cutoff == "2026-08-08T10:00:00Z"
    assert response.cache.cutoff_kind == "source_update_time"
    assert response.cache.license.state == "restricted"
    assert response.cache.coverage.truncated is True
    assert response.cache.coverage.requested_dimensions["title_query_sha256"] == [
        hashlib.sha256(b"climate policy").hexdigest()
    ]
    assert "title_query" not in response.cache.coverage.requested_dimensions


def test_failed_refresh_returns_no_stale_records_but_keeps_prior_evidence() -> None:
    state = {"healthy": True}
    current = {"value": NOW}

    def handler(_request: httpx.Request) -> httpx.Response:
        if state["healthy"]:
            return httpx.Response(200, json=_world_bank_payload())
        return httpx.Response(503, json={"error": "unavailable"})

    service, _ = _service(handler, now=lambda: current["value"])
    query = WorldBankQuery(country="CHN", indicator="SP.POP.TOTL", limit=2)

    async def scenario():
        first = await service.world_bank(query)
        state["healthy"] = False
        current["value"] = datetime(2026, 8, 9, 13, 0, tzinfo=timezone.utc)
        failed = await service.world_bank(query, refresh=True)
        return first, failed

    first, failed = asyncio.run(scenario())

    assert first.records
    assert failed.available is False
    assert failed.state == "unavailable"
    assert failed.records == []
    assert failed.reason_codes == ["UPSTREAM_UNAVAILABLE"]
    assert failed.cache.state == "unavailable"
    assert failed.cache.available is False
    assert failed.cache.cutoff == first.cache.cutoff
    assert failed.cache.last_success == first.cache.last_success
    assert failed.cache.payload_sha256 == first.cache.payload_sha256
    assert failed.cache.coverage.returned_records == 0
    assert failed.cache.coverage.state == "unknown"
    assert "no prior records are served" in failed.cache.coverage.scope


def test_transport_rejects_unsafe_hosts_and_decompressed_oversize_payloads() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"payload":"01234567890123456789"}',
        )

    transport = httpx.MockTransport(handler)
    client = BoundedJsonClient(
        transport=transport,
        maximum_response_bytes=20,
    )

    with pytest.raises(UpstreamFailure) as unsafe:
        asyncio.run(client.get_json("https://example.invalid/data"))
    assert unsafe.value.reason_code == "UNSAFE_UPSTREAM_URL"
    assert calls == 0

    with pytest.raises(UpstreamFailure) as oversized:
        asyncio.run(client.get_json("https://api.worldbank.org/v2/test"))
    assert oversized.value.reason_code == "UPSTREAM_PAYLOAD_TOO_LARGE"
    assert calls == 1


@pytest.mark.parametrize(
    "payload",
    (
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b'{"value":1e400}',
        b'{"value":1,"value":2}',
        b"[" * 2_000 + b"0" + b"]" * 2_000,
    ),
)
def test_transport_rejects_non_finite_json_numbers(payload: bytes) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=payload,
        )

    client = BoundedJsonClient(transport=httpx.MockTransport(handler))

    with pytest.raises(UpstreamFailure) as invalid:
        asyncio.run(client.get_json("https://api.worldbank.org/v2/test"))

    assert invalid.value.reason_code == "UPSTREAM_INVALID_JSON"


@pytest.mark.parametrize(
    "url",
    (
        "https://api.worldbank.org/v2/\nrecords",
        "https://api.worldbank.org/v2/\trecords",
        "https://api.worldbank.org/v2/\\records",
    ),
)
def test_transport_rejects_control_or_backslash_url_before_request(url: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    client = BoundedJsonClient(transport=httpx.MockTransport(handler))

    with pytest.raises(UpstreamFailure) as unsafe:
        asyncio.run(client.get_json(url))

    assert unsafe.value.reason_code == "UNSAFE_UPSTREAM_URL"
    assert calls == 0


def test_catalog_is_static_not_a_live_success_claim_and_inputs_are_safe() -> None:
    service, _ = _service(
        lambda _request: pytest.fail("catalog must not access the network")
    )
    catalog = service.catalog()

    assert catalog.available is False
    assert catalog.operational_state == "not_observed"
    assert len(catalog.connectors) == 4
    assert all(connector.available is False for connector in catalog.connectors)
    assert all(connector.live_checked is False for connector in catalog.connectors)
    assert {connector.license.state for connector in catalog.connectors} == {
        "restricted",
        "unknown",
    }

    with pytest.raises(ValidationError):
        ImfQuery(indicator="NGDP_RPCH", entities=["../../etc/passwd"])
    with pytest.raises(ValidationError):
        WorldBankQuery(
            country="CHN",
            indicator="SP.POP.TOTL",
            start_year=2000,
            end_year=2100,
        )


def test_routes_keep_catalog_public_queries_authenticated_and_fail_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.worldbank.org":
            return httpx.Response(200, json=_world_bank_payload())
        return httpx.Response(503, json={"error": "unavailable"})

    service, _ = _service(handler)
    app = FastAPI()
    app.include_router(authoritative_data.router)
    app.dependency_overrides[
        authoritative_data.get_authoritative_data_service
    ] = lambda: service

    with TestClient(app) as unauthenticated:
        catalog = unauthenticated.get("/api/authoritative-data/catalog")
        denied = unauthenticated.get(
            "/api/authoritative-data/world-bank",
            params={"country": "CHN", "indicator": "SP.POP.TOTL"},
        )
    assert catalog.status_code == 200
    assert catalog.json()["operational_state"] == "not_observed"
    assert denied.status_code == 401

    app.dependency_overrides[get_current_user_required] = lambda: {
        "id": 1,
        "role": "user",
    }
    with TestClient(app) as authenticated:
        ok = authenticated.get(
            "/api/authoritative-data/world-bank",
            params={"country": "CHN", "indicator": "SP.POP.TOTL", "limit": 2},
        )
        unavailable = authenticated.get(
            "/api/authoritative-data/crossref",
            params={"query": "climate policy", "limit": 1},
        )
        invalid = authenticated.get(
            "/api/authoritative-data/world-bank",
            params={"country": "../", "indicator": "SP.POP.TOTL"},
        )
        invalid_window = authenticated.get(
            "/api/authoritative-data/world-bank",
            params={
                "country": "CHN",
                "indicator": "SP.POP.TOTL",
                "start_year": 2020,
            },
        )
    assert ok.status_code == 200
    assert ok.json()["available"] is True
    assert unavailable.status_code == 503
    assert unavailable.json()["available"] is False
    assert unavailable.json()["records"] == []
    assert "climate policy" not in json.dumps(unavailable.json())
    assert invalid.status_code == 422
    assert invalid_window.status_code == 422


def test_country_profile_catalog_is_an_explicit_empty_schema_not_country_data() -> None:
    service, _ = _service(
        lambda _request: pytest.fail(
            "country profile catalog must not access the network"
        )
    )

    catalog = service.country_profile_catalog()

    assert catalog.catalog_id == "urn:globemind:country-profile:catalog:v1"
    assert catalog.available is False
    assert catalog.operational_state == "not_configured"
    assert catalog.live_checked is False
    assert catalog.profiles == ()
    assert catalog.profile_schema.country_identifier_standard == "ISO 3166-1 alpha-2"
    assert catalog.profile_schema.profile_identifier_format == (
        "urn:globemind:country-profile:{iso-alpha2-lower}:{sha256}"
    )

    section_ids = [section.section_id for section in catalog.profile_schema.sections]
    field_ids = [field.field_id for field in catalog.profile_schema.fields]
    assert section_ids == [
        "overview",
        "institutions",
        "politics",
        "law_policy",
        "economy",
        "society",
        "security",
        "external_relations",
        "environment",
        "evidence_governance",
    ]
    assert len(section_ids) == len(set(section_ids))
    assert len(field_ids) == len(set(field_ids))
    assert set(field_ids) == {
        field_id
        for section in catalog.profile_schema.sections
        for field_id in section.field_ids
    }
    assert {
        "overview.iso_alpha2",
        "overview.official_name",
        "institutions.system_of_government",
        "politics.elections",
        "law_policy.constitution",
        "economy.gdp",
        "society.population",
        "security.conflict_status",
        "external_relations.memberships",
        "environment.climate",
        "evidence_governance.profile_owner",
        "evidence_governance.last_review",
    } <= set(field_ids)

    evidence = catalog.profile_schema.minimum_evidence
    assert evidence.source_locator == "absolute_https_url"
    assert evidence.source_authority == "required"
    assert evidence.source_cutoff == "required_utc_datetime_or_period"
    assert evidence.future_source_cutoff_policy == "fail_closed"
    assert evidence.license_state == "verified_or_restricted"
    assert evidence.owner_role == "country-data-stewardship"
    assert evidence.owner_identifier == "required_stable_identifier"
    assert evidence.review_state == "approved"
    assert evidence.review_expires_at == "required_future_utc_datetime"
    assert evidence.future_review_policy == "fail_closed"
    assert evidence.expired_review_policy == "fail_closed"
    assert evidence.invalid_evidence_policy == "fail_closed"
    assert catalog.reason_codes == (
        "PILOT_COUNTRIES_NOT_SELECTED",
        "COUNTRY_PROFILES_NOT_CONFIGURED",
        "SOURCE_AND_CUTOFF_EVIDENCE_NOT_CONFIGURED",
        "LICENSE_EVIDENCE_NOT_CONFIGURED",
        "OWNER_AND_REVIEW_NOT_CONFIGURED",
    )


def test_country_profile_catalog_contract_rejects_claim_inflation_and_drift() -> None:
    service, _ = _service(lambda _request: pytest.fail("no network expected"))
    payload = service.country_profile_catalog().model_dump(mode="json")

    for field, value in (
        ("available", True),
        ("operational_state", "available"),
        ("live_checked", True),
        ("profiles", [{"country_code": "ZZ"}]),
        ("reason_codes", ["COUNTRY_PROFILES_NOT_CONFIGURED"]),
        ("generated_at", "2026-08-09T12:00:00"),
    ):
        mutated = {**payload, field: value}
        with pytest.raises(ValidationError):
            CountryProfileCatalogResponse.model_validate(mutated)

    duplicated_schema = payload["profile_schema"]
    duplicated_schema["sections"] = [
        *duplicated_schema["sections"],
        duplicated_schema["sections"][0],
    ]
    with pytest.raises(ValidationError):
        CountryProfileSchemaDescriptor.model_validate(duplicated_schema)

    unbounded_schema = service.country_profile_catalog().profile_schema.model_dump(
        mode="json"
    )
    unbounded_schema["sections"][0]["field_ids"][0] = "overview." + "x" * 100
    with pytest.raises(ValidationError):
        CountryProfileSchemaDescriptor.model_validate(unbounded_schema)


def test_country_profile_catalog_route_is_public_static_and_read_only() -> None:
    service, _ = _service(
        lambda _request: pytest.fail(
            "country profile catalog must not access the network"
        )
    )
    app = FastAPI()
    app.include_router(authoritative_data.router)
    app.dependency_overrides[
        authoritative_data.get_authoritative_data_service
    ] = lambda: service

    with TestClient(app) as client:
        response = client.get("/api/authoritative-data/country-profiles/catalog")
        write_attempt = client.post(
            "/api/authoritative-data/country-profiles/catalog",
            json={"profiles": []},
        )

    assert response.status_code == 200
    assert write_attempt.status_code == 405
    body = response.json()
    assert body["available"] is False
    assert body["operational_state"] == "not_configured"
    assert body["profiles"] == []
    assert body["reason_codes"] == [
        "PILOT_COUNTRIES_NOT_SELECTED",
        "COUNTRY_PROFILES_NOT_CONFIGURED",
        "SOURCE_AND_CUTOFF_EVIDENCE_NOT_CONFIGURED",
        "LICENSE_EVIDENCE_NOT_CONFIGURED",
        "OWNER_AND_REVIEW_NOT_CONFIGURED",
    ]
