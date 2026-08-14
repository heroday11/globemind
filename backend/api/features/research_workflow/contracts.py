"""Versioned contracts for the V1.5 research workflow slice."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ProjectRole = Literal["owner", "reviewer", "reader"]
EvidenceRelation = Literal["support", "opposing", "background"]
HumanDecisionKind = Literal["confirm", "modify", "reject"]
ReviewKind = Literal["peer_review", "approval"]
ReviewOutcome = Literal["approved", "changes_requested", "rejected"]
_SOURCE_URL_SECRET_SUFFIXES = (
    "_api_key",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_secret",
    "_signature",
    "_token",
)


def _source_url_key_is_sensitive(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    compact = normalized.replace("_", "")
    return (
        normalized
        in {
            "authorization",
            "cookie",
            "credentials",
            "password",
            "secret",
            "sig",
            "signature",
            "token",
        }
        or normalized.endswith(_SOURCE_URL_SECRET_SUFFIXES)
        or compact.endswith(
            (
                "apikey",
                "credential",
                "credentials",
                "password",
                "privatekey",
                "secret",
                "signature",
                "token",
            )
        )
    )


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MutationRequest(StrictContract):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=1000)


class ProjectCreateRequest(StrictContract):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=4000)
    scope_countries: list[str] = Field(default_factory=list, max_length=3)
    reason: str = Field(min_length=2, max_length=1000)

    @field_validator("scope_countries")
    @classmethod
    def validate_country_codes(cls, values: list[str]) -> list[str]:
        for value in values:
            normalized = value.strip()
            if normalized and (
                len(normalized) > 16
                or not all(char.isalnum() or char in "_-" for char in normalized)
            ):
                raise ValueError("scope country identifiers must be short codes")
        return values


class MemberChangeRequest(MutationRequest):
    role: Literal["reviewer", "reader"]


class QuestionCreateRequest(MutationRequest):
    question: str = Field(min_length=4, max_length=4000)


class SavedSearchCreateRequest(MutationRequest):
    name: str = Field(min_length=2, max_length=160)
    query: str = Field(min_length=1, max_length=4000)
    filters: dict[str, Any] = Field(default_factory=dict)
    search_snapshot_id: str | None = Field(
        default=None,
        max_length=80,
        pattern=r"^search-snap-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$",
    )
    query_receipt_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    normalized_contract_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    ordered_returned_ids_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def bound_filters(self) -> "SavedSearchCreateRequest":
        import json

        try:
            encoded = json.dumps(self.filters, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("filters must be finite JSON") from exc
        if len(encoded.encode("utf-8")) > 16_000:
            raise ValueError("filters exceed the 16 KB contract limit")
        return self

    @model_validator(mode="after")
    def validate_search_snapshot_reference_group(self) -> "SavedSearchCreateRequest":
        values = (
            self.search_snapshot_id,
            self.query_receipt_sha256,
            self.normalized_contract_sha256,
            self.ordered_returned_ids_sha256,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError(
                "search_snapshot_id, query_receipt_sha256, "
                "normalized_contract_sha256 and ordered_returned_ids_sha256 "
                "must be supplied together"
            )
        return self


class EvidenceCreateRequest(MutationRequest):
    relation: EvidenceRelation
    summary: str = Field(min_length=2, max_length=4000)
    source_id: str = Field(min_length=1, max_length=240)
    source_title: str = Field(default="", max_length=500)
    source_url: str | None = Field(
        default=None,
        max_length=2000,
        pattern=r"^https?://",
    )
    original_anchor: str | None = Field(default=None, max_length=300)
    source_published_at: str | None = Field(default=None, max_length=64)
    article_id: int | None = Field(default=None, ge=1)
    evidence_snapshot_id: str | None = Field(default=None, max_length=120)
    captured_at: str | None = Field(default=None, max_length=64)
    content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    parser_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )
    note: str = Field(default="", max_length=4000)

    @field_validator("source_published_at", "captured_at")
    @classmethod
    def validate_source_timestamps(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_timestamp(value)

    @field_validator("source_url")
    @classmethod
    def validate_public_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\\" in value or any(
            ord(character) <= 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("source_url contains ambiguous URL syntax")
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("source_url is invalid") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("source_url must be a public HTTP(S) locator")
        for raw_key, _value in (
            *parse_qsl(parsed.query, keep_blank_values=True),
            *parse_qsl(parsed.fragment, keep_blank_values=True),
        ):
            if _source_url_key_is_sensitive(raw_key):
                raise ValueError("source_url locator contains credential material")
        return value

    @model_validator(mode="after")
    def validate_snapshot_reference_group(self) -> "EvidenceCreateRequest":
        values = (
            self.article_id,
            self.evidence_snapshot_id,
            self.content_sha256,
            self.captured_at,
            self.parser_version,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError(
                "article_id, evidence_snapshot_id, content_sha256, captured_at and "
                "parser_version must be supplied together"
            )
        return self


class InformationGapCreateRequest(MutationRequest):
    description: str = Field(min_length=2, max_length=4000)
    impact: str = Field(min_length=2, max_length=2000)
    resolution_plan: str = Field(default="", max_length=3000)


class AlternativeHypothesisCreateRequest(MutationRequest):
    statement: str = Field(min_length=4, max_length=4000)
    discriminating_evidence: str = Field(default="", max_length=4000)


class JudgmentCreateRequest(MutationRequest):
    statement: str = Field(min_length=4, max_length=6000)
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=100)
    opposing_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    information_gap_ids: list[str] = Field(default_factory=list, max_length=100)
    alternative_hypothesis_ids: list[str] = Field(default_factory=list, max_length=100)
    uncertainty: str = Field(min_length=2, max_length=4000)


class HumanDecisionCreateRequest(MutationRequest):
    judgment_id: str = Field(min_length=1, max_length=100)
    decision: HumanDecisionKind
    rationale: str = Field(min_length=2, max_length=4000)
    modified_statement: str | None = Field(default=None, max_length=6000)

    @model_validator(mode="after")
    def validate_modified_statement(self) -> "HumanDecisionCreateRequest":
        if self.decision == "modify" and not self.modified_statement:
            raise ValueError("modified_statement is required when decision=modify")
        if self.decision != "modify" and self.modified_statement is not None:
            raise ValueError("modified_statement is only valid when decision=modify")
        return self


class ReviewCreateRequest(MutationRequest):
    review_type: ReviewKind
    target_type: Literal["judgment", "decision"]
    target_id: str = Field(min_length=1, max_length=100)
    outcome: ReviewOutcome
    comment: str = Field(min_length=2, max_length=4000)


class ModelReference(StrictContract):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    use: str = Field(min_length=1, max_length=500)


class ExportManifestCreateRequest(MutationRequest):
    report_title: str = Field(min_length=2, max_length=300)
    cutoff_at: str = Field(min_length=10, max_length=64)
    cutoff_basis: str = Field(min_length=2, max_length=1000)
    method: str = Field(min_length=2, max_length=6000)
    models: list[ModelReference] = Field(default_factory=list, max_length=50)
    uncertainty: str = Field(min_length=2, max_length=4000)

    @field_validator("cutoff_at")
    @classmethod
    def validate_cutoff_timestamp(cls, value: str) -> str:
        return _normalize_timestamp(value)


def _normalize_timestamp(value: str) -> str:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ProjectMember(StrictContract):
    username: str
    role: ProjectRole
    added_at: str
    added_by: str


class ChangeRecord(StrictContract):
    change_id: str
    version: int = Field(ge=1)
    previous_version: int | None
    actor: str
    timestamp: str
    reason: str
    action: str
    resource_type: str
    resource_id: str
    previous_change_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    change_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuditEvent(StrictContract):
    event_id: str
    project_id: str
    actor: str
    timestamp: str
    action: str
    resource_type: str
    resource_id: str
    version: int = Field(ge=1)
    previous_version: int | None
    reason_sha256: str
    reason_length: int = Field(ge=0)
    changed_fields: list[str]
    previous_event_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResearchQuestion(StrictContract):
    id: str
    question: str
    created_at: str
    created_by: str


class SavedSearch(StrictContract):
    id: str
    name: str
    query: str
    filters: dict[str, Any]
    query_sha256: str
    snapshot_status: Literal["verified", "unavailable"] = "unavailable"
    # Preserved as an always-null legacy field so already-persisted V1 projects
    # remain readable while the typed search_snapshot_id is explicit.
    snapshot_id: None = None
    search_snapshot_id: str | None = None
    query_receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_contract_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ordered_returned_ids_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    snapshot_integrity_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    snapshot_captured_at: str | None = None
    receipt_method_version: str | None = None
    entity_catalog_version: str | None = None
    entity_catalog_review_status: Literal["approved", "review_required"] | None = None
    result_id_namespace: Literal[
        "news", "l1_event", "l2_trend", "l3_macro", "none"
    ] | None = None
    returned_result_count: int | None = Field(default=None, ge=0, le=100)
    result_page: int | None = Field(default=None, ge=1)
    result_total: int | None = Field(default=None, ge=0)
    result_cutoff: str | None = None
    result_coverage_start: str | None = None
    result_coverage_end: str | None = None
    result_coverage_status: Literal["available", "partial", "unavailable"] | None = None
    snapshot_reason: str
    created_at: str
    created_by: str

    @model_validator(mode="after")
    def validate_snapshot_state(self) -> "SavedSearch":
        required_verified = (
            self.search_snapshot_id,
            self.query_receipt_sha256,
            self.normalized_contract_sha256,
            self.ordered_returned_ids_sha256,
            self.snapshot_integrity_sha256,
            self.snapshot_captured_at,
            self.receipt_method_version,
            self.entity_catalog_version,
            self.entity_catalog_review_status,
            self.result_id_namespace,
            self.returned_result_count,
            self.result_page,
            self.result_total,
            self.result_coverage_status,
        )
        if self.snapshot_status == "verified" and not all(
            value is not None for value in required_verified
        ):
            raise ValueError("verified saved search has incomplete snapshot metadata")
        if self.snapshot_status == "unavailable" and any(
            value is not None
            for value in (
                *required_verified,
                self.result_cutoff,
                self.result_coverage_start,
                self.result_coverage_end,
            )
        ):
            raise ValueError("unavailable saved search has contradictory snapshot metadata")
        return self


class EvidenceItem(StrictContract):
    id: str
    relation: EvidenceRelation
    summary: str
    source_id: str
    source_title: str
    source_url: str | None
    original_anchor: str | None
    source_published_at: str | None
    article_id: int | None
    evidence_snapshot_id: str | None
    content_sha256: str | None
    captured_at: str | None
    parser_version: str | None
    snapshot_status: Literal["verified", "unavailable"]
    snapshot_reason: str
    provenance_status: Literal["verified", "declared", "incomplete"]
    provenance_reason: str
    note: str
    created_at: str
    created_by: str


class InformationGap(StrictContract):
    id: str
    description: str
    impact: str
    resolution_plan: str
    created_at: str
    created_by: str


class AlternativeHypothesis(StrictContract):
    id: str
    statement: str
    discriminating_evidence: str
    created_at: str
    created_by: str


class Judgment(StrictContract):
    id: str
    statement: str
    supporting_evidence_ids: list[str]
    opposing_evidence_ids: list[str]
    information_gap_ids: list[str]
    alternative_hypothesis_ids: list[str]
    uncertainty: str
    created_at: str
    created_by: str


class HumanDecision(StrictContract):
    id: str
    judgment_id: str
    decision: HumanDecisionKind
    rationale: str
    modified_statement: str | None
    created_at: str
    created_by: str


class ReviewRecord(StrictContract):
    id: str
    review_type: ReviewKind
    target_type: Literal["judgment", "decision"]
    target_id: str
    outcome: ReviewOutcome
    comment: str
    created_at: str
    created_by: str


class ManifestSource(StrictContract):
    evidence_id: str
    relation: EvidenceRelation
    source_id: str
    source_title: str
    source_url: str | None
    original_anchor: str | None
    source_published_at: str | None
    article_id: int | None
    evidence_snapshot_id: str | None
    content_sha256: str | None
    captured_at: str | None
    parser_version: str | None
    snapshot_status: Literal["verified", "unavailable"]
    provenance_status: Literal["verified", "declared", "incomplete"]


class ManifestModelDisclosure(StrictContract):
    status: Literal["declared", "not_used"]
    items: list[ModelReference]


class ManifestAssurance(StrictContract):
    workflow_gate: Literal["passed"] = "passed"
    publication_status: Literal["reviewed_draft"] = "reviewed_draft"
    researcher_acceptance: Literal["unavailable"] = "unavailable"
    source_verification: Literal[
        "evidence_ledger_verified",
        "researcher_declared_not_server_verified",
        "incomplete",
    ]


class ReportExportManifest(StrictContract):
    schema_version: Literal["research-export-manifest-v1"] = (
        "research-export-manifest-v1"
    )
    manifest_id: str
    export_version: int = Field(ge=1)
    project_id: str
    project_version: int = Field(ge=1)
    previous_project_version: int = Field(ge=1)
    report_title: str
    created_at: str
    created_by: str
    sources: list[ManifestSource]
    cutoff: dict[str, str]
    method: str
    model: ManifestModelDisclosure
    uncertainty: str
    opposing_evidence: list[ManifestSource]
    gaps: list[InformationGap]
    judgments: list[Judgment]
    decisions: list[HumanDecision]
    reviews: list[ReviewRecord]
    research_questions: list[ResearchQuestion]
    saved_searches: list[SavedSearch]
    alternative_hypotheses: list[AlternativeHypothesis]
    assurance: ManifestAssurance
    integrity_sha256: str


class ManifestProjectScope(StrictContract):
    title: str
    description: str
    countries: list[str]
    capture_status: Literal["captured_in_manifest"] = "captured_in_manifest"


class ManifestEvidenceSource(ManifestSource):
    summary: str
    note: str


class ReportExportManifestV2(ReportExportManifest):
    schema_version: Literal["research-export-manifest-v2"] = (
        "research-export-manifest-v2"
    )
    project_scope: ManifestProjectScope
    sources: list[ManifestEvidenceSource]
    opposing_evidence: list[ManifestEvidenceSource]


class ResearchProject(StrictContract):
    schema_version: Literal["research-project-v1"] = "research-project-v1"
    id: str
    title: str
    description: str
    scope_countries: list[str]
    owner: str
    members: list[ProjectMember]
    version: int = Field(ge=1)
    created_at: str
    updated_at: str
    research_questions: list[ResearchQuestion]
    saved_searches: list[SavedSearch]
    evidence_items: list[EvidenceItem]
    information_gaps: list[InformationGap]
    alternative_hypotheses: list[AlternativeHypothesis]
    judgments: list[Judgment]
    human_decisions: list[HumanDecision]
    reviews: list[ReviewRecord]
    export_manifests: list[ReportExportManifest | ReportExportManifestV2]
    change_history: list[ChangeRecord]
    audit_events: list[AuditEvent]
    storage: dict[str, str]
    state_integrity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProjectSummary(StrictContract):
    id: str
    title: str
    scope_countries: list[str]
    role: ProjectRole
    version: int
    updated_at: str
    workflow_counts: dict[str, int]


class ProjectListResponse(StrictContract):
    schema_version: Literal["research-project-list-v1"] = "research-project-list-v1"
    storage_status: Literal["available"] = "available"
    projects: list[ProjectSummary]


class VersionDiffEntry(StrictContract):
    id: str
    value: Any


class VersionModifiedEntry(StrictContract):
    id: str
    before: Any
    after: Any
    changed_fields: list[str]


class VersionDiffCategory(StrictContract):
    id: str
    added: list[VersionDiffEntry]
    removed: list[VersionDiffEntry]
    modified: list[VersionModifiedEntry]


class ResearchVersionComparison(StrictContract):
    schema_version: Literal["research-version-comparison-v1"] = (
        "research-version-comparison-v1"
    )
    project_id: str
    from_export: dict[str, Any]
    to_export: dict[str, Any]
    categories: list[VersionDiffCategory]
    summary: dict[str, int]
    access: dict[str, Any]


__all__ = (
    "AlternativeHypothesisCreateRequest",
    "EvidenceCreateRequest",
    "ExportManifestCreateRequest",
    "HumanDecisionCreateRequest",
    "InformationGapCreateRequest",
    "JudgmentCreateRequest",
    "MemberChangeRequest",
    "ProjectCreateRequest",
    "ProjectListResponse",
    "QuestionCreateRequest",
    "ResearchProject",
    "ResearchVersionComparison",
    "ReportExportManifest",
    "ReportExportManifestV2",
    "ReviewCreateRequest",
    "SavedSearchCreateRequest",
)
