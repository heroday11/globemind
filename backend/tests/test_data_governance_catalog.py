from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.features import FeatureHealthCheck
from api.features.data_governance import (
    CatalogRecordDraft,
    build_data_catalog,
    evaluate_catalog_record,
    freshness_from_health,
    operational_from_health,
)
from api.features.model_assurance import (
    EvaluationManifest,
    ModelAssuranceService,
    ModelAssuranceStore,
)
from api.features.opinion import METHOD_VERSION, OPINION_MODEL_VERSION
from api.routes import data_governance


NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 8, 1, tzinfo=timezone.utc)
REVIEWED = datetime(2026, 8, 8, tzinfo=timezone.utc)
REVIEW_VALID_UNTIL = datetime(2099, 9, 1, tzinfo=timezone.utc)


def _assurance_slice() -> dict[str, object]:
    return {
        "confusion": {
            "true_positive": 40,
            "false_positive": 10,
            "true_negative": 40,
            "false_negative": 10,
        },
        "calibration_bins": [
            {
                "lower_bound": 0,
                "upper_bound": 0.5,
                "sample_count": 50,
                "positive_count": 10,
                "predicted_probability_sum": 10,
                "positive_probability_sum": 2,
                "squared_probability_sum": 2,
            },
            {
                "lower_bound": 0.5,
                "upper_bound": 1,
                "sample_count": 50,
                "positive_count": 40,
                "predicted_probability_sum": 40,
                "positive_probability_sum": 32,
                "squared_probability_sum": 32,
            },
        ],
    }


def _assurance_manifest(
    evaluation_id: str,
    *,
    model_id: str = "model.china_opinion_stance",
    model_version: str = OPINION_MODEL_VERSION,
    method_version: str = METHOD_VERSION,
    baseline: dict[str, str] | None = None,
) -> EvaluationManifest:
    slice_evidence = _assurance_slice()
    payload = {
        "evaluation_id": evaluation_id,
        "evaluation_version": "catalog-assurance-fixture-v1",
        "dataset": {
            "dataset_id": "private.gold.stance.v999",
            "dataset_version": "private-2026-08-01",
            "sha256": "d" * 64,
            "cutoff_at": CUTOFF.isoformat(),
            "evaluation_role": "gold_standard",
            "gold_standard_status": "independently_reviewed",
            "label_schema_version": "private-labels-v999",
            "annotation_protocol_ref": "private:annotation-protocol:v999",
            "provenance_ref": "private:gold-provenance:v999",
        },
        "model": {
            "model_id": model_id,
            "model_version": model_version,
            "method_version": method_version,
            "owner_organization": "Private Model Fixture Team",
            "task_type": "binary_classification",
            "positive_label": "private-positive-label",
        },
        "classification_threshold": 0.5,
        "overall": copy.deepcopy(slice_evidence),
        "strata": [
            {
                "dimension": "country",
                "value": "QZ",
                **copy.deepcopy(slice_evidence),
            },
            {
                "dimension": "language",
                "value": "zz",
                **copy.deepcopy(slice_evidence),
            },
            {
                "dimension": "topic",
                "value": "private-topic",
                **copy.deepcopy(slice_evidence),
            },
        ],
        "coverage": {
            "countries": ["QZ"],
            "languages": ["zz"],
            "topics": ["private-topic"],
        },
        "thresholds": {
            "minimum_precision": 0.75,
            "minimum_recall": 0.75,
            "minimum_f1": 0.75,
            "maximum_brier_score": 0.2,
            "maximum_ece": 0.05,
            "minimum_stratum_f1": 0.75,
            "minimum_overall_samples": 100,
            "minimum_samples_per_stratum": 100,
            "maximum_f1_drop_from_baseline": 0.05,
            "maximum_brier_increase_from_baseline": 0.05,
            "maximum_ece_increase_from_baseline": 0.05,
        },
        "independent_review": {
            "review_id": f"private-review:{evaluation_id}",
            "reviewer_id": "private-reviewer:999",
            "reviewer_organization": "Private Independent Fixture Lab",
            "independence_attestation": True,
            "decision": "approved",
            "reviewed_at": REVIEWED.isoformat(),
            "valid_until": REVIEW_VALID_UNTIL.isoformat(),
            "evidence_ref": f"private-review-evidence:{evaluation_id}",
            "evidence_sha256": "e" * 64,
        },
        "evaluation_integrity": {
            "label_source": "human_gold",
            "partition_role": "holdout",
            "holdout_access_status": "sealed",
            "development_dataset_sha256s": ["c" * 64],
            "separation_evidence_ref": (
                f"private-separation-evidence:{evaluation_id}"
            ),
            "separation_evidence_sha256": "f" * 64,
        },
        "baseline": baseline,
    }
    return EvaluationManifest.model_validate(payload)


def _append_release_eligible_assurance(
    root: Path,
    *,
    model_id: str = "model.china_opinion_stance",
    model_version: str = OPINION_MODEL_VERSION,
    method_version: str = METHOD_VERSION,
) -> None:
    service = ModelAssuranceService(ModelAssuranceStore(root), now=lambda: NOW)
    baseline = service.submit(
        _assurance_manifest(
            "eval.catalog.private-baseline",
            model_id=model_id,
            model_version=model_version,
            method_version=method_version,
        ),
        submitted_by="user:999",
    )
    candidate = service.submit(
        _assurance_manifest(
            "eval.catalog.private-candidate",
            model_id=model_id,
            model_version=model_version,
            method_version=method_version,
            baseline={
                "evaluation_id": baseline.manifest.evaluation_id,
                "entry_sha256": baseline.entry_sha256,
            },
        ),
        submitted_by="user:999",
    )
    assert candidate.result.release_eligible is True


def _fresh_check(feature_id: str) -> FeatureHealthCheck:
    return FeatureHealthCheck(
        feature_id=feature_id,
        status="up",
        latency_ms=1,
        dependencies=[f"test:{feature_id}"],
        metrics={
            "freshness_status": "current",
            "latest_data_at": "2026-08-09T10:00:00+00:00",
            "freshness_lag_hours": 2.0,
            "freshness_sla_hours": 48,
        },
    )


def _complete_draft() -> CatalogRecordDraft:
    evidence = [
        {
            "reference": "docs/governance/evidence-v1.json",
            "claim": "Test fixture represents verified registry evidence.",
            "status": "verified",
        }
    ]
    return CatalogRecordDraft(
        record_id="dataset.complete_fixture",
        kind="dataset",
        title="Complete fixture",
        description="A complete synthetic registration used only to prove the gate.",
        owner={
            "owner_id": "named-owner",
            "display_name": "Named owner",
            "assignment_status": "named",
            "evidence": evidence,
        },
        version={
            "value": "2026.08.09.1",
            "status": "verified",
            "scheme": "immutable-snapshot",
            "effective_at": NOW,
            "change_log_ref": "docs/governance/change-log.md",
            "evidence": evidence,
        },
        operational={
            "state": "available",
            "evidence_status": "verified",
            "observed_at": NOW,
            "source": "verified-capability-probe",
            "reason_codes": [],
        },
        freshness={
            "state": "live",
            "evidence_status": "verified",
            "cutoff_at": NOW,
            "last_success_at": NOW,
            "observed_at": NOW,
            "lag_hours": 0,
            "sla_hours": 48,
            "source": "verified-watermark",
            "reason_codes": [],
        },
        coverage={
            "status": "verified",
            "scope": "All declared countries, languages, and dates.",
            "metrics": {
                "record_count": 20,
                "completeness_ratio": 1.0,
                "missing_ratio": 0.0,
                "duplicate_ratio": 0.0,
            },
            "missing_dimensions": [],
            "evidence": evidence,
        },
        license={
            "status": "verified",
            "identifier": "fixture-license-v1",
            "usage_scope": "Internal test fixture only.",
            "terms_ref": "docs/governance/license.md",
            "retention_policy": "Retained with the immutable fixture version.",
            "evidence": evidence,
        },
        quality={
            "status": "passed",
            "evaluated_at": NOW,
            "evaluation_version": "fixture-eval-v1",
            "metrics": {"acceptance_rate": 1.0},
            "known_issues": [],
            "evidence": evidence,
        },
        provenance={
            "status": "verified",
            "capture_timestamp_status": "verified",
            "web_snapshot_status": "verified",
            "content_hash_status": "verified",
            "parser_version": "fixture-parser-v1",
            "revision_tracking_status": "verified",
            "evidence": evidence,
        },
        schema={
            "status": "verified",
            "record_identifier": "fixture.id",
            "schema_ref": "docs/governance/schema.json",
            "data_dictionary_ref": "docs/governance/dictionary.md",
            "mapping_refs": ["docs/governance/mappings.json"],
            "change_log_ref": "docs/governance/schema-change-log.md",
            "evidence": evidence,
        },
        evidence=evidence,
    )


def test_default_catalog_reuses_evidence_but_blocks_every_incomplete_record() -> None:
    checks = {
        feature_id: _fresh_check(feature_id)
        for feature_id in ("ground-news", "opinion-analysis", "story-graph")
    }

    catalog = build_data_catalog(health_checks=checks, generated_at=NOW)

    assert catalog.available is True
    assert catalog.schema_version == "data-governance-catalog-v1"
    assert catalog.summary.record_count == 9
    assert catalog.summary.dataset_count == 3
    assert catalog.summary.source_count == 5
    assert catalog.summary.model_count == 1
    assert catalog.summary.eligible_count == 0
    assert catalog.summary.formal_release_status == "blocked"
    assert all(record.status.state == "blocked" for record in catalog.records)
    assert all(record.owner.assignment_status == "role_only" for record in catalog.records)
    assert {record.license.status for record in catalog.records} == {
        "unknown",
        "restricted",
    }
    news = next(
        record for record in catalog.records if record.record_id == "dataset.news_articles"
    )
    assert news.operational.state == "available"
    assert news.freshness.state == "live"
    assert news.status.research_ready is False
    opinion_model = next(
        record for record in catalog.records if record.record_id == "model.china_opinion_stance"
    )
    assert opinion_model.version.value == METHOD_VERSION
    source_collection = next(
        record for record in catalog.records if record.kind == "source"
    )
    assert source_collection.version.value.startswith("sha256:")
    assert source_collection.coverage.status == "partial"
    assert "language_coverage" in source_collection.coverage.missing_dimensions
    authority_sources = {
        record.record_id: record for record in catalog.records
        if record.record_id.startswith("source.")
        and record.record_id != "source.news_ingestion_network"
    }
    assert set(authority_sources) == {
        "source.world_bank",
        "source.imf",
        "source.un_sdg",
        "source.crossref",
    }
    assert all(record.operational.state == "unknown" for record in authority_sources.values())
    assert all("LIVE_STATUS_NOT_OBSERVED" in record.status.reason_codes for record in authority_sources.values())
    serialized = json.dumps(catalog.model_dump(mode="json"))
    assert "/root/" not in serialized


def test_catalog_projects_only_aggregate_verified_exact_match_model_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assurance_root = tmp_path / "model-assurance"
    _append_release_eligible_assurance(assurance_root)
    monkeypatch.setenv("MODEL_ASSURANCE_ROOT", str(assurance_root))

    catalog = build_data_catalog(
        generated_at=NOW,
    )

    record = next(
        item
        for item in catalog.records
        if item.record_id == "model.china_opinion_stance"
    )
    assert record.quality.status == "passed"
    assert record.quality.evaluated_at == NOW
    assert record.quality.metrics == {
        "assurance_evidence_status": "manifest_only",
        "release_eligible": True,
        "overall_sample_count": 100,
        "overall_positive_count": 50,
        "overall_predicted_positive_count": 50,
        "overall_brier_score": 0.16,
        "overall_expected_calibration_error": 0.0,
        "strata_count": 3,
        "country_strata_count": 1,
        "language_strata_count": 1,
        "topic_strata_count": 1,
        "coverage_state": "complete",
        "coverage_minimum_samples_satisfied": True,
        "calibration_bin_count": 2,
        "drift_state": "within_threshold",
        "rollback_action": "proceed",
        "metric_method_version": "binary-assurance-metrics-1.0.0",
        "overall_precision": 0.8,
        "overall_recall": 0.8,
        "overall_f1": 0.8,
        "coverage_expected_country_count": 1,
        "coverage_observed_country_count": 1,
        "coverage_missing_country_count": 0,
        "coverage_unexpected_country_count": 0,
        "coverage_expected_language_count": 1,
        "coverage_observed_language_count": 1,
        "coverage_missing_language_count": 0,
        "coverage_unexpected_language_count": 0,
        "coverage_expected_topic_count": 1,
        "coverage_observed_topic_count": 1,
        "coverage_missing_topic_count": 0,
        "coverage_unexpected_topic_count": 0,
        "minimum_stratum_precision": 0.8,
        "minimum_stratum_recall": 0.8,
        "minimum_stratum_f1": 0.8,
        "maximum_stratum_brier_score": 0.16,
        "maximum_stratum_expected_calibration_error": 0.0,
        "drift_f1_delta": 0.0,
        "drift_brier_delta": 0.0,
        "drift_ece_delta": 0.0,
    }
    assert record.status.state == "blocked"
    assert record.status.release_eligible is False

    public_quality = json.dumps(record.quality.model_dump(mode="json"))
    for private_value in (
        "user:999",
        "private.gold.stance.v999",
        "d" * 64,
        "private-reviewer:999",
        "Private Independent Fixture Lab",
        "eval.catalog.private-baseline",
        "eval.catalog.private-candidate",
        "QZ",
        "zz",
        "private-topic",
        str(assurance_root),
    ):
        assert private_value not in public_quality


@pytest.mark.parametrize(
    ("identity_field", "mismatched_value"),
    [
        ("model_id", "model.other_opinion_stance"),
        ("model_version", "opinion-model-version-mismatch"),
        ("method_version", "opinion-method-version-mismatch"),
    ],
)
def test_catalog_rejects_release_eligible_model_identity_mismatches(
    tmp_path: Path,
    identity_field: str,
    mismatched_value: str,
) -> None:
    assurance_root = tmp_path / "model-assurance"
    identity = {
        "model_id": "model.china_opinion_stance",
        "model_version": OPINION_MODEL_VERSION,
        "method_version": METHOD_VERSION,
    }
    identity[identity_field] = mismatched_value
    _append_release_eligible_assurance(assurance_root, **identity)

    catalog = build_data_catalog(
        generated_at=NOW,
        model_assurance_root=assurance_root,
    )

    record = next(
        item
        for item in catalog.records
        if item.record_id == "model.china_opinion_stance"
    )
    assert record.quality.status == "unknown"
    assert record.quality.metrics == {}
    assert record.quality.evidence == []
    assert record.status.state == "blocked"


def test_catalog_rejects_noneligible_and_corrupt_assurance_ledgers(
    tmp_path: Path,
) -> None:
    noneligible_root = tmp_path / "noneligible-model-assurance"
    ModelAssuranceService(
        ModelAssuranceStore(noneligible_root),
        now=lambda: NOW,
    ).submit(
        _assurance_manifest("eval.catalog.noneligible"),
        submitted_by="user:999",
    )
    noneligible = build_data_catalog(
        generated_at=NOW,
        model_assurance_root=noneligible_root,
    )
    noneligible_record = next(
        item
        for item in noneligible.records
        if item.record_id == "model.china_opinion_stance"
    )
    assert noneligible_record.quality.status == "unknown"
    assert noneligible_record.status.state == "blocked"

    corrupt_root = tmp_path / "corrupt-model-assurance"
    _append_release_eligible_assurance(corrupt_root)
    candidate_path = (
        corrupt_root
        / "entries"
        / "00000002-eval.catalog.private-candidate.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["entry_sha256"] = "0" * 64
    candidate_path.write_text(
        json.dumps(candidate, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    corrupt = build_data_catalog(
        generated_at=NOW,
        model_assurance_root=corrupt_root,
    )
    corrupt_record = next(
        item
        for item in corrupt.records
        if item.record_id == "model.china_opinion_stance"
    )
    assert corrupt.available is True
    assert corrupt_record.quality.status == "unknown"
    assert corrupt_record.quality.metrics == {}
    assert corrupt_record.status.state == "blocked"
    assert str(corrupt_root) not in json.dumps(
        corrupt_record.model_dump(mode="json")
    )


def test_missing_registry_files_remain_visible_as_unknown_and_never_eligible(
    tmp_path: Path,
) -> None:
    catalog = build_data_catalog(
        generated_at=NOW,
        owner_registry_path=tmp_path / "missing-owners.json",
        source_catalog_path=tmp_path / "missing-sources.csv",
    )

    assert catalog.registry_sources.owner_registry == "unavailable"
    assert catalog.registry_sources.source_catalog == "unavailable"
    assert "OWNER_REGISTRY_UNAVAILABLE" in catalog.reason_codes
    assert "SOURCE_CATALOG_UNAVAILABLE" in catalog.reason_codes
    assert all(record.owner.assignment_status == "unknown" for record in catalog.records)
    assert all(record.status.release_eligible is False for record in catalog.records)


def test_registration_gate_recomputes_status_and_rejects_declared_but_empty_evidence() -> None:
    complete = _complete_draft()
    eligible = evaluate_catalog_record(complete, evaluated_at=NOW)
    assert eligible.status.state == "eligible"
    assert eligible.status.reason_codes == []

    damaged = complete.model_copy(
        update={
            "coverage": complete.coverage.model_copy(
                update={"metrics": {}, "evidence": []}
            )
        }
    )
    blocked = evaluate_catalog_record(damaged, evaluated_at=NOW)
    assert blocked.status.release_eligible is False
    assert "COVERAGE_EVIDENCE_INCOMPLETE" in blocked.status.reason_codes


@pytest.mark.parametrize(
    ("check", "state", "evidence_status"),
    [
        (_fresh_check("ground-news"), "live", "verified"),
        (
            FeatureHealthCheck(
                feature_id="ground-news",
                status="degraded",
                latency_ms=1,
                dependencies=["test"],
                metrics={"latest_data_at": "2026-08-09T08:00:00+00:00"},
            ),
            "delayed",
            "verified",
        ),
        (
            FeatureHealthCheck(
                feature_id="ground-news",
                status="stale",
                latency_ms=1,
                dependencies=["test"],
                metrics={
                    "freshness_status": "stale",
                    "latest_data_at": "2026-08-01T08:00:00+00:00",
                },
            ),
            "stale",
            "verified",
        ),
        (
            FeatureHealthCheck(
                feature_id="ground-news",
                status="down",
                latency_ms=1,
                dependencies=["test"],
            ),
            "offline",
            "unknown",
        ),
        (None, "offline", "unknown"),
    ],
)
def test_health_mapping_uses_one_fail_closed_freshness_state_model(
    check: FeatureHealthCheck | None,
    state: str,
    evidence_status: str,
) -> None:
    freshness = freshness_from_health(check, observed_at=NOW)
    assert freshness.state == state
    assert freshness.evidence_status == evidence_status
    assert freshness.last_success_at is None


def test_operational_status_is_independent_from_stale_business_data() -> None:
    stale = FeatureHealthCheck(
        feature_id="ground-news",
        status="stale",
        latency_ms=1,
        dependencies=["test"],
        metrics={
            "freshness_status": "stale",
            "latest_data_at": "2026-08-01T08:00:00+00:00",
        },
    )

    operational = operational_from_health(stale, observed_at=NOW)
    freshness = freshness_from_health(stale, observed_at=NOW)

    assert operational.state == "available"
    assert freshness.state == "stale"


def test_current_business_data_remains_live_when_technical_probe_is_degraded() -> None:
    degraded = FeatureHealthCheck(
        feature_id="ground-news",
        status="degraded",
        latency_ms=1,
        dependencies=["test"],
        metrics={
            "freshness_status": "current",
            "latest_data_at": "2026-08-09T10:00:00+00:00",
        },
    )

    operational = operational_from_health(degraded, observed_at=NOW)
    freshness = freshness_from_health(degraded, observed_at=NOW)

    assert operational.state == "degraded"
    assert freshness.state == "live"


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    checks = {
        feature_id: _fresh_check(feature_id)
        for feature_id in ("ground-news", "opinion-analysis", "story-graph")
    }
    monkeypatch.setattr(data_governance, "collect_catalog_health", lambda _db: checks)
    app = FastAPI()
    app.include_router(data_governance.router)

    def override_db():
        yield object()

    app.dependency_overrides[data_governance.get_db] = override_db
    return TestClient(app)


def test_catalog_http_contract_filters_kinds_and_returns_single_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch) as client:
        response = client.get("/api/data-governance/catalog?kind=model")
        detail = client.get(
            "/api/data-governance/catalog/model.china_opinion_stance"
        )
        missing = client.get("/api/data-governance/catalog/model.unknown")

    assert response.status_code == 200
    assert response.json()["summary"]["record_count"] == 1
    assert response.json()["records"][0]["kind"] == "model"
    assert detail.status_code == 200
    assert detail.json()["version"]["value"] == METHOD_VERSION
    assert "schema" in detail.json()
    assert "schema_registration" not in detail.json()
    assert missing.status_code == 404


def test_catalog_http_failure_is_redacted_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        data_governance,
        "_catalog_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("database password must not escape")
        ),
    )
    app = FastAPI()
    app.include_router(data_governance.router)
    with TestClient(app) as client:
        response = client.get("/api/data-governance/catalog")

    assert response.status_code == 503
    assert response.json()["available"] is False
    assert response.json()["summary"]["formal_release_status"] == "blocked"
    assert "password" not in response.text
