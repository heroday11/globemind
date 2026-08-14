"""Offline benchmark receipt for verified qrels and frozen ranked-result artifacts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evaluation import (
    SearchEvalObservation,
    SearchEvaluationReport,
    evaluate_search_run,
)
from .qrels_dataset import (
    MAX_SEARCH_QRELS_DATASET_BYTES,
    LoadedSearchQrelsBundle,
    SearchQrelsDatasetError,
    _read_bundle_evidence,
    _strict_json_artifact,
)

SEARCH_QRELS_RUN_ARTIFACT_SCHEMA_VERSION = "search-qrels-run-artifact-v1"
SEARCH_QRELS_BENCHMARK_RECEIPT_SCHEMA_VERSION = "search-qrels-benchmark-receipt-v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class SearchRunObservationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["search-qrels-run-artifact-v1"] = (
        SEARCH_QRELS_RUN_ARTIFACT_SCHEMA_VERSION
    )
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_version: str = Field(min_length=1, max_length=120)
    corpus_snapshot_id: str = Field(min_length=1, max_length=200)
    execution_source: Literal["isolated_candidate_export", "offline_snapshot_index"]
    engine_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    query_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    started_at: datetime
    completed_at: datetime
    observations: tuple[SearchEvalObservation, ...] = Field(
        min_length=1,
        max_length=1_000,
    )
    result_bodies_retained: Literal[False] = False
    error_details_retained: Literal[False] = False

    @field_validator("started_at", "completed_at")
    @classmethod
    def times_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run times must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def run_is_complete_and_unique(self) -> "SearchRunObservationArtifact":
        if self.completed_at < self.started_at:
            raise ValueError("run completion cannot precede start")
        query_ids = [item.query_id for item in self.observations]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("run observation query IDs must be unique")
        return self


class LoadedSearchRunObservationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: SearchRunObservationArtifact
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_bytes: int = Field(gt=0, le=MAX_SEARCH_QRELS_DATASET_BYTES)


class SearchQrelsBenchmarkReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["search-qrels-benchmark-receipt-v1"] = (
        SEARCH_QRELS_BENCHMARK_RECEIPT_SCHEMA_VERSION
    )
    qrels_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    corpus_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    annotation_guide_sha256: str = Field(pattern=_SHA256_PATTERN)
    adjudication_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_id: str
    execution_source: Literal["isolated_candidate_export", "offline_snapshot_index"]
    evaluation: SearchEvaluationReport
    threshold_approval_state: Literal["not_approved"] = "not_approved"
    release_decision: Literal["not_computable"] = "not_computable"
    quality_claim: Literal["not_established"] = "not_established"
    source_truth_review: Literal["not_performed"] = "not_performed"
    result_bodies_retained: Literal[False] = False


def load_search_run_observation_artifact(
    path: Path,
    *,
    expected_sha256: str,
    evaluated_at: datetime,
) -> LoadedSearchRunObservationArtifact:
    """Load one content-free frozen result artifact without following links."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise SearchQrelsDatasetError("evaluated_at must include a timezone")
    if not path.is_absolute():
        raise SearchQrelsDatasetError("run artifact path must be absolute")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise SearchQrelsDatasetError("run artifact expected SHA-256 is invalid")
    raw = _read_bundle_evidence(
        path.parent.resolve(strict=True),
        path.name,
        expected_sha256=expected_sha256,
        maximum_bytes=MAX_SEARCH_QRELS_DATASET_BYTES,
        field="run artifact",
    )
    parsed = _strict_json_artifact(
        raw,
        SearchRunObservationArtifact,
        "run artifact",
    )
    assert isinstance(parsed, SearchRunObservationArtifact)
    if parsed.completed_at > evaluated_at.astimezone(timezone.utc):
        raise SearchQrelsDatasetError("run artifact completion is in the future")
    return LoadedSearchRunObservationArtifact(
        artifact=parsed,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        artifact_bytes=len(raw),
    )


def evaluate_search_qrels_benchmark(
    qrels: LoadedSearchQrelsBundle,
    run: LoadedSearchRunObservationArtifact,
    *,
    evaluated_at: datetime,
    k_values: Sequence[int] = (1, 5, 10, 20, 100),
) -> SearchQrelsBenchmarkReceipt:
    """Evaluate exact bound artifacts without approving a quality threshold."""

    artifact = run.artifact
    dataset = qrels.dataset
    if (
        artifact.dataset_id != dataset.dataset_id
        or artifact.dataset_version != dataset.dataset_version
        or artifact.corpus_snapshot_id != dataset.corpus.corpus_snapshot_id
    ):
        raise SearchQrelsDatasetError("run artifact does not match qrels dataset and corpus")
    try:
        report = evaluate_search_run(
            dataset.queries,
            artifact.observations,
            k_values=k_values,
            evaluated_at=evaluated_at,
        )
    except ValueError as exc:
        raise SearchQrelsDatasetError("run observations do not exactly cover qrels queries") from exc
    return SearchQrelsBenchmarkReceipt(
        qrels_dataset_sha256=qrels.dataset_sha256,
        corpus_manifest_sha256=qrels.corpus_manifest_sha256,
        annotation_guide_sha256=qrels.annotation_guide_sha256,
        adjudication_artifact_sha256=qrels.adjudication_artifact_sha256,
        run_artifact_sha256=run.artifact_sha256,
        run_id=artifact.run_id,
        execution_source=artifact.execution_source,
        evaluation=report,
    )


__all__ = (
    "LoadedSearchRunObservationArtifact",
    "SEARCH_QRELS_BENCHMARK_RECEIPT_SCHEMA_VERSION",
    "SEARCH_QRELS_RUN_ARTIFACT_SCHEMA_VERSION",
    "SearchQrelsBenchmarkReceipt",
    "SearchRunObservationArtifact",
    "evaluate_search_qrels_benchmark",
    "load_search_run_observation_artifact",
)
