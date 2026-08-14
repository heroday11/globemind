"""Content-free, offline contracts for bounded generative-output evaluation.

The evaluator in this module checks only manifest conformance and the syntactic
disposition of body-free structured-claim projections.  It does not
independently replay an observation, split free-form Markdown into claims, read
provider output bodies, verify source truth, or produce a hallucination/quality
conclusion.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .surfaces import SourceCoverageIssue, SourceLocator

GENERATIVE_EVALUATION_SCHEMA_VERSION = (
    "globemind.generative-output-evaluation.v1"
)
GENERATIVE_EVALUATION_SURFACE_SCHEMA_VERSION = (
    "globemind.generative-evaluation-surfaces.v1"
)
GENERATIVE_EVALUATION_METHOD_VERSION = (
    "syntactic-boundary-evaluation-1.0.0"
)

SurfaceId = Literal[
    "assistant-interactive",
    "assistant-scheduled-report",
    "research-reviewed-draft-export",
]
ReasonCode = Annotated[
    str,
    Field(pattern=r"^[A-Z][A-Z0-9_]{1,95}$", max_length=96),
]
Sha256Hex = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", max_length=64)]
ClaimId = Annotated[
    str,
    Field(pattern=r"^claim-[a-f0-9]{24}$", max_length=30),
]
CitationId = Annotated[
    str,
    Field(pattern=r"^citation-[a-f0-9]{24}$", max_length=33),
]


class GenerativeScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    expected_disposition: str = Field(
        pattern=r"^[a-z][a-z0-9_]{2,79}$"
    )
    required_reason_codes: tuple[ReasonCode, ...] = Field(max_length=10)

    @model_validator(mode="after")
    def validate_reason_codes(self) -> "GenerativeScenarioSpec":
        if len(set(self.required_reason_codes)) != len(
            self.required_reason_codes
        ):
            raise ValueError("required reason codes must be unique")
        return self


class GenerativeEvaluationSurface(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    surface_id: SurfaceId
    output_shape: Literal["free_markdown", "structured_claim_records"]
    claim_structure_state: Literal["not_available", "structured_records"]
    per_claim_citation_coverage_state: Literal[
        "unknown", "syntactic_disposition_only"
    ]
    required_scenarios: tuple[GenerativeScenarioSpec, ...] = Field(
        min_length=1,
        max_length=10,
    )
    source_locators: tuple[SourceLocator, ...] = Field(
        min_length=2,
        max_length=6,
    )
    open_findings: tuple[ReasonCode, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_surface_contract(self) -> "GenerativeEvaluationSurface":
        scenario_ids = [item.scenario_id for item in self.required_scenarios]
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("scenario ids must be unique per surface")
        if len(set(self.open_findings)) != len(self.open_findings):
            raise ValueError("open findings must be unique")
        if self.output_shape == "free_markdown":
            if self.claim_structure_state != "not_available":
                raise ValueError("free Markdown has no structured claim records")
            if self.per_claim_citation_coverage_state != "unknown":
                raise ValueError("free Markdown per-claim coverage is unknown")
            required = {
                "UNSTRUCTURED_MODEL_OUTPUT_NOT_PER_CLAIM",
                "PER_CLAIM_CITATION_COVERAGE_UNKNOWN",
            }
            if not required.issubset(self.open_findings):
                raise ValueError("free Markdown limitations must remain open")
        else:
            if self.claim_structure_state != "structured_records":
                raise ValueError("structured output requires claim records")
            if (
                self.per_claim_citation_coverage_state
                != "syntactic_disposition_only"
            ):
                raise ValueError("structured coverage is syntactic only")
        return self


class GenerativeEvaluationSurfaceInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "globemind.generative-evaluation-surfaces.v1"
    ] = GENERATIVE_EVALUATION_SURFACE_SCHEMA_VERSION
    scope: Literal["bounded_claim_output_inventory_subset"] = (
        "bounded_claim_output_inventory_subset"
    )
    method_version: Literal["syntactic-boundary-evaluation-1.0.0"] = (
        GENERATIVE_EVALUATION_METHOD_VERSION
    )
    complete_hallucination_quality_claim: Literal[False] = False
    real_human_gold_observed: Literal[False] = False
    observation_verification_state: Literal[
        "manifest_attested_not_independently_observed"
    ] = "manifest_attested_not_independently_observed"
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1, max_length=20)
    surfaces: tuple[GenerativeEvaluationSurface, ...] = Field(
        min_length=1,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_inventory(self) -> "GenerativeEvaluationSurfaceInventory":
        ids = [surface.surface_id for surface in self.surfaces]
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise ValueError("surface ids must be sorted and unique")
        required = {
            "BOUNDED_OFFLINE_EVALUATION_ONLY",
            "MANIFEST_OBSERVATIONS_NOT_INDEPENDENTLY_REPLAYED",
            "REAL_HUMAN_GOLD_NOT_OBSERVED",
            "SEMANTIC_ENTAILMENT_NOT_VERIFIED",
        }
        if not required.issubset(self.reason_codes):
            raise ValueError("inventory limitations must be explicit")
        return self


def _scenario(
    scenario_id: str,
    expected_disposition: str,
    *required_reason_codes: ReasonCode,
) -> GenerativeScenarioSpec:
    return GenerativeScenarioSpec(
        scenario_id=scenario_id,
        expected_disposition=expected_disposition,
        required_reason_codes=required_reason_codes,
    )


def _source(path: str, locator: str) -> SourceLocator:
    return SourceLocator(path=path, locator=locator)


def build_generative_evaluation_surface_inventory(
) -> GenerativeEvaluationSurfaceInventory:
    """Return a deterministic subset of claim-output surfaces and limitations."""

    surfaces = (
        GenerativeEvaluationSurface(
            surface_id="assistant-interactive",
            output_shape="free_markdown",
            claim_structure_state="not_available",
            per_claim_citation_coverage_state="unknown",
            required_scenarios=(
                _scenario("refusal", "explicit_unknown"),
                _scenario(
                    "out_of_scope_citation",
                    "blocked_replaced_unknown",
                    "CITATION_SOURCE_ID_OUT_OF_SCOPE",
                ),
                _scenario(
                    "prompt_injection_forged_citation",
                    "blocked_replaced_unknown",
                    "CITATION_SOURCE_ID_OUT_OF_SCOPE",
                ),
                _scenario(
                    "provider_failure",
                    "blocked_replaced_unknown",
                    "MODEL_GENERATION_INCOMPLETE",
                ),
                _scenario(
                    "stream_truncation",
                    "blocked_replaced_unknown",
                    "MODEL_GENERATION_INCOMPLETE",
                ),
            ),
            source_locators=(
                _source(
                    "backend/api/features/assistant/interactive_citations.py",
                    "def assure_interactive_output(",
                ),
                _source(
                    "backend/api/features/assistant/interactive_citations.py",
                    "def finalize_interactive_output(",
                ),
                _source(
                    "backend/api/routes/assistant.py",
                    '@router.post("/api/assistant/cc/stream", tags=["AI"], response_model=None)',
                ),
            ),
            open_findings=(
                "UNSTRUCTURED_MODEL_OUTPUT_NOT_PER_CLAIM",
                "PER_CLAIM_CITATION_COVERAGE_UNKNOWN",
                "SEMANTIC_PROMPT_INJECTION_NOT_EVALUATED",
                "NONSTREAM_TRUNCATION_SIGNAL_NOT_AVAILABLE",
                "MANIFEST_OBSERVATIONS_NOT_INDEPENDENTLY_REPLAYED",
                "SOURCE_TRUTH_NOT_VERIFIED",
                "SEMANTIC_ENTAILMENT_NOT_VERIFIED",
                "FACT_CHECK_NOT_PERFORMED",
            ),
        ),
        GenerativeEvaluationSurface(
            surface_id="assistant-scheduled-report",
            output_shape="free_markdown",
            claim_structure_state="not_available",
            per_claim_citation_coverage_state="unknown",
            required_scenarios=(
                _scenario(
                    "refusal",
                    "quarantined_no_artifact",
                    "CITED_SUBSTANTIVE_BLOCKS_EMPTY",
                ),
                _scenario(
                    "out_of_scope_citation",
                    "quarantined_no_artifact",
                    "CITATION_IDENTIFIER_OUT_OF_SCOPE",
                ),
                _scenario(
                    "prompt_injection_active_markup",
                    "quarantined_no_artifact",
                    "GENERATED_CONTENT_ACTIVE_MARKUP",
                ),
                _scenario(
                    "provider_failure",
                    "quarantined_no_artifact",
                    "RUN_FAILED",
                ),
            ),
            source_locators=(
                _source(
                    "backend/api/features/assistant/report_assurance.py",
                    "def assure_generated_report(",
                ),
                _source(
                    "backend/api/routes/assistant_schedules.py",
                    '@router.post("/schedules/{schedule_id}/run")',
                ),
                _source(
                    "backend/api/services/assistant_schedule.py",
                    "def _run_failure_code(",
                ),
            ),
            open_findings=(
                "UNSTRUCTURED_MODEL_OUTPUT_NOT_PER_CLAIM",
                "PER_CLAIM_CITATION_COVERAGE_UNKNOWN",
                "SEMANTIC_PROMPT_INJECTION_NOT_EVALUATED",
                "PROVIDER_TRUNCATION_SIGNAL_NOT_AVAILABLE",
                "SCHEDULE_PROVIDER_FAILURE_ARTIFACT_ABSENCE_NOT_REPLAYED",
                "MANIFEST_OBSERVATIONS_NOT_INDEPENDENTLY_REPLAYED",
                "SOURCE_TRUTH_NOT_VERIFIED",
                "SEMANTIC_ENTAILMENT_NOT_VERIFIED",
                "FACT_CHECK_NOT_PERFORMED",
            ),
        ),
        GenerativeEvaluationSurface(
            surface_id="research-reviewed-draft-export",
            output_shape="structured_claim_records",
            claim_structure_state="structured_records",
            per_claim_citation_coverage_state="syntactic_disposition_only",
            required_scenarios=(
                _scenario(
                    "structured_claim_coverage",
                    "structured_claim_records",
                ),
            ),
            source_locators=(
                _source(
                    "backend/api/features/research_workflow/artifacts.py",
                    "def _citation_export(",
                ),
                _source(
                    "backend/api/routes/research_workflow.py",
                    "def download_export_artifact(",
                ),
            ),
            open_findings=(
                "GENERATION_STAGE_NOT_INVENTORIED",
                "ARTIFACT_SHA256_NOT_READ_OR_VERIFIED",
                "CITATION_INVENTORY_MANIFEST_DECLARED",
                "MANIFEST_OBSERVATIONS_NOT_INDEPENDENTLY_REPLAYED",
                "SOURCE_TRUTH_NOT_VERIFIED",
                "SEMANTIC_ENTAILMENT_NOT_VERIFIED",
                "FACT_CHECK_NOT_PERFORMED",
                "REAL_HUMAN_GOLD_NOT_OBSERVED",
            ),
        ),
    )
    return GenerativeEvaluationSurfaceInventory(
        reason_codes=(
            "BOUNDED_OFFLINE_EVALUATION_ONLY",
            "FREE_MARKDOWN_NOT_SPLIT_INTO_CLAIMS",
            "MANIFEST_OBSERVATIONS_NOT_INDEPENDENTLY_REPLAYED",
            "REAL_HUMAN_GOLD_NOT_OBSERVED",
            "SOURCE_TRUTH_NOT_VERIFIED",
            "SEMANTIC_ENTAILMENT_NOT_VERIFIED",
            "FACT_CHECK_NOT_PERFORMED",
        ),
        surfaces=surfaces,
    )


def audit_generative_evaluation_surface_sources(
    repository_root: Path,
    inventory: GenerativeEvaluationSurfaceInventory | None = None,
) -> tuple[SourceCoverageIssue, ...]:
    """Check bounded source locators without returning source contents."""

    root = repository_root.resolve()
    declared = inventory or build_generative_evaluation_surface_inventory()
    issues: list[SourceCoverageIssue] = []
    for surface in declared.surfaces:
        for source in surface.source_locators:
            candidate = root / source.path
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
            except (FileNotFoundError, OSError, ValueError):
                issues.append(
                    SourceCoverageIssue(
                        code="SOURCE_PATH_INVALID",
                        surface_id=surface.surface_id,
                        path=source.path,
                    )
                )
                continue
            if candidate.is_symlink() or not resolved.is_file():
                issues.append(
                    SourceCoverageIssue(
                        code="SOURCE_PATH_UNAVAILABLE",
                        surface_id=surface.surface_id,
                        path=source.path,
                    )
                )
                continue
            try:
                if resolved.stat().st_size > 5 * 1024 * 1024:
                    raise OSError("source exceeds audit bound")
                source_text = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                issues.append(
                    SourceCoverageIssue(
                        code="SOURCE_PATH_UNAVAILABLE",
                        surface_id=surface.surface_id,
                        path=source.path,
                    )
                )
                continue
            occurrences = source_text.count(source.locator)
            if occurrences == 1:
                continue
            issues.append(
                SourceCoverageIssue(
                    code=(
                        "SOURCE_LOCATOR_MISSING"
                        if occurrences == 0
                        else "SOURCE_LOCATOR_AMBIGUOUS"
                    ),
                    surface_id=surface.surface_id,
                    path=source.path,
                )
            )
    return tuple(issues)


class GenerativeEvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    dataset_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    sha256: Sha256Hex
    evidence_tier: Literal[
        "synthetic_fixture",
        "silver_label_manifest",
        "human_gold_manifest_claim",
    ]
    label_source: Literal["synthetic", "silver", "human"]
    independent_review_state: Literal["not_available", "declared"]
    external_evidence_verification: Literal["not_performed"]

    @model_validator(mode="after")
    def validate_evidence_tier(self) -> "GenerativeEvaluationDataset":
        expected = {
            "synthetic_fixture": ("synthetic", "not_available"),
            "silver_label_manifest": ("silver", "declared"),
            "human_gold_manifest_claim": ("human", "declared"),
        }[self.evidence_tier]
        if (self.label_source, self.independent_review_state) != expected:
            raise ValueError(
                "evidence tier, label source, and review state do not match"
            )
        return self


class ResearchUnknownDispositionProjection(BaseModel):
    """Content-free projection of the v3 unresolved-gap disposition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["explicit_unresolved_information_gaps"]
    reason_code: Literal["CLAIM_HAS_LINKED_INFORMATION_GAPS"]
    fact_verification: Literal["not_verified"]
    information_gap_count: int = Field(ge=1, le=1_000)
    information_gap_set_sha256: Sha256Hex


class StructuredClaimObservation(BaseModel):
    """Body-free projection of one research-citation-export-v3 claim binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: ClaimId
    statement_sha256: Sha256Hex
    supporting_citation_ids: tuple[CitationId, ...] = Field(max_length=100)
    opposing_citation_ids: tuple[CitationId, ...] = Field(max_length=100)
    unknown_disposition: ResearchUnknownDispositionProjection

    @model_validator(mode="after")
    def validate_citation_bindings(self) -> "StructuredClaimObservation":
        citation_ids = (
            *self.supporting_citation_ids,
            *self.opposing_citation_ids,
        )
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("claim citation ids must be unique across relations")
        return self


class GenerativeCaseObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$",
        max_length=120,
    )
    surface_id: SurfaceId
    scenario_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]{2,79}$",
        max_length=80,
    )
    declared_disposition: str = Field(
        pattern=r"^[a-z][a-z0-9_]{2,79}$",
        max_length=80,
    )
    reason_codes: tuple[ReasonCode, ...] = Field(max_length=20)
    citation_export_schema_version: Literal[
        "research-citation-export-v3"
    ] | None = None
    artifact_sha256: Sha256Hex | None = None
    citation_inventory_ids: tuple[CitationId, ...] = Field(
        default=(),
        max_length=5_000,
    )
    claim_records: tuple[StructuredClaimObservation, ...] = Field(
        default=(),
        max_length=5_000,
    )

    @model_validator(mode="after")
    def validate_unique_metadata(self) -> "GenerativeCaseObservation":
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason codes must be unique")
        if len(set(self.citation_inventory_ids)) != len(
            self.citation_inventory_ids
        ):
            raise ValueError("citation inventory ids must be unique")
        claim_ids = [claim.claim_id for claim in self.claim_records]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("claim ids must be unique within a case")
        return self


class GenerativeEvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["globemind.generative-output-evaluation.v1"] = (
        GENERATIVE_EVALUATION_SCHEMA_VERSION
    )
    evaluation_id: str = Field(pattern=r"^gen-eval\.[a-z0-9][a-z0-9._-]{2,119}$")
    evaluated_at: datetime
    dataset: GenerativeEvaluationDataset
    observations: tuple[GenerativeCaseObservation, ...] = Field(
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_manifest_boundaries(self) -> "GenerativeEvaluationManifest":
        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluation time must include a timezone")
        case_ids = [item.case_id for item in self.observations]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case ids must be unique")
        keys = [(item.surface_id, item.scenario_id) for item in self.observations]
        if len(set(keys)) != len(keys):
            raise ValueError("surface/scenario observations must be unique")

        inventory = build_generative_evaluation_surface_inventory()
        surfaces = {surface.surface_id: surface for surface in inventory.surfaces}
        valid_keys = {
            (surface.surface_id, scenario.scenario_id)
            for surface in inventory.surfaces
            for scenario in surface.required_scenarios
        }
        for observation in self.observations:
            key = (observation.surface_id, observation.scenario_id)
            if key not in valid_keys:
                raise ValueError("observation is outside the bounded surface inventory")
            surface = surfaces[observation.surface_id]
            projection_metadata_present = any(
                (
                    observation.citation_export_schema_version is not None,
                    observation.artifact_sha256 is not None,
                    bool(observation.citation_inventory_ids),
                    bool(observation.claim_records),
                )
            )
            if surface.output_shape == "free_markdown":
                if projection_metadata_present:
                    raise ValueError(
                        "unstructured Markdown cannot supply fabricated claim records"
                    )
            elif (
                observation.citation_export_schema_version
                != "research-citation-export-v3"
                or observation.artifact_sha256 is None
                or not observation.claim_records
            ):
                raise ValueError(
                    "structured claim scenario requires a v3 content-free projection"
                )
        return self


class GenerativeCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]{2,79}$",
        max_length=80,
    )
    state: Literal["manifest_conforms", "failed_closed"]
    declared_disposition: Annotated[
        str,
        Field(pattern=r"^[a-z][a-z0-9_]{2,79}$", max_length=80),
    ] | None
    reason_codes: tuple[ReasonCode, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def validate_case_state(self) -> "GenerativeCaseResult":
        if self.state == "manifest_conforms" and self.reason_codes:
            raise ValueError("conforming manifest case cannot carry failures")
        if self.state == "failed_closed" and not self.reason_codes:
            raise ValueError("failed case requires a reason code")
        return self


class ClaimCoverageAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal[
        "unknown_unstructured_output",
        "not_observed",
        "manifest_projection_syntactic_only",
    ]
    structured_claim_count: int | None = Field(default=None, ge=0)
    cited_claim_count: int | None = Field(default=None, ge=0)
    explicit_unknown_claim_count: int | None = Field(default=None, ge=0)
    syntactically_disposed_claim_count: int | None = Field(default=None, ge=0)
    undisposed_claim_count: int | None = Field(default=None, ge=0)
    out_of_scope_citation_claim_count: int | None = Field(default=None, ge=0)
    syntactic_cited_claim_ratio: str | None = Field(
        default=None,
        pattern=r"^(?:0\.\d{6}|1\.000000)$",
    )
    syntactic_disposition_ratio: str | None = Field(
        default=None,
        pattern=r"^(?:0\.\d{6}|1\.000000)$",
    )
    semantic_entailment_state: Literal["not_verified"] = "not_verified"
    source_truth_state: Literal["not_verified"] = "not_verified"
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_state_metrics(self) -> "ClaimCoverageAssessment":
        numeric_values = (
            self.structured_claim_count,
            self.cited_claim_count,
            self.explicit_unknown_claim_count,
            self.syntactically_disposed_claim_count,
            self.undisposed_claim_count,
            self.out_of_scope_citation_claim_count,
            self.syntactic_cited_claim_ratio,
            self.syntactic_disposition_ratio,
        )
        if self.state in {"unknown_unstructured_output", "not_observed"}:
            if any(value is not None for value in numeric_values):
                raise ValueError(
                    "unknown or not observed coverage cannot carry numeric metrics"
                )
            return self

        counts = numeric_values[:6]
        ratios = numeric_values[6:]
        if any(value is None for value in (*counts, *ratios)):
            raise ValueError("manifest projection coverage requires complete metrics")
        total = self.structured_claim_count
        if total is None or total <= 0:
            raise ValueError("manifest projection requires declared claim records")
        bounded_counts = (
            self.cited_claim_count,
            self.explicit_unknown_claim_count,
            self.syntactically_disposed_claim_count,
            self.undisposed_claim_count,
            self.out_of_scope_citation_claim_count,
        )
        if any(value is None or value > total for value in bounded_counts):
            raise ValueError("claim coverage counts exceed the projection")
        disposed = self.syntactically_disposed_claim_count or 0
        undisposed = self.undisposed_claim_count or 0
        if disposed + undisposed != total:
            raise ValueError("claim disposition counts do not partition the projection")
        if (self.explicit_unknown_claim_count or 0) > disposed:
            raise ValueError("explicit unknown count exceeds disposed claims")
        cited_ratio = f"{(self.cited_claim_count or 0) / total:.6f}"
        if self.syntactic_cited_claim_ratio != cited_ratio:
            raise ValueError("syntactic cited ratio conflicts with counts")
        disposition_ratio = f"{disposed / total:.6f}"
        if self.syntactic_disposition_ratio != disposition_ratio:
            raise ValueError("syntactic disposition ratio conflicts with counts")
        return self


class GenerativeSurfaceEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    surface_id: SurfaceId
    boundary_fixture_state: Literal["manifest_conforms", "failed_closed"]
    cases: tuple[GenerativeCaseResult, ...] = Field(min_length=1, max_length=10)
    claim_coverage: ClaimCoverageAssessment
    open_findings: tuple[ReasonCode, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_surface_state(self) -> "GenerativeSurfaceEvaluationResult":
        scenario_ids = [case.scenario_id for case in self.cases]
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("surface result scenario ids must be unique")
        if len(set(self.open_findings)) != len(self.open_findings):
            raise ValueError("surface result findings must be unique")
        all_conform = bool(self.cases) and all(
            case.state == "manifest_conforms" for case in self.cases
        )
        expected = "manifest_conforms" if all_conform else "failed_closed"
        if self.boundary_fixture_state != expected:
            raise ValueError("surface state conflicts with case results")
        if self.surface_id in {
            "assistant-interactive",
            "assistant-scheduled-report",
        }:
            if self.claim_coverage.state != "unknown_unstructured_output":
                raise ValueError("free Markdown coverage must remain unknown")
        elif all_conform:
            if self.claim_coverage.state != "manifest_projection_syntactic_only":
                raise ValueError("structured conforming manifest requires projection metrics")
        elif self.claim_coverage.state not in {
            "manifest_projection_syntactic_only",
            "not_observed",
        }:
            raise ValueError("structured failed state has invalid coverage status")
        return self


class GenerativeEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["globemind.generative-output-evaluation.v1"] = (
        GENERATIVE_EVALUATION_SCHEMA_VERSION
    )
    method_version: Literal["syntactic-boundary-evaluation-1.0.0"] = (
        GENERATIVE_EVALUATION_METHOD_VERSION
    )
    evaluation_id: str = Field(
        pattern=r"^gen-eval\.[a-z0-9][a-z0-9._-]{2,119}$",
        max_length=129,
    )
    evaluated_at: datetime
    dataset_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$",
        max_length=120,
    )
    dataset_sha256: Sha256Hex
    evidence_status: Literal[
        "synthetic_fixture_manifest_unverified",
        "silver_manifest_unverified",
        "human_gold_manifest_claim_unverified",
    ]
    observation_verification_state: Literal[
        "manifest_attested_not_independently_observed"
    ] = "manifest_attested_not_independently_observed"
    boundary_fixture_state: Literal[
        "manifest_conforms_with_open_findings", "failed_closed"
    ]
    quality_conclusion: Literal["not_available"] = "not_available"
    hallucination_rate: None = None
    hallucination_rate_state: Literal["not_computable"] = "not_computable"
    real_human_gold_observed: Literal[False] = False
    surfaces: tuple[GenerativeSurfaceEvaluationResult, ...] = Field(
        min_length=3,
        max_length=3,
    )
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_result_state(self) -> "GenerativeEvaluationResult":
        surface_ids = [surface.surface_id for surface in self.surfaces]
        if surface_ids != sorted(surface_ids) or len(set(surface_ids)) != 3:
            raise ValueError("result surface ids must be sorted and complete")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("result reason codes must be unique")
        all_conform = bool(self.surfaces) and all(
            surface.boundary_fixture_state == "manifest_conforms"
            for surface in self.surfaces
        )
        expected = (
            "manifest_conforms_with_open_findings"
            if all_conform
            else "failed_closed"
        )
        if self.boundary_fixture_state != expected:
            raise ValueError("overall state conflicts with surface results")
        if "MANIFEST_OBSERVATIONS_NOT_INDEPENDENTLY_REPLAYED" not in (
            self.reason_codes
        ):
            raise ValueError("manifest-only observation limitation is required")
        return self


def _unknown_claim_coverage() -> ClaimCoverageAssessment:
    return ClaimCoverageAssessment(
        state="unknown_unstructured_output",
        reason_codes=(
            "UNSTRUCTURED_MODEL_OUTPUT_NOT_PER_CLAIM",
            "PER_CLAIM_CITATION_COVERAGE_UNKNOWN",
            "SEMANTIC_ENTAILMENT_NOT_VERIFIED",
            "SOURCE_TRUTH_NOT_VERIFIED",
        ),
    )


def _structured_claim_coverage(
    observation: GenerativeCaseObservation | None,
) -> ClaimCoverageAssessment:
    if observation is None:
        return ClaimCoverageAssessment(
            state="not_observed",
            reason_codes=(
                "STRUCTURED_CLAIM_PROJECTION_NOT_OBSERVED",
                "MANIFEST_OBSERVATIONS_NOT_INDEPENDENTLY_REPLAYED",
                "SEMANTIC_ENTAILMENT_NOT_VERIFIED",
                "SOURCE_TRUTH_NOT_VERIFIED",
            ),
        )

    claims = observation.claim_records
    claim_count = len(claims)
    allowed_citations = set(observation.citation_inventory_ids)

    def bound_ids(claim: StructuredClaimObservation) -> set[str]:
        return {
            *claim.supporting_citation_ids,
            *claim.opposing_citation_ids,
        }

    cited = sum(
        bool(bound_ids(claim)) and bound_ids(claim).issubset(allowed_citations)
        for claim in claims
    )
    explicit_unknown = len(claims)
    out_of_scope = sum(
        bool(bound_ids(claim).difference(allowed_citations))
        for claim in claims
    )
    undisposed = 0
    disposed = sum(
        bool(bound_ids(claim).intersection(allowed_citations))
        or claim.unknown_disposition.state
        == "explicit_unresolved_information_gaps"
        for claim in claims
    )
    return ClaimCoverageAssessment(
        state="manifest_projection_syntactic_only",
        structured_claim_count=claim_count,
        cited_claim_count=cited,
        explicit_unknown_claim_count=explicit_unknown,
        syntactically_disposed_claim_count=disposed,
        undisposed_claim_count=undisposed,
        out_of_scope_citation_claim_count=out_of_scope,
        syntactic_cited_claim_ratio=(
            f"{cited / claim_count:.6f}" if claim_count else "0.000000"
        ),
        syntactic_disposition_ratio=(
            f"{disposed / claim_count:.6f}" if claim_count else "0.000000"
        ),
        reason_codes=(
            "STRUCTURED_CLAIM_PROJECTION_SYNTACTIC_ONLY",
            "ARTIFACT_SHA256_NOT_READ_OR_VERIFIED",
            "CITATION_INVENTORY_MANIFEST_DECLARED",
            "MANIFEST_OBSERVATIONS_NOT_INDEPENDENTLY_REPLAYED",
            "SEMANTIC_ENTAILMENT_NOT_VERIFIED",
            "SOURCE_TRUTH_NOT_VERIFIED",
            "FACT_CHECK_NOT_PERFORMED",
        ),
    )


def evaluate_generative_outputs(
    manifest: GenerativeEvaluationManifest,
) -> GenerativeEvaluationResult:
    """Check bounded manifest metadata; never claim independent observation."""

    inventory = build_generative_evaluation_surface_inventory()
    declared = {
        (item.surface_id, item.scenario_id): item
        for item in manifest.observations
    }
    surface_results: list[GenerativeSurfaceEvaluationResult] = []
    all_cases_conform = True

    for surface in inventory.surfaces:
        case_results: list[GenerativeCaseResult] = []
        surface_conforms = True
        for scenario in surface.required_scenarios:
            observation = declared.get((surface.surface_id, scenario.scenario_id))
            failure_reasons: list[str] = []
            if observation is None:
                failure_reasons.append("FIXTURE_OBSERVATION_MISSING")
            else:
                if observation.declared_disposition != scenario.expected_disposition:
                    failure_reasons.append("EXPECTED_DISPOSITION_NOT_OBSERVED")
                missing_reasons = set(scenario.required_reason_codes).difference(
                    observation.reason_codes
                )
                if missing_reasons:
                    failure_reasons.append("REQUIRED_REASON_CODE_NOT_OBSERVED")
                unexpected_reasons = set(observation.reason_codes).difference(
                    scenario.required_reason_codes
                )
                if unexpected_reasons:
                    failure_reasons.append("UNEXPECTED_REASON_CODE_OBSERVED")
                if scenario.scenario_id == "structured_claim_coverage":
                    declared_citations = set(observation.citation_inventory_ids)
                    if any(
                        {
                            *claim.supporting_citation_ids,
                            *claim.opposing_citation_ids,
                        }.difference(declared_citations)
                        for claim in observation.claim_records
                    ):
                        failure_reasons.append(
                            "STRUCTURED_CITATION_ID_OUT_OF_SCOPE"
                        )
            conforms = not failure_reasons
            surface_conforms = surface_conforms and conforms
            all_cases_conform = all_cases_conform and conforms
            case_results.append(
                GenerativeCaseResult(
                    scenario_id=scenario.scenario_id,
                    state=(
                        "manifest_conforms" if conforms else "failed_closed"
                    ),
                    declared_disposition=(
                        observation.declared_disposition if observation else None
                    ),
                    reason_codes=tuple(failure_reasons),
                )
            )

        structured_observation = declared.get(
            (surface.surface_id, "structured_claim_coverage")
        )
        coverage = (
            _unknown_claim_coverage()
            if surface.output_shape == "free_markdown"
            else _structured_claim_coverage(structured_observation)
        )
        surface_results.append(
            GenerativeSurfaceEvaluationResult(
                surface_id=surface.surface_id,
                boundary_fixture_state=(
                    "manifest_conforms"
                    if surface_conforms
                    else "failed_closed"
                ),
                cases=tuple(case_results),
                claim_coverage=coverage,
                open_findings=surface.open_findings,
            )
        )

    evidence_status = {
        "synthetic_fixture": "synthetic_fixture_manifest_unverified",
        "silver_label_manifest": "silver_manifest_unverified",
        "human_gold_manifest_claim": "human_gold_manifest_claim_unverified",
    }[manifest.dataset.evidence_tier]
    reason_codes = sorted(
        {
            *inventory.reason_codes,
            *(finding for surface in inventory.surfaces for finding in surface.open_findings),
            *(
                reason
                for surface in surface_results
                for case in surface.cases
                for reason in case.reason_codes
            ),
        }
    )
    return GenerativeEvaluationResult(
        evaluation_id=manifest.evaluation_id,
        evaluated_at=manifest.evaluated_at,
        dataset_id=manifest.dataset.dataset_id,
        dataset_sha256=manifest.dataset.sha256,
        evidence_status=evidence_status,
        boundary_fixture_state=(
            "manifest_conforms_with_open_findings"
            if all_cases_conform
            else "failed_closed"
        ),
        surfaces=tuple(surface_results),
        reason_codes=tuple(reason_codes),
    )


__all__ = (
    "GENERATIVE_EVALUATION_METHOD_VERSION",
    "GENERATIVE_EVALUATION_SCHEMA_VERSION",
    "GENERATIVE_EVALUATION_SURFACE_SCHEMA_VERSION",
    "ClaimCoverageAssessment",
    "GenerativeCaseObservation",
    "GenerativeCaseResult",
    "GenerativeEvaluationDataset",
    "GenerativeEvaluationManifest",
    "GenerativeEvaluationResult",
    "GenerativeEvaluationSurface",
    "GenerativeEvaluationSurfaceInventory",
    "GenerativeScenarioSpec",
    "GenerativeSurfaceEvaluationResult",
    "ResearchUnknownDispositionProjection",
    "StructuredClaimObservation",
    "audit_generative_evaluation_surface_sources",
    "build_generative_evaluation_surface_inventory",
    "evaluate_generative_outputs",
)
