"""Content-free baseline/current comparison for exact qrels slice receipts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from .qrels_dataset import (
    MAX_SEARCH_QRELS_DATASET_BYTES,
    SearchQrelsDatasetError,
    _read_bundle_evidence,
    _strict_json_artifact,
)
from .qrels_slices import SearchQrelsSliceReceipt

SEARCH_QRELS_REGRESSION_RECEIPT_SCHEMA_VERSION = "search-qrels-regression-receipt-v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class LoadedSearchQrelsSliceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt: SearchQrelsSliceReceipt
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_bytes: int = Field(gt=0, le=MAX_SEARCH_QRELS_DATASET_BYTES)


class SearchMetricDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline: FiniteFloat | None
    current: FiniteFloat | None
    delta: FiniteFloat | None


class SearchSliceRegressionDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: Literal["language", "country"]
    value: str
    query_count: int = Field(ge=1, strict=True)
    ndcg_at_k: dict[str, SearchMetricDelta]
    recall_at_k: dict[str, SearchMetricDelta]
    reciprocal_rank_mean: SearchMetricDelta


class SearchTranslatedIntentRegressionDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str
    query_ids: tuple[str, ...]
    languages: tuple[str, ...]
    country: str
    result_set_jaccard_at_k: dict[str, SearchMetricDelta]
    ndcg_spread_at_k: dict[str, SearchMetricDelta]
    recall_spread_at_k: dict[str, SearchMetricDelta]


class SearchQrelsRegressionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["search-qrels-regression-receipt-v1"] = (
        SEARCH_QRELS_REGRESSION_RECEIPT_SCHEMA_VERSION
    )
    evaluated_at: datetime
    qrels_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    slice_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    current_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_run_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    current_run_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_run_id: str
    current_run_id: str
    comparison_scope: Literal["exact_qrels_plan_query_and_slice_scope"] = (
        "exact_qrels_plan_query_and_slice_scope"
    )
    aggregate_human_gold: dict[str, dict[str, SearchMetricDelta] | SearchMetricDelta]
    operational: dict[str, SearchMetricDelta]
    slices: tuple[SearchSliceRegressionDelta, ...]
    translated_intent_comparisons: tuple[SearchTranslatedIntentRegressionDelta, ...]
    threshold_approval_state: Literal["not_approved"] = "not_approved"
    regression_claim: Literal["not_established"] = "not_established"
    quality_claim: Literal["not_established"] = "not_established"
    release_decision: Literal["not_computable"] = "not_computable"
    source_truth_review: Literal["not_performed"] = "not_performed"


def load_search_qrels_slice_receipt(
    path: Path,
    *,
    expected_sha256: str,
) -> LoadedSearchQrelsSliceReceipt:
    """Load one bounded slice receipt without reading search result bodies."""

    if not path.is_absolute():
        raise SearchQrelsDatasetError("qrels slice receipt path must be absolute")
    raw = _read_bundle_evidence(
        path.parent.resolve(strict=True),
        path.name,
        expected_sha256=expected_sha256,
        maximum_bytes=MAX_SEARCH_QRELS_DATASET_BYTES,
        field="qrels slice receipt",
    )
    parsed = _strict_json_artifact(raw, SearchQrelsSliceReceipt, "qrels slice receipt")
    assert isinstance(parsed, SearchQrelsSliceReceipt)
    return LoadedSearchQrelsSliceReceipt(
        receipt=parsed,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        artifact_bytes=len(raw),
    )


def _delta(baseline: float | None, current: float | None) -> SearchMetricDelta:
    return SearchMetricDelta(
        baseline=baseline,
        current=current,
        delta=(
            round(current - baseline, 6)
            if baseline is not None and current is not None
            else None
        ),
    )


def _mapping_delta(
    baseline: Mapping[str, float | None],
    current: Mapping[str, float | None],
    *,
    field: str,
) -> dict[str, SearchMetricDelta]:
    if set(baseline) != set(current):
        raise SearchQrelsDatasetError(f"{field} metric keys do not match")
    return {
        key: _delta(baseline[key], current[key])
        for key in sorted(baseline, key=int)
    }


def compare_search_qrels_slice_receipts(
    baseline: LoadedSearchQrelsSliceReceipt,
    current: LoadedSearchQrelsSliceReceipt,
    *,
    evaluated_at: datetime,
) -> SearchQrelsRegressionReceipt:
    """Compare exact scopes while withholding any thresholded regression claim."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise SearchQrelsDatasetError("evaluated_at must include a timezone")
    left = baseline.receipt
    right = current.receipt
    if left.qrels_dataset_sha256 != right.qrels_dataset_sha256:
        raise SearchQrelsDatasetError("baseline and current qrels datasets do not match")
    if left.plan_sha256 != right.plan_sha256:
        raise SearchQrelsDatasetError("baseline and current slice plans do not match")
    if left.evaluated_at >= right.evaluated_at:
        raise SearchQrelsDatasetError("current slice receipt must follow baseline")
    if right.evaluated_at > evaluated_at.astimezone(timezone.utc):
        raise SearchQrelsDatasetError("current slice receipt is in the future")
    if left.run_artifact_sha256 == right.run_artifact_sha256:
        raise SearchQrelsDatasetError("baseline and current run artifacts must differ")
    left_eval = left.benchmark.evaluation
    right_eval = right.benchmark.evaluation
    if left_eval.k_values != right_eval.k_values:
        raise SearchQrelsDatasetError("baseline and current k values do not match")
    if [item.query_id for item in left_eval.per_query] != [
        item.query_id for item in right_eval.per_query
    ]:
        raise SearchQrelsDatasetError("baseline and current query scopes do not match")
    for field in (
        "corpus_manifest_sha256",
        "annotation_guide_sha256",
        "adjudication_artifact_sha256",
    ):
        if getattr(left.benchmark, field) != getattr(right.benchmark, field):
            raise SearchQrelsDatasetError(
                "baseline and current qrels evidence artifacts do not match"
            )

    left_gold = left_eval.relevance_by_judgment_tier["human_gold"]
    right_gold = right_eval.relevance_by_judgment_tier["human_gold"]
    if (
        left_gold.query_count != right_gold.query_count
        or left_gold.evaluated_query_count != right_gold.evaluated_query_count
    ):
        raise SearchQrelsDatasetError("baseline and current evaluated query counts differ")
    aggregate: dict[str, dict[str, SearchMetricDelta] | SearchMetricDelta] = {
        "precision_at_k": _mapping_delta(
            left_gold.precision_at_k,
            right_gold.precision_at_k,
            field="aggregate precision",
        ),
        "recall_at_k": _mapping_delta(
            left_gold.recall_at_k,
            right_gold.recall_at_k,
            field="aggregate recall",
        ),
        "ndcg_at_k": _mapping_delta(
            left_gold.ndcg_at_k,
            right_gold.ndcg_at_k,
            field="aggregate nDCG",
        ),
        "mean_reciprocal_rank": _delta(
            left_gold.mean_reciprocal_rank,
            right_gold.mean_reciprocal_rank,
        ),
    }
    operational = {
        "timeout_rate": _delta(
            left_eval.operational.timeout_rate,
            right_eval.operational.timeout_rate,
        ),
        "zero_result_rate": _delta(
            left_eval.operational.zero_result_rate,
            right_eval.operational.zero_result_rate,
        ),
        "latency_p50_ms": _delta(
            left_eval.operational.latency_p50_ms,
            right_eval.operational.latency_p50_ms,
        ),
        "latency_p95_ms": _delta(
            left_eval.operational.latency_p95_ms,
            right_eval.operational.latency_p95_ms,
        ),
    }

    left_slices = {(item.dimension, item.value): item for item in left.slices}
    right_slices = {(item.dimension, item.value): item for item in right.slices}
    if set(left_slices) != set(right_slices):
        raise SearchQrelsDatasetError("baseline and current slice scopes do not match")
    slice_deltas: list[SearchSliceRegressionDelta] = []
    for key in sorted(left_slices):
        left_slice = left_slices[key]
        right_slice = right_slices[key]
        if left_slice.query_count != right_slice.query_count:
            raise SearchQrelsDatasetError("baseline and current slice query counts differ")
        slice_deltas.append(
            SearchSliceRegressionDelta(
                dimension=left_slice.dimension,
                value=left_slice.value,
                query_count=left_slice.query_count,
                ndcg_at_k=_mapping_delta(
                    left_slice.ndcg_at_k,
                    right_slice.ndcg_at_k,
                    field=f"slice {key} nDCG",
                ),
                recall_at_k=_mapping_delta(
                    left_slice.recall_at_k,
                    right_slice.recall_at_k,
                    field=f"slice {key} recall",
                ),
                reciprocal_rank_mean=_delta(
                    left_slice.reciprocal_rank_mean,
                    right_slice.reciprocal_rank_mean,
                ),
            )
        )

    left_groups = {item.group_id: item for item in left.translated_intent_comparisons}
    right_groups = {item.group_id: item for item in right.translated_intent_comparisons}
    if set(left_groups) != set(right_groups):
        raise SearchQrelsDatasetError("baseline and current comparison groups do not match")
    group_deltas: list[SearchTranslatedIntentRegressionDelta] = []
    for group_id in sorted(left_groups):
        left_group = left_groups[group_id]
        right_group = right_groups[group_id]
        if (
            left_group.query_ids != right_group.query_ids
            or left_group.languages != right_group.languages
            or left_group.country != right_group.country
        ):
            raise SearchQrelsDatasetError("baseline and current group identities differ")
        group_deltas.append(
            SearchTranslatedIntentRegressionDelta(
                group_id=group_id,
                query_ids=left_group.query_ids,
                languages=left_group.languages,
                country=left_group.country,
                result_set_jaccard_at_k=_mapping_delta(
                    left_group.result_set_jaccard_at_k,
                    right_group.result_set_jaccard_at_k,
                    field=f"group {group_id} overlap",
                ),
                ndcg_spread_at_k=_mapping_delta(
                    left_group.ndcg_spread_at_k,
                    right_group.ndcg_spread_at_k,
                    field=f"group {group_id} nDCG spread",
                ),
                recall_spread_at_k=_mapping_delta(
                    left_group.recall_spread_at_k,
                    right_group.recall_spread_at_k,
                    field=f"group {group_id} recall spread",
                ),
            )
        )

    return SearchQrelsRegressionReceipt(
        evaluated_at=evaluated_at.astimezone(timezone.utc),
        qrels_dataset_sha256=left.qrels_dataset_sha256,
        slice_plan_sha256=left.plan_sha256,
        baseline_receipt_sha256=baseline.artifact_sha256,
        current_receipt_sha256=current.artifact_sha256,
        baseline_run_artifact_sha256=left.run_artifact_sha256,
        current_run_artifact_sha256=right.run_artifact_sha256,
        baseline_run_id=left.benchmark.run_id,
        current_run_id=right.benchmark.run_id,
        aggregate_human_gold=aggregate,
        operational=operational,
        slices=tuple(slice_deltas),
        translated_intent_comparisons=tuple(group_deltas),
    )


__all__ = (
    "LoadedSearchQrelsSliceReceipt",
    "SEARCH_QRELS_REGRESSION_RECEIPT_SCHEMA_VERSION",
    "SearchMetricDelta",
    "SearchQrelsRegressionReceipt",
    "SearchSliceRegressionDelta",
    "SearchTranslatedIntentRegressionDelta",
    "compare_search_qrels_slice_receipts",
    "load_search_qrels_slice_receipt",
)
