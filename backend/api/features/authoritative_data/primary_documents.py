"""Schema-only country primary-document catalog with no bundled documents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COUNTRY_PRIMARY_DOCUMENT_SCHEMA_VERSION = "globemind.country-primary-document.v1"
COUNTRY_PRIMARY_DOCUMENT_CONTRACT_VERSION = "1.0.0"
COUNTRY_PRIMARY_DOCUMENT_CATALOG_ID = (
    "urn:globemind:country-primary-document:catalog:v1"
)

CountryPrimaryDocumentReasonCodes = tuple[
    Literal["PILOT_COUNTRIES_NOT_SELECTED"],
    Literal["PRIMARY_DOCUMENTS_NOT_CONFIGURED"],
    Literal["OFFICIAL_SOURCE_EVIDENCE_NOT_CONFIGURED"],
    Literal["LICENSE_EVIDENCE_NOT_CONFIGURED"],
    Literal["OWNER_NOT_CONFIGURED"],
    Literal["REVIEWER_NOT_CONFIGURED"],
]

COUNTRY_PRIMARY_DOCUMENT_REASON_CODES: CountryPrimaryDocumentReasonCodes = (
    "PILOT_COUNTRIES_NOT_SELECTED",
    "PRIMARY_DOCUMENTS_NOT_CONFIGURED",
    "OFFICIAL_SOURCE_EVIDENCE_NOT_CONFIGURED",
    "LICENSE_EVIDENCE_NOT_CONFIGURED",
    "OWNER_NOT_CONFIGURED",
    "REVIEWER_NOT_CONFIGURED",
)

_DOCUMENT_KINDS = (
    "constitution",
    "statute",
    "regulation",
    "official_gazette",
    "judicial_decision",
    "policy_document",
    "treaty",
)
_REQUIRED_FIELDS = (
    "identity.country_code",
    "identity.issuing_authority",
    "identity.official_identifier",
    "identity.document_kind",
    "identity.original_title",
    "text.original_language",
    "text.official_locator",
    "text.section_anchor",
    "text.content_sha256",
    "temporal.issued_at",
    "temporal.effective_from",
    "temporal.effective_until",
    "temporal.status_as_of",
    "version.version_identifier",
    "version.amends",
    "version.amended_by",
    "version.supersedes",
    "version.superseded_by",
    "governance.retrieved_at",
    "governance.source_cutoff",
    "governance.license_state",
    "governance.owner_identifier",
    "governance.reviewer_identifier",
    "governance.reviewed_at",
    "governance.review_expires_at",
)


class CountryPrimaryDocumentEvidenceRequirements(BaseModel):
    """Minimum future document gate; this catalog supplies no evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    country_identifier: Literal["ISO_3166_1_ALPHA_2"] = "ISO_3166_1_ALPHA_2"
    official_locator: Literal["absolute_https_url"] = "absolute_https_url"
    official_source_authority: Literal["required"] = "required"
    original_language: Literal["required_bcp47"] = "required_bcp47"
    content_sha256: Literal["required_lowercase_hex"] = "required_lowercase_hex"
    section_anchor: Literal["required_for_claim_citation"] = (
        "required_for_claim_citation"
    )
    retrieved_at: Literal["required_utc_datetime"] = "required_utc_datetime"
    source_cutoff: Literal["required_utc_datetime_or_period"] = (
        "required_utc_datetime_or_period"
    )
    legal_effective_period: Literal["required_and_not_inferred"] = (
        "required_and_not_inferred"
    )
    version_relationships: Literal["explicit_or_unknown"] = "explicit_or_unknown"
    translation_state: Literal["original_machine_or_human_reviewed"] = (
        "original_machine_or_human_reviewed"
    )
    license_state: Literal["verified_or_restricted"] = "verified_or_restricted"
    owner_role: Literal["country-data-stewardship"] = "country-data-stewardship"
    owner_identifier: Literal["required_stable_identifier"] = (
        "required_stable_identifier"
    )
    reviewer_identifier: Literal["required_stable_identifier"] = (
        "required_stable_identifier"
    )
    review_state: Literal["approved"] = "approved"
    review_expires_at: Literal["required_future_utc_datetime"] = (
        "required_future_utc_datetime"
    )
    invalid_or_expired_policy: Literal["fail_closed"] = "fail_closed"


class CountryPrimaryDocumentSchemaDescriptor(BaseModel):
    """Exact v1 structure for future country primary-source records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "urn:globemind:country-primary-document:schema:v1"
    ] = "urn:globemind:country-primary-document:schema:v1"
    schema_version: Literal[
        "globemind.country-primary-document.v1"
    ] = COUNTRY_PRIMARY_DOCUMENT_SCHEMA_VERSION
    document_identifier_format: Literal[
        "urn:globemind:country-document:{iso-alpha2-lower}:{sha256}"
    ] = "urn:globemind:country-document:{iso-alpha2-lower}:{sha256}"
    document_kinds: tuple[
        Literal[
            "constitution",
            "statute",
            "regulation",
            "official_gazette",
            "judicial_decision",
            "policy_document",
            "treaty",
        ],
        ...,
    ] = _DOCUMENT_KINDS
    required_fields: tuple[str, ...] = Field(min_length=25, max_length=25)
    minimum_evidence: CountryPrimaryDocumentEvidenceRequirements = Field(
        default_factory=CountryPrimaryDocumentEvidenceRequirements
    )

    @model_validator(mode="after")
    def inventory_is_exact(self) -> "CountryPrimaryDocumentSchemaDescriptor":
        if self.document_kinds != _DOCUMENT_KINDS:
            raise ValueError("document kind inventory is not the v1 contract")
        if self.required_fields != _REQUIRED_FIELDS:
            raise ValueError("required field inventory is not the v1 contract")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required fields must be unique")
        return self


class CountryPrimaryDocumentCatalogResponse(BaseModel):
    """Static catalog whose type forbids document availability claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_id: Literal[
        "urn:globemind:country-primary-document:catalog:v1"
    ] = COUNTRY_PRIMARY_DOCUMENT_CATALOG_ID
    schema_version: Literal[
        "globemind.country-primary-document.v1"
    ] = COUNTRY_PRIMARY_DOCUMENT_SCHEMA_VERSION
    contract_version: Literal["1.0.0"] = COUNTRY_PRIMARY_DOCUMENT_CONTRACT_VERSION
    generated_at: datetime
    available: Literal[False] = False
    operational_state: Literal["not_configured"] = "not_configured"
    implementation_scope: Literal["schema_catalog_only"] = "schema_catalog_only"
    live_checked: Literal[False] = False
    country_scope_status: Literal["not_configured"] = "not_configured"
    source_status: Literal["not_configured"] = "not_configured"
    license_status: Literal["not_configured"] = "not_configured"
    owner_status: Literal["not_configured"] = "not_configured"
    reviewer_status: Literal["not_configured"] = "not_configured"
    document_schema: CountryPrimaryDocumentSchemaDescriptor
    documents: tuple[()] = ()
    reason_codes: CountryPrimaryDocumentReasonCodes = (
        COUNTRY_PRIMARY_DOCUMENT_REASON_CODES
    )

    @field_validator("generated_at")
    @classmethod
    def generated_at_is_utc_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value.astimezone(timezone.utc)


def country_primary_document_schema_descriptor(
) -> CountryPrimaryDocumentSchemaDescriptor:
    return CountryPrimaryDocumentSchemaDescriptor(required_fields=_REQUIRED_FIELDS)


def country_primary_document_catalog(
    *, generated_at: datetime,
) -> CountryPrimaryDocumentCatalogResponse:
    return CountryPrimaryDocumentCatalogResponse(
        generated_at=generated_at,
        document_schema=country_primary_document_schema_descriptor(),
    )


__all__ = (
    "COUNTRY_PRIMARY_DOCUMENT_CATALOG_ID",
    "COUNTRY_PRIMARY_DOCUMENT_CONTRACT_VERSION",
    "COUNTRY_PRIMARY_DOCUMENT_REASON_CODES",
    "COUNTRY_PRIMARY_DOCUMENT_SCHEMA_VERSION",
    "CountryPrimaryDocumentCatalogResponse",
    "CountryPrimaryDocumentEvidenceRequirements",
    "CountryPrimaryDocumentSchemaDescriptor",
    "country_primary_document_catalog",
    "country_primary_document_schema_descriptor",
)
