"""Schema-only country profile catalog with no bundled country facts."""

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

COUNTRY_PROFILE_SCHEMA_VERSION = "globemind.country-profile.v1"
COUNTRY_PROFILE_CONTRACT_VERSION = "1.0.0"
COUNTRY_PROFILE_CATALOG_ID = "urn:globemind:country-profile:catalog:v1"

CountryProfileSectionId = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]{1,31}$",
        max_length=32,
    ),
]
CountryProfileFieldId = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]{1,31}\.[a-z][a-z0-9_]{1,47}$",
        max_length=80,
    ),
]

CountryProfileReasonCodes = tuple[
    Literal["PILOT_COUNTRIES_NOT_SELECTED"],
    Literal["COUNTRY_PROFILES_NOT_CONFIGURED"],
    Literal["SOURCE_AND_CUTOFF_EVIDENCE_NOT_CONFIGURED"],
    Literal["LICENSE_EVIDENCE_NOT_CONFIGURED"],
    Literal["OWNER_AND_REVIEW_NOT_CONFIGURED"],
]

COUNTRY_PROFILE_REASON_CODES: CountryProfileReasonCodes = (
    "PILOT_COUNTRIES_NOT_SELECTED",
    "COUNTRY_PROFILES_NOT_CONFIGURED",
    "SOURCE_AND_CUTOFF_EVIDENCE_NOT_CONFIGURED",
    "LICENSE_EVIDENCE_NOT_CONFIGURED",
    "OWNER_AND_REVIEW_NOT_CONFIGURED",
)


class CountryProfileFieldDescriptor(BaseModel):
    """One bounded field slot; this describes structure, never a fact value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_id: CountryProfileFieldId
    section_id: CountryProfileSectionId
    title: str = Field(min_length=1, max_length=120)
    value_kind: Literal[
        "identifier",
        "text",
        "classification",
        "quantity",
        "date",
        "relation",
        "document",
        "review",
    ]
    cardinality: Literal["one", "zero_or_one", "many"]
    required_for_publish: bool
    evidence_required: Literal[True] = True

    @model_validator(mode="after")
    def field_belongs_to_section(self) -> "CountryProfileFieldDescriptor":
        if not self.field_id.startswith(f"{self.section_id}."):
            raise ValueError("field_id must be namespaced by section_id")
        return self


class CountryProfileSectionDescriptor(BaseModel):
    """Ordered profile section and the exact field IDs it owns."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: CountryProfileSectionId
    title: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=400)
    field_ids: tuple[CountryProfileFieldId, ...] = Field(
        min_length=1,
        max_length=12,
    )

    @model_validator(mode="after")
    def owns_unique_namespaced_fields(self) -> "CountryProfileSectionDescriptor":
        if len(self.field_ids) != len(set(self.field_ids)):
            raise ValueError("section field_ids must be unique")
        if any(not value.startswith(f"{self.section_id}.") for value in self.field_ids):
            raise ValueError("section field_ids must use the section namespace")
        return self


class CountryProfileEvidenceRequirements(BaseModel):
    """Minimum publish gate for a future profile; no evidence is loaded here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_locator: Literal["absolute_https_url"] = "absolute_https_url"
    source_authority: Literal["required"] = "required"
    source_retrieved_at: Literal["required_utc_datetime"] = (
        "required_utc_datetime"
    )
    source_cutoff: Literal["required_utc_datetime_or_period"] = (
        "required_utc_datetime_or_period"
    )
    future_source_cutoff_policy: Literal["fail_closed"] = "fail_closed"
    license_state: Literal["verified_or_restricted"] = "verified_or_restricted"
    owner_role: Literal["country-data-stewardship"] = "country-data-stewardship"
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


class CountryProfileSchemaDescriptor(BaseModel):
    """Versioned inventory for a future, evidence-backed country profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["urn:globemind:country-profile:schema:v1"] = (
        "urn:globemind:country-profile:schema:v1"
    )
    schema_version: Literal["globemind.country-profile.v1"] = (
        COUNTRY_PROFILE_SCHEMA_VERSION
    )
    country_identifier_standard: Literal["ISO 3166-1 alpha-2"] = (
        "ISO 3166-1 alpha-2"
    )
    country_identifier_pattern: Literal["^[A-Z]{2}$"] = "^[A-Z]{2}$"
    profile_identifier_format: Literal[
        "urn:globemind:country-profile:{iso-alpha2-lower}:{sha256}"
    ] = "urn:globemind:country-profile:{iso-alpha2-lower}:{sha256}"
    sections: tuple[CountryProfileSectionDescriptor, ...] = Field(
        min_length=1,
        max_length=16,
    )
    fields: tuple[CountryProfileFieldDescriptor, ...] = Field(
        min_length=1,
        max_length=64,
    )
    minimum_evidence: CountryProfileEvidenceRequirements = Field(
        default_factory=CountryProfileEvidenceRequirements
    )

    @model_validator(mode="after")
    def inventory_is_complete_and_unique(self) -> "CountryProfileSchemaDescriptor":
        section_ids = [section.section_id for section in self.sections]
        field_ids = [field.field_id for field in self.fields]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section IDs must be unique")
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("field IDs must be unique")

        section_fields = [
            field_id for section in self.sections for field_id in section.field_ids
        ]
        if len(section_fields) != len(set(section_fields)):
            raise ValueError("a field may belong to only one section")
        if set(section_fields) != set(field_ids):
            raise ValueError("section inventory must exactly cover the field inventory")

        owner_by_field = {field.field_id: field.section_id for field in self.fields}
        for section in self.sections:
            if any(
                field_id not in owner_by_field
                or owner_by_field[field_id] != section.section_id
                for field_id in section.field_ids
            ):
                raise ValueError(
                    "field descriptor section does not match section owner"
                )
        return self


class CountryProfileCatalogResponse(BaseModel):
    """Static schema catalog; its type prevents availability or profile claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_id: Literal["urn:globemind:country-profile:catalog:v1"] = (
        COUNTRY_PROFILE_CATALOG_ID
    )
    schema_version: Literal["globemind.country-profile.v1"] = (
        COUNTRY_PROFILE_SCHEMA_VERSION
    )
    contract_version: Literal["1.0.0"] = COUNTRY_PROFILE_CONTRACT_VERSION
    generated_at: datetime
    available: Literal[False] = False
    operational_state: Literal["not_configured"] = "not_configured"
    live_checked: Literal[False] = False
    implementation_scope: Literal["schema_catalog_only"] = (
        "schema_catalog_only"
    )
    profile_schema: CountryProfileSchemaDescriptor
    profiles: tuple[()] = ()
    reason_codes: CountryProfileReasonCodes = COUNTRY_PROFILE_REASON_CODES

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
        "identifier",
        "text",
        "classification",
        "quantity",
        "date",
        "relation",
        "document",
        "review",
    ],
    cardinality: Literal["one", "zero_or_one", "many"],
    *,
    required: bool = False,
) -> CountryProfileFieldDescriptor:
    return CountryProfileFieldDescriptor(
        field_id=f"{section_id}.{name}",
        section_id=section_id,
        title=title,
        value_kind=value_kind,
        cardinality=cardinality,
        required_for_publish=required,
    )


def country_profile_schema_descriptor() -> CountryProfileSchemaDescriptor:
    """Build a fresh immutable descriptor with no country-specific values."""

    fields = (
        _field(
            "overview",
            "iso_alpha2",
            "ISO alpha-2 code",
            "identifier",
            "one",
            required=True,
        ),
        _field(
            "overview",
            "official_name",
            "Official name",
            "text",
            "one",
            required=True,
        ),
        _field("overview", "short_name", "Short name", "text", "one", required=True),
        _field("overview", "local_names", "Local names", "text", "many"),
        _field("overview", "capital", "Capital", "relation", "zero_or_one"),
        _field("overview", "geography", "Geographic overview", "text", "zero_or_one"),
        _field(
            "institutions",
            "system_of_government",
            "System of government",
            "classification",
            "zero_or_one",
        ),
        _field(
            "institutions",
            "constitution",
            "Constitutional framework",
            "document",
            "many",
        ),
        _field(
            "institutions",
            "administrative_divisions",
            "Administrative divisions",
            "relation",
            "many",
        ),
        _field(
            "politics",
            "executive_leadership",
            "Executive leadership",
            "relation",
            "many",
        ),
        _field("politics", "legislature", "Legislature", "relation", "many"),
        _field("politics", "elections", "Elections", "relation", "many"),
        _field("politics", "parties", "Political parties", "relation", "many"),
        _field("law_policy", "constitution", "Constitution", "document", "many"),
        _field(
            "law_policy",
            "legal_system",
            "Legal system",
            "classification",
            "zero_or_one",
        ),
        _field(
            "law_policy",
            "official_publications",
            "Official publications",
            "document",
            "many",
        ),
        _field("economy", "gdp", "Gross domestic product", "quantity", "many"),
        _field("economy", "currency", "Currency", "identifier", "many"),
        _field("economy", "trade", "Trade", "quantity", "many"),
        _field("society", "population", "Population", "quantity", "many"),
        _field("society", "languages", "Languages", "classification", "many"),
        _field("society", "human_development", "Human development", "quantity", "many"),
        _field(
            "security",
            "security_institutions",
            "Security institutions",
            "relation",
            "many",
        ),
        _field(
            "security",
            "conflict_status",
            "Conflict status",
            "classification",
            "many",
        ),
        _field(
            "external_relations",
            "memberships",
            "International memberships",
            "relation",
            "many",
        ),
        _field("external_relations", "treaties", "Treaties", "document", "many"),
        _field("environment", "climate", "Climate", "classification", "many"),
        _field("environment", "emissions", "Emissions", "quantity", "many"),
        _field(
            "evidence_governance",
            "profile_owner",
            "Profile owner",
            "review",
            "one",
            required=True,
        ),
        _field(
            "evidence_governance",
            "source_cutoff",
            "Source cutoff",
            "date",
            "one",
            required=True,
        ),
        _field(
            "evidence_governance",
            "license_review",
            "License review",
            "review",
            "one",
            required=True,
        ),
        _field(
            "evidence_governance",
            "last_review",
            "Last human review",
            "review",
            "one",
            required=True,
        ),
    )
    fields_by_section = {
        section_id: tuple(
            field.field_id for field in fields if field.section_id == section_id
        )
        for section_id in (
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
        )
    }
    sections = (
        CountryProfileSectionDescriptor(
            section_id="overview",
            title="Overview",
            purpose="Stable identity, names, capital, and geographic orientation.",
            field_ids=fields_by_section["overview"],
        ),
        CountryProfileSectionDescriptor(
            section_id="institutions",
            title="Institutions",
            purpose="Constitutional and administrative institutional structure.",
            field_ids=fields_by_section["institutions"],
        ),
        CountryProfileSectionDescriptor(
            section_id="politics",
            title="Politics",
            purpose="Time-bounded leadership, legislature, elections, and parties.",
            field_ids=fields_by_section["politics"],
        ),
        CountryProfileSectionDescriptor(
            section_id="law_policy",
            title="Law and policy",
            purpose=(
                "Versioned constitutional, legal-system, and publication references."
            ),
            field_ids=fields_by_section["law_policy"],
        ),
        CountryProfileSectionDescriptor(
            section_id="economy",
            title="Economy",
            purpose="Dated and sourced economic measures and classifications.",
            field_ids=fields_by_section["economy"],
        ),
        CountryProfileSectionDescriptor(
            section_id="society",
            title="Society",
            purpose="Dated demographic, language, and development measures.",
            field_ids=fields_by_section["society"],
        ),
        CountryProfileSectionDescriptor(
            section_id="security",
            title="Security",
            purpose=(
                "Sourced institutions and explicitly time-bounded conflict "
                "classifications."
            ),
            field_ids=fields_by_section["security"],
        ),
        CountryProfileSectionDescriptor(
            section_id="external_relations",
            title="External relations",
            purpose="Memberships and treaty documents with effective dates.",
            field_ids=fields_by_section["external_relations"],
        ),
        CountryProfileSectionDescriptor(
            section_id="environment",
            title="Environment",
            purpose="Dated climate classifications and environmental measures.",
            field_ids=fields_by_section["environment"],
        ),
        CountryProfileSectionDescriptor(
            section_id="evidence_governance",
            title="Evidence governance",
            purpose=(
                "Required ownership, cutoff, licensing, and human-review evidence."
            ),
            field_ids=fields_by_section["evidence_governance"],
        ),
    )
    return CountryProfileSchemaDescriptor(sections=sections, fields=fields)


def country_profile_catalog(generated_at: datetime) -> CountryProfileCatalogResponse:
    """Return the permanently empty catalog until governed profiles are configured."""

    return CountryProfileCatalogResponse(
        generated_at=generated_at,
        profile_schema=country_profile_schema_descriptor(),
    )


__all__ = (
    "COUNTRY_PROFILE_CATALOG_ID",
    "COUNTRY_PROFILE_CONTRACT_VERSION",
    "COUNTRY_PROFILE_REASON_CODES",
    "COUNTRY_PROFILE_SCHEMA_VERSION",
    "CountryProfileCatalogResponse",
    "CountryProfileEvidenceRequirements",
    "CountryProfileFieldDescriptor",
    "CountryProfileSchemaDescriptor",
    "CountryProfileSectionDescriptor",
    "country_profile_catalog",
    "country_profile_schema_descriptor",
)
