"""Versioned contracts for manifest-only model assurance evidence."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

MODEL_ASSURANCE_SCHEMA_VERSION = "globemind.model-assurance.v1"
MODEL_ASSURANCE_CONTRACT_VERSION = "1.0.0"
METRIC_METHOD_VERSION = "binary-assurance-metrics-1.0.0"
STORE_SCHEMA_VERSION = "globemind.model-assurance.entry.v1"
_TOLERANCE = 1e-8
_MAX_SAMPLE_COUNT = 1_000_000_000

Probability = Annotated[FiniteFloat, Field(ge=0, le=1)]
NonNegativeFinite = Annotated[FiniteFloat, Field(ge=0)]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9-]{1,11}$")]
LanguageCode = Annotated[
    str,
    Field(pattern=r"^[A-Za-z][A-Za-z0-9-]{1,23}$"),
]
TopicCode = Annotated[
    str,
    Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,63}$"),
]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _require_timezone(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value


def _clean_reference(value: str, *, field: str) -> str:
    normalized = " ".join(value.split()).strip()
    if not normalized or any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{field} is invalid")
    return normalized


class DatasetIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"
    )
    dataset_version: str = Field(min_length=1, max_length=120)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_at: datetime
    evaluation_role: Literal["holdout", "evaluation_set", "gold_standard"]
    gold_standard_status: Literal[
        "not_observed",
        "declared",
        "independently_reviewed",
    ] = "not_observed"
    label_schema_version: str | None = Field(default=None, max_length=120)
    annotation_protocol_ref: str | None = Field(default=None, max_length=500)
    provenance_ref: str | None = Field(default=None, max_length=500)

    @field_validator("cutoff_at")
    @classmethod
    def validate_cutoff(cls, value: datetime) -> datetime:
        return _require_timezone(value, field="dataset cutoff")

    @field_validator("annotation_protocol_ref", "provenance_ref")
    @classmethod
    def validate_references(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _clean_reference(value, field=info.field_name)

    @field_validator("dataset_version", "label_schema_version")
    @classmethod
    def validate_versions(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _clean_reference(value, field=info.field_name)

    @model_validator(mode="after")
    def validate_gold_standard_claim(self) -> "DatasetIdentity":
        if (
            self.evaluation_role != "gold_standard"
            and self.gold_standard_status != "not_observed"
        ):
            raise ValueError(
                "only a gold-standard dataset may declare gold-standard status"
            )
        return self


class ModelIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
    model_version: str = Field(min_length=1, max_length=120)
    method_version: str = Field(min_length=1, max_length=120)
    owner_organization: str = Field(min_length=2, max_length=200)
    task_type: Literal["binary_classification"] = "binary_classification"
    positive_label: str = Field(min_length=1, max_length=120)

    @field_validator(
        "model_version",
        "method_version",
        "owner_organization",
        "positive_label",
    )
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _clean_reference(value, field=info.field_name)


class ConfusionCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    true_positive: int = Field(ge=0, le=_MAX_SAMPLE_COUNT)
    false_positive: int = Field(ge=0, le=_MAX_SAMPLE_COUNT)
    true_negative: int = Field(ge=0, le=_MAX_SAMPLE_COUNT)
    false_negative: int = Field(ge=0, le=_MAX_SAMPLE_COUNT)

    @property
    def sample_count(self) -> int:
        return (
            self.true_positive
            + self.false_positive
            + self.true_negative
            + self.false_negative
        )

    @model_validator(mode="after")
    def validate_non_empty(self) -> "ConfusionCounts":
        if self.sample_count <= 0:
            raise ValueError("confusion counts must contain at least one sample")
        if self.sample_count > _MAX_SAMPLE_COUNT:
            raise ValueError("confusion counts exceed the sample bound")
        return self


class CalibrationBin(BaseModel):
    """Sufficient statistics for server-side Brier and ECE recomputation."""

    model_config = ConfigDict(extra="forbid")

    lower_bound: Probability
    upper_bound: Probability
    sample_count: int = Field(gt=0, le=_MAX_SAMPLE_COUNT)
    positive_count: int = Field(ge=0, le=_MAX_SAMPLE_COUNT)
    predicted_probability_sum: NonNegativeFinite
    positive_probability_sum: NonNegativeFinite
    squared_probability_sum: NonNegativeFinite

    @model_validator(mode="after")
    def validate_sufficient_statistics(self) -> "CalibrationBin":
        if self.upper_bound <= self.lower_bound:
            raise ValueError("calibration bin bounds must increase")
        if self.positive_count > self.sample_count:
            raise ValueError("positive_count exceeds sample_count")

        count = float(self.sample_count)
        positive_count = float(self.positive_count)
        probability_sum = float(self.predicted_probability_sum)
        positive_probability_sum = float(self.positive_probability_sum)
        squared_probability_sum = float(self.squared_probability_sum)
        lower = float(self.lower_bound)
        upper = float(self.upper_bound)

        if probability_sum > count + _TOLERANCE:
            raise ValueError("predicted_probability_sum exceeds sample_count")
        if positive_probability_sum > positive_count + _TOLERANCE:
            raise ValueError("positive_probability_sum exceeds positive_count")
        if positive_probability_sum > probability_sum + _TOLERANCE:
            raise ValueError("positive_probability_sum exceeds total probability")
        if squared_probability_sum > probability_sum + _TOLERANCE:
            raise ValueError("squared_probability_sum is inconsistent")
        if squared_probability_sum + _TOLERANCE < probability_sum**2 / count:
            raise ValueError("squared_probability_sum violates the lower bound")
        moment_upper_bound = (
            (lower + upper) * probability_sum - lower * upper * count
        )
        if squared_probability_sum > moment_upper_bound + _TOLERANCE:
            raise ValueError("squared_probability_sum violates the upper bound")
        if probability_sum + _TOLERANCE < lower * count:
            raise ValueError("probability sum falls below the bin")
        if probability_sum > upper * count + _TOLERANCE:
            raise ValueError("probability sum exceeds the bin")
        if positive_count and (
            positive_probability_sum + _TOLERANCE < lower * positive_count
            or positive_probability_sum
            > upper * positive_count + _TOLERANCE
        ):
            raise ValueError("positive probability sum falls outside the bin")
        negative_count = count - positive_count
        negative_probability_sum = probability_sum - positive_probability_sum
        if negative_probability_sum < -_TOLERANCE:
            raise ValueError("negative-label probability sum is invalid")
        if negative_count:
            if (
                negative_probability_sum + _TOLERANCE < lower * negative_count
                or negative_probability_sum
                > upper * negative_count + _TOLERANCE
            ):
                raise ValueError(
                    "negative-label probability sum falls outside the bin"
                )
        elif abs(negative_probability_sum) > _TOLERANCE:
            raise ValueError("negative-label probability sum must be zero")

        grouped_moment_lower_bound = 0.0
        if positive_count:
            grouped_moment_lower_bound += (
                positive_probability_sum**2 / positive_count
            )
        elif abs(positive_probability_sum) > _TOLERANCE:
            raise ValueError("positive probability sum must be zero")
        if negative_count:
            grouped_moment_lower_bound += (
                negative_probability_sum**2 / negative_count
            )
        if squared_probability_sum + _TOLERANCE < grouped_moment_lower_bound:
            raise ValueError(
                "squared_probability_sum is inconsistent with label groups"
            )
        if squared_probability_sum + _TOLERANCE < lower**2 * count:
            raise ValueError("squared probability sum falls below the bin")
        if squared_probability_sum > upper**2 * count + _TOLERANCE:
            raise ValueError("squared probability sum exceeds the bin")

        brier_numerator = (
            squared_probability_sum
            - 2 * positive_probability_sum
            + positive_count
        )
        if (
            not math.isfinite(brier_numerator)
            or brier_numerator < -_TOLERANCE
            or brier_numerator > count + _TOLERANCE
        ):
            raise ValueError("calibration statistics imply an invalid Brier score")
        return self


class SliceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confusion: ConfusionCounts
    calibration_bins: list[CalibrationBin] = Field(min_length=1, max_length=20)


class StratumEvidence(SliceEvidence):
    dimension: Literal["country", "language", "topic"]
    value: str = Field(min_length=1, max_length=64)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("stratum value is invalid")
        return normalized


class CoverageDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countries: list[CountryCode] = Field(default_factory=list, max_length=100)
    languages: list[LanguageCode] = Field(default_factory=list, max_length=50)
    topics: list[TopicCode] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_unique_values(self) -> "CoverageDeclaration":
        for field_name in ("countries", "languages", "topics"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} contains duplicate values")
        return self


class EvaluationIntegrityDeclaration(BaseModel):
    """Manifest-only declarations that keep label and split roles distinct."""

    model_config = ConfigDict(extra="forbid")

    label_source: Literal["human_gold", "silver", "synthetic", "unreviewed"]
    partition_role: Literal["holdout", "validation", "development", "not_observed"]
    holdout_access_status: Literal[
        "sealed",
        "development_accessed",
        "not_observed",
    ]
    development_dataset_sha256s: list[Sha256Digest] = Field(
        min_length=1,
        max_length=100,
    )
    separation_evidence_ref: str = Field(min_length=1, max_length=500)
    separation_evidence_sha256: Sha256Digest

    @field_validator("separation_evidence_ref")
    @classmethod
    def validate_reference(cls, value: str) -> str:
        return _clean_reference(value, field="separation_evidence_ref")

    @field_validator("development_dataset_sha256s")
    @classmethod
    def validate_unique_development_datasets(
        cls,
        value: list[str],
    ) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("development dataset digests must be unique")
        return value


class AssuranceThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_precision: Probability
    minimum_recall: Probability
    minimum_f1: Probability
    maximum_brier_score: Probability
    maximum_ece: Probability
    minimum_stratum_f1: Probability
    minimum_overall_samples: int = Field(gt=0, le=_MAX_SAMPLE_COUNT)
    minimum_samples_per_stratum: int = Field(gt=0, le=_MAX_SAMPLE_COUNT)
    maximum_f1_drop_from_baseline: Probability
    maximum_brier_increase_from_baseline: Probability
    maximum_ece_increase_from_baseline: Probability


class IndependentReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
    reviewer_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:._@-]{1,127}$")
    reviewer_organization: str = Field(min_length=2, max_length=200)
    independence_attestation: bool
    decision: Literal["approved", "rejected"]
    reviewed_at: datetime
    valid_until: datetime | None = None
    evidence_ref: str = Field(min_length=1, max_length=500)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("reviewed_at", "valid_until")
    @classmethod
    def validate_review_timestamps(
        cls,
        value: datetime | None,
        info,
    ) -> datetime | None:
        if value is None:
            return None
        return _require_timezone(value, field=info.field_name)

    @field_validator("reviewer_organization", "evidence_ref")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _clean_reference(value, field=info.field_name)

    @model_validator(mode="after")
    def validate_validity_window(self) -> "IndependentReview":
        if self.valid_until is not None and self.valid_until <= self.reviewed_at:
            raise ValueError("review valid_until must follow reviewed_at")
        return self


class BaselineReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str = Field(pattern=r"^eval\.[a-z0-9][a-z0-9_.-]{1,119}$")
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationManifest(BaseModel):
    """Submission contract. Derived metrics are intentionally not accepted."""

    model_config = ConfigDict(extra="forbid")

    evaluation_id: str = Field(pattern=r"^eval\.[a-z0-9][a-z0-9_.-]{1,119}$")
    evaluation_version: str = Field(min_length=1, max_length=120)
    dataset: DatasetIdentity
    model: ModelIdentity
    classification_threshold: Probability
    overall: SliceEvidence
    strata: list[StratumEvidence] = Field(default_factory=list, max_length=250)
    coverage: CoverageDeclaration
    thresholds: AssuranceThresholds | None = None
    independent_review: IndependentReview | None = None
    evaluation_integrity: EvaluationIntegrityDeclaration | None = None
    baseline: BaselineReference | None = None

    @field_validator("evaluation_version")
    @classmethod
    def validate_evaluation_version(cls, value: str) -> str:
        return _clean_reference(value, field="evaluation_version")

    @model_validator(mode="after")
    def validate_internal_consistency(self) -> "EvaluationManifest":
        if not 0 < float(self.classification_threshold) < 1:
            raise ValueError("classification_threshold must be inside (0, 1)")
        _validate_slice(
            self.overall,
            threshold=float(self.classification_threshold),
            label="overall",
        )
        expected_boundaries = _bin_boundaries(self.overall.calibration_bins)
        seen: set[tuple[str, str]] = set()
        for stratum in self.strata:
            identity = (stratum.dimension, stratum.value)
            if identity in seen:
                raise ValueError("duplicate stratum")
            seen.add(identity)
            _validate_slice(
                stratum,
                threshold=float(self.classification_threshold),
                label=f"{stratum.dimension}:{stratum.value}",
            )
            if _bin_boundaries(stratum.calibration_bins) != expected_boundaries:
                raise ValueError("all strata must use the overall calibration bins")

        if self.baseline and self.baseline.evaluation_id == self.evaluation_id:
            raise ValueError("an evaluation cannot be its own baseline")
        review = self.independent_review
        if review is not None:
            if review.reviewed_at < self.dataset.cutoff_at:
                raise ValueError("review timestamp precedes the dataset cutoff")
            if (
                review.independence_attestation
                and review.reviewer_organization.casefold()
                == self.model.owner_organization.casefold()
            ):
                raise ValueError("independent reviewer organization matches model owner")

        integrity = self.evaluation_integrity
        if (
            integrity is not None
            and self.dataset.sha256 in integrity.development_dataset_sha256s
        ):
            raise ValueError(
                "evaluation dataset overlaps development dataset digests"
            )

        declared = {
            "country": set(self.coverage.countries),
            "language": set(self.coverage.languages),
            "topic": set(self.coverage.topics),
        }
        for dimension, expected_values in declared.items():
            observed = [row for row in self.strata if row.dimension == dimension]
            observed_values = {row.value for row in observed}
            if expected_values and observed_values == expected_values:
                _validate_partition(
                    overall=self.overall,
                    strata=observed,
                    dimension=dimension,
                )
        return self


def _bin_boundaries(
    bins: list[CalibrationBin],
) -> tuple[tuple[float, float], ...]:
    return tuple(
        (round(float(item.lower_bound), 12), round(float(item.upper_bound), 12))
        for item in bins
    )


def _validate_slice(
    evidence: SliceEvidence,
    *,
    threshold: float,
    label: str,
) -> None:
    bins = evidence.calibration_bins
    if abs(float(bins[0].lower_bound)) > _TOLERANCE:
        raise ValueError(f"{label} calibration bins must start at zero")
    if abs(float(bins[-1].upper_bound) - 1.0) > _TOLERANCE:
        raise ValueError(f"{label} calibration bins must end at one")
    for previous, current in zip(bins, bins[1:]):
        if abs(float(previous.upper_bound) - float(current.lower_bound)) > _TOLERANCE:
            raise ValueError(f"{label} calibration bins must be contiguous")
    boundaries = [float(item.lower_bound) for item in bins[1:]]
    if not any(abs(boundary - threshold) <= _TOLERANCE for boundary in boundaries):
        raise ValueError(f"{label} bins must contain the classification threshold")

    confusion = evidence.confusion
    if sum(item.sample_count for item in bins) != confusion.sample_count:
        raise ValueError(f"{label} calibration sample count is inconsistent")
    if sum(item.positive_count for item in bins) != (
        confusion.true_positive + confusion.false_negative
    ):
        raise ValueError(f"{label} calibration positive count is inconsistent")
    predicted_positive = sum(
        item.sample_count
        for item in bins
        if float(item.lower_bound) >= threshold - _TOLERANCE
    )
    if predicted_positive != confusion.true_positive + confusion.false_positive:
        raise ValueError(f"{label} predicted-positive count is inconsistent")


def _aggregate_confusion(rows: list[StratumEvidence]) -> dict[str, int]:
    return {
        field: sum(getattr(row.confusion, field) for row in rows)
        for field in (
            "true_positive",
            "false_positive",
            "true_negative",
            "false_negative",
        )
    }


def _near(left: float, right: float) -> bool:
    scale = max(1.0, abs(left), abs(right))
    return abs(left - right) <= _TOLERANCE * scale


def _validate_partition(
    *,
    overall: SliceEvidence,
    strata: list[StratumEvidence],
    dimension: str,
) -> None:
    if _aggregate_confusion(strata) != overall.confusion.model_dump():
        raise ValueError(
            f"{dimension} strata do not partition overall confusion counts"
        )
    for index, overall_bin in enumerate(overall.calibration_bins):
        stratum_bins = [item.calibration_bins[index] for item in strata]
        if sum(item.sample_count for item in stratum_bins) != overall_bin.sample_count:
            raise ValueError(
                f"{dimension} strata do not partition calibration sample counts"
            )
        if (
            sum(item.positive_count for item in stratum_bins)
            != overall_bin.positive_count
        ):
            raise ValueError(
                f"{dimension} strata do not partition calibration positive counts"
            )
        for field_name in (
            "predicted_probability_sum",
            "positive_probability_sum",
            "squared_probability_sum",
        ):
            aggregate = sum(
                float(getattr(item, field_name)) for item in stratum_bins
            )
            expected = float(getattr(overall_bin, field_name))
            if not _near(aggregate, expected):
                raise ValueError(
                    f"{dimension} strata do not partition calibration {field_name}"
                )


class MetricSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_count: int = Field(gt=0)
    positive_count: int = Field(ge=0)
    predicted_positive_count: int = Field(ge=0)
    precision: Probability | None
    recall: Probability | None
    f1: Probability | None
    brier_score: Probability
    expected_calibration_error: Probability


class StratumMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Literal["country", "language", "topic"]
    value: str
    metrics: MetricSet


class CoverageAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["complete", "incomplete"]
    expected: dict[str, list[str]]
    observed: dict[str, list[str]]
    missing: dict[str, list[str]]
    unexpected: dict[str, list[str]]
    minimum_samples_satisfied: bool


class DriftAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["not_observed", "within_threshold", "detected"]
    baseline_evaluation_id: str | None = None
    f1_delta: FiniteFloat | None = None
    brier_delta: FiniteFloat | None = None
    ece_delta: FiniteFloat | None = None
    reason_codes: list[str] = Field(default_factory=list)


class RollbackRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["proceed", "hold_release", "rollback_to_baseline"]
    target_evaluation_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["globemind.model-assurance.v1"] = (
        MODEL_ASSURANCE_SCHEMA_VERSION
    )
    contract_version: Literal["1.0.0"] = MODEL_ASSURANCE_CONTRACT_VERSION
    metric_method_version: Literal["binary-assurance-metrics-1.0.0"] = (
        METRIC_METHOD_VERSION
    )
    evaluation_id: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluated_at: datetime
    evidence_status: Literal["manifest_only"] = "manifest_only"
    overall: MetricSet
    strata: list[StratumMetrics]
    coverage: CoverageAssessment
    drift: DriftAssessment
    rollback: RollbackRecommendation
    gate_state: Literal["eligible", "blocked"]
    release_eligible: bool
    reason_codes: list[str] = Field(default_factory=list)


class StoredEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage_schema_version: Literal["globemind.model-assurance.entry.v1"] = (
        STORE_SCHEMA_VERSION
    )
    sequence: int = Field(gt=0)
    stored_at: datetime
    submitted_by: str = Field(pattern=r"^user:[1-9][0-9]*$")
    previous_entry_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    manifest: EvaluationManifest
    result: EvaluationResult
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    model_id: str
    model_version: str
    method_version: str
    dataset_id: str
    dataset_sha256: str
    cutoff_at: datetime
    stored_at: datetime
    entry_sha256: str
    gate_state: Literal["eligible", "blocked"]
    release_eligible: bool
    drift_state: Literal["not_observed", "within_threshold", "detected"]
    rollback_action: Literal["proceed", "hold_release", "rollback_to_baseline"]
    reason_codes: list[str]


class ModelAssuranceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["globemind.model-assurance.v1"] = (
        MODEL_ASSURANCE_SCHEMA_VERSION
    )
    generated_at: datetime
    available: bool
    operational_state: Literal["not_observed", "observed"]
    release_status: Literal["blocked", "eligible"]
    gold_standard_state: Literal["not_observed", "manifest_attested"]
    evaluation_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    latest: EvaluationSummary | None = None
    reason_codes: list[str] = Field(default_factory=list)


class ModelAssuranceCatalog(ModelAssuranceStatus):
    metric_method_version: Literal["binary-assurance-metrics-1.0.0"] = (
        METRIC_METHOD_VERSION
    )
    required_strata: list[Literal["country", "language", "topic"]] = Field(
        default_factory=lambda: ["country", "language", "topic"]
    )
    accepted_metrics: list[
        Literal["precision", "recall", "f1", "brier_score", "ece"]
    ] = Field(
        default_factory=lambda: [
            "precision",
            "recall",
            "f1",
            "brier_score",
            "ece",
        ]
    )
    persistence: Literal["append_only_hash_chain"] = "append_only_hash_chain"


__all__ = (
    "AssuranceThresholds",
    "BaselineReference",
    "CalibrationBin",
    "ConfusionCounts",
    "CoverageAssessment",
    "CoverageDeclaration",
    "DatasetIdentity",
    "DriftAssessment",
    "EvaluationIntegrityDeclaration",
    "EvaluationManifest",
    "EvaluationResult",
    "EvaluationSummary",
    "IndependentReview",
    "METRIC_METHOD_VERSION",
    "MODEL_ASSURANCE_CONTRACT_VERSION",
    "MODEL_ASSURANCE_SCHEMA_VERSION",
    "MetricSet",
    "ModelAssuranceCatalog",
    "ModelAssuranceStatus",
    "ModelIdentity",
    "RollbackRecommendation",
    "STORE_SCHEMA_VERSION",
    "SliceEvidence",
    "StoredEvaluation",
    "StratumEvidence",
    "StratumMetrics",
)
