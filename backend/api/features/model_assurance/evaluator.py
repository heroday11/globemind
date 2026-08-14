"""Deterministic metric recomputation and fail-closed release gating."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .contracts import (
    CoverageAssessment,
    DriftAssessment,
    EvaluationManifest,
    EvaluationResult,
    IndependentReview,
    MetricSet,
    RollbackRecommendation,
    SliceEvidence,
    StoredEvaluation,
    StratumMetrics,
)


class ManifestRejected(ValueError):
    """The manifest is internally valid JSON but cannot support evaluation."""


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ManifestRejected("evaluation timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _metric(value: float | None) -> float | None:
    if value is None:
        return None
    return round(min(1.0, max(0.0, value)), 10)


def _bin_boundaries(evidence: SliceEvidence) -> tuple[tuple[float, float], ...]:
    return tuple(
        (round(float(item.lower_bound), 12), round(float(item.upper_bound), 12))
        for item in evidence.calibration_bins
    )


def compute_metrics(evidence: SliceEvidence) -> MetricSet:
    """Recompute every accepted metric from sufficient statistics."""

    confusion = evidence.confusion
    predicted_positive = confusion.true_positive + confusion.false_positive
    positive = confusion.true_positive + confusion.false_negative
    precision = _ratio(confusion.true_positive, predicted_positive)
    recall = _ratio(confusion.true_positive, positive)
    f1_denominator = (
        2 * confusion.true_positive
        + confusion.false_positive
        + confusion.false_negative
    )
    f1 = _ratio(2 * confusion.true_positive, f1_denominator)

    brier_numerator = sum(
        float(item.squared_probability_sum)
        - 2 * float(item.positive_probability_sum)
        + item.positive_count
        for item in evidence.calibration_bins
    )
    sample_count = confusion.sample_count
    brier = min(1.0, max(0.0, brier_numerator / sample_count))
    ece = sum(
        item.sample_count
        / sample_count
        * abs(
            float(item.predicted_probability_sum) / item.sample_count
            - item.positive_count / item.sample_count
        )
        for item in evidence.calibration_bins
    )
    return MetricSet(
        sample_count=sample_count,
        positive_count=positive,
        predicted_positive_count=predicted_positive,
        precision=_metric(precision),
        recall=_metric(recall),
        f1=_metric(f1),
        brier_score=_metric(brier),
        expected_calibration_error=_metric(ece),
    )


def _coverage(
    manifest: EvaluationManifest,
    *,
    metrics: list[StratumMetrics],
) -> CoverageAssessment:
    expected = {
        "country": sorted(manifest.coverage.countries),
        "language": sorted(manifest.coverage.languages),
        "topic": sorted(manifest.coverage.topics),
    }
    observed = {
        dimension: sorted(
            item.value for item in metrics if item.dimension == dimension
        )
        for dimension in ("country", "language", "topic")
    }
    missing = {
        dimension: sorted(set(expected[dimension]) - set(observed[dimension]))
        for dimension in expected
    }
    unexpected = {
        dimension: sorted(set(observed[dimension]) - set(expected[dimension]))
        for dimension in expected
    }
    thresholds = manifest.thresholds
    minimum_samples_satisfied = bool(
        thresholds
        and manifest.overall.confusion.sample_count
        >= thresholds.minimum_overall_samples
        and all(
            item.metrics.sample_count >= thresholds.minimum_samples_per_stratum
            for item in metrics
        )
    )
    dimensions_complete = all(
        expected[dimension]
        and not missing[dimension]
        and not unexpected[dimension]
        for dimension in expected
    )
    return CoverageAssessment(
        state=(
            "complete"
            if dimensions_complete and minimum_samples_satisfied
            else "incomplete"
        ),
        expected=expected,
        observed=observed,
        missing=missing,
        unexpected=unexpected,
        minimum_samples_satisfied=minimum_samples_satisfied,
    )


def _validate_baseline(
    manifest: EvaluationManifest,
    baseline: StoredEvaluation,
) -> None:
    baseline_manifest = baseline.manifest
    if manifest.baseline is None:
        raise ManifestRejected("baseline was not declared")
    if manifest.baseline.entry_sha256 != baseline.entry_sha256:
        raise ManifestRejected("baseline entry digest does not match")
    if baseline_manifest.evaluation_id != manifest.baseline.evaluation_id:
        raise ManifestRejected("baseline evaluation id does not match")
    if baseline_manifest.model.model_id != manifest.model.model_id:
        raise ManifestRejected("baseline belongs to another model")
    if baseline_manifest.model.method_version != manifest.model.method_version:
        raise ManifestRejected("baseline method version differs")
    if baseline_manifest.model.positive_label != manifest.model.positive_label:
        raise ManifestRejected("baseline positive label differs")
    if (
        float(baseline_manifest.classification_threshold)
        != float(manifest.classification_threshold)
    ):
        raise ManifestRejected("baseline classification threshold differs")
    if (
        baseline_manifest.dataset.dataset_id != manifest.dataset.dataset_id
        or baseline_manifest.dataset.sha256 != manifest.dataset.sha256
        or baseline_manifest.dataset.cutoff_at != manifest.dataset.cutoff_at
    ):
        raise ManifestRejected("baseline evaluation dataset differs")
    if (
        baseline_manifest.dataset.dataset_version
        != manifest.dataset.dataset_version
        or baseline_manifest.dataset.evaluation_role
        != manifest.dataset.evaluation_role
        or baseline_manifest.dataset.gold_standard_status
        != manifest.dataset.gold_standard_status
        or baseline_manifest.dataset.label_schema_version
        != manifest.dataset.label_schema_version
        or baseline_manifest.dataset.annotation_protocol_ref
        != manifest.dataset.annotation_protocol_ref
        or baseline_manifest.dataset.provenance_ref
        != manifest.dataset.provenance_ref
    ):
        raise ManifestRejected("baseline dataset governance metadata differs")
    if any(
        set(getattr(baseline_manifest.coverage, dimension))
        != set(getattr(manifest.coverage, dimension))
        for dimension in ("countries", "languages", "topics")
    ):
        raise ManifestRejected("baseline coverage declaration differs")
    if (
        baseline_manifest.overall.confusion.sample_count
        != manifest.overall.confusion.sample_count
        or (
            baseline_manifest.overall.confusion.true_positive
            + baseline_manifest.overall.confusion.false_negative
        )
        != (
            manifest.overall.confusion.true_positive
            + manifest.overall.confusion.false_negative
        )
    ):
        raise ManifestRejected("baseline evaluation cohort differs")
    if _bin_boundaries(baseline_manifest.overall) != _bin_boundaries(
        manifest.overall
    ):
        raise ManifestRejected("baseline calibration bin scheme differs")

    def cohort_by_stratum(
        candidate: EvaluationManifest,
    ) -> dict[tuple[str, str], tuple[int, int]]:
        return {
            (item.dimension, item.value): (
                item.confusion.sample_count,
                item.confusion.true_positive + item.confusion.false_negative,
            )
            for item in candidate.strata
        }

    baseline_cohorts = cohort_by_stratum(baseline_manifest)
    current_cohorts = cohort_by_stratum(manifest)
    if baseline_cohorts.keys() != current_cohorts.keys():
        raise ManifestRejected("baseline stratum coverage differs")
    if baseline_cohorts != current_cohorts:
        raise ManifestRejected("baseline stratum cohorts differ")


def _drift(
    manifest: EvaluationManifest,
    current: MetricSet,
    baseline: StoredEvaluation | None,
) -> DriftAssessment:
    if baseline is None or manifest.baseline is None:
        return DriftAssessment(
            state="not_observed",
            reason_codes=["BASELINE_NOT_PROVIDED"],
        )
    _validate_baseline(manifest, baseline)
    prior = baseline.result.overall
    if current.f1 is None or prior.f1 is None:
        return DriftAssessment(
            state="not_observed",
            baseline_evaluation_id=baseline.manifest.evaluation_id,
            reason_codes=["F1_UNDEFINED_FOR_DRIFT"],
        )
    f1_delta = round(float(current.f1) - float(prior.f1), 10)
    brier_delta = round(
        float(current.brier_score) - float(prior.brier_score),
        10,
    )
    ece_delta = round(
        float(current.expected_calibration_error)
        - float(prior.expected_calibration_error),
        10,
    )
    thresholds = manifest.thresholds
    if thresholds is None:
        return DriftAssessment(
            state="not_observed",
            baseline_evaluation_id=baseline.manifest.evaluation_id,
            f1_delta=f1_delta,
            brier_delta=brier_delta,
            ece_delta=ece_delta,
            reason_codes=["DRIFT_THRESHOLDS_NOT_PROVIDED"],
        )

    reasons: list[str] = []
    if -f1_delta > float(thresholds.maximum_f1_drop_from_baseline):
        reasons.append("F1_DRIFT_EXCEEDED")
    if brier_delta > float(thresholds.maximum_brier_increase_from_baseline):
        reasons.append("BRIER_DRIFT_EXCEEDED")
    if ece_delta > float(thresholds.maximum_ece_increase_from_baseline):
        reasons.append("ECE_DRIFT_EXCEEDED")
    return DriftAssessment(
        state="detected" if reasons else "within_threshold",
        baseline_evaluation_id=baseline.manifest.evaluation_id,
        f1_delta=f1_delta,
        brier_delta=brier_delta,
        ece_delta=ece_delta,
        reason_codes=reasons,
    )


def review_validity_reason(
    review: IndependentReview | None,
    *,
    as_of: datetime,
) -> str | None:
    """Return the fail-closed temporal blocker for an approval attestation."""

    if review is None:
        return "INDEPENDENT_REVIEW_NOT_PROVIDED"
    if review.valid_until is None:
        return "REVIEW_EXPIRY_NOT_DECLARED"
    if review.valid_until <= _utc(as_of):
        return "INDEPENDENT_REVIEW_EXPIRED"
    return None


def _baseline_is_qualified(
    baseline: StoredEvaluation | None,
    *,
    as_of: datetime,
) -> bool:
    if baseline is None:
        return False
    allowed_bootstrap_reasons = {"BASELINE_NOT_PROVIDED"}
    if not set(baseline.result.reason_codes).issubset(allowed_bootstrap_reasons):
        return False
    review = baseline.manifest.independent_review
    return bool(
        baseline.result.coverage.state == "complete"
        and review is not None
        and review.independence_attestation
        and review.decision == "approved"
        and review_validity_reason(review, as_of=as_of) is None
    )


def evaluate_manifest(
    manifest: EvaluationManifest,
    *,
    baseline: StoredEvaluation | None,
    baseline_dependency_reasons: tuple[str, ...] = (),
    evaluated_at: datetime,
    submitted_by: str,
) -> EvaluationResult:
    evaluated_at = _utc(evaluated_at)
    review = manifest.independent_review
    if review is not None and review.reviewed_at > evaluated_at:
        raise ManifestRejected("review timestamp is in the future")
    if (
        review is not None
        and review.independence_attestation
        and review.reviewer_id == submitted_by
    ):
        raise ManifestRejected("submitter cannot attest their own independent review")
    if manifest.baseline is not None and baseline is None:
        raise ManifestRejected("declared baseline does not exist")
    if baseline is not None and _utc(baseline.stored_at) > evaluated_at:
        raise ManifestRejected("baseline was stored after the candidate timestamp")

    overall = compute_metrics(manifest.overall)
    strata = [
        StratumMetrics(
            dimension=item.dimension,
            value=item.value,
            metrics=compute_metrics(item),
        )
        for item in manifest.strata
    ]
    coverage = _coverage(manifest, metrics=strata)
    drift = _drift(manifest, overall, baseline)
    baseline_qualified = _baseline_is_qualified(
        baseline,
        as_of=evaluated_at,
    ) and not baseline_dependency_reasons
    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    thresholds = manifest.thresholds
    if thresholds is None:
        add("THRESHOLDS_NOT_PROVIDED")
    else:
        for metric_name, value, minimum in (
            ("PRECISION", overall.precision, thresholds.minimum_precision),
            ("RECALL", overall.recall, thresholds.minimum_recall),
            ("F1", overall.f1, thresholds.minimum_f1),
        ):
            if value is None:
                add(f"{metric_name}_UNDEFINED")
            elif float(value) < float(minimum):
                add(f"{metric_name}_THRESHOLD_NOT_MET")
        if float(overall.brier_score) > float(thresholds.maximum_brier_score):
            add("BRIER_THRESHOLD_NOT_MET")
        if float(overall.expected_calibration_error) > float(
            thresholds.maximum_ece
        ):
            add("ECE_THRESHOLD_NOT_MET")
        for item in strata:
            if item.metrics.f1 is None:
                add("STRATUM_F1_UNDEFINED")
            elif float(item.metrics.f1) < float(thresholds.minimum_stratum_f1):
                add("STRATUM_F1_THRESHOLD_NOT_MET")
        if overall.sample_count < thresholds.minimum_overall_samples:
            add("OVERALL_SAMPLE_MINIMUM_NOT_MET")
        if any(
            item.metrics.sample_count < thresholds.minimum_samples_per_stratum
            for item in strata
        ):
            add("STRATUM_SAMPLE_MINIMUM_NOT_MET")

    for dimension in ("country", "language", "topic"):
        if not coverage.expected[dimension]:
            add(f"{dimension.upper()}_COVERAGE_NOT_DECLARED")
        if coverage.missing[dimension] or coverage.unexpected[dimension]:
            add(f"{dimension.upper()}_COVERAGE_INCOMPLETE")
    if coverage.state != "complete":
        add("COVERAGE_INCOMPLETE")

    dataset = manifest.dataset
    if dataset.evaluation_role != "gold_standard":
        add("DATASET_NOT_GOLD_STANDARD")
    if dataset.gold_standard_status != "independently_reviewed":
        add("GOLD_STANDARD_NOT_INDEPENDENTLY_REVIEWED")
    if not (
        dataset.label_schema_version
        and dataset.annotation_protocol_ref
        and dataset.provenance_ref
    ):
        add("DATASET_GOVERNANCE_EVIDENCE_INCOMPLETE")

    integrity = manifest.evaluation_integrity
    if integrity is None:
        add("EVALUATION_INTEGRITY_NOT_PROVIDED")
    else:
        if integrity.label_source != "human_gold":
            add("LABEL_SOURCE_NOT_HUMAN_GOLD")
        if integrity.partition_role != "holdout":
            add("EVALUATION_PARTITION_NOT_HOLDOUT")
        if integrity.holdout_access_status != "sealed":
            add("HOLDOUT_ISOLATION_NOT_ATTESTED")

    if review is None:
        add("INDEPENDENT_REVIEW_NOT_PROVIDED")
    else:
        if not review.independence_attestation:
            add("INDEPENDENCE_NOT_ATTESTED")
        if review.decision != "approved":
            add("INDEPENDENT_REVIEW_NOT_APPROVED")
        validity_reason = review_validity_reason(review, as_of=evaluated_at)
        if validity_reason is not None:
            add(validity_reason)

    if manifest.baseline is None:
        add("BASELINE_NOT_PROVIDED")
    elif baseline is not None:
        baseline_review = baseline.manifest.independent_review
        if (
            baseline_review is None
            or not baseline_review.independence_attestation
            or baseline_review.decision != "approved"
        ):
            add("BASELINE_REVIEW_INCOMPLETE")
        baseline_validity_reason = review_validity_reason(
            baseline_review,
            as_of=evaluated_at,
        )
        if baseline_validity_reason == "REVIEW_EXPIRY_NOT_DECLARED":
            add("BASELINE_REVIEW_EXPIRY_NOT_DECLARED")
        elif baseline_validity_reason == "INDEPENDENT_REVIEW_EXPIRED":
            add("BASELINE_REVIEW_EXPIRED")
        if baseline.result.coverage.state != "complete":
            add("BASELINE_COVERAGE_INCOMPLETE")
        for dependency_reason in baseline_dependency_reasons:
            add(dependency_reason)
        if not baseline_qualified:
            add("BASELINE_ASSURANCE_INCOMPLETE")
    for reason in drift.reason_codes:
        add(reason)
    if drift.state == "detected":
        add("DRIFT_DETECTED")
    elif manifest.baseline is not None and drift.state != "within_threshold":
        add("DRIFT_NOT_ESTABLISHED")

    release_eligible = not reasons
    if release_eligible:
        rollback = RollbackRecommendation(
            action="proceed",
            reason_codes=[],
        )
    elif drift.state == "detected" and baseline is not None and baseline_qualified:
        rollback = RollbackRecommendation(
            action="rollback_to_baseline",
            target_evaluation_id=baseline.manifest.evaluation_id,
            reason_codes=list(drift.reason_codes),
        )
    else:
        rollback = RollbackRecommendation(
            action="hold_release",
            reason_codes=list(reasons),
        )

    return EvaluationResult(
        evaluation_id=manifest.evaluation_id,
        manifest_sha256=canonical_sha256(manifest.model_dump(mode="json")),
        evaluated_at=evaluated_at,
        overall=overall,
        strata=strata,
        coverage=coverage,
        drift=drift,
        rollback=rollback,
        gate_state="eligible" if release_eligible else "blocked",
        release_eligible=release_eligible,
        reason_codes=reasons,
    )


__all__ = (
    "ManifestRejected",
    "canonical_sha256",
    "compute_metrics",
    "evaluate_manifest",
    "review_validity_reason",
)
