"""Pure, offline search evaluation with explicit judgment provenance.

This module intentionally does not connect to a database or search service. It
only evaluates caller-supplied ranked IDs against versioned qrels, and it keeps
silver judgments separate from adjudicated human gold.
"""

from __future__ import annotations

from datetime import datetime
from math import ceil, log2
from typing import Dict, List, Literal, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

SEARCH_EVAL_SCHEMA_VERSION = "search-eval-v1"
SEARCH_EVAL_METHOD_VERSION = "search-eval-metrics-v1"
MAX_SEARCH_EVAL_QUERIES = 1000

JudgmentTier = Literal["silver", "human_gold"]
ReviewState = Literal["unreviewed", "reviewed", "adjudicated"]


class SearchEvalProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_version: str = Field(min_length=1, max_length=120)
    corpus_snapshot_id: str = Field(min_length=1, max_length=200)
    source_kind: Literal[
        "synthetic_fixture",
        "researcher_annotation",
        "adjudicated_annotation",
        "imported_benchmark",
    ]
    source_locator: str = Field(min_length=1, max_length=500)
    created_at: datetime
    reviewer_evidence: str | None = Field(default=None, max_length=500)

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class SearchQrel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=240)
    relevance_grade: int = Field(ge=0, le=3)
    rationale: str | None = Field(default=None, max_length=1000)


class SearchEvalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["search-eval-v1"] = SEARCH_EVAL_SCHEMA_VERSION
    query_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    query: str = Field(min_length=1, max_length=2000)
    language: str = Field(min_length=1, max_length=40)
    country: str | None = Field(default=None, max_length=80)
    topic: str = Field(min_length=1, max_length=200)
    intent: str = Field(min_length=1, max_length=500)
    qrels: List[SearchQrel] = Field(default_factory=list, max_length=2000)
    provenance: SearchEvalProvenance
    judgment_tier: JudgmentTier
    review_state: ReviewState

    @model_validator(mode="after")
    def _validate_judgments(self) -> "SearchEvalQuery":
        document_ids = [item.document_id for item in self.qrels]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("duplicate qrel document_id")
        if self.judgment_tier == "human_gold":
            if self.review_state != "adjudicated":
                raise ValueError("human_gold requires adjudicated review_state")
            if (
                self.provenance.source_kind != "adjudicated_annotation"
                or not self.provenance.reviewer_evidence
            ):
                raise ValueError(
                    "human_gold requires adjudicated provenance and reviewer_evidence"
                )
        return self


class SearchEvalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    ordered_result_ids: List[str] = Field(default_factory=list, max_length=2000)
    latency_ms: FiniteFloat = Field(ge=0)
    timed_out: bool = False
    error_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _validate_observation(self) -> "SearchEvalObservation":
        if len(self.ordered_result_ids) != len(set(self.ordered_result_ids)):
            raise ValueError("duplicate ordered_result_ids")
        if self.timed_out and self.ordered_result_ids:
            raise ValueError("timed_out observations cannot claim ranked results")
        if self.timed_out and not self.error_code:
            raise ValueError("timed_out observations require error_code")
        return self


class SearchQueryMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    judgment_tier: JudgmentTier
    review_state: ReviewState
    metric_status: Literal["computed", "qrels_unavailable", "timeout"]
    returned_result_count: int = Field(ge=0)
    relevant_qrel_count: int = Field(ge=0)
    timed_out: bool
    zero_result: bool | None
    latency_ms: FiniteFloat = Field(ge=0)
    precision_at_k: Dict[str, FiniteFloat | None]
    recall_at_k: Dict[str, FiniteFloat | None]
    ndcg_at_k: Dict[str, FiniteFloat | None]
    reciprocal_rank: FiniteFloat | None


class SearchRelevanceAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgment_tier: JudgmentTier
    query_count: int = Field(ge=0)
    evaluated_query_count: int = Field(ge=0)
    precision_at_k: Dict[str, FiniteFloat | None]
    recall_at_k: Dict[str, FiniteFloat | None]
    ndcg_at_k: Dict[str, FiniteFloat | None]
    mean_reciprocal_rank: FiniteFloat | None


class SearchOperationalMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    timeout_count: int = Field(ge=0)
    timeout_rate: FiniteFloat | None
    zero_result_count: int = Field(ge=0)
    zero_result_rate: FiniteFloat | None
    latency_method: Literal["nearest_rank_completed_only"] = (
        "nearest_rank_completed_only"
    )
    latency_observation_count: int = Field(ge=0)
    latency_p50_ms: FiniteFloat | None
    latency_p95_ms: FiniteFloat | None


class SearchEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["search-eval-v1"] = SEARCH_EVAL_SCHEMA_VERSION
    metric_method_version: Literal["search-eval-metrics-v1"] = (
        SEARCH_EVAL_METHOD_VERSION
    )
    evaluated_at: datetime
    k_values: List[int]
    dataset_ids: List[str]
    corpus_snapshot_ids: List[str]
    per_query: List[SearchQueryMetrics]
    operational: SearchOperationalMetrics
    relevance_by_judgment_tier: Dict[str, SearchRelevanceAggregate]
    threshold_approval_state: Literal["not_approved"] = "not_approved"
    release_decision: Literal["not_computable"] = "not_computable"
    quality_claim: Literal["not_established"] = "not_established"
    limitations: List[str]


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def _metric_keys(k_values: Sequence[int], value: float | None) -> Dict[str, float | None]:
    return {str(k): value for k in k_values}


def _dcg(grades: Sequence[int]) -> float:
    return sum((2**grade - 1) / log2(rank + 1) for rank, grade in enumerate(grades, 1))


def _rank_metrics(
    query: SearchEvalQuery,
    observation: SearchEvalObservation,
    k_values: Sequence[int],
) -> SearchQueryMetrics:
    relevant = {
        qrel.document_id: qrel.relevance_grade
        for qrel in query.qrels
        if qrel.relevance_grade > 0
    }
    empty_metrics = _metric_keys(k_values, None)
    if observation.timed_out:
        return SearchQueryMetrics(
            query_id=query.query_id,
            judgment_tier=query.judgment_tier,
            review_state=query.review_state,
            metric_status="timeout",
            returned_result_count=0,
            relevant_qrel_count=len(relevant),
            timed_out=True,
            zero_result=None,
            latency_ms=observation.latency_ms,
            precision_at_k=empty_metrics,
            recall_at_k=empty_metrics,
            ndcg_at_k=empty_metrics,
            reciprocal_rank=None,
        )
    if query.review_state == "unreviewed":
        return SearchQueryMetrics(
            query_id=query.query_id,
            judgment_tier=query.judgment_tier,
            review_state=query.review_state,
            metric_status="qrels_unavailable",
            returned_result_count=len(observation.ordered_result_ids),
            relevant_qrel_count=0,
            timed_out=False,
            zero_result=not observation.ordered_result_ids,
            latency_ms=observation.latency_ms,
            precision_at_k=empty_metrics,
            recall_at_k=empty_metrics,
            ndcg_at_k=empty_metrics,
            reciprocal_rank=None,
        )
    if not relevant:
        return SearchQueryMetrics(
            query_id=query.query_id,
            judgment_tier=query.judgment_tier,
            review_state=query.review_state,
            metric_status="qrels_unavailable",
            returned_result_count=len(observation.ordered_result_ids),
            relevant_qrel_count=0,
            timed_out=False,
            zero_result=not observation.ordered_result_ids,
            latency_ms=observation.latency_ms,
            precision_at_k=empty_metrics,
            recall_at_k=empty_metrics,
            ndcg_at_k=empty_metrics,
            reciprocal_rank=None,
        )

    precision: Dict[str, float | None] = {}
    recall: Dict[str, float | None] = {}
    ndcg: Dict[str, float | None] = {}
    ideal_grades = sorted(relevant.values(), reverse=True)
    for k in k_values:
        returned = observation.ordered_result_ids[:k]
        relevant_returned = sum(identifier in relevant for identifier in returned)
        precision[str(k)] = _round_metric(relevant_returned / k)
        recall[str(k)] = _round_metric(relevant_returned / len(relevant))
        observed_grades = [relevant.get(identifier, 0) for identifier in returned]
        observed_grades.extend([0] * (k - len(observed_grades)))
        ideal = _dcg(ideal_grades[:k])
        ndcg[str(k)] = _round_metric(_dcg(observed_grades) / ideal) if ideal else None

    first_relevant_rank = next(
        (
            rank
            for rank, identifier in enumerate(observation.ordered_result_ids, 1)
            if identifier in relevant
        ),
        None,
    )
    reciprocal_rank = (
        _round_metric(1 / first_relevant_rank)
        if first_relevant_rank is not None
        else 0.0
    )
    return SearchQueryMetrics(
        query_id=query.query_id,
        judgment_tier=query.judgment_tier,
        review_state=query.review_state,
        metric_status="computed",
        returned_result_count=len(observation.ordered_result_ids),
        relevant_qrel_count=len(relevant),
        timed_out=False,
        zero_result=not observation.ordered_result_ids,
        latency_ms=observation.latency_ms,
        precision_at_k=precision,
        recall_at_k=recall,
        ndcg_at_k=ndcg,
        reciprocal_rank=reciprocal_rank,
    )


def _mean(values: Sequence[float]) -> float | None:
    return _round_metric(sum(values) / len(values)) if values else None


def _relevance_aggregate(
    tier: JudgmentTier,
    metrics: Sequence[SearchQueryMetrics],
    k_values: Sequence[int],
) -> SearchRelevanceAggregate:
    selected = [item for item in metrics if item.judgment_tier == tier]
    computed = [item for item in selected if item.metric_status == "computed"]

    def averages(attribute: str) -> Dict[str, float | None]:
        output: Dict[str, float | None] = {}
        for k in k_values:
            values = [
                value
                for item in computed
                if (value := getattr(item, attribute).get(str(k))) is not None
            ]
            output[str(k)] = _mean(values)
        return output

    return SearchRelevanceAggregate(
        judgment_tier=tier,
        query_count=len(selected),
        evaluated_query_count=len(computed),
        precision_at_k=averages("precision_at_k"),
        recall_at_k=averages("recall_at_k"),
        ndcg_at_k=averages("ndcg_at_k"),
        mean_reciprocal_rank=_mean(
            [
                item.reciprocal_rank
                for item in computed
                if item.reciprocal_rank is not None
            ]
        ),
    )


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, ceil(percentile * len(ordered)))
    return float(ordered[rank - 1])


def _operational_metrics(
    observations: Sequence[SearchEvalObservation],
) -> SearchOperationalMetrics:
    completed = [item for item in observations if not item.timed_out]
    timeouts = [item for item in observations if item.timed_out]
    zero_results = [item for item in completed if not item.ordered_result_ids]
    latencies = [item.latency_ms for item in completed]
    return SearchOperationalMetrics(
        query_count=len(observations),
        completed_count=len(completed),
        timeout_count=len(timeouts),
        timeout_rate=_round_metric(len(timeouts) / len(observations)) if observations else None,
        zero_result_count=len(zero_results),
        zero_result_rate=(
            _round_metric(len(zero_results) / len(completed)) if completed else None
        ),
        latency_observation_count=len(latencies),
        latency_p50_ms=_nearest_rank(latencies, 0.50),
        latency_p95_ms=_nearest_rank(latencies, 0.95),
    )


def evaluate_search_run(
    queries: Sequence[SearchEvalQuery],
    observations: Sequence[SearchEvalObservation],
    *,
    k_values: Sequence[int] = (1, 5, 10),
    evaluated_at: datetime,
) -> SearchEvaluationReport:
    """Evaluate one complete offline run without producing a release claim."""

    if len(queries) > MAX_SEARCH_EVAL_QUERIES:
        raise ValueError("query count exceeds the bounded evaluation limit")
    if len(observations) > MAX_SEARCH_EVAL_QUERIES:
        raise ValueError("observation count exceeds the bounded evaluation limit")
    query_list = list(queries)
    observation_list = list(observations)
    query_ids = [item.query_id for item in query_list]
    observation_ids = [item.query_id for item in observation_list]
    if not query_ids or set(query_ids) != set(observation_ids):
        raise ValueError("observation query IDs must exactly match non-empty query IDs")
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("query IDs must be unique")
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("observation query IDs must be unique")
    if any(type(value) is not int for value in k_values):
        raise ValueError("k_values must be unique ascending integers between 1 and 1000")
    normalized_k = tuple(k_values)
    if (
        not normalized_k
        or any(value < 1 or value > 1000 for value in normalized_k)
        or len(normalized_k) != len(set(normalized_k))
        or tuple(sorted(normalized_k)) != normalized_k
    ):
        raise ValueError("k_values must be unique ascending integers between 1 and 1000")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must include a timezone")

    observations_by_id = {item.query_id: item for item in observation_list}
    per_query = [
        _rank_metrics(query, observations_by_id[query.query_id], normalized_k)
        for query in query_list
    ]
    return SearchEvaluationReport(
        evaluated_at=evaluated_at,
        k_values=list(normalized_k),
        dataset_ids=sorted({item.provenance.dataset_id for item in query_list}),
        corpus_snapshot_ids=sorted(
            {item.provenance.corpus_snapshot_id for item in query_list}
        ),
        per_query=per_query,
        operational=_operational_metrics(observation_list),
        relevance_by_judgment_tier={
            tier: _relevance_aggregate(tier, per_query, normalized_k)
            for tier in ("silver", "human_gold")
        },
        limitations=[
            "Metrics apply only to the supplied queries, qrels, ranked IDs, and corpus snapshot.",
            "Silver and adjudicated human-gold relevance metrics are not pooled.",
            "No threshold or release decision has been approved from this offline report.",
        ],
    )


__all__ = (
    "MAX_SEARCH_EVAL_QUERIES",
    "SEARCH_EVAL_METHOD_VERSION",
    "SEARCH_EVAL_SCHEMA_VERSION",
    "SearchEvalObservation",
    "SearchEvalProvenance",
    "SearchEvalQuery",
    "SearchEvaluationReport",
    "SearchOperationalMetrics",
    "SearchQrel",
    "SearchQueryMetrics",
    "SearchRelevanceAggregate",
    "evaluate_search_run",
)
