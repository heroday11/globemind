"""Versioned, syntax-only geography contracts with no bundled geography facts.

The normalizers in this module preserve explicitly supplied structured values.
They do not query an authority, geocode text, infer countries from language or
publishers, verify coordinates, or merge the four country roles.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GEOGRAPHY_SCHEMA_VERSION = "globemind.geography-semantics.v1"
GEOGRAPHY_DIMENSIONS_SCHEMA_VERSION = "globemind.geography-dimensions.v1"
GEOGRAPHY_CONTRACT_VERSION = "1.0.0"
GEOGRAPHY_CATALOG_ID = "urn:globemind:geography-semantics:catalog:v1"

IdentifierNamespace = Literal[
    "globemind_entity_urn",
    "iso_3166_1_alpha2",
    "iso_3166_2",
    "geonames",
    "wikidata",
]
GeographicScope = Literal["country", "administrative_area", "locality", "point"]
CoordinatePrecision = Literal[
    "unknown",
    "reported_point",
    "reported_admin_centroid",
    "reported_country_centroid",
    "reported_bounding_box_center",
]
CountryRole = Literal[
    "source_country",
    "audience_country",
    "event_country",
    "mentioned_country",
]
GeographyReasonCode = Literal[
    "AUTHORITY_DATA_NOT_CONFIGURED",
    "LICENSE_REVIEW_NOT_CONFIGURED",
    "COORDINATE_ACCURACY_NOT_MEASURED",
    "ROLE_BACKFILL_NOT_CONFIGURED",
    "HUMAN_REVIEW_NOT_CONFIGURED",
]

GEOGRAPHY_REASON_CODES: tuple[GeographyReasonCode, ...] = (
    "AUTHORITY_DATA_NOT_CONFIGURED",
    "LICENSE_REVIEW_NOT_CONFIGURED",
    "COORDINATE_ACCURACY_NOT_MEASURED",
    "ROLE_BACKFILL_NOT_CONFIGURED",
    "HUMAN_REVIEW_NOT_CONFIGURED",
)

_IDENTIFIER_PATTERNS: dict[str, re.Pattern[str]] = {
    "globemind_entity_urn": re.compile(
        r"^urn:globemind:entity:(country|location):"
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    ),
    "iso_3166_1_alpha2": re.compile(r"^[A-Z]{2}$"),
    "iso_3166_2": re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,3}$"),
    "geonames": re.compile(r"^[1-9][0-9]{0,19}$"),
    "wikidata": re.compile(r"^Q[1-9][0-9]*$"),
}
_REFERENCE_ID = re.compile(r"^urn:globemind:geo-reference:[0-9a-f]{64}$")
_COUNTRY_ROLES: tuple[CountryRole, ...] = (
    "source_country",
    "audience_country",
    "event_country",
    "mentioned_country",
)
_MAX_REFERENCES_PER_ROLE = 32
_MAX_REFERENCES_TOTAL = 64


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class GeographicReferenceInput(_StrictModel):
    """One caller-supplied identifier and optional reported coordinate pair."""

    identifier_namespace: IdentifierNamespace
    identifier: str = Field(min_length=1, max_length=180)
    declared_scope: GeographicScope
    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        allow_inf_nan=False,
        strict=True,
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        allow_inf_nan=False,
        strict=True,
    )
    coordinate_precision: CoordinatePrecision = "unknown"
    uncertainty_meters: float | None = Field(
        default=None,
        gt=0,
        le=40_100_000,
        allow_inf_nan=False,
        strict=True,
    )
    input_provenance: Literal["caller_supplied_structured"]

    @field_validator("identifier")
    @classmethod
    def identifier_is_plain_text(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("geographic identifier contains control characters")
        return value

    @model_validator(mode="after")
    def identifier_and_scope_are_syntactically_consistent(
        self,
    ) -> "GeographicReferenceInput":
        pattern = _IDENTIFIER_PATTERNS[self.identifier_namespace]
        match = pattern.fullmatch(self.identifier)
        if match is None:
            raise ValueError("geographic identifier syntax is invalid")

        if self.identifier_namespace == "iso_3166_1_alpha2":
            if self.declared_scope != "country":
                raise ValueError("ISO alpha-2 identifiers require country scope")
        elif self.identifier_namespace == "iso_3166_2":
            if self.declared_scope != "administrative_area":
                raise ValueError("ISO 3166-2 identifiers require administrative-area scope")
        elif self.identifier_namespace == "globemind_entity_urn":
            entity_type = match.group(1)
            if entity_type == "country" and self.declared_scope != "country":
                raise ValueError("country entity URNs require country scope")
            if entity_type == "location" and self.declared_scope == "country":
                raise ValueError("location entity URNs cannot claim country scope")

        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_latitude != has_longitude:
            raise ValueError("coordinates require both latitude and longitude")
        if not has_latitude:
            if self.coordinate_precision != "unknown" or self.uncertainty_meters is not None:
                raise ValueError("coordinate metadata requires a coordinate pair")
            return self

        if self.coordinate_precision == "unknown":
            raise ValueError("reported coordinates require an explicit precision level")
        if self.uncertainty_meters is None:
            raise ValueError("reported coordinates require bounded uncertainty")
        if (
            self.coordinate_precision == "reported_admin_centroid"
            and self.declared_scope != "administrative_area"
        ):
            raise ValueError("admin centroids require administrative-area scope")
        if (
            self.coordinate_precision == "reported_country_centroid"
            and self.declared_scope != "country"
        ):
            raise ValueError("country centroids require country scope")
        return self


class NormalizedGeographicReference(_StrictModel):
    schema_version: Literal["globemind.geography-semantics.v1"] = (
        GEOGRAPHY_SCHEMA_VERSION
    )
    reference_id: str = Field(pattern=r"^urn:globemind:geo-reference:[0-9a-f]{64}$")
    identifier_namespace: IdentifierNamespace
    identifier: str
    declared_scope: GeographicScope
    latitude: float | None
    longitude: float | None
    coordinate_precision: CoordinatePrecision
    uncertainty_meters: float | None
    input_provenance: Literal["caller_supplied_structured"]
    identifier_validation_state: Literal["syntax_only"] = "syntax_only"
    authority_mapping_state: Literal["not_configured"] = "not_configured"
    coordinate_validation_state: Literal["not_provided", "not_verified"]
    accuracy_claim: Literal["not_measured"] = "not_measured"
    human_review_state: Literal["not_reviewed"] = "not_reviewed"
    canonical_name: None = None

    @field_validator("reference_id")
    @classmethod
    def validate_reference_id(cls, value: str) -> str:
        if _REFERENCE_ID.fullmatch(value) is None:
            raise ValueError("geographic reference ID is invalid")
        return value


class GeographicDimensionsInput(_StrictModel):
    """Four explicit country roles; absence means unknown, never empty fact."""

    source_country: list[GeographicReferenceInput] | None = Field(
        default=None,
        max_length=_MAX_REFERENCES_PER_ROLE,
    )
    audience_country: list[GeographicReferenceInput] | None = Field(
        default=None,
        max_length=_MAX_REFERENCES_PER_ROLE,
    )
    event_country: list[GeographicReferenceInput] | None = Field(
        default=None,
        max_length=_MAX_REFERENCES_PER_ROLE,
    )
    mentioned_country: list[GeographicReferenceInput] | None = Field(
        default=None,
        max_length=_MAX_REFERENCES_PER_ROLE,
    )

    @model_validator(mode="after")
    def inventory_is_bounded_explicit_and_country_scoped(
        self,
    ) -> "GeographicDimensionsInput":
        total = 0
        for role in _COUNTRY_ROLES:
            references = getattr(self, role) or []
            total += len(references)
            identifiers: set[tuple[str, str]] = set()
            for reference in references:
                if reference.declared_scope != "country":
                    raise ValueError(f"{role} references require declared country scope")
                if (
                    reference.latitude is not None
                    and reference.coordinate_precision
                    not in {
                        "reported_country_centroid",
                        "reported_bounding_box_center",
                    }
                ):
                    raise ValueError(
                        f"{role} coordinates require an explicit country-level precision"
                    )
                key = (reference.identifier_namespace, reference.identifier)
                if key in identifiers:
                    raise ValueError(f"{role} contains a duplicate geographic identifier")
                identifiers.add(key)
        if total > _MAX_REFERENCES_TOTAL:
            raise ValueError("geographic dimension inventory exceeds its total limit")
        return self


class GeographicRoleDimension(_StrictModel):
    role: CountryRole
    state: Literal["unknown", "reported_unverified"]
    references: tuple[NormalizedGeographicReference, ...]
    inference_policy: Literal["explicit_only"] = "explicit_only"

    @model_validator(mode="after")
    def state_matches_inventory(self) -> "GeographicRoleDimension":
        expected = "reported_unverified" if self.references else "unknown"
        if self.state != expected:
            raise ValueError("geographic dimension state contradicts its inventory")
        return self


class NormalizedGeographicDimensions(_StrictModel):
    schema_version: Literal["globemind.geography-dimensions.v1"] = (
        GEOGRAPHY_DIMENSIONS_SCHEMA_VERSION
    )
    role_semantics_version: Literal["1.0.0"] = GEOGRAPHY_CONTRACT_VERSION
    source_country: GeographicRoleDimension
    audience_country: GeographicRoleDimension
    event_country: GeographicRoleDimension
    mentioned_country: GeographicRoleDimension
    cross_role_merge_performed: Literal[False] = False
    authority_data_checked: Literal[False] = False
    coordinates_verified: Literal[False] = False
    accuracy_claim: Literal["not_measured"] = "not_measured"


class IdentifierNamespaceDescriptor(_StrictModel):
    namespace: IdentifierNamespace
    identifier_pattern: str = Field(min_length=1, max_length=240)
    validation_scope: Literal["syntax_only"] = "syntax_only"
    authority_mapping_available: Literal[False] = False


class CoordinatePrecisionDescriptor(_StrictModel):
    level: CoordinatePrecision
    semantics: str = Field(min_length=1, max_length=300)
    verified_accuracy_available: Literal[False] = False


class CountryRoleDescriptor(_StrictModel):
    role: CountryRole
    semantics: str = Field(min_length=1, max_length=400)
    cross_role_inference: Literal["forbidden"] = "forbidden"
    free_text_inference: Literal["forbidden"] = "forbidden"


class GeographySchemaDescriptor(_StrictModel):
    schema_id: Literal["urn:globemind:geography-semantics:schema:v1"] = (
        "urn:globemind:geography-semantics:schema:v1"
    )
    reference_schema_version: Literal["globemind.geography-semantics.v1"] = (
        GEOGRAPHY_SCHEMA_VERSION
    )
    dimension_schema_version: Literal["globemind.geography-dimensions.v1"] = (
        GEOGRAPHY_DIMENSIONS_SCHEMA_VERSION
    )
    identifier_namespaces: tuple[IdentifierNamespaceDescriptor, ...]
    coordinate_precision_levels: tuple[CoordinatePrecisionDescriptor, ...]
    country_roles: tuple[CountryRoleDescriptor, ...]
    maximum_references_per_role: Literal[32] = _MAX_REFERENCES_PER_ROLE
    maximum_references_total: Literal[64] = _MAX_REFERENCES_TOTAL
    unknown_policy: Literal["preserve_unknown"] = "preserve_unknown"
    identifier_policy: Literal["syntax_is_not_authority_attestation"] = (
        "syntax_is_not_authority_attestation"
    )
    coordinate_policy: Literal["reported_is_not_verified"] = (
        "reported_is_not_verified"
    )

    @model_validator(mode="after")
    def descriptor_inventory_is_exact(self) -> "GeographySchemaDescriptor":
        namespaces = [item.namespace for item in self.identifier_namespaces]
        precision_levels = [item.level for item in self.coordinate_precision_levels]
        roles = [item.role for item in self.country_roles]
        if namespaces != list(_IDENTIFIER_PATTERNS):
            raise ValueError("identifier namespace inventory is incomplete or reordered")
        if precision_levels != [
            "unknown",
            "reported_point",
            "reported_admin_centroid",
            "reported_country_centroid",
            "reported_bounding_box_center",
        ]:
            raise ValueError("coordinate precision inventory is incomplete or reordered")
        if roles != list(_COUNTRY_ROLES):
            raise ValueError("country role inventory is incomplete or reordered")
        return self


class GeographySemanticsCatalog(_StrictModel):
    catalog_id: Literal["urn:globemind:geography-semantics:catalog:v1"] = (
        GEOGRAPHY_CATALOG_ID
    )
    schema_version: Literal["globemind.geography-semantics.v1"] = (
        GEOGRAPHY_SCHEMA_VERSION
    )
    contract_version: Literal["1.0.0"] = GEOGRAPHY_CONTRACT_VERSION
    generated_at: datetime
    available: Literal[False] = False
    operational_state: Literal["not_configured"] = "not_configured"
    live_checked: Literal[False] = False
    implementation_scope: Literal["schema_catalog_and_syntax_normalizer_only"] = (
        "schema_catalog_and_syntax_normalizer_only"
    )
    accuracy_claim: Literal["not_measured"] = "not_measured"
    human_review_state: Literal["not_configured"] = "not_configured"
    license_review_state: Literal["not_configured"] = "not_configured"
    geography_schema: GeographySchemaDescriptor
    authority_mappings: tuple[()] = ()
    profiles: tuple[()] = ()
    reason_codes: tuple[GeographyReasonCode, ...] = GEOGRAPHY_REASON_CODES

    @field_validator("generated_at")
    @classmethod
    def generated_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("catalog generated_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def reason_inventory_is_exact(self) -> "GeographySemanticsCatalog":
        if self.reason_codes != GEOGRAPHY_REASON_CODES:
            raise ValueError("geography reason code inventory is incomplete or reordered")
        return self


def normalize_geographic_reference(
    value: GeographicReferenceInput | dict[str, Any],
) -> NormalizedGeographicReference:
    """Validate syntax and add a stable ID without asserting geographic truth."""

    source = (
        value
        if isinstance(value, GeographicReferenceInput)
        else GeographicReferenceInput.model_validate(value)
    )
    canonical = json.dumps(
        source.model_dump(mode="json"),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    reference_id = "urn:globemind:geo-reference:" + hashlib.sha256(canonical).hexdigest()
    coordinates_provided = source.latitude is not None
    return NormalizedGeographicReference(
        reference_id=reference_id,
        identifier_namespace=source.identifier_namespace,
        identifier=source.identifier,
        declared_scope=source.declared_scope,
        latitude=source.latitude,
        longitude=source.longitude,
        coordinate_precision=source.coordinate_precision,
        uncertainty_meters=source.uncertainty_meters,
        input_provenance=source.input_provenance,
        coordinate_validation_state=(
            "not_verified" if coordinates_provided else "not_provided"
        ),
    )


def _normalize_role(
    role: CountryRole,
    values: list[GeographicReferenceInput] | None,
) -> GeographicRoleDimension:
    references = tuple(normalize_geographic_reference(value) for value in values or [])
    return GeographicRoleDimension(
        role=role,
        state="reported_unverified" if references else "unknown",
        references=references,
    )


def normalize_geographic_dimensions(
    value: GeographicDimensionsInput | dict[str, Any],
) -> NormalizedGeographicDimensions:
    """Normalize four caller-declared roles without cross-role or text inference."""

    source = (
        value
        if isinstance(value, GeographicDimensionsInput)
        else GeographicDimensionsInput.model_validate(value)
    )
    return NormalizedGeographicDimensions(
        source_country=_normalize_role("source_country", source.source_country),
        audience_country=_normalize_role("audience_country", source.audience_country),
        event_country=_normalize_role("event_country", source.event_country),
        mentioned_country=_normalize_role("mentioned_country", source.mentioned_country),
    )


def _schema_descriptor() -> GeographySchemaDescriptor:
    return GeographySchemaDescriptor(
        identifier_namespaces=tuple(
            IdentifierNamespaceDescriptor(
                namespace=namespace,
                identifier_pattern=pattern.pattern,
            )
            for namespace, pattern in _IDENTIFIER_PATTERNS.items()
        ),
        coordinate_precision_levels=(
            CoordinatePrecisionDescriptor(
                level="unknown",
                semantics="No coordinate pair was supplied.",
            ),
            CoordinatePrecisionDescriptor(
                level="reported_point",
                semantics="Caller reports a point; the platform has not verified it.",
            ),
            CoordinatePrecisionDescriptor(
                level="reported_admin_centroid",
                semantics="Caller reports an administrative centroid; boundaries are not loaded.",
            ),
            CoordinatePrecisionDescriptor(
                level="reported_country_centroid",
                semantics="Caller reports a country centroid; it is not an event coordinate.",
            ),
            CoordinatePrecisionDescriptor(
                level="reported_bounding_box_center",
                semantics="Caller reports a bounding-box center; the box is not loaded or verified.",
            ),
        ),
        country_roles=(
            CountryRoleDescriptor(
                role="source_country",
                semantics="Country explicitly attached to the publishing source, not the event or audience.",
            ),
            CountryRoleDescriptor(
                role="audience_country",
                semantics="Country explicitly attached to intended audience metadata, not inferred from language.",
            ),
            CountryRoleDescriptor(
                role="event_country",
                semantics="Country explicitly attached to the event location, not inferred from the source.",
            ),
            CountryRoleDescriptor(
                role="mentioned_country",
                semantics="Country explicitly supplied by an upstream extractor, not derived here from text.",
            ),
        ),
    )


def geography_semantics_catalog(
    *,
    generated_at: datetime | None = None,
) -> GeographySemanticsCatalog:
    """Return a fresh empty catalog; this performs no I/O or live lookup."""

    return GeographySemanticsCatalog(
        generated_at=generated_at or datetime.now(timezone.utc),
        geography_schema=_schema_descriptor(),
    )


__all__ = (
    "GEOGRAPHY_CATALOG_ID",
    "GEOGRAPHY_CONTRACT_VERSION",
    "GEOGRAPHY_DIMENSIONS_SCHEMA_VERSION",
    "GEOGRAPHY_REASON_CODES",
    "GEOGRAPHY_SCHEMA_VERSION",
    "GeographicDimensionsInput",
    "GeographicReferenceInput",
    "GeographySemanticsCatalog",
    "NormalizedGeographicDimensions",
    "NormalizedGeographicReference",
    "geography_semantics_catalog",
    "normalize_geographic_dimensions",
    "normalize_geographic_reference",
)
