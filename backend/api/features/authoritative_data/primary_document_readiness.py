"""Offline readiness receipt for approved country primary-document pilot plans.

The plan and bundle are externally supplied evidence.  This module compares
their exact hashes and declared coverage without publishing facts, calling a
service, or deciding that a country profile is complete.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .primary_document_bundle import (
    _ACTOR_RE,
    _COUNTRY_RE,
    _SHA256_RE,
    CountryPrimaryDocumentBundleError,
    LoadedCountryPrimaryDocumentBundle,
    _read_single_link_file,
    _reject_duplicate_keys,
)

COUNTRY_PRIMARY_DOCUMENT_PILOT_PLAN_SCHEMA_VERSION = (
    "globemind.country-primary-document-pilot-plan.v1"
)
COUNTRY_PRIMARY_DOCUMENT_READINESS_SCHEMA_VERSION = (
    "globemind.country-primary-document-readiness.v1"
)
MAX_COUNTRY_PILOT_PLAN_BYTES = 256 * 1024

DocumentKind = Literal[
    "constitution",
    "statute",
    "regulation",
    "official_gazette",
    "judicial_decision",
    "policy_document",
    "treaty",
]


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


class CountryPilotDocumentRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    country_code: str = Field(pattern=_COUNTRY_RE.pattern)
    required_document_kinds: tuple[DocumentKind, ...] = Field(
        min_length=1,
        max_length=7,
    )
    minimum_documents_per_kind: int = Field(ge=1, le=20, strict=True)

    @model_validator(mode="after")
    def kinds_are_unique(self) -> "CountryPilotDocumentRequirement":
        if len(self.required_document_kinds) != len(
            set(self.required_document_kinds)
        ):
            raise ValueError("required document kinds must be unique")
        return self


class CountryPrimaryDocumentPilotPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "globemind.country-primary-document-pilot-plan.v1"
    ] = COUNTRY_PRIMARY_DOCUMENT_PILOT_PLAN_SCHEMA_VERSION
    plan_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
    plan_version: str = Field(min_length=1, max_length=120)
    requirements: tuple[CountryPilotDocumentRequirement, ...] = Field(
        min_length=1,
        max_length=3,
    )
    owner_identifier: str = Field(pattern=_ACTOR_RE.pattern)
    reviewer_identifier: str = Field(pattern=_ACTOR_RE.pattern)
    approved_at: datetime
    expires_at: datetime
    approval_state: Literal["approved"] = "approved"
    public_promotion_state: Literal[
        "separate_explicit_decision_required"
    ] = "separate_explicit_decision_required"

    @field_validator("approved_at", "expires_at")
    @classmethod
    def times_are_aware(cls, value: datetime, info: Any) -> datetime:
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def approval_and_scope_are_valid(self) -> "CountryPrimaryDocumentPilotPlan":
        if self.owner_identifier == self.reviewer_identifier:
            raise ValueError("pilot plan owner and reviewer must be distinct")
        if self.expires_at <= self.approved_at:
            raise ValueError("pilot plan expiry must follow approval")
        countries = [item.country_code for item in self.requirements]
        if len(countries) != len(set(countries)):
            raise ValueError("pilot plan country requirements must be unique")
        return self


@dataclass(frozen=True)
class LoadedCountryPrimaryDocumentPilotPlan:
    plan: CountryPrimaryDocumentPilotPlan
    artifact_sha256: str
    artifact_bytes: int


class CountryPilotCountryReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    country_code: str = Field(pattern=_COUNTRY_RE.pattern)
    required_document_kinds: tuple[DocumentKind, ...]
    minimum_documents_per_kind: int = Field(ge=1, le=20, strict=True)
    verified_license_counts: dict[DocumentKind, int]
    restricted_license_document_count: int = Field(ge=0, strict=True)
    missing_document_kinds: tuple[DocumentKind, ...]
    intake_state: Literal["requirements_met", "requirements_not_met"]


class CountryPrimaryDocumentReadinessReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "globemind.country-primary-document-readiness.v1"
    ] = COUNTRY_PRIMARY_DOCUMENT_READINESS_SCHEMA_VERSION
    evaluated_at: datetime
    plan_sha256: str = Field(pattern=_SHA256_RE.pattern)
    bundle_manifest_sha256: str = Field(pattern=_SHA256_RE.pattern)
    plan_id: str
    plan_version: str
    bundle_id: str
    bundle_version: str
    countries: tuple[CountryPilotCountryReadiness, ...]
    intake_coverage_state: Literal[
        "requirements_met_not_published",
        "requirements_not_met",
    ]
    facts_published: Literal[False] = False
    public_catalog_mutated: Literal[False] = False
    source_truth_scope: Literal[
        "reviewed_primary_documents_not_country_fact_synthesis"
    ] = "reviewed_primary_documents_not_country_fact_synthesis"
    publication_decision: Literal["not_computable"] = "not_computable"
    candidate_acceptance: Literal["not_performed"] = "not_performed"


def load_country_primary_document_pilot_plan(
    path: Path,
    *,
    expected_sha256: str,
    evaluated_at: datetime,
) -> LoadedCountryPrimaryDocumentPilotPlan:
    """Load a bounded, approved pilot plan from an isolated local artifact."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise CountryPrimaryDocumentBundleError("evaluated_at must include a timezone")
    if not path.is_absolute():
        raise CountryPrimaryDocumentBundleError("pilot plan path must be absolute")
    if not isinstance(expected_sha256, str) or _SHA256_RE.fullmatch(expected_sha256) is None:
        raise CountryPrimaryDocumentBundleError("pilot plan SHA-256 is invalid")
    raw = _read_single_link_file(
        path,
        maximum=MAX_COUNTRY_PILOT_PLAN_BYTES,
        field="pilot plan",
    )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise CountryPrimaryDocumentBundleError("pilot plan SHA-256 mismatch")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                CountryPrimaryDocumentBundleError(
                    f"pilot plan contains non-finite JSON number: {value}"
                )
            ),
        )
        plan = CountryPrimaryDocumentPilotPlan.model_validate(payload)
    except CountryPrimaryDocumentBundleError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CountryPrimaryDocumentBundleError(
            "pilot plan failed strict validation"
        ) from exc
    if plan.expires_at <= evaluated_at.astimezone(timezone.utc):
        raise CountryPrimaryDocumentBundleError("pilot plan approval is expired")
    return LoadedCountryPrimaryDocumentPilotPlan(
        plan=plan,
        artifact_sha256=digest,
        artifact_bytes=len(raw),
    )


def evaluate_country_primary_document_readiness(
    plan: LoadedCountryPrimaryDocumentPilotPlan,
    bundle: LoadedCountryPrimaryDocumentBundle,
    *,
    evaluated_at: datetime,
) -> CountryPrimaryDocumentReadinessReceipt:
    """Compare exact reviewed artifacts without publishing or synthesizing facts."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise CountryPrimaryDocumentBundleError("evaluated_at must include a timezone")
    now = evaluated_at.astimezone(timezone.utc)
    if plan.plan.expires_at <= now:
        raise CountryPrimaryDocumentBundleError("pilot plan approval is expired")
    plan_countries = tuple(item.country_code for item in plan.plan.requirements)
    if set(plan_countries) != set(bundle.bundle.pilot_country_codes):
        raise CountryPrimaryDocumentBundleError(
            "pilot plan country scope does not match the primary-document bundle"
        )

    documents_by_country: dict[str, list[Any]] = {
        country: [] for country in plan_countries
    }
    for document in bundle.bundle.documents:
        if document.governance.review_expires_at <= now:
            raise CountryPrimaryDocumentBundleError(
                "bundle document review expired before readiness evaluation"
            )
        documents_by_country[document.identity.country_code].append(document)

    country_receipts: list[CountryPilotCountryReadiness] = []
    all_met = True
    for requirement in plan.plan.requirements:
        country_documents = documents_by_country[requirement.country_code]
        verified_counts = {
            kind: sum(
                document.identity.document_kind == kind
                and document.governance.license_state == "verified"
                for document in country_documents
            )
            for kind in requirement.required_document_kinds
        }
        missing = tuple(
            kind
            for kind in requirement.required_document_kinds
            if verified_counts[kind] < requirement.minimum_documents_per_kind
        )
        restricted_count = sum(
            document.governance.license_state == "restricted"
            for document in country_documents
        )
        met = not missing and restricted_count == 0
        all_met = all_met and met
        country_receipts.append(
            CountryPilotCountryReadiness(
                country_code=requirement.country_code,
                required_document_kinds=requirement.required_document_kinds,
                minimum_documents_per_kind=requirement.minimum_documents_per_kind,
                verified_license_counts=verified_counts,
                restricted_license_document_count=restricted_count,
                missing_document_kinds=missing,
                intake_state="requirements_met" if met else "requirements_not_met",
            )
        )

    return CountryPrimaryDocumentReadinessReceipt(
        evaluated_at=now,
        plan_sha256=plan.artifact_sha256,
        bundle_manifest_sha256=bundle.manifest_sha256,
        plan_id=plan.plan.plan_id,
        plan_version=plan.plan.plan_version,
        bundle_id=bundle.bundle.bundle_id,
        bundle_version=bundle.bundle.bundle_version,
        countries=tuple(country_receipts),
        intake_coverage_state=(
            "requirements_met_not_published" if all_met else "requirements_not_met"
        ),
    )


__all__ = (
    "COUNTRY_PRIMARY_DOCUMENT_PILOT_PLAN_SCHEMA_VERSION",
    "COUNTRY_PRIMARY_DOCUMENT_READINESS_SCHEMA_VERSION",
    "CountryPilotCountryReadiness",
    "CountryPilotDocumentRequirement",
    "CountryPrimaryDocumentPilotPlan",
    "CountryPrimaryDocumentReadinessReceipt",
    "LoadedCountryPrimaryDocumentPilotPlan",
    "evaluate_country_primary_document_readiness",
    "load_country_primary_document_pilot_plan",
)
