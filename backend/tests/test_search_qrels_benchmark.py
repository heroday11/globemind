from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api.features.search import (
    LoadedSearchQrelsBundle,
    SearchAdjudicationEvidence,
    SearchCorpusSnapshotEvidence,
    SearchEvalObservation,
    SearchEvalProvenance,
    SearchEvalQuery,
    SearchQrel,
    SearchQrelsDataset,
    SearchQrelsDatasetError,
    SearchQrelsSlicePlan,
    SearchRunObservationArtifact,
    SearchTranslatedIntentGroup,
    compare_search_qrels_slice_receipts,
    evaluate_search_qrels_benchmark,
    evaluate_search_qrels_slices,
    load_search_qrels_slice_plan,
    load_search_qrels_slice_receipt,
    load_search_run_observation_artifact,
)

NOW = datetime(2026, 8, 10, 6, 30, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _qrels_bundle(*, paired: bool = False) -> LoadedSearchQrelsBundle:
    def query(query_id: str, text: str, language: str) -> SearchEvalQuery:
        return SearchEvalQuery(
            query_id=query_id,
            query=text,
            language=language,
            country="CN",
            topic="shipping",
            intent="retrieve relevant source records",
            qrels=[
                SearchQrel(
                    document_id="news:42",
                    relevance_grade=3,
                    rationale="Adjudicated direct relevance.",
                )
            ],
            provenance=SearchEvalProvenance(
                dataset_id="real-search-human-gold",
                dataset_version="2026.08.10-v1",
                corpus_snapshot_id="corpus-20260810",
                source_kind="adjudicated_annotation",
                source_locator="evidence/adjudication.json",
                created_at=NOW - timedelta(hours=2),
                reviewer_evidence="sha256:" + SHA_C,
            ),
            judgment_tier="human_gold",
            review_state="adjudicated",
        )

    dataset = SearchQrelsDataset(
        dataset_id="real-search-human-gold",
        dataset_version="2026.08.10-v1",
        corpus=SearchCorpusSnapshotEvidence(
            corpus_snapshot_id="corpus-20260810",
            corpus_sha256=SHA_A,
            document_count=500,
            cutoff="2026-08-09T00:00:00Z",
            manifest_locator="evidence/corpus.json",
            manifest_sha256=SHA_B,
            document_id_namespace="news",
        ),
        adjudication=SearchAdjudicationEvidence(
            annotation_guide_id="guide",
            annotation_guide_version="1.0.0",
            annotation_guide_locator="evidence/guide.md",
            annotation_guide_sha256=SHA_B,
            reviewer_ids=("reviewer:alpha001", "reviewer:beta0002"),
            adjudication_artifact_locator="evidence/adjudication.json",
            adjudication_artifact_sha256=SHA_C,
            agreement_method="krippendorff_alpha",
            agreement_value=0.8,
        ),
        queries=(
            query("expert-001", "bounded expert query", "en"),
            *(
                (query("expert-001-zh", "有界专家查询", "zh-Hans"),)
                if paired
                else ()
            ),
        ),
    )
    return LoadedSearchQrelsBundle(
        dataset=dataset,
        dataset_sha256=SHA_A,
        corpus_manifest_sha256=SHA_B,
        annotation_guide_sha256=SHA_B,
        adjudication_artifact_sha256=SHA_C,
        verified_evidence_bytes=1024,
    )


def _run_artifact(
    *,
    query_id: str = "expert-001",
    paired: bool = False,
) -> SearchRunObservationArtifact:
    return SearchRunObservationArtifact(
        run_id="candidate-export-001",
        dataset_id="real-search-human-gold",
        dataset_version="2026.08.10-v1",
        corpus_snapshot_id="corpus-20260810",
        execution_source="isolated_candidate_export",
        engine_config_sha256=SHA_A,
        query_contract_sha256=SHA_B,
        started_at=NOW - timedelta(minutes=10),
        completed_at=NOW - timedelta(minutes=5),
        observations=(
            SearchEvalObservation(
                query_id=query_id,
                ordered_result_ids=["news:42", "news:7"],
                latency_ms=125.0,
            ),
            *(
                (
                    SearchEvalObservation(
                        query_id="expert-001-zh",
                        ordered_result_ids=["news:42", "news:8"],
                        latency_ms=130.0,
                    ),
                )
                if paired
                else ()
            ),
        ),
    )


def _write_run(path: Path, artifact: SearchRunObservationArtifact) -> str:
    raw = artifact.model_dump_json().encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _write_slice_plan(path: Path, *, query_ids: tuple[str, ...]) -> str:
    plan = SearchQrelsSlicePlan(
        plan_id="translated-intent-plan",
        plan_version="2026.08.10-v1",
        dataset_id="real-search-human-gold",
        dataset_version="2026.08.10-v1",
        corpus_snapshot_id="corpus-20260810",
        reviewer_ids=("reviewer:alpha001", "reviewer:beta0002"),
        reviewed_at=NOW - timedelta(hours=1),
        review_expires_at=NOW + timedelta(days=30),
        groups=(
            SearchTranslatedIntentGroup(
                group_id="shipping-cn-translations",
                query_ids=query_ids,
            ),
        ),
    )
    raw = plan.model_dump_json().encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _write_slice_receipt(path: Path, receipt: object) -> str:
    raw = receipt.model_dump_json().encode()  # type: ignore[attr-defined]
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_frozen_run_artifact_produces_hash_bound_non_approval_receipt(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.json"
    digest = _write_run(path, _run_artifact())
    run = load_search_run_observation_artifact(
        path,
        expected_sha256=digest,
        evaluated_at=NOW,
    )

    receipt = evaluate_search_qrels_benchmark(
        _qrels_bundle(),
        run,
        evaluated_at=NOW,
    )

    assert receipt.run_artifact_sha256 == digest
    assert receipt.evaluation.relevance_by_judgment_tier["human_gold"].recall_at_k[
        "100"
    ] == 1.0
    assert receipt.evaluation.operational.latency_p95_ms == 125.0
    assert receipt.threshold_approval_state == "not_approved"
    assert receipt.release_decision == "not_computable"
    assert receipt.quality_claim == "not_established"
    assert receipt.source_truth_review == "not_performed"
    assert receipt.result_bodies_retained is False


def test_benchmark_rejects_query_dataset_and_corpus_drift(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    digest = _write_run(path, _run_artifact(query_id="other-query"))
    run = load_search_run_observation_artifact(
        path,
        expected_sha256=digest,
        evaluated_at=NOW,
    )
    with pytest.raises(SearchQrelsDatasetError, match="exactly cover"):
        evaluate_search_qrels_benchmark(_qrels_bundle(), run, evaluated_at=NOW)

    payload = _run_artifact().model_copy(update={"corpus_snapshot_id": "other-corpus"})
    path = tmp_path / "other-run.json"
    digest = _write_run(path, payload)
    run = load_search_run_observation_artifact(
        path,
        expected_sha256=digest,
        evaluated_at=NOW,
    )
    with pytest.raises(SearchQrelsDatasetError, match="does not match"):
        evaluate_search_qrels_benchmark(_qrels_bundle(), run, evaluated_at=NOW)


def test_run_loader_rejects_future_hash_relative_and_hardlink_inputs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.json"
    digest = _write_run(
        path,
        _run_artifact().model_copy(update={"completed_at": NOW + timedelta(minutes=1)}),
    )
    with pytest.raises(SearchQrelsDatasetError, match="future"):
        load_search_run_observation_artifact(
            path,
            expected_sha256=digest,
            evaluated_at=NOW,
        )
    with pytest.raises(SearchQrelsDatasetError, match="absolute"):
        load_search_run_observation_artifact(
            Path("run.json"),
            expected_sha256=digest,
            evaluated_at=NOW,
        )
    with pytest.raises(SearchQrelsDatasetError, match="SHA-256 mismatch"):
        load_search_run_observation_artifact(
            path,
            expected_sha256="f" * 64,
            evaluated_at=NOW + timedelta(minutes=2),
        )
    hardlink = tmp_path / "run-hardlink.json"
    hardlink.hardlink_to(path)
    with pytest.raises(SearchQrelsDatasetError, match="single-link"):
        load_search_run_observation_artifact(
            path,
            expected_sha256=digest,
            evaluated_at=NOW + timedelta(minutes=2),
        )


def test_reviewed_cross_language_plan_produces_descriptive_slices_without_parity_claim(
    tmp_path: Path,
) -> None:
    run_path = tmp_path / "paired-run.json"
    run_sha = _write_run(run_path, _run_artifact(paired=True))
    run = load_search_run_observation_artifact(
        run_path,
        expected_sha256=run_sha,
        evaluated_at=NOW,
    )
    plan_path = tmp_path / "slice-plan.json"
    plan_sha = _write_slice_plan(
        plan_path,
        query_ids=("expert-001", "expert-001-zh"),
    )
    plan = load_search_qrels_slice_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )

    receipt = evaluate_search_qrels_slices(
        _qrels_bundle(paired=True),
        run,
        plan,
        evaluated_at=NOW,
    )

    assert receipt.query_coverage_state == "exact"
    assert receipt.adjudicated_qrel_parity_state == "exact_within_each_group"
    language_slices = {
        item.value: item for item in receipt.slices if item.dimension == "language"
    }
    assert set(language_slices) == {"en", "zh-Hans"}
    assert language_slices["en"].ndcg_at_k["1"] == 1.0
    comparison = receipt.translated_intent_comparisons[0]
    assert comparison.result_set_jaccard_at_k["1"] == 1.0
    assert comparison.result_set_jaccard_at_k["5"] == pytest.approx(1 / 3, abs=1e-6)
    assert comparison.ndcg_spread_at_k["10"] == 0.0
    assert receipt.parity_claim == "not_established"
    assert receipt.quality_claim == "not_established"
    assert receipt.release_decision == "not_computable"
    assert receipt.source_truth_review == "not_performed"


def test_cross_language_slice_plan_rejects_cherry_picked_or_unreviewed_scope(
    tmp_path: Path,
) -> None:
    run_path = tmp_path / "paired-run.json"
    run_sha = _write_run(run_path, _run_artifact(paired=True))
    run = load_search_run_observation_artifact(
        run_path,
        expected_sha256=run_sha,
        evaluated_at=NOW,
    )
    plan_path = tmp_path / "slice-plan.json"
    plan_sha = _write_slice_plan(
        plan_path,
        query_ids=("expert-001", "unknown-query"),
    )
    plan = load_search_qrels_slice_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )
    with pytest.raises(SearchQrelsDatasetError, match="cover every query"):
        evaluate_search_qrels_slices(
            _qrels_bundle(paired=True),
            run,
            plan,
            evaluated_at=NOW,
        )

    payload = plan.plan.model_dump(mode="json")
    payload["reviewer_ids"] = ["reviewer:other001", "reviewer:other002"]
    changed = SearchQrelsSlicePlan.model_validate(payload)
    raw = changed.model_dump_json().encode()
    plan_path.write_bytes(raw)
    changed_plan = load_search_qrels_slice_plan(
        plan_path,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        evaluated_at=NOW,
    )
    with pytest.raises(SearchQrelsDatasetError, match="reviewers"):
        evaluate_search_qrels_slices(
            _qrels_bundle(paired=True),
            run,
            changed_plan,
            evaluated_at=NOW,
        )


def test_same_qrels_regression_receipt_reports_deltas_without_regression_claim(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "slice-plan.json"
    plan_sha = _write_slice_plan(
        plan_path,
        query_ids=("expert-001", "expert-001-zh"),
    )
    plan = load_search_qrels_slice_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )
    baseline_run_path = tmp_path / "baseline-run.json"
    baseline_run_sha = _write_run(baseline_run_path, _run_artifact(paired=True))
    baseline_run = load_search_run_observation_artifact(
        baseline_run_path,
        expected_sha256=baseline_run_sha,
        evaluated_at=NOW,
    )
    current_artifact = _run_artifact(paired=True).model_copy(
        update={
            "run_id": "candidate-export-002",
            "observations": (
                SearchEvalObservation(
                    query_id="expert-001",
                    ordered_result_ids=["news:42", "news:7"],
                    latency_ms=150.0,
                ),
                SearchEvalObservation(
                    query_id="expert-001-zh",
                    ordered_result_ids=["news:8", "news:42"],
                    latency_ms=160.0,
                ),
            ),
        }
    )
    current_run_path = tmp_path / "current-run.json"
    current_run_sha = _write_run(current_run_path, current_artifact)
    current_run = load_search_run_observation_artifact(
        current_run_path,
        expected_sha256=current_run_sha,
        evaluated_at=NOW,
    )
    bundle = _qrels_bundle(paired=True)
    baseline_receipt = evaluate_search_qrels_slices(
        bundle,
        baseline_run,
        plan,
        evaluated_at=NOW,
    )
    current_receipt = evaluate_search_qrels_slices(
        bundle,
        current_run,
        plan,
        evaluated_at=NOW + timedelta(minutes=5),
    )
    baseline_path = tmp_path / "baseline-receipt.json"
    baseline_sha = _write_slice_receipt(baseline_path, baseline_receipt)
    current_path = tmp_path / "current-receipt.json"
    current_sha = _write_slice_receipt(current_path, current_receipt)

    regression = compare_search_qrels_slice_receipts(
        load_search_qrels_slice_receipt(
            baseline_path,
            expected_sha256=baseline_sha,
        ),
        load_search_qrels_slice_receipt(
            current_path,
            expected_sha256=current_sha,
        ),
        evaluated_at=NOW + timedelta(minutes=10),
    )

    assert regression.comparison_scope == "exact_qrels_plan_query_and_slice_scope"
    assert regression.aggregate_human_gold["ndcg_at_k"]["1"].delta < 0  # type: ignore[index, union-attr]
    assert regression.operational["latency_p95_ms"].delta == 30.0
    assert regression.translated_intent_comparisons[0].ndcg_spread_at_k[
        "1"
    ].delta > 0
    assert regression.threshold_approval_state == "not_approved"
    assert regression.regression_claim == "not_established"
    assert regression.quality_claim == "not_established"
    assert regression.release_decision == "not_computable"
    assert regression.source_truth_review == "not_performed"


def test_qrels_regression_rejects_scope_time_and_run_evidence_drift(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "slice-plan.json"
    plan_sha = _write_slice_plan(
        plan_path,
        query_ids=("expert-001", "expert-001-zh"),
    )
    plan = load_search_qrels_slice_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )
    run_path = tmp_path / "run.json"
    run_sha = _write_run(run_path, _run_artifact(paired=True))
    run = load_search_run_observation_artifact(
        run_path,
        expected_sha256=run_sha,
        evaluated_at=NOW,
    )
    receipt = evaluate_search_qrels_slices(
        _qrels_bundle(paired=True),
        run,
        plan,
        evaluated_at=NOW,
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_sha = _write_slice_receipt(baseline_path, receipt)
    loaded = load_search_qrels_slice_receipt(
        baseline_path,
        expected_sha256=baseline_sha,
    )

    with pytest.raises(SearchQrelsDatasetError, match="must follow baseline"):
        compare_search_qrels_slice_receipts(
            loaded,
            loaded.model_copy(
                update={
                    "artifact_sha256": "d" * 64,
                    "receipt": receipt.model_copy(
                        update={"run_artifact_sha256": "e" * 64}
                    ),
                }
            ),
            evaluated_at=NOW + timedelta(minutes=1),
        )

    drifted = receipt.model_copy(
        update={
            "evaluated_at": NOW + timedelta(minutes=1),
            "qrels_dataset_sha256": "f" * 64,
            "run_artifact_sha256": "e" * 64,
        }
    )
    with pytest.raises(SearchQrelsDatasetError, match="qrels datasets"):
        compare_search_qrels_slice_receipts(
            loaded,
            loaded.model_copy(
                update={"artifact_sha256": "d" * 64, "receipt": drifted}
            ),
            evaluated_at=NOW + timedelta(minutes=2),
        )

    non_finite_payload = receipt.model_dump(mode="json")
    non_finite_payload["benchmark"]["evaluation"]["operational"][  # type: ignore[index]
        "latency_p95_ms"
    ] = "__NON_FINITE__"
    non_finite_raw = json.dumps(
        non_finite_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).replace('"__NON_FINITE__"', "1e400").encode()
    non_finite_path = tmp_path / "non-finite-receipt.json"
    non_finite_path.write_bytes(non_finite_raw)
    with pytest.raises(SearchQrelsDatasetError, match="strict validation"):
        load_search_qrels_slice_receipt(
            non_finite_path,
            expected_sha256=hashlib.sha256(non_finite_raw).hexdigest(),
        )
