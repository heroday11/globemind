from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from api.features.search import (
    MAX_SEARCH_EVAL_QUERIES,
    SEARCH_EVAL_METHOD_VERSION,
    SEARCH_EVAL_SCHEMA_VERSION,
    SearchEvalObservation,
    SearchEvalProvenance,
    SearchEvalQuery,
    SearchQrel,
    evaluate_search_run,
)

EVALUATED_AT = datetime(2026, 8, 9, 16, 30, tzinfo=timezone.utc)


def _provenance(source_kind: str = "synthetic_fixture") -> SearchEvalProvenance:
    return SearchEvalProvenance(
        dataset_id="search-silver-fixture",
        dataset_version="2026.08.09-v1",
        corpus_snapshot_id="fixture-corpus-v1",
        source_kind=source_kind,
        source_locator="backend/tests/test_search_evaluation.py",
        created_at=EVALUATED_AT,
        reviewer_evidence=(
            "review-ledger://adjudication-1"
            if source_kind == "adjudicated_annotation"
            else None
        ),
    )


def _query(
    query_id: str,
    *,
    qrels: list[SearchQrel],
    judgment_tier: str = "silver",
    review_state: str = "reviewed",
) -> SearchEvalQuery:
    source_kind = (
        "adjudicated_annotation"
        if judgment_tier == "human_gold"
        else "synthetic_fixture"
    )
    return SearchEvalQuery(
        query_id=query_id,
        query="China semiconductor",
        language="en",
        country="CN",
        topic="technology controls",
        intent="find policy-relevant reporting",
        qrels=qrels,
        provenance=_provenance(source_kind),
        judgment_tier=judgment_tier,
        review_state=review_state,
    )


def test_search_eval_v1_contract_rejects_duplicate_or_unadjudicated_gold() -> None:
    assert SEARCH_EVAL_SCHEMA_VERSION == "search-eval-v1"
    assert SEARCH_EVAL_METHOD_VERSION == "search-eval-metrics-v1"

    with pytest.raises(ValidationError, match="duplicate qrel document_id"):
        _query(
            "duplicate-qrel",
            qrels=[
                SearchQrel(document_id="news:1", relevance_grade=3),
                SearchQrel(document_id="news:1", relevance_grade=1),
            ],
        )

    with pytest.raises(ValidationError, match="human_gold requires adjudicated"):
        _query(
            "unreviewed-gold",
            qrels=[SearchQrel(document_id="news:1", relevance_grade=3)],
            judgment_tier="human_gold",
            review_state="reviewed",
        )


def test_search_eval_computes_standard_rank_metrics_without_quality_claims() -> None:
    query = _query(
        "silver-1",
        qrels=[
            SearchQrel(document_id="news:a", relevance_grade=3),
            SearchQrel(document_id="news:b", relevance_grade=1),
            SearchQrel(document_id="news:c", relevance_grade=0),
        ],
    )
    report = evaluate_search_run(
        [query],
        [
            SearchEvalObservation(
                query_id="silver-1",
                ordered_result_ids=["news:c", "news:a", "news:x", "news:b"],
                latency_ms=12.5,
            )
        ],
        k_values=(1, 3, 4),
        evaluated_at=EVALUATED_AT,
    )

    metrics = report.per_query[0]
    assert metrics.precision_at_k == {"1": 0.0, "3": pytest.approx(1 / 3), "4": 0.5}
    assert metrics.recall_at_k == {"1": 0.0, "3": 0.5, "4": 1.0}
    assert metrics.reciprocal_rank == 0.5
    assert metrics.ndcg_at_k["1"] == 0.0
    assert metrics.ndcg_at_k["3"] == pytest.approx(0.578764, abs=1e-6)
    assert report.threshold_approval_state == "not_approved"
    assert report.release_decision == "not_computable"
    assert report.quality_claim == "not_established"


def test_search_eval_separates_silver_gold_timeouts_zero_results_and_latency() -> None:
    queries = [
        _query(
            "silver-complete",
            qrels=[SearchQrel(document_id="news:1", relevance_grade=2)],
        ),
        _query(
            "gold-zero",
            qrels=[SearchQrel(document_id="news:2", relevance_grade=3)],
            judgment_tier="human_gold",
            review_state="adjudicated",
        ),
        _query(
            "silver-timeout",
            qrels=[SearchQrel(document_id="news:3", relevance_grade=1)],
        ),
    ]
    observations = [
        SearchEvalObservation(
            query_id="silver-complete",
            ordered_result_ids=["news:1"],
            latency_ms=10,
        ),
        SearchEvalObservation(
            query_id="gold-zero",
            ordered_result_ids=[],
            latency_ms=20,
        ),
        SearchEvalObservation(
            query_id="silver-timeout",
            ordered_result_ids=[],
            latency_ms=5000,
            timed_out=True,
            error_code="timeout",
        ),
    ]

    report = evaluate_search_run(
        queries,
        observations,
        k_values=(1,),
        evaluated_at=EVALUATED_AT,
    )

    assert set(report.relevance_by_judgment_tier) == {"silver", "human_gold"}
    assert report.relevance_by_judgment_tier["silver"].query_count == 2
    assert report.relevance_by_judgment_tier["silver"].evaluated_query_count == 1
    assert report.relevance_by_judgment_tier["human_gold"].query_count == 1
    assert report.operational.query_count == 3
    assert report.operational.completed_count == 2
    assert report.operational.timeout_count == 1
    assert report.operational.timeout_rate == pytest.approx(1 / 3)
    assert report.operational.zero_result_count == 1
    assert report.operational.zero_result_rate == 0.5
    assert report.operational.latency_p50_ms == 10
    assert report.operational.latency_p95_ms == 20
    assert report.operational.latency_observation_count == 2


def test_search_eval_fails_closed_on_observation_drift_or_duplicate_results() -> None:
    query = _query(
        "silver-1",
        qrels=[SearchQrel(document_id="news:1", relevance_grade=1)],
    )
    with pytest.raises(ValueError, match="observation query IDs"):
        evaluate_search_run([], [], k_values=(10,), evaluated_at=EVALUATED_AT)
    with pytest.raises(ValueError, match="observation query IDs"):
        evaluate_search_run(
            [query],
            [],
            k_values=(10,),
            evaluated_at=EVALUATED_AT,
        )
    with pytest.raises(ValidationError, match="duplicate ordered_result_ids"):
        SearchEvalObservation(
            query_id="silver-1",
            ordered_result_ids=["news:1", "news:1"],
            latency_ms=1,
        )


@pytest.mark.parametrize("k_values", [(True,), (1.5,), ("1",)])
def test_search_eval_rejects_coerced_k_values(k_values) -> None:
    query = _query(
        "silver-1",
        qrels=[SearchQrel(document_id="news:1", relevance_grade=1)],
    )
    observation = SearchEvalObservation(
        query_id="silver-1",
        ordered_result_ids=["news:1"],
        latency_ms=1,
    )

    with pytest.raises(ValueError, match="k_values must be unique ascending integers"):
        evaluate_search_run(
            [query],
            [observation],
            k_values=k_values,
            evaluated_at=EVALUATED_AT,
        )


def test_search_eval_does_not_score_unreviewed_silver_qrels() -> None:
    query = _query(
        "silver-unreviewed",
        qrels=[SearchQrel(document_id="news:1", relevance_grade=3)],
        review_state="unreviewed",
    )
    report = evaluate_search_run(
        [query],
        [
            SearchEvalObservation(
                query_id=query.query_id,
                ordered_result_ids=["news:1"],
                latency_ms=1,
            )
        ],
        k_values=(1,),
        evaluated_at=EVALUATED_AT,
    )

    assert report.per_query[0].metric_status == "qrels_unavailable"
    assert report.relevance_by_judgment_tier["silver"].evaluated_query_count == 0


def test_search_eval_bounds_query_and_observation_cardinality() -> None:
    query = _query(
        "silver-0",
        qrels=[SearchQrel(document_id="news:1", relevance_grade=1)],
    )
    too_many_queries = [
        query.model_copy(update={"query_id": f"silver-{index}"})
        for index in range(MAX_SEARCH_EVAL_QUERIES + 1)
    ]

    with pytest.raises(ValueError, match="query count exceeds"):
        evaluate_search_run(
            too_many_queries,
            [],
            k_values=(1,),
            evaluated_at=EVALUATED_AT,
        )
