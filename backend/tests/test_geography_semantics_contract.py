from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from api.features import entity_governance


def _symbol(name: str):
    value = getattr(entity_governance, name, None)
    assert value is not None, f"entity-governance facade is missing {name}"
    return value


def _country(
    identifier: str,
    *,
    namespace: str = "iso_3166_1_alpha2",
) -> dict:
    return {
        "identifier_namespace": namespace,
        "identifier": identifier,
        "declared_scope": "country",
        "input_provenance": "caller_supplied_structured",
    }


def test_geography_catalog_is_versioned_empty_and_explicitly_not_configured():
    build_catalog = _symbol("geography_semantics_catalog")
    generated_at = datetime(2026, 8, 9, 20, 0, tzinfo=timezone.utc)

    payload = build_catalog(generated_at=generated_at).model_dump(mode="json")

    assert payload["catalog_id"] == "urn:globemind:geography-semantics:catalog:v1"
    assert payload["schema_version"] == "globemind.geography-semantics.v1"
    assert payload["generated_at"] == "2026-08-09T20:00:00Z"
    assert payload["available"] is False
    assert payload["operational_state"] == "not_configured"
    assert payload["live_checked"] is False
    assert payload["implementation_scope"] == "schema_catalog_and_syntax_normalizer_only"
    assert payload["authority_mappings"] == []
    assert payload["profiles"] == []
    assert payload["accuracy_claim"] == "not_measured"
    assert payload["human_review_state"] == "not_configured"
    assert payload["license_review_state"] == "not_configured"
    assert payload["reason_codes"] == [
        "AUTHORITY_DATA_NOT_CONFIGURED",
        "LICENSE_REVIEW_NOT_CONFIGURED",
        "COORDINATE_ACCURACY_NOT_MEASURED",
        "ROLE_BACKFILL_NOT_CONFIGURED",
        "HUMAN_REVIEW_NOT_CONFIGURED",
    ]
    serialized = json.dumps(payload, sort_keys=True)
    assert '"Q30"' not in serialized
    assert '"US"' not in serialized


def test_catalog_defines_identifier_precision_and_four_non_interchangeable_roles():
    payload = _symbol("geography_semantics_catalog")().model_dump(mode="json")

    namespaces = payload["geography_schema"]["identifier_namespaces"]
    assert [item["namespace"] for item in namespaces] == [
        "globemind_entity_urn",
        "iso_3166_1_alpha2",
        "iso_3166_2",
        "geonames",
        "wikidata",
    ]
    assert all(item["validation_scope"] == "syntax_only" for item in namespaces)
    assert all(item["authority_mapping_available"] is False for item in namespaces)

    assert [item["level"] for item in payload["geography_schema"]["coordinate_precision_levels"]] == [
        "unknown",
        "reported_point",
        "reported_admin_centroid",
        "reported_country_centroid",
        "reported_bounding_box_center",
    ]
    assert [item["role"] for item in payload["geography_schema"]["country_roles"]] == [
        "source_country",
        "audience_country",
        "event_country",
        "mentioned_country",
    ]
    assert all(
        item["cross_role_inference"] == "forbidden"
        for item in payload["geography_schema"]["country_roles"]
    )


@pytest.mark.parametrize(
    "payload",
    [
        _country("us"),
        _country("USA"),
        _country("Q0", namespace="wikidata"),
        _country("q30", namespace="wikidata"),
        _country("0", namespace="geonames"),
        _country("US-", namespace="iso_3166_2"),
        {
            **_country("US"),
            "declared_scope": "locality",
        },
        {
            "identifier_namespace": "globemind_entity_urn",
            "identifier": "urn:globemind:entity:person:alice",
            "declared_scope": "locality",
            "input_provenance": "caller_supplied_structured",
        },
        {
            **_country("US"),
            "identifier": True,
        },
        _country(" US "),
        {
            **_country("US"),
            "identifier_namespace": " iso_3166_1_alpha2 ",
        },
        {
            **_country("US"),
            "canonical_name": "United States",
        },
    ],
)
def test_reference_contract_rejects_invalid_or_scope_ambiguous_identifiers(payload):
    request_type = _symbol("GeographicReferenceInput")
    with pytest.raises(ValidationError):
        request_type.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"latitude": 10.0},
        {"longitude": 10.0},
        {
            "latitude": 91.0,
            "longitude": 10.0,
            "coordinate_precision": "reported_point",
            "uncertainty_meters": 100.0,
        },
        {
            "latitude": float("nan"),
            "longitude": 10.0,
            "coordinate_precision": "reported_point",
            "uncertainty_meters": 100.0,
        },
        {
            "latitude": True,
            "longitude": 10.0,
            "coordinate_precision": "reported_point",
            "uncertainty_meters": 100.0,
        },
        {
            "latitude": 10.0,
            "longitude": 20.0,
            "coordinate_precision": "unknown",
            "uncertainty_meters": 100.0,
        },
        {
            "latitude": 10.0,
            "longitude": 20.0,
            "coordinate_precision": "reported_point",
        },
        {"coordinate_precision": "reported_admin_centroid"},
        {"uncertainty_meters": 100.0},
    ],
)
def test_coordinate_contract_requires_a_bounded_pair_precision_and_uncertainty(overrides):
    request_type = _symbol("GeographicReferenceInput")
    payload = {
        "identifier_namespace": "geonames",
        "identifier": "123",
        "declared_scope": "locality",
        "input_provenance": "caller_supplied_structured",
        **overrides,
    }
    with pytest.raises(ValidationError):
        request_type.model_validate(payload)


def test_reference_normalizer_is_stable_but_never_claims_authority_or_accuracy():
    normalize = _symbol("normalize_geographic_reference")
    source = {
        "identifier_namespace": "geonames",
        "identifier": "123",
        "declared_scope": "locality",
        "latitude": 40.5,
        "longitude": -73.5,
        "coordinate_precision": "reported_point",
        "uncertainty_meters": 250.0,
        "input_provenance": "caller_supplied_structured",
    }

    first = normalize(source).model_dump(mode="json")
    second = normalize(dict(source)).model_dump(mode="json")

    assert first == second
    assert first["reference_id"].startswith("urn:globemind:geo-reference:")
    assert len(first["reference_id"].rsplit(":", 1)[1]) == 64
    assert first["identifier_validation_state"] == "syntax_only"
    assert first["authority_mapping_state"] == "not_configured"
    assert first["coordinate_validation_state"] == "not_verified"
    assert first["accuracy_claim"] == "not_measured"
    assert first["human_review_state"] == "not_reviewed"
    assert first["canonical_name"] is None
    assert first["input_provenance"] == "caller_supplied_structured"
    assert source["identifier"] == "123"


def test_country_centroid_is_preserved_as_reported_and_never_marked_verified():
    normalize = _symbol("normalize_geographic_reference")
    source = {
        **_country("US"),
        "latitude": 39.8,
        "longitude": -98.6,
        "coordinate_precision": "reported_country_centroid",
        "uncertainty_meters": 2_500_000.0,
    }

    payload = normalize(source).model_dump(mode="json")

    assert payload["latitude"] == 39.8
    assert payload["longitude"] == -98.6
    assert payload["coordinate_precision"] == "reported_country_centroid"
    assert payload["coordinate_validation_state"] == "not_verified"
    assert payload["accuracy_claim"] == "not_measured"


@pytest.mark.parametrize(
    "source",
    [
        _country("US"),
        {
            "identifier_namespace": "iso_3166_2",
            "identifier": "US-CA",
            "declared_scope": "administrative_area",
            "input_provenance": "caller_supplied_structured",
        },
        _country("6252001", namespace="geonames"),
        _country("Q30", namespace="wikidata"),
        {
            "identifier_namespace": "globemind_entity_urn",
            "identifier": "urn:globemind:entity:country:US",
            "declared_scope": "country",
            "input_provenance": "caller_supplied_structured",
        },
        {
            "identifier_namespace": "globemind_entity_urn",
            "identifier": "urn:globemind:entity:location:example-place",
            "declared_scope": "locality",
            "input_provenance": "caller_supplied_structured",
        },
    ],
)
def test_supported_identifier_namespaces_are_only_syntax_normalized(source):
    payload = _symbol("normalize_geographic_reference")(source).model_dump(mode="json")
    assert payload["identifier"] == source["identifier"]
    assert payload["identifier_validation_state"] == "syntax_only"
    assert payload["authority_mapping_state"] == "not_configured"
    assert payload["accuracy_claim"] == "not_measured"


def test_four_country_dimensions_remain_separate_and_missing_roles_stay_unknown():
    normalize = _symbol("normalize_geographic_dimensions")
    source = {
        "source_country": [_country("US")],
        "event_country": [_country("FR")],
        "mentioned_country": [_country("US"), _country("CN")],
    }

    payload = normalize(source).model_dump(mode="json")

    assert payload["schema_version"] == "globemind.geography-dimensions.v1"
    assert payload["source_country"]["state"] == "reported_unverified"
    assert [item["identifier"] for item in payload["source_country"]["references"]] == ["US"]
    assert payload["audience_country"] == {
        "role": "audience_country",
        "state": "unknown",
        "references": [],
        "inference_policy": "explicit_only",
    }
    assert [item["identifier"] for item in payload["event_country"]["references"]] == ["FR"]
    assert [item["identifier"] for item in payload["mentioned_country"]["references"]] == ["US", "CN"]
    assert payload["cross_role_merge_performed"] is False
    assert payload["authority_data_checked"] is False
    assert payload["coordinates_verified"] is False
    assert payload["accuracy_claim"] == "not_measured"
    assert source["source_country"][0]["identifier"] == "US"

    unknown = normalize({}).model_dump(mode="json")
    for role in (
        "source_country",
        "audience_country",
        "event_country",
        "mentioned_country",
    ):
        assert unknown[role]["state"] == "unknown"
        assert unknown[role]["references"] == []


def test_dimension_contract_rejects_free_text_duplicates_and_non_country_scope():
    request_type = _symbol("GeographicDimensionsInput")
    invalid_payloads = [
        {"country": "US"},
        {"location": "Paris"},
        {"source_country": ["US"]},
        {"source_country": [_country("US"), _country("US")]},
        {
            "event_country": [
                {
                    "identifier_namespace": "geonames",
                    "identifier": "123",
                    "declared_scope": "locality",
                    "input_provenance": "caller_supplied_structured",
                }
            ]
        },
        {
            "event_country": [
                {
                    **_country("US"),
                    "latitude": 40.0,
                    "longitude": -75.0,
                    "coordinate_precision": "reported_point",
                    "uncertainty_meters": 1000.0,
                }
            ]
        },
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            request_type.model_validate(payload)


def test_dimension_inventory_is_bounded_per_role_and_across_roles():
    request_type = _symbol("GeographicDimensionsInput")
    thirty_three = [
        _country(str(index + 1), namespace="geonames") for index in range(33)
    ]
    with pytest.raises(ValidationError):
        request_type.model_validate({"mentioned_country": thirty_three})

    too_many_total = {
        role: [
            _country(str(offset + index + 1), namespace="geonames")
            for index in range(17)
        ]
        for role, offset in (
            ("source_country", 0),
            ("audience_country", 100),
            ("event_country", 200),
            ("mentioned_country", 300),
        )
    }
    with pytest.raises(ValidationError):
        request_type.model_validate(too_many_total)
