"""Deterministic download artifacts rendered from immutable export manifests."""

from __future__ import annotations

import copy
import csv
import hashlib
import hmac
import html
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

from pydantic import ValidationError

from .contracts import ReportExportManifest, ReportExportManifestV2

ARTIFACT_SCHEMA_VERSION = "research-export-artifact-v3"
ARTIFACT_SOURCE_POLICY = "persisted_export_manifest_only"
CITATION_EXPORT_SCHEMA_VERSION = "research-citation-export-v3"
CITATION_STYLE_NAME = "generic_structured_draft"
CITATION_STYLE_STATUS = "draft_not_verified"
FIELD_SELECTION_SCHEMA_VERSION = "research-export-field-selection-v1"
SOURCE_LICENSE_SCHEMA_VERSION = "research-source-license-boundary-v1"
SOURCE_LICENSE_STATUS = "unknown"
SOURCE_LICENSE_REASON_CODE = "SOURCE_LICENSE_NOT_CAPTURED_IN_MANIFEST"
SOURCE_LICENSE_NOTICE = (
    "Locator availability does not grant reuse permission; verify each source's "
    "terms before redistribution."
)
ARTIFACT_DISTRIBUTION_STATUS = "not_for_publication"
ARTIFACT_DISTRIBUTION_WARNING = (
    "REVIEWED DRAFT — RESEARCHER ACCEPTANCE UNAVAILABLE — NOT FOR PUBLICATION"
)
ArtifactFormat = Literal["json", "markdown", "html", "csv"]
EXPORT_OPTIONAL_FIELDS = (
    "project_scope",
    "cutoff",
    "method",
    "uncertainty",
    "research_questions",
    "saved_search_receipts",
    "evidence_summaries",
    "information_gaps",
    "alternative_hypotheses",
    "judgments",
    "human_decisions",
    "review_outcomes",
)
DEFAULT_EXPORT_FIELDS = EXPORT_OPTIONAL_FIELDS
MANDATORY_EXPORT_FIELDS = (
    "identity",
    "version",
    "claims_and_citations",
    "assurance",
    "distribution_boundary",
    "license_boundary",
    "rendering_assurance",
)
ALWAYS_EXCLUDED_SENSITIVE_FIELDS = (
    "created_by",
    "source_note",
    "saved_search_query",
    "saved_search_filters",
    "decision_rationale",
    "review_comment",
)
MAX_ARTIFACT_BODY_BYTES = 64 * 1024 * 1024
MAX_HTML_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_CSV_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_CSV_EVIDENCE_ROWS = 5_000
MAX_CITATION_COUNT = 5_000
MAX_CLAIM_BINDING_COUNT = 5_000
MAX_CITATIONS_PER_CLAIM = 1_000
MAX_CITATION_TEXT_BYTES = 4_096
_SAFE_FILENAME_COMPONENT = re.compile(r"[^A-Za-z0-9_-]+")
_SAFE_ARTIFACT_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
_CSV_FORMULA_PREFIX = re.compile(r"^[\s\x00-\x1f\x7f\ufeff]*[=+@-]")
_MANIFEST_SCHEMAS = frozenset(
    {"research-export-manifest-v1", "research-export-manifest-v2"}
)
_CSV_COLUMNS = (
    "artifact_schema_version",
    "artifact_format",
    "artifact_kind",
    "source_policy",
    "deterministic",
    "new_facts_generated",
    "unreferenced_ai_narrative_generated",
    "report_content_sha256",
    "manifest_integrity_sha256",
    "field_selection_schema_version",
    "selected_fields",
    "mandatory_fields",
    "always_excluded_sensitive_fields",
    "selected_content_sha256",
    "manifest_schema_version",
    "manifest_id",
    "export_version",
    "project_version",
    "project_id",
    "report_title",
    "project_scope_title",
    "project_scope_description",
    "project_scope_countries",
    "project_scope_capture_status",
    "cutoff_at",
    "cutoff_basis",
    "method_description",
    "model_disclosure",
    "uncertainty",
    "publication_status",
    "researcher_acceptance",
    "distribution_status",
    "distribution_warning",
    "workflow_gate",
    "source_verification",
    "source_license_schema_version",
    "license_status",
    "license_redistribution_permission",
    "source_license_reason_code",
    "source_license_notice",
    "citation_schema_version",
    "citation_style_name",
    "citation_style_status",
    "citation_id",
    "footnote_number",
    "locator_status",
    "locator_url",
    "locator_permanence",
    "locator_reason_code",
    "reference_text",
    "bound_claim_ids",
    "bound_claim_count",
    "bound_claim_unknown_dispositions",
    "evidence_index",
    "relation",
    "evidence_id",
    "source_id",
    "source_title",
    "source_url",
    "original_anchor",
    "source_published_at",
    "snapshot_status",
    "provenance_status",
    "summary",
    "research_questions",
    "saved_search_receipts",
    "information_gaps",
    "alternative_hypotheses",
    "judgments",
    "human_decisions",
    "review_outcomes",
)


class ResearchArtifactError(ValueError):
    """A persisted manifest cannot be represented by the artifact contract."""


@dataclass(frozen=True)
class ResearchExportArtifact:
    schema_version: str
    artifact_format: ArtifactFormat
    filename: str
    media_type: str
    body: bytes
    response_sha256: str
    report_content_sha256: str
    manifest_integrity_sha256: str
    publication_status: str
    researcher_acceptance: str
    distribution_status: str
    field_selection_schema_version: str
    selected_fields: tuple[str, ...]
    source_license_status: str


class _BoundedUtf8Buffer:
    """Collect deterministic text while rejecting before aggregate amplification."""

    def __init__(self, maximum_bytes: int, error_message: str) -> None:
        self._maximum_bytes = maximum_bytes
        self._error_message = error_message
        self._size = 0
        self._chunks: list[bytes] = []

    def write(self, value: str) -> int:
        try:
            encoded = value.encode("utf-8")
        except UnicodeError as exc:
            raise ResearchArtifactError("artifact text is not valid UTF-8") from exc
        next_size = self._size + len(encoded)
        if next_size > self._maximum_bytes:
            raise ResearchArtifactError(self._error_message)
        self._chunks.append(encoded)
        self._size = next_size
        return len(value)

    def to_bytes(self) -> bytes:
        return b"".join(self._chunks)


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ResearchArtifactError("artifact content is not canonical JSON") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _normalize_export_fields(value: Any) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_EXPORT_FIELDS
    if not isinstance(value, (list, tuple)) or not value:
        raise ResearchArtifactError("artifact field selection is invalid")
    if len(value) > len(EXPORT_OPTIONAL_FIELDS):
        raise ResearchArtifactError("artifact field selection exceeds the field limit")
    normalized: list[str] = []
    for field in value:
        if (
            not isinstance(field, str)
            or not field
            or len(field) > 64
            or field not in EXPORT_OPTIONAL_FIELDS
        ):
            raise ResearchArtifactError("artifact field selection is unsupported")
        normalized.append(field)
    if len(set(normalized)) != len(normalized):
        raise ResearchArtifactError("artifact field selection is ambiguous")
    selected = set(normalized)
    return tuple(field for field in EXPORT_OPTIONAL_FIELDS if field in selected)


def _safe_public_locator(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if len(value.encode("utf-8")) > 2_000:
            return None
    except UnicodeError:
        return None
    if "\\" in value or any(
        ord(character) <= 32 or ord(character) == 127 for character in value
    ):
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return value


def _bounded_citation_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ResearchArtifactError(f"citation {field} is invalid")
    try:
        normalized = " ".join(value.replace("\x7f", " ").split())
        encoded = normalized.encode("utf-8")
    except UnicodeError as exc:
        raise ResearchArtifactError(f"citation {field} is not valid UTF-8") from exc
    if len(encoded) > MAX_CITATION_TEXT_BYTES:
        raise ResearchArtifactError(f"citation {field} exceeds the byte limit")
    return normalized


def _validated_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    manifest = copy.deepcopy(dict(value))
    schema_version = manifest.get("schema_version")
    claimed_integrity = manifest.pop("integrity_sha256", None)
    if (
        schema_version not in _MANIFEST_SCHEMAS
        or not isinstance(claimed_integrity, str)
        or not re.fullmatch(r"[0-9a-f]{64}", claimed_integrity)
        or not hmac.compare_digest(claimed_integrity, _sha256(manifest))
    ):
        raise ResearchArtifactError("export manifest integrity is unavailable")
    manifest["integrity_sha256"] = claimed_integrity
    assurance = manifest.get("assurance")
    if not isinstance(assurance, dict) or (
        assurance.get("publication_status") != "reviewed_draft"
        or assurance.get("researcher_acceptance") != "unavailable"
    ):
        raise ResearchArtifactError("export manifest assurance boundary is invalid")
    contract = (
        ReportExportManifestV2
        if schema_version == "research-export-manifest-v2"
        else ReportExportManifest
    )
    try:
        contract.model_validate(manifest)
    except ValidationError as exc:
        raise ResearchArtifactError("export manifest contract is invalid") from exc
    return manifest


def _project_scope(manifest: Mapping[str, Any]) -> dict[str, Any]:
    scope = manifest.get("project_scope")
    if manifest.get("schema_version") == "research-export-manifest-v2":
        if not isinstance(scope, dict):
            raise ResearchArtifactError("export manifest project scope is unavailable")
        return copy.deepcopy(scope)
    return {
        "title": None,
        "description": None,
        "countries": [],
        "capture_status": "unavailable_in_manifest_v1",
    }


def _evidence_groups(manifest: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise ResearchArtifactError("export manifest sources are unavailable")
    groups: dict[str, list[dict[str, Any]]] = {
        "support": [],
        "opposing": [],
        "background": [],
    }
    for source in sources:
        if not isinstance(source, dict) or source.get("relation") not in groups:
            raise ResearchArtifactError("export manifest source relation is invalid")
        normalized = copy.deepcopy(source)
        if manifest.get("schema_version") == "research-export-manifest-v1":
            normalized["summary"] = "unavailable_in_manifest_v1"
            normalized["note"] = "unavailable_in_manifest_v1"
        elif not isinstance(normalized.get("summary"), str) or not isinstance(
            normalized.get("note"), str
        ):
            raise ResearchArtifactError("export manifest source detail is unavailable")
        # A verified manifest hash proves integrity, not that a legacy locator is
        # safe to redistribute. Export only credential-free HTTP(S) locators.
        raw_source_url = normalized.get("source_url")
        normalized["source_url"] = _safe_public_locator(raw_source_url)
        normalized["_locator_reason_code"] = (
            "SOURCE_LOCATOR_NOT_PROVIDED"
            if raw_source_url is None
            else "SOURCE_LOCATOR_UNSAFE"
            if normalized["source_url"] is None
            else None
        )
        groups[str(source["relation"])].append(normalized)
    return groups


def _citation_export(
    manifest: Mapping[str, Any],
    evidence_groups: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    sources = [
        source
        for relation in ("support", "opposing", "background")
        for source in evidence_groups[relation]
    ]
    if len(sources) > MAX_CITATION_COUNT:
        raise ResearchArtifactError("artifact exceeds the citation count limit")

    citations: list[dict[str, Any]] = []
    citation_by_evidence_id: dict[str, dict[str, Any]] = {}
    citation_ids: set[str] = set()
    project_id = _bounded_citation_text(manifest.get("project_id"), field="project id")
    for footnote_number, source in enumerate(sources, start=1):
        evidence_id = _bounded_citation_text(
            source.get("evidence_id"), field="evidence id"
        )
        source_id = _bounded_citation_text(source.get("source_id"), field="source id")
        if not evidence_id or evidence_id in citation_by_evidence_id:
            raise ResearchArtifactError("citation evidence identifiers are ambiguous")
        citation_id = "citation-" + _sha256(
            {
                "schema_version": "research-citation-identity-v1",
                "project_id": project_id,
                "evidence_id": evidence_id,
            }
        )[:24]
        if citation_id in citation_ids:
            raise ResearchArtifactError("citation identifier collision")
        citation_ids.add(citation_id)

        source_title = _bounded_citation_text(
            source.get("source_title"), field="source title"
        )
        original_anchor_value = source.get("original_anchor")
        original_anchor = (
            _bounded_citation_text(original_anchor_value, field="original anchor")
            if isinstance(original_anchor_value, str)
            else None
        )
        published_value = source.get("source_published_at")
        source_published_at = (
            _bounded_citation_text(published_value, field="publication time")
            if isinstance(published_value, str)
            else None
        )
        source_url_value = source.get("source_url")
        locator_url = _safe_public_locator(source_url_value)
        if locator_url is None:
            locator = {
                "status": "unavailable",
                "url": None,
                "permanence": "unavailable",
                "reason_code": (
                    source.get("_locator_reason_code")
                    or "SOURCE_LOCATOR_UNSAFE"
                ),
            }
        else:
            locator = {
                "status": "declared_not_verified",
                "url": locator_url,
                "permanence": "not_verified",
                "reason_code": "HTTP_LOCATOR_PERSISTED_PERMANENCE_NOT_VERIFIED",
            }
        reference_parts = [
            source_title or "[untitled source]",
            f"Source ID: {source_id}",
            f"Published: {source_published_at or 'unavailable'}",
            f"Locator: {locator_url or 'unavailable'}",
            f"Anchor: {original_anchor or 'unavailable'}",
        ]
        reference_text = _bounded_citation_text(
            ". ".join(reference_parts) + ".", field="reference text"
        )
        citation = {
            "citation_id": citation_id,
            "evidence_id": evidence_id,
            "footnote_number": footnote_number,
            "relation": source["relation"],
            "locator": locator,
            "source": {
                "source_id": source_id,
                "source_title": source_title,
                "source_published_at": source_published_at,
                "original_anchor": original_anchor,
            },
            "license": {
                "status": SOURCE_LICENSE_STATUS,
                "redistribution_permission": "not_established",
                "reason_code": SOURCE_LICENSE_REASON_CODE,
            },
            "reference_text": reference_text,
        }
        citations.append(citation)
        citation_by_evidence_id[evidence_id] = citation

    judgments = manifest.get("judgments")
    if not isinstance(judgments, list):
        raise ResearchArtifactError("citation claim bindings are unavailable")
    if len(judgments) > MAX_CLAIM_BINDING_COUNT:
        raise ResearchArtifactError("artifact exceeds the claim binding count limit")
    gaps = manifest.get("gaps")
    if not isinstance(gaps, list) or len(gaps) > MAX_CLAIM_BINDING_COUNT:
        raise ResearchArtifactError("claim unknown disposition is unavailable")
    available_gap_ids: set[str] = set()
    for gap in gaps:
        if not isinstance(gap, dict):
            raise ResearchArtifactError("claim unknown disposition is unavailable")
        gap_id = _bounded_citation_text(gap.get("id"), field="information gap id")
        if not gap_id or gap_id in available_gap_ids:
            raise ResearchArtifactError("claim unknown disposition is unavailable")
        available_gap_ids.add(gap_id)
    claim_bindings: list[dict[str, Any]] = []
    claim_ids: set[str] = set()
    for judgment in judgments:
        if not isinstance(judgment, dict):
            raise ResearchArtifactError("citation claim binding is invalid")
        judgment_id = _bounded_citation_text(
            judgment.get("id"), field="judgment id"
        )
        statement = _bounded_citation_text(
            judgment.get("statement"), field="claim statement"
        )
        supporting_ids = judgment.get("supporting_evidence_ids")
        opposing_ids = judgment.get("opposing_evidence_ids")
        information_gap_ids = judgment.get("information_gap_ids")
        if not isinstance(supporting_ids, list) or not isinstance(opposing_ids, list):
            raise ResearchArtifactError("citation claim evidence binding is invalid")
        if not isinstance(information_gap_ids, list) or not information_gap_ids:
            raise ResearchArtifactError("claim unknown disposition is unavailable")
        normalized_gap_ids = [
            _bounded_citation_text(value, field="information gap id")
            for value in information_gap_ids
        ]
        if (
            any(not value for value in normalized_gap_ids)
            or len(set(normalized_gap_ids)) != len(normalized_gap_ids)
            or any(value not in available_gap_ids for value in normalized_gap_ids)
        ):
            raise ResearchArtifactError("claim unknown disposition is unavailable")
        if len(supporting_ids) + len(opposing_ids) > MAX_CITATIONS_PER_CLAIM:
            raise ResearchArtifactError("claim exceeds the citation count limit")
        normalized_evidence_ids = [
            str(value) for value in (*supporting_ids, *opposing_ids)
        ]
        if len(set(normalized_evidence_ids)) != len(normalized_evidence_ids):
            raise ResearchArtifactError("claim citation bindings are ambiguous")
        try:
            supporting = [
                citation_by_evidence_id[str(evidence_id)] for evidence_id in supporting_ids
            ]
            opposing = [
                citation_by_evidence_id[str(evidence_id)] for evidence_id in opposing_ids
            ]
        except KeyError as exc:
            raise ResearchArtifactError(
                "claim references an unavailable citation"
            ) from exc
        claim_id = "claim-" + _sha256(
            {
                "schema_version": "research-claim-identity-v1",
                "project_id": project_id,
                "judgment_id": judgment_id,
            }
        )[:24]
        if claim_id in claim_ids:
            raise ResearchArtifactError("claim identifiers are ambiguous")
        claim_ids.add(claim_id)
        try:
            statement_sha256 = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        except UnicodeError as exc:
            raise ResearchArtifactError("claim statement is not valid UTF-8") from exc
        claim_bindings.append(
            {
                "claim_id": claim_id,
                "judgment_id": judgment_id,
                "statement": statement,
                "statement_sha256": statement_sha256,
                "supporting_citation_ids": [row["citation_id"] for row in supporting],
                "opposing_citation_ids": [row["citation_id"] for row in opposing],
                "footnote_numbers": [
                    row["footnote_number"] for row in (*supporting, *opposing)
                ],
                "unknown_disposition": {
                    "state": "explicit_unresolved_information_gaps",
                    "reason_code": "CLAIM_HAS_LINKED_INFORMATION_GAPS",
                    "fact_verification": "not_verified",
                    "information_gap_ids": normalized_gap_ids,
                },
            }
        )
    return {
        "schema_version": CITATION_EXPORT_SCHEMA_VERSION,
        "style": {
            "name": CITATION_STYLE_NAME,
            "status": CITATION_STYLE_STATUS,
            "verified_standard": None,
            "not_claimed_standards": ["APA", "Chicago", "GB/T 7714"],
        },
        "citations": citations,
        "claim_bindings": claim_bindings,
    }


def _review_groups(manifest: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    reviews = manifest.get("reviews")
    if not isinstance(reviews, list):
        raise ResearchArtifactError("export manifest reviews are unavailable")
    peer_reviews: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    for review in reviews:
        if not isinstance(review, dict):
            raise ResearchArtifactError("export manifest review is invalid")
        review_type = review.get("review_type")
        if review_type == "peer_review":
            peer_reviews.append(copy.deepcopy(review))
        elif review_type == "approval":
            approvals.append(copy.deepcopy(review))
        else:
            raise ResearchArtifactError("export manifest review type is invalid")
    return {"peer_reviews": peer_reviews, "approvals": approvals}


def _project_rows(
    rows: Any,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ResearchArtifactError("selected artifact content is unavailable")
    return [
        {field: copy.deepcopy(row.get(field)) for field in fields}
        for row in rows
    ]


def _selected_report_content(
    manifest: Mapping[str, Any],
    evidence_groups: Mapping[str, list[dict[str, Any]]],
    selected_fields: tuple[str, ...],
) -> dict[str, Any]:
    evidence = [
        source
        for relation in ("support", "opposing", "background")
        for source in evidence_groups[relation]
    ]
    review_groups = _review_groups(manifest)
    values: dict[str, Any] = {
        "project_scope": _project_scope(manifest),
        "cutoff": copy.deepcopy(manifest["cutoff"]),
        "method": {
            "description": manifest["method"],
            "model_disclosure": copy.deepcopy(manifest["model"]),
        },
        "uncertainty": manifest["uncertainty"],
        "research_questions": _project_rows(
            manifest["research_questions"],
            ("id", "question"),
        ),
        "saved_search_receipts": _project_rows(
            manifest["saved_searches"],
            (
                "id",
                "name",
                "query_sha256",
                "snapshot_status",
                "search_snapshot_id",
                "query_receipt_sha256",
                "normalized_contract_sha256",
                "ordered_returned_ids_sha256",
                "snapshot_integrity_sha256",
                "snapshot_captured_at",
                "receipt_method_version",
                "entity_catalog_version",
                "entity_catalog_review_status",
                "result_id_namespace",
                "returned_result_count",
                "result_page",
                "result_total",
                "result_cutoff",
                "result_coverage_start",
                "result_coverage_end",
                "result_coverage_status",
                "snapshot_reason",
            ),
        ),
        "evidence_summaries": _project_rows(
            evidence,
            (
                "evidence_id",
                "relation",
                "summary",
                "source_id",
                "snapshot_status",
                "provenance_status",
            ),
        ),
        "information_gaps": _project_rows(
            manifest["gaps"],
            ("id", "description", "impact", "resolution_plan"),
        ),
        "alternative_hypotheses": _project_rows(
            manifest["alternative_hypotheses"],
            ("id", "statement", "discriminating_evidence"),
        ),
        "judgments": _project_rows(
            manifest["judgments"],
            (
                "id",
                "statement",
                "supporting_evidence_ids",
                "opposing_evidence_ids",
                "information_gap_ids",
                "alternative_hypothesis_ids",
                "uncertainty",
            ),
        ),
        "human_decisions": _project_rows(
            manifest["decisions"],
            ("id", "judgment_id", "decision", "modified_statement"),
        ),
        "review_outcomes": {
            "peer_reviews": _project_rows(
                review_groups["peer_reviews"],
                ("id", "review_type", "target_type", "target_id", "outcome"),
            ),
            "approvals": _project_rows(
                review_groups["approvals"],
                ("id", "review_type", "target_type", "target_id", "outcome"),
            ),
        },
    }
    return {field: values[field] for field in selected_fields}


def _report_content(
    manifest: Mapping[str, Any],
    selected_fields: tuple[str, ...],
) -> dict[str, Any]:
    evidence_groups = _evidence_groups(manifest)
    version = {
        "manifest_schema_version": manifest["schema_version"],
        "manifest_id": manifest["manifest_id"],
        "export_version": manifest["export_version"],
        "project_version": manifest["project_version"],
        "previous_project_version": manifest["previous_project_version"],
        "created_at": manifest["created_at"],
    }
    return {
        "report_title": manifest["report_title"],
        "project_id": manifest["project_id"],
        "version": version,
        "field_selection": {
            "schema_version": FIELD_SELECTION_SCHEMA_VERSION,
            "selected_fields": list(selected_fields),
            "mandatory_fields": list(MANDATORY_EXPORT_FIELDS),
            "always_excluded_sensitive_fields": list(
                ALWAYS_EXCLUDED_SENSITIVE_FIELDS
            ),
        },
        "selected_content": _selected_report_content(
            manifest,
            evidence_groups,
            selected_fields,
        ),
        "citation_export": _citation_export(manifest, evidence_groups),
        "assurance": copy.deepcopy(manifest["assurance"]),
        "distribution_boundary": {
            "status": ARTIFACT_DISTRIBUTION_STATUS,
            "warning": ARTIFACT_DISTRIBUTION_WARNING,
        },
        "license_boundary": {
            "schema_version": SOURCE_LICENSE_SCHEMA_VERSION,
            "status": SOURCE_LICENSE_STATUS,
            "redistribution_permission": "not_established",
            "reason_code": SOURCE_LICENSE_REASON_CODE,
            "notice": SOURCE_LICENSE_NOTICE,
        },
        "manifest_integrity_sha256": manifest["integrity_sha256"],
        "rendering_assurance": {
            "source_policy": ARTIFACT_SOURCE_POLICY,
            "deterministic": True,
            "new_facts_generated": False,
            "unreferenced_ai_narrative_generated": False,
        },
    }


def _markdown_json(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    )
    longest = max((len(run) for run in re.findall(r"`+", rendered)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}json\n{rendered}\n{fence}"


def _markdown_text(value: Any) -> str:
    text = str(value)
    for character in "\\`*_{}[]<>()#+-.!|":
        text = text.replace(character, f"\\{character}")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "  \n")


def _report_sections(report: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    selected = report["selected_content"]
    headings = {
        "project_scope": "Project and country scope",
        "cutoff": "Cutoff and basis",
        "method": "Method and version disclosure",
        "uncertainty": "Uncertainty",
        "research_questions": "Research questions",
        "saved_search_receipts": "Saved search receipts without query or filters",
        "evidence_summaries": "Evidence summaries without notes",
        "information_gaps": "Information gaps",
        "alternative_hypotheses": "Alternative hypotheses",
        "judgments": "Judgments",
        "human_decisions": "Human decisions without rationale",
        "review_outcomes": "Peer review and approval outcomes without comments",
    }
    sections: list[tuple[str, Any]] = [
        ("Export field selection", report["field_selection"]),
    ]
    for field in selected:
        if field == "evidence_summaries":
            for relation, heading in (
                ("support", "Supporting evidence references"),
                ("opposing", "Opposing evidence references"),
                ("background", "Background evidence references"),
            ):
                sections.append(
                    (
                        heading,
                        [
                            row
                            for row in selected[field]
                            if row["relation"] == relation
                        ],
                    )
                )
        else:
            sections.append((headings[field], selected[field]))
    sections.extend(
        (
            ("Citation export contract", report["citation_export"]),
            ("Source permission boundary", report["license_boundary"]),
            ("Manifest assurance", report["assurance"]),
            ("Rendering assurance", report["rendering_assurance"]),
        )
    )
    return tuple(sections)


def _markdown_document(
    report: Mapping[str, Any],
    *,
    report_content_sha256: str,
) -> bytes:
    assurance = report["assurance"]
    version = report["version"]
    lines = [
        f"# {ARTIFACT_DISTRIBUTION_WARNING}",
        "",
        f"## {_markdown_text(report['report_title'])}",
        "",
        "## Artifact contract",
        "",
        f"- Schema: `{ARTIFACT_SCHEMA_VERSION}`",
        "- Format: `markdown`",
        f"- Source policy: `{ARTIFACT_SOURCE_POLICY}`",
        f"- Report content SHA-256: `{report_content_sha256}`",
        f"- Manifest integrity SHA-256: `{report['manifest_integrity_sha256']}`",
        f"- Manifest ID: `{_markdown_text(version['manifest_id'])}`",
        f"- Export version: `{version['export_version']}`",
        f"- Project version: `{version['project_version']}`",
        f"- Publication status: `{_markdown_text(assurance['publication_status'])}`",
        "- Researcher acceptance: "
        f"`{_markdown_text(assurance['researcher_acceptance'])}`",
    ]
    for heading, value in _report_sections(report):
        lines.extend(("", f"## {heading}", "", _markdown_json(value)))
    citation_export = report["citation_export"]
    lines.extend(("", "## Claim citation bindings", ""))
    for claim in citation_export["claim_bindings"]:
        markers = " ".join(
            f"[^{number}]" for number in claim["footnote_numbers"]
        )
        lines.append(
            f"- `{claim['claim_id']}` {_markdown_text(claim['statement'])} {markers} "
            "(unknown disposition: "
            f"`{claim['unknown_disposition']['state']}`; fact verification: "
            f"`{claim['unknown_disposition']['fact_verification']}`)"
        )
    lines.extend(("", "## Footnotes and reference list", ""))
    for citation in citation_export["citations"]:
        lines.append(
            f"[^{citation['footnote_number']}]: "
            f"{_markdown_text(citation['reference_text'])} "
            f"(citation ID: `{citation['citation_id']}`; source license: "
            f"`{citation['license']['status']}`; redistribution permission: "
            f"`{citation['license']['redistribution_permission']}`; reason: "
            f"`{citation['license']['reason_code']}`)"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_html_json(output: _BoundedUtf8Buffer, value: Any) -> None:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    )
    try:
        for chunk in encoder.iterencode(value):
            # JSONEncoder can yield an entire string token. Slice it so an
            # oversized persisted value cannot be expanded by html.escape in
            # one unbounded intermediate allocation.
            for offset in range(0, len(chunk), 8 * 1024):
                output.write(html.escape(chunk[offset : offset + 8 * 1024], quote=True))
    except ResearchArtifactError:
        raise
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ResearchArtifactError("html artifact content is unavailable") from exc


def _html_document(
    report: Mapping[str, Any],
    *,
    report_content_sha256: str,
) -> bytes:
    assurance = report["assurance"]
    version = report["version"]
    title = html.escape(str(report["report_title"]), quote=True)
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="robots" content="noindex,nofollow">',
        '<meta name="referrer" content="no-referrer">',
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src &#x27;none&#x27;; base-uri &#x27;none&#x27;; '
        'form-action &#x27;none&#x27;; frame-ancestors &#x27;none&#x27;; sandbox">',
        f"<title>{title}</title>",
        "</head>",
        "<body>",
        "<main>",
        f'<aside role="note">{ARTIFACT_DISTRIBUTION_WARNING}</aside>',
        f"<h1>{title}</h1>",
        '<section aria-labelledby="artifact-contract">',
        '<h2 id="artifact-contract">Artifact contract</h2>',
        "<dl>",
        f"<dt>Schema</dt><dd>{ARTIFACT_SCHEMA_VERSION}</dd>",
        "<dt>Format</dt><dd>html</dd>",
        "<dt>Artifact kind</dt><dd>deterministic_persisted_manifest_render</dd>",
        f"<dt>Source policy</dt><dd>{ARTIFACT_SOURCE_POLICY}</dd>",
        f"<dt>Report content SHA-256</dt><dd>{report_content_sha256}</dd>",
        "<dt>Manifest integrity SHA-256</dt>"
        f"<dd>{report['manifest_integrity_sha256']}</dd>",
        f"<dt>Manifest ID</dt><dd>{html.escape(str(version['manifest_id']), quote=True)}</dd>",
        f"<dt>Export version</dt><dd>{int(version['export_version'])}</dd>",
        f"<dt>Project version</dt><dd>{int(version['project_version'])}</dd>",
        "<dt>Publication status</dt>"
        f"<dd>{html.escape(str(assurance['publication_status']), quote=True)}</dd>",
        "<dt>Researcher acceptance</dt>"
        f"<dd>{html.escape(str(assurance['researcher_acceptance']), quote=True)}</dd>",
        "</dl>",
        "</section>",
    ]
    output = _BoundedUtf8Buffer(
        MAX_HTML_ARTIFACT_BYTES,
        "html artifact exceeds the byte limit",
    )
    for line in lines:
        output.write(f"{line}\n")
    for index, (heading, value) in enumerate(_report_sections(report), start=1):
        escaped_heading = html.escape(heading, quote=True)
        output.write(
            f'<section aria-labelledby="report-section-{index}">\n'
        )
        output.write(
            f'<h2 id="report-section-{index}">{escaped_heading}</h2>\n'
        )
        output.write("<pre><code>")
        _write_html_json(output, value)
        output.write("</code></pre>\n</section>\n")
    citation_export = report["citation_export"]
    output.write('<section aria-labelledby="claim-citation-bindings">\n')
    output.write('<h2 id="claim-citation-bindings">Claim citation bindings</h2>\n<ul>\n')
    for claim in citation_export["claim_bindings"]:
        output.write(
            f'<li id="{claim["claim_id"]}"><code>{claim["claim_id"]}</code> '
            f'{html.escape(claim["statement"], quote=True)} '
        )
        for number in claim["footnote_numbers"]:
            output.write(f"<sup>[{int(number)}]</sup>")
        disposition = claim["unknown_disposition"]
        output.write(
            '<span data-claim-unknown-disposition="'
            f'{html.escape(disposition["state"], quote=True)}"> '
            "unknown disposition: "
            f'{html.escape(disposition["state"], quote=True)}; '
            "fact verification: "
            f'{html.escape(disposition["fact_verification"], quote=True)}</span>'
        )
        output.write("</li>\n")
    output.write("</ul>\n</section>\n")
    output.write('<section aria-labelledby="citation-reference-list">\n')
    output.write('<h2 id="citation-reference-list">Footnotes and reference list</h2>\n<ol>\n')
    for citation in citation_export["citations"]:
        citation_license = citation["license"]
        output.write(
            f'<li id="{citation["citation_id"]}" value="{int(citation["footnote_number"])}">'
            f'{html.escape(citation["reference_text"], quote=True)} '
            f'<code>{citation["citation_id"]}</code> '
            '<span data-source-license-status="'
            f'{html.escape(citation_license["status"], quote=True)}">source license: '
            f'{html.escape(citation_license["status"], quote=True)}; '
            "redistribution permission: "
            f'{html.escape(citation_license["redistribution_permission"], quote=True)}; '
            "reason: "
            f'{html.escape(citation_license["reason_code"], quote=True)}</span></li>\n'
        )
    output.write("</ol>\n</section>\n")
    output.write("</main>\n</body>\n</html>\n")
    return output.to_bytes()


def _csv_scalar(value: Any) -> str:
    if value is None:
        text = "unavailable"
    elif isinstance(value, (dict, list)):
        text = _canonical_json_bytes(value).decode("utf-8")
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    text = "".join(
        character
        if ord(character) >= 32 or character in "\t\r\n"
        else f"\\u{ord(character):04x}"
        for character in text
    )
    if _CSV_FORMULA_PREFIX.match(text):
        return f"'{text}"
    return text


def _csv_document(
    report: Mapping[str, Any],
    *,
    report_content_sha256: str,
) -> bytes:
    citation_export = report["citation_export"]
    citations = citation_export["citations"]
    if len(citations) > MAX_CSV_EVIDENCE_ROWS:
        raise ResearchArtifactError("csv artifact exceeds the evidence row limit")
    version = report["version"]
    selected = report["selected_content"]
    selection = report["field_selection"]
    scope = selected.get("project_scope") or {}
    cutoff = selected.get("cutoff") or {}
    method = selected.get("method") or {}
    assurance = report["assurance"]
    license_boundary = report["license_boundary"]
    summaries = {
        source["evidence_id"]: source
        for source in selected.get("evidence_summaries", [])
    }
    claims_by_citation_id: dict[str, list[str]] = {
        citation["citation_id"]: [] for citation in citations
    }
    claim_dispositions_by_citation_id: dict[str, list[dict[str, str]]] = {
        citation["citation_id"]: [] for citation in citations
    }
    for claim in citation_export["claim_bindings"]:
        for citation_id in (
            *claim["supporting_citation_ids"],
            *claim["opposing_citation_ids"],
        ):
            claims_by_citation_id[citation_id].append(claim["claim_id"])
            disposition = claim["unknown_disposition"]
            claim_dispositions_by_citation_id[citation_id].append(
                {
                    "claim_id": claim["claim_id"],
                    "state": disposition["state"],
                    "reason_code": disposition["reason_code"],
                    "fact_verification": disposition["fact_verification"],
                }
            )
    shared = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_format": "csv",
        "artifact_kind": "deterministic_evidence_reference_inventory",
        "source_policy": ARTIFACT_SOURCE_POLICY,
        "deterministic": report["rendering_assurance"]["deterministic"],
        "new_facts_generated": report["rendering_assurance"][
            "new_facts_generated"
        ],
        "unreferenced_ai_narrative_generated": report["rendering_assurance"][
            "unreferenced_ai_narrative_generated"
        ],
        "report_content_sha256": report_content_sha256,
        "manifest_integrity_sha256": report["manifest_integrity_sha256"],
        "field_selection_schema_version": selection["schema_version"],
        "selected_fields": selection["selected_fields"],
        "mandatory_fields": selection["mandatory_fields"],
        "always_excluded_sensitive_fields": selection[
            "always_excluded_sensitive_fields"
        ],
        "selected_content_sha256": _sha256(selected),
        "manifest_schema_version": version["manifest_schema_version"],
        "manifest_id": version["manifest_id"],
        "export_version": version["export_version"],
        "project_version": version["project_version"],
        "project_id": report["project_id"],
        "report_title": report["report_title"],
        "project_scope_title": scope.get("title"),
        "project_scope_description": scope.get("description"),
        "project_scope_countries": scope.get("countries"),
        "project_scope_capture_status": scope.get("capture_status"),
        "cutoff_at": cutoff.get("at"),
        "cutoff_basis": cutoff.get("basis"),
        "method_description": method.get("description"),
        "model_disclosure": method.get("model_disclosure"),
        "uncertainty": selected.get("uncertainty"),
        "publication_status": assurance["publication_status"],
        "researcher_acceptance": assurance["researcher_acceptance"],
        "distribution_status": report["distribution_boundary"]["status"],
        "distribution_warning": report["distribution_boundary"]["warning"],
        "workflow_gate": assurance["workflow_gate"],
        "source_verification": assurance["source_verification"],
        "source_license_schema_version": license_boundary["schema_version"],
        "license_status": license_boundary["status"],
        "license_redistribution_permission": license_boundary[
            "redistribution_permission"
        ],
        "source_license_reason_code": license_boundary["reason_code"],
        "source_license_notice": license_boundary["notice"],
        "research_questions": selected.get("research_questions"),
        "saved_search_receipts": selected.get("saved_search_receipts"),
        "information_gaps": selected.get("information_gaps"),
        "alternative_hypotheses": selected.get("alternative_hypotheses"),
        "judgments": selected.get("judgments"),
        "human_decisions": selected.get("human_decisions"),
        "review_outcomes": selected.get("review_outcomes"),
    }
    output = _BoundedUtf8Buffer(
        MAX_CSV_ARTIFACT_BYTES,
        "csv artifact exceeds the byte limit",
    )
    writer = csv.DictWriter(
        output,
        fieldnames=_CSV_COLUMNS,
        extrasaction="raise",
        lineterminator="\r\n",
        quoting=csv.QUOTE_ALL,
    )
    writer.writerow({column: column for column in _CSV_COLUMNS})
    for index, citation in enumerate(citations, start=1):
        locator = citation["locator"]
        source = citation["source"]
        summary = summaries.get(citation["evidence_id"], {})
        bound_claim_ids = claims_by_citation_id[citation["citation_id"]]
        bound_claim_unknown_dispositions = claim_dispositions_by_citation_id[
            citation["citation_id"]
        ]
        row = {
            **shared,
            "citation_schema_version": citation_export["schema_version"],
            "citation_style_name": citation_export["style"]["name"],
            "citation_style_status": citation_export["style"]["status"],
            "citation_id": citation["citation_id"],
            "footnote_number": citation["footnote_number"],
            "locator_status": locator["status"],
            "locator_url": locator["url"],
            "locator_permanence": locator["permanence"],
            "locator_reason_code": locator["reason_code"],
            "reference_text": citation["reference_text"],
            "license_status": citation["license"]["status"],
            "license_redistribution_permission": citation["license"][
                "redistribution_permission"
            ],
            "source_license_reason_code": citation["license"]["reason_code"],
            "bound_claim_ids": bound_claim_ids,
            "bound_claim_count": len(bound_claim_ids),
            "bound_claim_unknown_dispositions": bound_claim_unknown_dispositions,
            "evidence_index": index,
            "relation": citation["relation"],
            "evidence_id": citation["evidence_id"],
            "source_id": source["source_id"],
            "source_title": source["source_title"],
            "source_url": locator["url"],
            "original_anchor": source["original_anchor"],
            "source_published_at": source["source_published_at"],
            "snapshot_status": summary.get("snapshot_status"),
            "provenance_status": summary.get("provenance_status"),
            "summary": summary.get("summary"),
        }
        writer.writerow(
            {column: _csv_scalar(row.get(column)) for column in _CSV_COLUMNS}
        )
    return output.to_bytes()


def _filename(
    manifest: Mapping[str, Any],
    artifact_format: ArtifactFormat,
    selected_fields: tuple[str, ...],
) -> str:
    project = _SAFE_FILENAME_COMPONENT.sub("-", str(manifest["project_id"])).strip(
        "-_"
    )[:48]
    if not project:
        project = "project"
    extension = {
        "json": "json",
        "markdown": "md",
        "html": "html",
        "csv": "csv",
    }[artifact_format]
    field_selection_sha256 = _sha256(
        {
            "schema_version": FIELD_SELECTION_SCHEMA_VERSION,
            "selected_fields": list(selected_fields),
        }
    )[:12]
    filename = (
        "research-reviewed-draft-"
        f"{project}-v{int(manifest['export_version'])}-"
        f"fields-{field_selection_sha256}.{extension}"
    )
    if not _SAFE_ARTIFACT_FILENAME.fullmatch(filename):
        raise ResearchArtifactError("artifact filename is unavailable")
    return filename


def build_research_export_artifact(
    manifest_value: Mapping[str, Any],
    artifact_format: ArtifactFormat,
    *,
    export_fields: list[str] | tuple[str, ...] | None = None,
) -> ResearchExportArtifact:
    """Render bytes using only one verified, persisted manifest."""
    if artifact_format not in {"json", "markdown", "html", "csv"}:
        raise ResearchArtifactError("unsupported research artifact format")
    selected_fields = _normalize_export_fields(export_fields)
    manifest = _validated_manifest(manifest_value)
    report = _report_content(manifest, selected_fields)
    report_hash = _sha256(report)
    if artifact_format == "json":
        envelope = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "artifact_format": "json",
            "artifact_kind": "deterministic_persisted_manifest_render",
            "source_policy": ARTIFACT_SOURCE_POLICY,
            "report_content_sha256": report_hash,
            "manifest_integrity_sha256": manifest["integrity_sha256"],
            "report": report,
        }
        body = _canonical_json_bytes(envelope) + b"\n"
        media_type = "application/json"
    elif artifact_format == "markdown":
        body = _markdown_document(report, report_content_sha256=report_hash)
        media_type = "text/markdown"
    elif artifact_format == "html":
        body = _html_document(report, report_content_sha256=report_hash)
        media_type = "text/html"
    else:
        body = _csv_document(report, report_content_sha256=report_hash)
        media_type = "text/csv"
    if len(body) > MAX_ARTIFACT_BODY_BYTES:
        raise ResearchArtifactError("artifact exceeds the global byte limit")
    return ResearchExportArtifact(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        artifact_format=artifact_format,
        filename=_filename(manifest, artifact_format, selected_fields),
        media_type=media_type,
        body=body,
        response_sha256=hashlib.sha256(body).hexdigest(),
        report_content_sha256=report_hash,
        manifest_integrity_sha256=str(manifest["integrity_sha256"]),
        publication_status=str(manifest["assurance"]["publication_status"]),
        researcher_acceptance=str(manifest["assurance"]["researcher_acceptance"]),
        distribution_status=ARTIFACT_DISTRIBUTION_STATUS,
        field_selection_schema_version=FIELD_SELECTION_SCHEMA_VERSION,
        selected_fields=selected_fields,
        source_license_status=SOURCE_LICENSE_STATUS,
    )


__all__ = (
    "ARTIFACT_SCHEMA_VERSION",
    "ARTIFACT_SOURCE_POLICY",
    "ARTIFACT_DISTRIBUTION_STATUS",
    "ARTIFACT_DISTRIBUTION_WARNING",
    "CITATION_EXPORT_SCHEMA_VERSION",
    "CITATION_STYLE_NAME",
    "CITATION_STYLE_STATUS",
    "FIELD_SELECTION_SCHEMA_VERSION",
    "SOURCE_LICENSE_SCHEMA_VERSION",
    "SOURCE_LICENSE_STATUS",
    "SOURCE_LICENSE_REASON_CODE",
    "SOURCE_LICENSE_NOTICE",
    "EXPORT_OPTIONAL_FIELDS",
    "DEFAULT_EXPORT_FIELDS",
    "MANDATORY_EXPORT_FIELDS",
    "ALWAYS_EXCLUDED_SENSITIVE_FIELDS",
    "ArtifactFormat",
    "MAX_ARTIFACT_BODY_BYTES",
    "MAX_CSV_ARTIFACT_BYTES",
    "MAX_CSV_EVIDENCE_ROWS",
    "MAX_CITATION_COUNT",
    "MAX_CLAIM_BINDING_COUNT",
    "MAX_CITATIONS_PER_CLAIM",
    "MAX_CITATION_TEXT_BYTES",
    "MAX_HTML_ARTIFACT_BYTES",
    "ResearchArtifactError",
    "ResearchExportArtifact",
    "build_research_export_artifact",
)
