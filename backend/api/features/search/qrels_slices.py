"""Cross-language and country slice receipts for verified search qrels runs.

The comparison plan must cover every qrels query exactly once and may group
only adjudicated translations that share the same country, topic, and graded
qrels.  Metrics remain descriptive: no parity threshold, source-truth claim,
or release decision is inferred.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

from .qrels_benchmark import (
    LoadedSearchRunObservationArtifact,
    SearchQrelsBenchmarkReceipt,
    evaluate_search_qrels_benchmark,
)
from .qrels_dataset import (
    _REVIEWER_ID_RE,
    LoadedSearchQrelsBundle,
    SearchQrelsDatasetError,
    _read_bundle_evidence,
    _strict_json_artifact,
)

SEARCH_QRELS_SLICE_PLAN_SCHEMA_VERSION = "search-qrels-slice-plan-v1"
SEARCH_QRELS_SLICE_RECEIPT_SCHEMA_VERSION = "search-qrels-slice-receipt-v1"
MAX_SEARCH_QRELS_SLICE_PLAN_BYTES = 2 * 1024 * 1024
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class SearchTranslatedIntentGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
    query_ids: tuple[str, ...] = Field(min_length=2, max_length=20)
    comparison_kind: Literal["adjudicated_translated_intent"] = (
        "adjudicated_translated_intent"
    )

    @model_validator(mode="after")
    def query_ids_are_unique(self) -> "SearchTranslatedIntentGroup":
        if len(self.query_ids) != len(set(self.query_ids)):
            raise ValueError("translated-intent query IDs must be unique")
        return self


class SearchQrelsSlicePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["search-qrels-slice-plan-v1"] = (
        SEARCH_QRELS_SLICE_PLAN_SCHEMA_VERSION
    )
    plan_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
    plan_version: str = Field(min_length=1, max_length=120)
    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_version: str = Field(min_length=1, max_length=120)
    corpus_snapshot_id: str = Field(min_length=1, max_length=200)
    reviewer_ids: tuple[str, ...] = Field(min_length=2, max_length=20)
    reviewed_at: datetime
    review_expires_at: datetime
    review_state: Literal["adjudicated"] = "adjudicated"
    groups: tuple[SearchTranslatedIntentGroup, ...] = Field(
        min_length=1,
        max_length=500,
    )
    threshold_approval_state: Literal["not_approved"] = "not_approved"

    @field_validator("reviewed_at", "review_expires_at")
    @classmethod
    def times_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("slice plan review times must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def review_and_groups_are_unique(self) -> "SearchQrelsSlicePlan":
        if len(self.reviewer_ids) != len(set(self.reviewer_ids)) or any(
            _REVIEWER_ID_RE.fullmatch(value) is None for value in self.reviewer_ids
        ):
            raise ValueError("slice plan reviewer IDs are invalid or duplicated")
        if self.review_expires_at <= self.reviewed_at:
            raise ValueError("slice plan review expiry must follow review")
        group_ids = [group.group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("slice plan group IDs must be unique")
        all_query_ids = [query_id for group in self.groups for query_id in group.query_ids]
        if len(all_query_ids) != len(set(all_query_ids)):
            raise ValueError("slice plan query IDs cannot appear in multiple groups")
        return self


class LoadedSearchQrelsSlicePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: SearchQrelsSlicePlan
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_bytes: int = Field(gt=0, le=MAX_SEARCH_QRELS_SLICE_PLAN_BYTES)


class SearchMetricSlice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: Literal["language", "country"]
    value: str
    query_count: int = Field(ge=1, strict=True)
    evaluated_query_count: int = Field(ge=0, strict=True)
    ndcg_at_k: dict[str, FiniteFloat | None]
    recall_at_k: dict[str, FiniteFloat | None]
    reciprocal_rank_mean: FiniteFloat | None


class SearchTranslatedIntentComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str
    query_ids: tuple[str, ...]
    languages: tuple[str, ...]
    country: str
    result_set_jaccard_at_k: dict[str, FiniteFloat | None]
    ndcg_spread_at_k: dict[str, FiniteFloat | None]
    recall_spread_at_k: dict[str, FiniteFloat | None]
    comparison_state: Literal["descriptive_not_thresholded"] = (
        "descriptive_not_thresholded"
    )


class SearchQrelsSliceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["search-qrels-slice-receipt-v1"] = (
        SEARCH_QRELS_SLICE_RECEIPT_SCHEMA_VERSION
    )
    plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    qrels_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    evaluated_at: datetime
    query_coverage_state: Literal["exact"] = "exact"
    adjudicated_qrel_parity_state: Literal["exact_within_each_group"] = (
        "exact_within_each_group"
    )
    slices: tuple[SearchMetricSlice, ...]
    translated_intent_comparisons: tuple[SearchTranslatedIntentComparison, ...]
    benchmark: SearchQrelsBenchmarkReceipt
    threshold_approval_state: Literal["not_approved"] = "not_approved"
    parity_claim: Literal["not_established"] = "not_established"
    quality_claim: Literal["not_established"] = "not_established"
    release_decision: Literal["not_computable"] = "not_computable"
    source_truth_review: Literal["not_performed"] = "not_performed"


def load_search_qrels_slice_plan(
    path: Path,
    *,
    expected_sha256: str,
    evaluated_at: datetime,
) -> LoadedSearchQrelsSlicePlan:
    """Load one bounded reviewed comparison plan without following links."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise SearchQrelsDatasetError("evaluated_at must include a timezone")
    if not path.is_absolute():
        raise SearchQrelsDatasetError("slice plan path must be absolute")
    raw = _read_bundle_evidence(
        path.parent.resolve(strict=True),
        path.name,
        expected_sha256=expected_sha256,
        maximum_bytes=MAX_SEARCH_QRELS_SLICE_PLAN_BYTES,
        field="qrels slice plan",
    )
    parsed = _strict_json_artifact(raw, SearchQrelsSlicePlan, "qrels slice plan")
    assert isinstance(parsed, SearchQrelsSlicePlan)
    now = evaluated_at.astimezone(timezone.utc)
    if parsed.reviewed_at > now:
        raise SearchQrelsDatasetError("qrels slice plan review is in the future")
    if parsed.review_expires_at <= now:
        raise SearchQrelsDatasetError("qrels slice plan review is expired")
    return LoadedSearchQrelsSlicePlan(
        plan=parsed,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        artifact_bytes=len(raw),
    )


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _slice_metrics(
    *,
    dimension: Literal["language", "country"],
    queries: Sequence[object],
    metrics_by_id: Mapping[str, object],
    k_values: Sequence[int],
) -> tuple[SearchMetricSlice, ...]:
    grouped: dict[str, list[object]] = {}
    for query in queries:
        raw_value = getattr(query, dimension)
        value = str(raw_value) if raw_value is not None else "unknown"
        grouped.setdefault(value, []).append(query)
    output: list[SearchMetricSlice] = []
    for value in sorted(grouped):
        selected = [metrics_by_id[getattr(query, "query_id")] for query in grouped[value]]

        def means(attribute: str) -> dict[str, float | None]:
            return {
                str(k): _mean(
                    [
                        metric_value
                        for metric in selected
                        if (
                            metric_value := getattr(metric, attribute).get(str(k))
                        )
                        is not None
                    ]
                )
                for k in k_values
            }

        output.append(
            SearchMetricSlice(
                dimension=dimension,
                value=value,
                query_count=len(selected),
                evaluated_query_count=sum(
                    getattr(metric, "metric_status") == "computed"
                    for metric in selected
                ),
                ndcg_at_k=means("ndcg_at_k"),
                recall_at_k=means("recall_at_k"),
                reciprocal_rank_mean=_mean(
                    [
                        value
                        for metric in selected
                        if (value := getattr(metric, "reciprocal_rank")) is not None
                    ]
                ),
            )
        )
    return tuple(output)


def _spread(values: Sequence[float]) -> float | None:
    return round(max(values) - min(values), 6) if len(values) >= 2 else None


def _multiway_jaccard(result_sets: Sequence[set[str]]) -> float | None:
    if len(result_sets) < 2:
        return None
    union = set().union(*result_sets)
    if not union:
        return 1.0
    intersection = set(result_sets[0]).intersection(*result_sets[1:])
    return round(len(intersection) / len(union), 6)


def evaluate_search_qrels_slices(
    qrels: LoadedSearchQrelsBundle,
    run: LoadedSearchRunObservationArtifact,
    plan: LoadedSearchQrelsSlicePlan,
    *,
    evaluated_at: datetime,
    k_values: Sequence[int] = (1, 5, 10, 20, 100),
) -> SearchQrelsSliceReceipt:
    """Compute exact descriptive slices while withholding parity approval."""

    dataset = qrels.dataset
    slice_plan = plan.plan
    if (
        slice_plan.dataset_id != dataset.dataset_id
        or slice_plan.dataset_version != dataset.dataset_version
        or slice_plan.corpus_snapshot_id != dataset.corpus.corpus_snapshot_id
    ):
        raise SearchQrelsDatasetError("qrels slice plan does not match dataset and corpus")
    if set(slice_plan.reviewer_ids) != set(dataset.adjudication.reviewer_ids):
        raise SearchQrelsDatasetError("qrels slice plan reviewers do not match adjudication")
    queries_by_id = {query.query_id: query for query in dataset.queries}
    planned_ids = [query_id for group in slice_plan.groups for query_id in group.query_ids]
    if set(planned_ids) != set(queries_by_id) or len(planned_ids) != len(queries_by_id):
        raise SearchQrelsDatasetError("qrels slice plan must cover every query exactly once")

    for group in slice_plan.groups:
        queries = [queries_by_id[query_id] for query_id in group.query_ids]
        languages = [query.language for query in queries]
        if len(languages) != len(set(languages)):
            raise SearchQrelsDatasetError("translated-intent group languages must be unique")
        countries = {query.country for query in queries}
        topics = {query.topic for query in queries}
        if len(countries) != 1 or None in countries or len(topics) != 1:
            raise SearchQrelsDatasetError(
                "translated-intent group must share one explicit country and topic"
            )
        graded_qrels = [
            {(item.document_id, item.relevance_grade) for item in query.qrels}
            for query in queries
        ]
        if any(value != graded_qrels[0] for value in graded_qrels[1:]):
            raise SearchQrelsDatasetError(
                "translated-intent group must have identical adjudicated graded qrels"
            )

    benchmark = evaluate_search_qrels_benchmark(
        qrels,
        run,
        evaluated_at=evaluated_at,
        k_values=k_values,
    )
    metrics_by_id = {item.query_id: item for item in benchmark.evaluation.per_query}
    observations_by_id = {
        item.query_id: item for item in run.artifact.observations
    }
    comparisons: list[SearchTranslatedIntentComparison] = []
    for group in slice_plan.groups:
        queries = [queries_by_id[query_id] for query_id in group.query_ids]
        comparisons.append(
            SearchTranslatedIntentComparison(
                group_id=group.group_id,
                query_ids=group.query_ids,
                languages=tuple(query.language for query in queries),
                country=str(queries[0].country),
                result_set_jaccard_at_k={
                    str(k): _multiway_jaccard(
                        [
                            set(observations_by_id[query_id].ordered_result_ids[:k])
                            for query_id in group.query_ids
                            if not observations_by_id[query_id].timed_out
                        ]
                    )
                    for k in k_values
                },
                ndcg_spread_at_k={
                    str(k): _spread(
                        [
                            value
                            for query_id in group.query_ids
                            if (value := metrics_by_id[query_id].ndcg_at_k.get(str(k)))
                            is not None
                        ]
                    )
                    for k in k_values
                },
                recall_spread_at_k={
                    str(k): _spread(
                        [
                            value
                            for query_id in group.query_ids
                            if (value := metrics_by_id[query_id].recall_at_k.get(str(k)))
                            is not None
                        ]
                    )
                    for k in k_values
                },
            )
        )
    slices = (
        *_slice_metrics(
            dimension="language",
            queries=dataset.queries,
            metrics_by_id=metrics_by_id,
            k_values=k_values,
        ),
        *_slice_metrics(
            dimension="country",
            queries=dataset.queries,
            metrics_by_id=metrics_by_id,
            k_values=k_values,
        ),
    )
    return SearchQrelsSliceReceipt(
        plan_sha256=plan.artifact_sha256,
        qrels_dataset_sha256=qrels.dataset_sha256,
        run_artifact_sha256=run.artifact_sha256,
        evaluated_at=evaluated_at.astimezone(timezone.utc),
        slices=slices,
        translated_intent_comparisons=tuple(comparisons),
        benchmark=benchmark,
    )


__all__ = (
    "LoadedSearchQrelsSlicePlan",
    "SEARCH_QRELS_SLICE_PLAN_SCHEMA_VERSION",
    "SEARCH_QRELS_SLICE_RECEIPT_SCHEMA_VERSION",
    "SearchMetricSlice",
    "SearchQrelsSlicePlan",
    "SearchQrelsSliceReceipt",
    "SearchTranslatedIntentComparison",
    "SearchTranslatedIntentGroup",
    "evaluate_search_qrels_slices",
    "load_search_qrels_slice_plan",
)
