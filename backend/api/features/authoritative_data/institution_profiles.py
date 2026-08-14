"""Schema-only country institution catalog with no bundled country facts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

COUNTRY_INSTITUTION_SCHEMA_VERSION = (
    "globemind.country-institution-governance.v1"
)
COUNTRY_INSTITUTION_CONTRACT_VERSION = "1.0.0"
COUNTRY_INSTITUTION_CATALOG_ID = (
    "urn:globemind:country-institution-governance:catalog:v1"
)

CountryInstitutionSectionId = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]{1,31}$",
        max_length=32,
    ),
]
CountryInstitutionFieldId = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^[a-z][a-z0-9_]{1,31}\."
            r"[a-z][a-z0-9_]{1,47}$"
        ),
        max_length=80,
    ),
]

CountryInstitutionReasonCodes = tuple[
    Literal["PILOT_COUNTRIES_NOT_SELECTED"],
    Literal["INSTITUTION_FACTS_NOT_CONFIGURED"],
    Literal["OFFICIAL_SOURCE_EVIDENCE_NOT_CONFIGURED"],
    Literal["DE_FACTO_METHOD_NOT_CONFIGURED"],
    Literal["LICENSE_EVIDENCE_NOT_CONFIGURED"],
    Literal["OWNER_NOT_CONFIGURED"],
    Literal["REVIEWER_NOT_CONFIGURED"],
]

COUNTRY_INSTITUTION_REASON_CODES: CountryInstitutionReasonCodes = (
    "PILOT_COUNTRIES_NOT_SELECTED",
    "INSTITUTION_FACTS_NOT_CONFIGURED",
    "OFFICIAL_SOURCE_EVIDENCE_NOT_CONFIGURED",
    "DE_FACTO_METHOD_NOT_CONFIGURED",
    "LICENSE_EVIDENCE_NOT_CONFIGURED",
    "OWNER_NOT_CONFIGURED",
    "REVIEWER_NOT_CONFIGURED",
)

_SECTION_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "constitutional_order",
        (
            "constitutional_order.government_form",
            "constitutional_order.constitutional_basis",
            "constitutional_order.constitutional_status",
            "constitutional_order.effective_period",
            "constitutional_order.amendment_records",
        ),
    ),
    (
        "power_structure",
        (
            "power_structure.head_of_state_authority",
            "power_structure.head_of_government_authority",
            "power_structure.executive_authority",
            "power_structure.legislative_authority",
            "power_structure.judicial_authority",
            "power_structure.oversight_authority",
            "power_structure.formal_power_claims",
            "power_structure.observed_power_claims",
            "power_structure.formal_observed_comparison",
        ),
    ),
    (
        "administrative_system",
        (
            "administrative_system.administrative_model",
            "administrative_system.administrative_levels",
            "administrative_system.subnational_units",
            "administrative_system.civil_service_structure",
            "administrative_system.appointment_or_election_rules",
            "administrative_system.delegated_authority",
            "administrative_system.autonomy_arrangements",
        ),
    ),
    (
        "evidence_governance",
        (
            "evidence_governance.claim_source_bindings",
            "evidence_governance.source_cutoff",
            "evidence_governance.license_review",
            "evidence_governance.profile_owner",
            "evidence_governance.last_review",
            "evidence_governance.conflicting_evidence",
        ),
    ),
)
_EXPECTED_SECTION_IDS = tuple(section_id for section_id, _ in _SECTION_FIELDS)
_EXPECTED_FIELD_IDS = tuple(
    field_id for _, field_ids in _SECTION_FIELDS for field_id in field_ids
)

EvidenceProfile = Literal[
    "official_legal_primary",
    "official_administrative_primary",
    "independent_observation_corroborated",
    "separate_de_jure_and_de_facto_bindings",
    "governance_audit_record",
]


def _expected_evidence_profile(field_id: str) -> EvidenceProfile:
    if field_id == "power_structure.observed_power_claims":
        return "independent_observation_corroborated"
    if field_id == "power_structure.formal_observed_comparison":
        return "separate_de_jure_and_de_facto_bindings"
    if field_id.startswith("administrative_system."):
        return "official_administrative_primary"
    if field_id.startswith("evidence_governance."):
        return "governance_audit_record"
    return "official_legal_primary"


class CountryInstitutionFieldDescriptor(BaseModel):
    """One structural slot; it carries requirements and never a fact value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_id: CountryInstitutionFieldId
    section_id: CountryInstitutionSectionId
    title: str = Field(min_length=1, max_length=120)
    value_kind: Literal[
        "classification",
        "document",
        "date",
        "relation",
        "text",
        "comparison",
        "review",
    ]
    cardinality: Literal["zero_or_one", "many"]
    evidence_profile: EvidenceProfile
    evidence_required: Literal[True] = True
    citation_required: Literal[True] = True
    temporal_scope_required: Literal[True] = True
    license_evidence_required: Literal[True] = True
    owner_review_required: Literal[True] = True

    @model_validator(mode="after")
    def field_contract_is_exact(self) -> "CountryInstitutionFieldDescriptor":
        if not self.field_id.startswith(f"{self.section_id}."):
            raise ValueError("field_id must be namespaced by section_id")
        if self.evidence_profile != _expected_evidence_profile(self.field_id):
            raise ValueError("field evidence profile does not match its claim class")
        return self


class CountryInstitutionSectionDescriptor(BaseModel):
    """Ordered institution section and the exact field IDs it owns."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: CountryInstitutionSectionId
    title: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=400)
    field_ids: tuple[CountryInstitutionFieldId, ...] = Field(
        min_length=1,
        max_length=12,
    )

    @model_validator(mode="after")
    def owns_unique_namespaced_fields(
        self,
    ) -> "CountryInstitutionSectionDescriptor":
        if len(self.field_ids) != len(set(self.field_ids)):
            raise ValueError("section field_ids must be unique")
        if any(
            not field_id.startswith(f"{self.section_id}.")
            for field_id in self.field_ids
        ):
            raise ValueError("section field_ids must use the section namespace")
        return self


class CountryInstitutionEvidenceRequirements(BaseModel):
    """Minimum future fact gate; this catalog supplies none of the evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_granularity: Literal["one_fact_per_evidence_binding"] = (
        "one_fact_per_evidence_binding"
    )
    source_locator: Literal["absolute_https_url"] = "absolute_https_url"
    source_authority: Literal["required"] = "required"
    source_language: Literal["required_bcp47"] = "required_bcp47"
    source_retrieved_at: Literal["required_utc_datetime"] = (
        "required_utc_datetime"
    )
    source_cutoff: Literal["required_utc_datetime_or_period"] = (
        "required_utc_datetime_or_period"
    )
    future_source_cutoff_policy: Literal["fail_closed"] = "fail_closed"
    legal_effective_period: Literal["required_for_de_jure_claims"] = (
        "required_for_de_jure_claims"
    )
    observation_period: Literal["required_for_de_facto_claims"] = (
        "required_for_de_facto_claims"
    )
    formal_actual_separation: Literal["required"] = "required"
    independent_corroboration: Literal["required_for_de_facto_claims"] = (
        "required_for_de_facto_claims"
    )
    contradiction_disposition: Literal["required"] = "required"
    license_state: Literal["verified_or_restricted"] = "verified_or_restricted"
    owner_role: Literal["country-data-stewardship"] = (
        "country-data-stewardship"
    )
    owner_identifier: Literal["required_stable_identifier"] = (
        "required_stable_identifier"
    )
    review_state: Literal["approved"] = "approved"
    reviewer_identifier: Literal["required_stable_identifier"] = (
        "required_stable_identifier"
    )
    reviewed_at: Literal["required_utc_datetime"] = "required_utc_datetime"
    future_review_policy: Literal["fail_closed"] = "fail_closed"
    review_expires_at: Literal["required_future_utc_datetime"] = (
        "required_future_utc_datetime"
    )
    expired_review_policy: Literal["fail_closed"] = "fail_closed"
    invalid_evidence_policy: Literal["fail_closed"] = "fail_closed"


class CountryInstitutionSchemaDescriptor(BaseModel):
    """Strict inventory for a future institution/governance fact contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "urn:globemind:country-institution-governance:schema:v1"
    ] = "urn:globemind:country-institution-governance:schema:v1"
    schema_version: Literal[
        "globemind.country-institution-governance.v1"
    ] = COUNTRY_INSTITUTION_SCHEMA_VERSION
    country_identifier_standard: Literal["ISO 3166-1 alpha-2"] = (
        "ISO 3166-1 alpha-2"
    )
    fact_identifier_format: Literal[
        "urn:globemind:country-institution-fact:{iso-alpha2-lower}:{sha256}"
    ] = "urn:globemind:country-institution-fact:{iso-alpha2-lower}:{sha256}"
    sections: tuple[CountryInstitutionSectionDescriptor, ...] = Field(
        min_length=4,
        max_length=4,
    )
    fields: tuple[CountryInstitutionFieldDescriptor, ...] = Field(
        min_length=27,
        max_length=27,
    )
    minimum_evidence: CountryInstitutionEvidenceRequirements = Field(
        default_factory=CountryInstitutionEvidenceRequirements
    )

    @model_validator(mode="after")
    def inventory_is_exact(self) -> "CountryInstitutionSchemaDescriptor":
        section_ids = tuple(section.section_id for section in self.sections)
        field_ids = tuple(field.field_id for field in self.fields)
        if section_ids != _EXPECTED_SECTION_IDS:
            raise ValueError("institution section inventory is not the v1 contract")
        if field_ids != _EXPECTED_FIELD_IDS:
            raise ValueError("institution field inventory is not the v1 contract")

        expected_by_section = dict(_SECTION_FIELDS)
        for section in self.sections:
            if section.field_ids != expected_by_section[section.section_id]:
                raise ValueError("section does not exactly own its v1 fields")
        return self


class CountryInstitutionCatalogResponse(BaseModel):
    """Static catalog whose type forbids country-fact availability claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_id: Literal[
        "urn:globemind:country-institution-governance:catalog:v1"
    ] = COUNTRY_INSTITUTION_CATALOG_ID
    schema_version: Literal[
        "globemind.country-institution-governance.v1"
    ] = COUNTRY_INSTITUTION_SCHEMA_VERSION
    contract_version: Literal["1.0.0"] = COUNTRY_INSTITUTION_CONTRACT_VERSION
    generated_at: datetime
    available: Literal[False] = False
    operational_state: Literal["not_configured"] = "not_configured"
    implementation_scope: Literal["schema_catalog_only"] = "schema_catalog_only"
    live_checked: Literal[False] = False
    live_data_status: Literal["not_configured"] = "not_configured"
    owner_status: Literal["not_configured"] = "not_configured"
    reviewer_status: Literal["not_configured"] = "not_configured"
    license_status: Literal["not_configured"] = "not_configured"
    country_scope_status: Literal["not_configured"] = "not_configured"
    institution_schema: CountryInstitutionSchemaDescriptor
    facts: tuple[()] = ()
    reason_codes: CountryInstitutionReasonCodes = COUNTRY_INSTITUTION_REASON_CODES

    @field_validator("generated_at")
    @classmethod
    def generated_at_is_utc_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value.astimezone(timezone.utc)


def _field(
    section_id: str,
    name: str,
    title: str,
    value_kind: Literal[
        "classification",
        "document",
        "date",
        "relation",
        "text",
        "comparison",
        "review",
    ],
    cardinality: Literal["zero_or_one", "many"],
) -> CountryInstitutionFieldDescriptor:
    field_id = f"{section_id}.{name}"
    return CountryInstitutionFieldDescriptor(
        field_id=field_id,
        section_id=section_id,
        title=title,
        value_kind=value_kind,
        cardinality=cardinality,
        evidence_profile=_expected_evidence_profile(field_id),
    )


def country_institution_schema_descriptor() -> CountryInstitutionSchemaDescriptor:
    """Build the exact v1 descriptor without any country-specific value."""

    fields = (
        _field(
            "constitutional_order",
            "government_form",
            "Form of government",
            "classification",
            "zero_or_one",
        ),
        _field(
            "constitutional_order",
            "constitutional_basis",
            "Constitutional basis documents",
            "document",
            "many",
        ),
        _field(
            "constitutional_order",
            "constitutional_status",
            "Constitutional status",
            "classification",
            "zero_or_one",
        ),
        _field(
            "constitutional_order",
            "effective_period",
            "Constitutional effective period",
            "date",
            "many",
        ),
        _field(
            "constitutional_order",
            "amendment_records",
            "Constitutional amendment records",
            "document",
            "many",
        ),
        _field(
            "power_structure",
            "head_of_state_authority",
            "Head-of-state authority",
            "relation",
            "many",
        ),
        _field(
            "power_structure",
            "head_of_government_authority",
            "Head-of-government authority",
            "relation",
            "many",
        ),
        _field(
            "power_structure",
            "executive_authority",
            "Executive authority",
            "relation",
            "many",
        ),
        _field(
            "power_structure",
            "legislative_authority",
            "Legislative authority",
            "relation",
            "many",
        ),
        _field(
            "power_structure",
            "judicial_authority",
            "Judicial authority",
            "relation",
            "many",
        ),
        _field(
            "power_structure",
            "oversight_authority",
            "Oversight authority",
            "relation",
            "many",
        ),
        _field(
            "power_structure",
            "formal_power_claims",
            "Formal power claims",
            "text",
            "many",
        ),
        _field(
            "power_structure",
            "observed_power_claims",
            "Observed power claims",
            "text",
            "many",
        ),
        _field(
            "power_structure",
            "formal_observed_comparison",
            "Formal and observed power comparison",
            "comparison",
            "many",
        ),
        _field(
            "administrative_system",
            "administrative_model",
            "Administrative model",
            "classification",
            "zero_or_one",
        ),
        _field(
            "administrative_system",
            "administrative_levels",
            "Administrative levels",
            "relation",
            "many",
        ),
        _field(
            "administrative_system",
            "subnational_units",
            "Subnational units",
            "relation",
            "many",
        ),
        _field(
            "administrative_system",
            "civil_service_structure",
            "Civil-service structure",
            "relation",
            "many",
        ),
        _field(
            "administrative_system",
            "appointment_or_election_rules",
            "Appointment or election rules",
            "document",
            "many",
        ),
        _field(
            "administrative_system",
            "delegated_authority",
            "Delegated administrative authority",
            "relation",
            "many",
        ),
        _field(
            "administrative_system",
            "autonomy_arrangements",
            "Subnational autonomy arrangements",
            "relation",
            "many",
        ),
        _field(
            "evidence_governance",
            "claim_source_bindings",
            "Claim-to-source bindings",
            "review",
            "many",
        ),
        _field(
            "evidence_governance",
            "source_cutoff",
            "Source cutoff",
            "date",
            "many",
        ),
        _field(
            "evidence_governance",
            "license_review",
            "License review",
            "review",
            "many",
        ),
        _field(
            "evidence_governance",
            "profile_owner",
            "Country institution profile owner",
            "review",
            "zero_or_one",
        ),
        _field(
            "evidence_governance",
            "last_review",
            "Last human review",
            "review",
            "zero_or_one",
        ),
        _field(
            "evidence_governance",
            "conflicting_evidence",
            "Conflicting evidence disposition",
            "review",
            "many",
        ),
    )
    field_by_id = {field.field_id: field for field in fields}
    sections = (
        CountryInstitutionSectionDescriptor(
            section_id="constitutional_order",
            title="Constitutional order",
            purpose=(
                "Slots for sourced constitutional classifications, documents, "
                "effective periods, and amendments."
            ),
            field_ids=_SECTION_FIELDS[0][1],
        ),
        CountryInstitutionSectionDescriptor(
            section_id="power_structure",
            title="Power structure",
            purpose=(
                "Separate slots for legally assigned authority, observed practice, "
                "and an evidence-bound comparison that cannot merge the two."
            ),
            field_ids=_SECTION_FIELDS[1][1],
        ),
        CountryInstitutionSectionDescriptor(
            section_id="administrative_system",
            title="Administrative system",
            purpose=(
                "Slots for official administrative levels, units, civil service, "
                "selection rules, delegated authority, and autonomy arrangements."
            ),
            field_ids=_SECTION_FIELDS[2][1],
        ),
        CountryInstitutionSectionDescriptor(
            section_id="evidence_governance",
            title="Evidence governance",
            purpose=(
                "Slots for citations, cutoff, licensing, ownership, review, and "
                "contradiction disposition required before facts can be published."
            ),
            field_ids=_SECTION_FIELDS[3][1],
        ),
    )
    ordered_fields = tuple(field_by_id[field_id] for field_id in _EXPECTED_FIELD_IDS)
    return CountryInstitutionSchemaDescriptor(
        sections=sections,
        fields=ordered_fields,
    )


def country_institution_catalog(
    generated_at: datetime,
) -> CountryInstitutionCatalogResponse:
    """Return an empty catalog until country facts and governance are configured."""

    return CountryInstitutionCatalogResponse(
        generated_at=generated_at,
        institution_schema=country_institution_schema_descriptor(),
    )


__all__ = (
    "COUNTRY_INSTITUTION_CATALOG_ID",
    "COUNTRY_INSTITUTION_CONTRACT_VERSION",
    "COUNTRY_INSTITUTION_REASON_CODES",
    "COUNTRY_INSTITUTION_SCHEMA_VERSION",
    "CountryInstitutionCatalogResponse",
    "CountryInstitutionEvidenceRequirements",
    "CountryInstitutionFieldDescriptor",
    "CountryInstitutionSchemaDescriptor",
    "CountryInstitutionSectionDescriptor",
    "country_institution_catalog",
    "country_institution_schema_descriptor",
)
