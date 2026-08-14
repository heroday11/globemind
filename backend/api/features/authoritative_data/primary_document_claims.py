"""Content-free claim/citation readiness over verified country documents.

This boundary validates exact document and section-anchor references plus
temporal, licence, and conflict structure.  It never reads claim text and
therefore cannot verify semantic entailment or publish a country fact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

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

COUNTRY_PRIMARY_DOCUMENT_CLAIM_PLAN_SCHEMA_VERSION = (
    "globemind.country-primary-document-claim-plan.v1"
)
COUNTRY_PRIMARY_DOCUMENT_CLAIM_RECEIPT_SCHEMA_VERSION = (
    "globemind.country-primary-document-claim-receipt.v1"
)
MAX_COUNTRY_CLAIM_PLAN_BYTES = 4 * 1024 * 1024


class CountryDocumentClaimCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, max_length=200)
    anchor_id: str = Field(min_length=1, max_length=120)
    citation_role: Literal["supporting", "opposing"]
    interpretation_scope: Literal[
        "direct_text",
        "legal_status",
        "temporal_context",
        "version_context",
    ]


class CountryDocumentClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(min_length=1, max_length=200)
    country_code: str = Field(pattern=_COUNTRY_RE.pattern)
    statement_sha256: str = Field(pattern=_SHA256_RE.pattern)
    valid_at: datetime
    disposition: Literal["supported_for_draft", "unresolved", "not_supported"]
    citations: tuple[CountryDocumentClaimCitation, ...] = Field(
        min_length=1,
        max_length=100,
    )

    @field_validator("valid_at")
    @classmethod
    def valid_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("claim valid_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def identity_and_disposition_are_consistent(self) -> "CountryDocumentClaimRecord":
        expected = (
            "urn:globemind:country-claim:"
            f"{self.country_code.casefold()}:{self.statement_sha256}"
        )
        if self.claim_id != expected:
            raise ValueError("claim_id must bind country and statement SHA-256")
        citation_keys = [
            (item.document_id, item.anchor_id, item.citation_role)
            for item in self.citations
        ]
        if len(citation_keys) != len(set(citation_keys)):
            raise ValueError("claim citations must be unique")
        roles = {item.citation_role for item in self.citations}
        if self.disposition == "supported_for_draft" and roles != {"supporting"}:
            raise ValueError("supported draft claim can contain only supporting citations")
        if self.disposition == "unresolved" and roles != {"supporting", "opposing"}:
            raise ValueError("unresolved claim requires supporting and opposing citations")
        if self.disposition == "not_supported" and roles != {"opposing"}:
            raise ValueError("not-supported claim can contain only opposing citations")
        return self


class CountryPrimaryDocumentClaimPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "globemind.country-primary-document-claim-plan.v1"
    ] = COUNTRY_PRIMARY_DOCUMENT_CLAIM_PLAN_SCHEMA_VERSION
    plan_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
    plan_version: str = Field(min_length=1, max_length=120)
    bundle_id: str = Field(min_length=1, max_length=120)
    bundle_version: str = Field(min_length=1, max_length=120)
    bundle_manifest_sha256: str = Field(pattern=_SHA256_RE.pattern)
    claims: tuple[CountryDocumentClaimRecord, ...] = Field(
        min_length=1,
        max_length=2_000,
    )
    owner_identifier: str = Field(pattern=_ACTOR_RE.pattern)
    reviewer_identifier: str = Field(pattern=_ACTOR_RE.pattern)
    reviewed_at: datetime
    review_expires_at: datetime
    review_state: Literal["approved"] = "approved"
    semantic_review_scope: Literal[
        "external_review_asserted_not_verified_by_loader"
    ] = "external_review_asserted_not_verified_by_loader"

    @field_validator("reviewed_at", "review_expires_at")
    @classmethod
    def review_times_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("claim plan review times must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def review_and_claims_are_unique(self) -> "CountryPrimaryDocumentClaimPlan":
        if self.owner_identifier == self.reviewer_identifier:
            raise ValueError("claim plan owner and reviewer must be distinct")
        if self.review_expires_at <= self.reviewed_at:
            raise ValueError("claim plan review expiry must follow review")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim plan IDs must be unique")
        return self


@dataclass(frozen=True)
class LoadedCountryPrimaryDocumentClaimPlan:
    plan: CountryPrimaryDocumentClaimPlan
    artifact_sha256: str
    artifact_bytes: int


class CountryDocumentClaimReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    country_code: str = Field(pattern=_COUNTRY_RE.pattern)
    statement_sha256: str = Field(pattern=_SHA256_RE.pattern)
    disposition: Literal["supported_for_draft", "unresolved", "not_supported"]
    supporting_citation_count: int = Field(ge=0, strict=True)
    opposing_citation_count: int = Field(ge=0, strict=True)
    restricted_license_citation_count: int = Field(ge=0, strict=True)
    readiness_state: Literal[
        "citation_structure_ready_not_semantically_verified",
        "unresolved_conflict",
        "not_supported",
        "license_blocked",
    ]


class CountryPrimaryDocumentClaimReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "globemind.country-primary-document-claim-receipt.v1"
    ] = COUNTRY_PRIMARY_DOCUMENT_CLAIM_RECEIPT_SCHEMA_VERSION
    evaluated_at: datetime
    plan_sha256: str = Field(pattern=_SHA256_RE.pattern)
    bundle_manifest_sha256: str = Field(pattern=_SHA256_RE.pattern)
    claims: tuple[CountryDocumentClaimReadiness, ...]
    facts_published: Literal[False] = False
    public_catalog_mutated: Literal[False] = False
    semantic_entailment: Literal["not_verified_by_loader"] = "not_verified_by_loader"
    source_truth: Literal["not_verified_by_loader"] = "not_verified_by_loader"
    publication_decision: Literal["not_computable"] = "not_computable"
    candidate_acceptance: Literal["not_performed"] = "not_performed"


def load_country_primary_document_claim_plan(
    path: Path,
    *,
    expected_sha256: str,
    evaluated_at: datetime,
) -> LoadedCountryPrimaryDocumentClaimPlan:
    """Load an externally reviewed, statement-body-free claim plan."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise CountryPrimaryDocumentBundleError("evaluated_at must include a timezone")
    if not path.is_absolute():
        raise CountryPrimaryDocumentBundleError("claim plan path must be absolute")
    if not isinstance(expected_sha256, str) or _SHA256_RE.fullmatch(expected_sha256) is None:
        raise CountryPrimaryDocumentBundleError("claim plan SHA-256 is invalid")
    raw = _read_single_link_file(
        path,
        maximum=MAX_COUNTRY_CLAIM_PLAN_BYTES,
        field="country claim plan",
    )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise CountryPrimaryDocumentBundleError("claim plan SHA-256 mismatch")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                CountryPrimaryDocumentBundleError(
                    f"claim plan contains non-finite JSON number: {value}"
                )
            ),
        )
        plan = CountryPrimaryDocumentClaimPlan.model_validate(payload)
    except CountryPrimaryDocumentBundleError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CountryPrimaryDocumentBundleError(
            "claim plan failed strict validation"
        ) from exc
    if plan.reviewed_at > evaluated_at.astimezone(timezone.utc):
        raise CountryPrimaryDocumentBundleError("claim plan review is in the future")
    if plan.review_expires_at <= evaluated_at.astimezone(timezone.utc):
        raise CountryPrimaryDocumentBundleError("claim plan review is expired")
    return LoadedCountryPrimaryDocumentClaimPlan(
        plan=plan,
        artifact_sha256=digest,
        artifact_bytes=len(raw),
    )


def evaluate_country_primary_document_claims(
    plan: LoadedCountryPrimaryDocumentClaimPlan,
    bundle: LoadedCountryPrimaryDocumentBundle,
    *,
    evaluated_at: datetime,
) -> CountryPrimaryDocumentClaimReceipt:
    """Verify citation identity and conflict structure without reading claim text."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise CountryPrimaryDocumentBundleError("evaluated_at must include a timezone")
    now = evaluated_at.astimezone(timezone.utc)
    claim_plan = plan.plan
    if claim_plan.review_expires_at <= now:
        raise CountryPrimaryDocumentBundleError("claim plan review is expired")
    if (
        claim_plan.bundle_id != bundle.bundle.bundle_id
        or claim_plan.bundle_version != bundle.bundle.bundle_version
        or claim_plan.bundle_manifest_sha256 != bundle.manifest_sha256
    ):
        raise CountryPrimaryDocumentBundleError(
            "claim plan does not match the verified primary-document bundle"
        )
    documents = {document.document_id: document for document in bundle.bundle.documents}
    receipts: list[CountryDocumentClaimReadiness] = []
    for claim in claim_plan.claims:
        restricted_count = 0
        supporting_count = 0
        opposing_count = 0
        for citation in claim.citations:
            document = documents.get(citation.document_id)
            if document is None:
                raise CountryPrimaryDocumentBundleError(
                    "claim citation points outside the verified bundle"
                )
            if document.identity.country_code != claim.country_code:
                raise CountryPrimaryDocumentBundleError(
                    "claim citation country does not match the claim"
                )
            anchors = {anchor.anchor_id for anchor in document.text.section_anchors}
            if citation.anchor_id not in anchors:
                raise CountryPrimaryDocumentBundleError(
                    "claim citation anchor is not verified by the bundle"
                )
            temporal = document.temporal
            if (
                claim.valid_at < temporal.effective_from
                or (
                    temporal.effective_until is not None
                    and claim.valid_at >= temporal.effective_until
                )
                or claim.valid_at > temporal.status_as_of
            ):
                raise CountryPrimaryDocumentBundleError(
                    "claim citation is outside the document temporal evidence"
                )
            if document.governance.review_expires_at <= now:
                raise CountryPrimaryDocumentBundleError(
                    "claim citation document review is expired"
                )
            restricted_count += document.governance.license_state == "restricted"
            supporting_count += citation.citation_role == "supporting"
            opposing_count += citation.citation_role == "opposing"
        if restricted_count:
            readiness = "license_blocked"
        elif claim.disposition == "unresolved":
            readiness = "unresolved_conflict"
        elif claim.disposition == "not_supported":
            readiness = "not_supported"
        else:
            readiness = "citation_structure_ready_not_semantically_verified"
        receipts.append(
            CountryDocumentClaimReadiness(
                claim_id=claim.claim_id,
                country_code=claim.country_code,
                statement_sha256=claim.statement_sha256,
                disposition=claim.disposition,
                supporting_citation_count=supporting_count,
                opposing_citation_count=opposing_count,
                restricted_license_citation_count=restricted_count,
                readiness_state=readiness,
            )
        )
    return CountryPrimaryDocumentClaimReceipt(
        evaluated_at=now,
        plan_sha256=plan.artifact_sha256,
        bundle_manifest_sha256=bundle.manifest_sha256,
        claims=tuple(receipts),
    )


__all__ = (
    "COUNTRY_PRIMARY_DOCUMENT_CLAIM_PLAN_SCHEMA_VERSION",
    "COUNTRY_PRIMARY_DOCUMENT_CLAIM_RECEIPT_SCHEMA_VERSION",
    "CountryDocumentClaimCitation",
    "CountryDocumentClaimReadiness",
    "CountryDocumentClaimRecord",
    "CountryPrimaryDocumentClaimPlan",
    "CountryPrimaryDocumentClaimReceipt",
    "LoadedCountryPrimaryDocumentClaimPlan",
    "evaluate_country_primary_document_claims",
    "load_country_primary_document_claim_plan",
)
