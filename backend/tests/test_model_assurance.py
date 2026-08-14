from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.features.model_assurance import (
    AssuranceConflict,
    AssuranceStoreUnavailable,
    EvaluationManifest,
    ManifestRejected,
    ModelAssuranceService,
    ModelAssuranceStore,
    canonical_sha256,
)
from api.routes import model_assurance
from api.services.auth import get_current_admin_user, get_current_user_required


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
REVIEWED = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
REVIEW_VALID_UNTIL = datetime(2099, 9, 1, 0, 0, tzinfo=timezone.utc)


def _slice(true_positive: int, false_positive: int, true_negative: int, false_negative: int):
    negative_count = true_negative + false_negative
    positive_count = true_positive + false_positive
    assert negative_count > 0 and positive_count > 0
    low_probability = false_negative / negative_count
    high_probability = true_positive / positive_count
    assert 0 <= low_probability < 0.5 <= high_probability <= 1
    return {
        "confusion": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "true_negative": true_negative,
            "false_negative": false_negative,
        },
        "calibration_bins": [
            {
                "lower_bound": 0,
                "upper_bound": 0.5,
                "sample_count": negative_count,
                "positive_count": false_negative,
                "predicted_probability_sum": low_probability * negative_count,
                "positive_probability_sum": low_probability * false_negative,
                "squared_probability_sum": low_probability**2 * negative_count,
            },
            {
                "lower_bound": 0.5,
                "upper_bound": 1,
                "sample_count": positive_count,
                "positive_count": true_positive,
                "predicted_probability_sum": high_probability * positive_count,
                "positive_probability_sum": high_probability * true_positive,
                "squared_probability_sum": high_probability**2 * positive_count,
            },
        ],
    }


def _manifest_payload(
    evaluation_id: str,
    model_version: str,
    *,
    degraded: bool = False,
    baseline: dict[str, str] | None = None,
) -> dict:
    overall = _slice(30, 20, 30, 20) if degraded else _slice(40, 10, 40, 10)
    half = _slice(15, 10, 15, 10) if degraded else _slice(20, 5, 20, 5)
    strata = [
        {"dimension": dimension, "value": value, **copy.deepcopy(half)}
        for dimension, values in (
            ("country", ["CHN", "USA"]),
            ("language", ["zh", "en"]),
            ("topic", ["economy", "security"]),
        )
        for value in values
    ]
    return {
        "evaluation_id": evaluation_id,
        "evaluation_version": "2026.08.09.1",
        "dataset": {
            "dataset_id": "gold.news-stance.v1",
            "dataset_version": "2026.08.01",
            "sha256": "a" * 64,
            "cutoff_at": CUTOFF.isoformat(),
            "evaluation_role": "gold_standard",
            "gold_standard_status": "independently_reviewed",
            "label_schema_version": "stance-labels-v1",
            "annotation_protocol_ref": "governance:annotation-protocol:v1",
            "provenance_ref": "governance:gold-dataset:v1",
        },
        "model": {
            "model_id": "stance.classifier",
            "model_version": model_version,
            "method_version": "stance-method-v3",
            "owner_organization": "GlobeMind Model Team",
            "task_type": "binary_classification",
            "positive_label": "relevant",
        },
        "classification_threshold": 0.5,
        "overall": overall,
        "strata": strata,
        "coverage": {
            "countries": ["CHN", "USA"],
            "languages": ["zh", "en"],
            "topics": ["economy", "security"],
        },
        "thresholds": {
            "minimum_precision": 0.75,
            "minimum_recall": 0.75,
            "minimum_f1": 0.75,
            "maximum_brier_score": 0.2,
            "maximum_ece": 0.05,
            "minimum_stratum_f1": 0.75,
            "minimum_overall_samples": 100,
            "minimum_samples_per_stratum": 50,
            "maximum_f1_drop_from_baseline": 0.05,
            "maximum_brier_increase_from_baseline": 0.05,
            "maximum_ece_increase_from_baseline": 0.05,
        },
        "independent_review": {
            "review_id": f"review:{evaluation_id}",
            "reviewer_id": "reviewer:external-7",
            "reviewer_organization": "Independent Evaluation Lab",
            "independence_attestation": True,
            "decision": "approved",
            "reviewed_at": REVIEWED.isoformat(),
            "valid_until": REVIEW_VALID_UNTIL.isoformat(),
            "evidence_ref": f"review-evidence:{evaluation_id}",
            "evidence_sha256": "b" * 64,
        },
        "evaluation_integrity": {
            "label_source": "human_gold",
            "partition_role": "holdout",
            "holdout_access_status": "sealed",
            "development_dataset_sha256s": ["c" * 64],
            "separation_evidence_ref": f"separation-evidence:{evaluation_id}",
            "separation_evidence_sha256": "f" * 64,
        },
        "baseline": baseline,
    }


def _service(root: Path) -> ModelAssuranceService:
    return ModelAssuranceService(ModelAssuranceStore(root), now=lambda: NOW)


def test_server_recomputes_metrics_and_first_manifest_stays_blocked_without_baseline(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "assurance")
    manifest = EvaluationManifest.model_validate(
        _manifest_payload("eval.stance.v1", "1.0.0")
    )

    entry = service.submit(manifest, submitted_by="user:7")

    assert entry.result.overall.sample_count == 100
    assert entry.result.overall.precision == 0.8
    assert entry.result.overall.recall == 0.8
    assert entry.result.overall.f1 == 0.8
    assert entry.result.overall.brier_score == 0.16
    assert entry.result.overall.expected_calibration_error == 0
    assert entry.result.coverage.state == "complete"
    assert entry.result.evidence_status == "manifest_only"
    assert entry.result.release_eligible is False
    assert entry.result.gate_state == "blocked"
    assert "BASELINE_NOT_PROVIDED" in entry.result.reason_codes
    assert entry.result.drift.state == "not_observed"
    assert entry.result.rollback.action == "hold_release"
    assert entry.result.manifest_sha256 == canonical_sha256(
        manifest.model_dump(mode="json")
    )


def test_reviewed_baseline_enables_eligibility_and_degradation_recommends_rollback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "assurance")
    baseline_manifest = EvaluationManifest.model_validate(
        _manifest_payload("eval.stance.baseline", "1.0.0")
    )
    baseline = service.submit(baseline_manifest, submitted_by="user:7")
    baseline_ref = {
        "evaluation_id": baseline.manifest.evaluation_id,
        "entry_sha256": baseline.entry_sha256,
    }

    candidate_manifest = EvaluationManifest.model_validate(
        _manifest_payload(
            "eval.stance.candidate",
            "2.0.0",
            baseline=baseline_ref,
        )
    )
    candidate = service.submit(candidate_manifest, submitted_by="user:7")

    assert candidate.result.release_eligible is True
    assert candidate.result.reason_codes == []
    assert candidate.result.drift.state == "within_threshold"
    assert candidate.result.drift.f1_delta == 0
    assert candidate.result.rollback.action == "proceed"
    assert service.status().release_status == "eligible"
    assert service.status().eligible_count == 1

    degraded_manifest = EvaluationManifest.model_validate(
        _manifest_payload(
            "eval.stance.degraded",
            "3.0.0",
            degraded=True,
            baseline=baseline_ref,
        )
    )
    degraded = service.submit(degraded_manifest, submitted_by="user:7")

    assert degraded.result.overall.f1 == 0.6
    assert degraded.result.release_eligible is False
    assert degraded.result.drift.state == "detected"
    assert degraded.result.drift.f1_delta == -0.2
    assert "F1_DRIFT_EXCEEDED" in degraded.result.reason_codes
    assert "DRIFT_DETECTED" in degraded.result.reason_codes
    assert degraded.result.rollback.action == "rollback_to_baseline"
    assert degraded.result.rollback.target_evaluation_id == "eval.stance.baseline"
    assert service.status().release_status == "blocked"


def test_append_only_hash_chain_preserves_prior_entry_and_rejects_duplicates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "assurance"
    service = _service(root)
    manifest = EvaluationManifest.model_validate(
        _manifest_payload("eval.stance.baseline", "1.0.0")
    )
    first = service.submit(manifest, submitted_by="user:7")
    first_path = root / "entries" / "00000001-eval.stance.baseline.json"
    first_bytes = first_path.read_bytes()

    candidate = EvaluationManifest.model_validate(
        _manifest_payload(
            "eval.stance.candidate",
            "2.0.0",
            baseline={
                "evaluation_id": first.manifest.evaluation_id,
                "entry_sha256": first.entry_sha256,
            },
        )
    )
    second = service.submit(candidate, submitted_by="user:7")

    assert first_path.read_bytes() == first_bytes
    assert second.sequence == 2
    assert second.previous_entry_sha256 == first.entry_sha256
    assert (root / "entries" / "00000002-eval.stance.candidate.json").is_file()
    assert first_path.stat().st_mode & 0o777 == 0o640
    with pytest.raises(AssuranceConflict):
        service.submit(manifest, submitted_by="user:7")


def test_recomputation_detects_a_tampered_result_even_if_attacker_rehashes_entry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "assurance"
    service = _service(root)
    service.submit(
        EvaluationManifest.model_validate(
            _manifest_payload("eval.stance.baseline", "1.0.0")
        ),
        submitted_by="user:7",
    )
    path = root / "entries" / "00000001-eval.stance.baseline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["result"]["overall"]["precision"] = 0.99
    digest_payload = {key: value for key, value in payload.items() if key != "entry_sha256"}
    payload["entry_sha256"] = canonical_sha256(digest_payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(AssuranceStoreUnavailable):
        service.status()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["overall"]["confusion"].update(
            {
                "true_positive": 0,
                "false_positive": 0,
                "true_negative": 0,
                "false_negative": 0,
            }
        ),
        lambda payload: payload["strata"].append(copy.deepcopy(payload["strata"][0])),
        lambda payload: payload["overall"]["calibration_bins"][0].update(
            {"predicted_probability_sum": float("nan")}
        ),
        lambda payload: payload["overall"].update({"precision": 0.99}),
        lambda payload: payload["model"].update({"method_version": "  "}),
    ],
)
def test_manifest_rejects_empty_duplicate_non_finite_and_client_metrics(mutate) -> None:
    payload = _manifest_payload("eval.stance.invalid", "1.0.0")
    mutate(payload)
    with pytest.raises(ValidationError):
        EvaluationManifest.model_validate(payload)


def test_manifest_rejects_calibration_and_partition_inconsistency() -> None:
    calibration = _manifest_payload("eval.stance.invalid-calibration", "1.0.0")
    calibration["overall"]["calibration_bins"][0]["sample_count"] = 49
    with pytest.raises(ValidationError):
        EvaluationManifest.model_validate(calibration)

    partition = _manifest_payload("eval.stance.invalid-partition", "1.0.0")
    partition["strata"][0] = {
        "dimension": "country",
        "value": "CHN",
        **_slice(19, 6, 20, 5),
    }
    with pytest.raises(ValidationError):
        EvaluationManifest.model_validate(partition)

    calibration_partition = _manifest_payload(
        "eval.stance.invalid-calibration-partition",
        "1.0.0",
    )
    calibration_partition["strata"][0]["calibration_bins"][0][
        "predicted_probability_sum"
    ] += 0.1
    with pytest.raises(ValidationError):
        EvaluationManifest.model_validate(calibration_partition)


def test_impossible_calibration_moments_and_contradictory_gold_claim_are_rejected(
) -> None:
    impossible = _manifest_payload("eval.stance.impossible-moment", "1.0.0")
    low_bin = impossible["overall"]["calibration_bins"][0]
    low_bin["positive_probability_sum"] = 0
    with pytest.raises(ValidationError):
        EvaluationManifest.model_validate(impossible)

    contradictory = _manifest_payload("eval.stance.contradictory-gold", "1.0.0")
    contradictory["dataset"]["evaluation_role"] = "holdout"
    with pytest.raises(ValidationError):
        EvaluationManifest.model_validate(contradictory)


def test_unqualified_baseline_cannot_enable_release_or_be_a_rollback_target(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "assurance")
    baseline_payload = _manifest_payload("eval.stance.weak-baseline", "1.0.0")
    baseline_payload["thresholds"]["minimum_f1"] = 0.9
    baseline = service.submit(
        EvaluationManifest.model_validate(baseline_payload),
        submitted_by="user:7",
    )
    degraded = service.submit(
        EvaluationManifest.model_validate(
            _manifest_payload(
                "eval.stance.weak-baseline-candidate",
                "2.0.0",
                degraded=True,
                baseline={
                    "evaluation_id": baseline.manifest.evaluation_id,
                    "entry_sha256": baseline.entry_sha256,
                },
            )
        ),
        submitted_by="user:7",
    )

    assert "BASELINE_ASSURANCE_INCOMPLETE" in degraded.result.reason_codes
    assert degraded.result.drift.state == "detected"
    assert degraded.result.release_eligible is False
    assert degraded.result.rollback.action == "hold_release"
    assert degraded.result.rollback.target_evaluation_id is None


def test_baseline_digest_and_independent_review_actor_are_fail_closed(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "assurance")
    baseline = service.submit(
        EvaluationManifest.model_validate(
            _manifest_payload("eval.stance.review-baseline", "1.0.0")
        ),
        submitted_by="user:7",
    )
    bad_digest = _manifest_payload(
        "eval.stance.bad-baseline-digest",
        "2.0.0",
        baseline={
            "evaluation_id": baseline.manifest.evaluation_id,
            "entry_sha256": "c" * 64,
        },
    )
    with pytest.raises(ManifestRejected, match="digest"):
        service.submit(
            EvaluationManifest.model_validate(bad_digest),
            submitted_by="user:7",
        )

    self_review = _manifest_payload("eval.stance.self-review", "2.0.0")
    self_review["independent_review"]["reviewer_id"] = "user:7"
    with pytest.raises(ManifestRejected, match="own independent review"):
        service.submit(
            EvaluationManifest.model_validate(self_review),
            submitted_by="user:7",
        )

    future_review = _manifest_payload("eval.stance.future-review", "2.0.0")
    future_review["independent_review"]["reviewed_at"] = (
        datetime(2026, 8, 10, tzinfo=timezone.utc).isoformat()
    )
    with pytest.raises(ManifestRejected, match="future"):
        service.submit(
            EvaluationManifest.model_validate(future_review),
            submitted_by="user:7",
        )

    replayed_review = _manifest_payload(
        "eval.stance.replayed-review",
        "2.0.0",
        baseline={
            "evaluation_id": baseline.manifest.evaluation_id,
            "entry_sha256": baseline.entry_sha256,
        },
    )
    replayed_review["independent_review"]["review_id"] = (
        baseline.manifest.independent_review.review_id
    )
    with pytest.raises(ManifestRejected, match="review id"):
        service.submit(
            EvaluationManifest.model_validate(replayed_review),
            submitted_by="user:7",
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("dataset_version", "2026.08.01-relabelled"),
        ("label_schema_version", "stance-labels-v2"),
        ("annotation_protocol_ref", "governance:annotation-protocol:v2"),
        ("provenance_ref", "governance:gold-dataset:replacement"),
    ],
)
def test_baseline_dataset_governance_metadata_must_match_exactly(
    tmp_path: Path,
    field_name: str,
    replacement: str,
) -> None:
    service = _service(tmp_path / "assurance")
    baseline = service.submit(
        EvaluationManifest.model_validate(
            _manifest_payload("eval.stance.metadata-baseline", "1.0.0")
        ),
        submitted_by="user:7",
    )
    candidate = _manifest_payload(
        f"eval.stance.metadata-{field_name.replace('_', '-')}",
        "2.0.0",
        baseline={
            "evaluation_id": baseline.manifest.evaluation_id,
            "entry_sha256": baseline.entry_sha256,
        },
    )
    candidate["dataset"][field_name] = replacement

    with pytest.raises(ManifestRejected, match="dataset governance metadata"):
        service.submit(
            EvaluationManifest.model_validate(candidate),
            submitted_by="user:7",
        )


def test_baseline_requires_the_same_cohort_and_slice_definitions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "assurance")
    baseline = service.submit(
        EvaluationManifest.model_validate(
            _manifest_payload("eval.stance.cohort-baseline", "1.0.0")
        ),
        submitted_by="user:7",
    )
    baseline_ref = {
        "evaluation_id": baseline.manifest.evaluation_id,
        "entry_sha256": baseline.entry_sha256,
    }

    smaller_cohort = _manifest_payload(
        "eval.stance.smaller-cohort",
        "2.0.0",
        baseline=baseline_ref,
    )
    smaller_cohort["overall"] = _slice(32, 8, 32, 8)
    smaller_half = _slice(16, 4, 16, 4)
    smaller_cohort["strata"] = [
        {"dimension": item["dimension"], "value": item["value"], **copy.deepcopy(smaller_half)}
        for item in smaller_cohort["strata"]
    ]
    smaller_cohort["thresholds"]["minimum_overall_samples"] = 80
    smaller_cohort["thresholds"]["minimum_samples_per_stratum"] = 40
    with pytest.raises(ManifestRejected, match="cohort"):
        service.submit(
            EvaluationManifest.model_validate(smaller_cohort),
            submitted_by="user:7",
        )

    changed_coverage = _manifest_payload(
        "eval.stance.changed-coverage",
        "2.0.0",
        baseline=baseline_ref,
    )
    changed_coverage["coverage"]["countries"] = ["CHN", "GBR"]
    country_rows = [
        item for item in changed_coverage["strata"] if item["dimension"] == "country"
    ]
    country_rows[1]["value"] = "GBR"
    with pytest.raises(ManifestRejected, match="coverage"):
        service.submit(
            EvaluationManifest.model_validate(changed_coverage),
            submitted_by="user:7",
        )


def test_silver_labels_remain_computable_but_cannot_cross_the_gold_gate(
    tmp_path: Path,
) -> None:
    payload = _manifest_payload("eval.stance.silver", "1.0.0")
    payload["dataset"].update(
        {
            "evaluation_role": "evaluation_set",
            "gold_standard_status": "not_observed",
        }
    )
    payload["evaluation_integrity"] = {
        "label_source": "silver",
        "partition_role": "holdout",
        "holdout_access_status": "sealed",
        "development_dataset_sha256s": ["c" * 64],
        "separation_evidence_ref": "governance:holdout-separation:silver",
        "separation_evidence_sha256": "f" * 64,
    }
    payload["independent_review"]["valid_until"] = REVIEW_VALID_UNTIL.isoformat()

    entry = _service(tmp_path / "assurance").submit(
        EvaluationManifest.model_validate(payload),
        submitted_by="user:7",
    )

    assert entry.result.overall.f1 == 0.8
    assert entry.result.evidence_status == "manifest_only"
    assert entry.result.release_eligible is False
    assert "LABEL_SOURCE_NOT_HUMAN_GOLD" in entry.result.reason_codes


def test_holdout_overlap_and_unsealed_access_are_fail_closed(
    tmp_path: Path,
) -> None:
    overlap = _manifest_payload("eval.stance.holdout-overlap", "1.0.0")
    overlap["evaluation_integrity"] = {
        "label_source": "human_gold",
        "partition_role": "holdout",
        "holdout_access_status": "sealed",
        "development_dataset_sha256s": [overlap["dataset"]["sha256"]],
        "separation_evidence_ref": "governance:holdout-separation:overlap",
        "separation_evidence_sha256": "f" * 64,
    }
    overlap["independent_review"]["valid_until"] = REVIEW_VALID_UNTIL.isoformat()
    with pytest.raises(ValidationError, match="overlaps development"):
        EvaluationManifest.model_validate(overlap)

    unsealed = _manifest_payload("eval.stance.holdout-unsealed", "1.0.0")
    unsealed["evaluation_integrity"] = {
        "label_source": "human_gold",
        "partition_role": "holdout",
        "holdout_access_status": "development_accessed",
        "development_dataset_sha256s": ["c" * 64],
        "separation_evidence_ref": "governance:holdout-separation:unsealed",
        "separation_evidence_sha256": "f" * 64,
    }
    unsealed["independent_review"]["valid_until"] = REVIEW_VALID_UNTIL.isoformat()
    entry = _service(tmp_path / "unsealed-assurance").submit(
        EvaluationManifest.model_validate(unsealed),
        submitted_by="user:7",
    )
    assert entry.result.release_eligible is False
    assert "HOLDOUT_ISOLATION_NOT_ATTESTED" in entry.result.reason_codes


def test_review_validity_is_required_and_expiration_blocks_current_status(
    tmp_path: Path,
) -> None:
    clock = [NOW]
    service = ModelAssuranceService(
        ModelAssuranceStore(tmp_path / "assurance"),
        now=lambda: clock[0],
    )
    baseline_payload = _manifest_payload("eval.stance.expiring-baseline", "1.0.0")
    baseline_payload["evaluation_integrity"] = {
        "label_source": "human_gold",
        "partition_role": "holdout",
        "holdout_access_status": "sealed",
        "development_dataset_sha256s": ["c" * 64],
        "separation_evidence_ref": "governance:holdout-separation:baseline",
        "separation_evidence_sha256": "f" * 64,
    }
    baseline_payload["independent_review"]["valid_until"] = (
        NOW + timedelta(hours=1)
    ).isoformat()
    baseline = service.submit(
        EvaluationManifest.model_validate(baseline_payload),
        submitted_by="user:7",
    )

    candidate_payload = _manifest_payload(
        "eval.stance.expiring-candidate",
        "2.0.0",
        baseline={
            "evaluation_id": baseline.manifest.evaluation_id,
            "entry_sha256": baseline.entry_sha256,
        },
    )
    candidate_payload["evaluation_integrity"] = {
        "label_source": "human_gold",
        "partition_role": "holdout",
        "holdout_access_status": "sealed",
        "development_dataset_sha256s": ["c" * 64],
        "separation_evidence_ref": "governance:holdout-separation:candidate",
        "separation_evidence_sha256": "f" * 64,
    }
    candidate_payload["independent_review"]["valid_until"] = (
        NOW + timedelta(hours=1)
    ).isoformat()
    candidate = service.submit(
        EvaluationManifest.model_validate(candidate_payload),
        submitted_by="user:7",
    )
    assert candidate.result.release_eligible is True

    clock[0] = NOW + timedelta(hours=2)
    assert service.latest_release_eligible_evaluation(
        model_id="stance.classifier",
        model_version="2.0.0",
        method_version="stance-method-v3",
    ) is None
    status = service.status()
    assert status.release_status == "blocked"
    assert status.latest is not None
    assert status.latest.release_eligible is False
    assert "INDEPENDENT_REVIEW_EXPIRED" in status.reason_codes
    summaries = service.list_evaluations()
    assert summaries[0].release_eligible is False
    assert "INDEPENDENT_REVIEW_EXPIRED" in summaries[0].reason_codes


def test_expired_baseline_review_invalidates_candidates_and_descendants(
    tmp_path: Path,
) -> None:
    clock = [NOW]
    service = ModelAssuranceService(
        ModelAssuranceStore(tmp_path / "assurance"),
        now=lambda: clock[0],
    )
    baseline_payload = _manifest_payload(
        "eval.stance.short-lived-baseline",
        "1.0.0",
    )
    baseline_payload["independent_review"]["valid_until"] = (
        NOW + timedelta(hours=1)
    ).isoformat()
    baseline = service.submit(
        EvaluationManifest.model_validate(baseline_payload),
        submitted_by="user:7",
    )

    candidate_payload = _manifest_payload(
        "eval.stance.long-lived-candidate",
        "2.0.0",
        baseline={
            "evaluation_id": baseline.manifest.evaluation_id,
            "entry_sha256": baseline.entry_sha256,
        },
    )
    candidate_payload["independent_review"]["valid_until"] = (
        NOW + timedelta(hours=3)
    ).isoformat()
    candidate = service.submit(
        EvaluationManifest.model_validate(candidate_payload),
        submitted_by="user:7",
    )
    descendant_payload = _manifest_payload(
        "eval.stance.long-lived-descendant",
        "3.0.0",
        baseline={
            "evaluation_id": candidate.manifest.evaluation_id,
            "entry_sha256": candidate.entry_sha256,
        },
    )
    descendant_payload["independent_review"]["valid_until"] = (
        NOW + timedelta(hours=4)
    ).isoformat()
    descendant = service.submit(
        EvaluationManifest.model_validate(descendant_payload),
        submitted_by="user:7",
    )
    assert candidate.result.release_eligible is True
    assert descendant.result.release_eligible is True

    clock[0] = NOW + timedelta(hours=2)
    summaries = {
        item.evaluation_id: item for item in service.list_evaluations()
    }
    for evaluation_id in (
        candidate.manifest.evaluation_id,
        descendant.manifest.evaluation_id,
    ):
        summary = summaries[evaluation_id]
        assert summary.release_eligible is False
        assert summary.gate_state == "blocked"
        assert summary.rollback_action == "hold_release"
        assert "BASELINE_REVIEW_EXPIRED" in summary.reason_codes
        assert "BASELINE_ASSURANCE_INCOMPLETE" in summary.reason_codes

    assert service.latest_release_eligible_evaluation(
        model_id="stance.classifier",
        model_version="2.0.0",
        method_version="stance-method-v3",
    ) is None
    assert service.latest_release_eligible_evaluation(
        model_id="stance.classifier",
        model_version="3.0.0",
        method_version="stance-method-v3",
    ) is None
    status = service.status()
    assert status.release_status == "blocked"
    assert status.latest is not None
    assert status.latest.evaluation_id == descendant.manifest.evaluation_id
    assert "BASELINE_REVIEW_EXPIRED" in status.reason_codes


def test_expired_ancestor_review_blocks_a_new_descendant_at_submission(
    tmp_path: Path,
) -> None:
    clock = [NOW]
    service = ModelAssuranceService(
        ModelAssuranceStore(tmp_path / "assurance"),
        now=lambda: clock[0],
    )
    root_payload = _manifest_payload("eval.stance.root-baseline", "1.0.0")
    root_payload["independent_review"]["valid_until"] = (
        NOW + timedelta(hours=1)
    ).isoformat()
    root = service.submit(
        EvaluationManifest.model_validate(root_payload),
        submitted_by="user:7",
    )
    middle_payload = _manifest_payload(
        "eval.stance.middle-baseline",
        "2.0.0",
        baseline={
            "evaluation_id": root.manifest.evaluation_id,
            "entry_sha256": root.entry_sha256,
        },
    )
    middle_payload["independent_review"]["valid_until"] = (
        NOW + timedelta(hours=4)
    ).isoformat()
    middle = service.submit(
        EvaluationManifest.model_validate(middle_payload),
        submitted_by="user:7",
    )
    assert middle.result.release_eligible is True

    clock[0] = NOW + timedelta(hours=2)
    descendant_payload = _manifest_payload(
        "eval.stance.descendant-after-expiry",
        "3.0.0",
        baseline={
            "evaluation_id": middle.manifest.evaluation_id,
            "entry_sha256": middle.entry_sha256,
        },
    )
    descendant_payload["independent_review"]["valid_until"] = (
        NOW + timedelta(hours=5)
    ).isoformat()
    descendant = service.submit(
        EvaluationManifest.model_validate(descendant_payload),
        submitted_by="user:7",
    )

    assert descendant.result.release_eligible is False
    assert descendant.result.rollback.action == "hold_release"
    assert "BASELINE_REVIEW_EXPIRED" in descendant.result.reason_codes
    assert "BASELINE_ASSURANCE_INCOMPLETE" in descendant.result.reason_codes


def test_candidate_timestamp_cannot_precede_its_baseline(
    tmp_path: Path,
) -> None:
    clock = [NOW]
    service = ModelAssuranceService(
        ModelAssuranceStore(tmp_path / "assurance"),
        now=lambda: clock[0],
    )
    baseline = service.submit(
        EvaluationManifest.model_validate(
            _manifest_payload("eval.stance.clock-baseline", "1.0.0")
        ),
        submitted_by="user:7",
    )
    clock[0] = NOW - timedelta(minutes=1)
    candidate = _manifest_payload(
        "eval.stance.clock-candidate",
        "2.0.0",
        baseline={
            "evaluation_id": baseline.manifest.evaluation_id,
            "entry_sha256": baseline.entry_sha256,
        },
    )

    with pytest.raises(ManifestRejected, match="clock"):
        service.submit(
            EvaluationManifest.model_validate(candidate),
            submitted_by="user:7",
        )
    with pytest.raises(ManifestRejected, match="clock"):
        service.submit(
            EvaluationManifest.model_validate(
                _manifest_payload("eval.stance.clock-standalone", "2.0.0")
            ),
            submitted_by="user:7",
        )
    with pytest.raises(AssuranceStoreUnavailable, match="clock"):
        service.status()


def test_missing_integrity_and_review_expiry_declarations_stay_blocked(
    tmp_path: Path,
) -> None:
    payload = _manifest_payload("eval.stance.missing-integrity", "1.0.0")
    payload.pop("evaluation_integrity")
    payload["independent_review"].pop("valid_until")

    entry = _service(tmp_path / "assurance").submit(
        EvaluationManifest.model_validate(payload),
        submitted_by="user:7",
    )

    assert entry.result.release_eligible is False
    assert "EVALUATION_INTEGRITY_NOT_PROVIDED" in entry.result.reason_codes
    assert "REVIEW_EXPIRY_NOT_DECLARED" in entry.result.reason_codes


def test_default_status_is_blocked_not_observed_and_get_does_not_create_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "not-created"
    service = _service(root)

    status = service.status()
    catalog = service.catalog()

    assert root.exists() is False
    assert status.available is False
    assert status.operational_state == "not_observed"
    assert status.release_status == "blocked"
    assert status.gold_standard_state == "not_observed"
    assert status.evaluation_count == 0
    assert "NO_EVALUATION_MANIFESTS" in status.reason_codes
    assert catalog.persistence == "append_only_hash_chain"
    assert catalog.required_strata == ["country", "language", "topic"]


def test_store_rejects_release_roots_and_symbolic_links(tmp_path: Path) -> None:
    with pytest.raises(AssuranceStoreUnavailable):
        ModelAssuranceStore(Path("/root/data/releases/globemind/current/model"))

    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(AssuranceStoreUnavailable):
        ModelAssuranceStore(link)


def test_routes_require_user_for_get_admin_for_post_and_return_derived_result(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "assurance")
    app = FastAPI()
    app.include_router(model_assurance.router)
    app.dependency_overrides[model_assurance.get_model_assurance_service] = (
        lambda: service
    )

    with TestClient(app) as anonymous:
        denied = anonymous.get("/api/model-assurance/status")
    assert denied.status_code == 401

    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 9,
        "role": "user",
    }
    with TestClient(app) as user_client:
        status = user_client.get("/api/model-assurance/status")
        forbidden = user_client.post(
            "/api/model-assurance/evaluations",
            json=_manifest_payload("eval.stance.route", "1.0.0"),
        )
    assert status.status_code == 200
    assert status.json()["operational_state"] == "not_observed"
    assert forbidden.status_code == 403

    app.dependency_overrides[get_current_admin_user] = lambda: {
        "id": 9,
        "role": "admin",
    }
    with TestClient(app) as alias_client:
        rejected_alias = alias_client.post(
            "/api/model-assurance/evaluations",
            json=_manifest_payload("eval.stance.route", "1.0.0"),
        )
    assert rejected_alias.status_code == 403

    app.dependency_overrides[get_current_admin_user] = lambda: {
        "user_id": 9,
        "username": "assurance-admin",
        "role": "admin",
    }
    with TestClient(app) as admin_client:
        created = admin_client.post(
            "/api/model-assurance/evaluations",
            json=_manifest_payload("eval.stance.route", "1.0.0"),
        )
        detail = admin_client.get(
            "/api/model-assurance/evaluations/eval.stance.route"
        )
        listing = admin_client.get("/api/model-assurance/evaluations")
    assert created.status_code == 201
    assert created.json()["result"]["overall"]["f1"] == 0.8
    assert created.json()["result"]["release_eligible"] is False
    assert detail.status_code == 200
    assert detail.json()["entry_sha256"] == created.json()["entry_sha256"]
    assert listing.status_code == 200
    assert listing.json()[0]["evaluation_id"] == "eval.stance.route"


def test_route_rejects_ambiguous_duplicate_json_keys(tmp_path: Path) -> None:
    root = tmp_path / "assurance"
    service = _service(root)
    app = FastAPI()
    app.include_router(model_assurance.router)
    app.dependency_overrides[model_assurance.get_model_assurance_service] = (
        lambda: service
    )
    app.dependency_overrides[get_current_admin_user] = lambda: {
        "user_id": 9,
        "role": "admin",
    }
    source = json.dumps(_manifest_payload("eval.stance.duplicate", "1.0.0"))
    source = source.replace(
        '"evaluation_id": "eval.stance.duplicate"',
        '"evaluation_id": "eval.stance.shadow", '
        '"evaluation_id": "eval.stance.duplicate"',
        1,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/model-assurance/evaluations",
            content=source,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 422
    assert root.exists() is False


def test_route_rejects_excessively_nested_json_without_internal_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "assurance"
    service = _service(root)
    app = FastAPI()
    app.include_router(model_assurance.router)
    app.dependency_overrides[model_assurance.get_model_assurance_service] = (
        lambda: service
    )
    app.dependency_overrides[get_current_admin_user] = lambda: {
        "user_id": 9,
        "role": "admin",
    }
    source = '{"nested":' + "[" * 1100 + "0" + "]" * 1100 + "}"

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/model-assurance/evaluations",
            content=source,
            headers={"content-type": "application/json"},
        )

    assert response.status_code in {400, 422}
    assert root.exists() is False
