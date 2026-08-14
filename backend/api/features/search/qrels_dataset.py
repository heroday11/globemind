"""Strict offline loader for externally adjudicated search qrels datasets."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evaluation import MAX_SEARCH_EVAL_QUERIES, SearchEvalQuery

SEARCH_QRELS_DATASET_SCHEMA_VERSION = "search-qrels-dataset-v1"
MAX_SEARCH_QRELS_DATASET_BYTES = 16 * 1024 * 1024
FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVIEWER_ID_RE = re.compile(r"^reviewer:[a-z0-9][a-z0-9._-]{7,63}$")


class SearchQrelsDatasetError(RuntimeError):
    """The qrels artifact cannot enter offline evaluation."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SearchQrelsDatasetError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


class SearchCorpusSnapshotEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_snapshot_id: str = Field(min_length=1, max_length=200)
    corpus_sha256: str
    document_count: int = Field(gt=0, le=100_000_000, strict=True)
    cutoff: str = Field(min_length=1, max_length=80)
    manifest_locator: str = Field(min_length=1, max_length=500)
    manifest_sha256: str
    document_id_namespace: str = Field(min_length=1, max_length=120)

    @field_validator("corpus_sha256", "manifest_sha256")
    @classmethod
    def sha256_is_canonical(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("SHA-256 must be lowercase hexadecimal")
        return value


class SearchAdjudicationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    annotation_guide_id: str = Field(min_length=1, max_length=120)
    annotation_guide_version: str = Field(min_length=1, max_length=120)
    annotation_guide_locator: str = Field(min_length=1, max_length=500)
    annotation_guide_sha256: str
    reviewer_ids: tuple[str, ...] = Field(min_length=2, max_length=20)
    adjudication_state: Literal["completed"] = "completed"
    adjudication_artifact_locator: str = Field(min_length=1, max_length=500)
    adjudication_artifact_sha256: str
    agreement_method: Literal[
        "cohen_kappa", "fleiss_kappa", "krippendorff_alpha", "percent_agreement"
    ]
    agreement_value: float = Field(ge=-1, le=1, allow_inf_nan=False)

    @field_validator("annotation_guide_sha256", "adjudication_artifact_sha256")
    @classmethod
    def sha256_is_canonical(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("SHA-256 must be lowercase hexadecimal")
        return value

    @model_validator(mode="after")
    def reviewer_evidence_is_bounded(self) -> "SearchAdjudicationEvidence":
        if len(set(self.reviewer_ids)) != len(self.reviewer_ids):
            raise ValueError("reviewer IDs must be unique")
        if any(_REVIEWER_ID_RE.fullmatch(value) is None for value in self.reviewer_ids):
            raise ValueError("reviewer IDs must be stable pseudonymous identifiers")
        return self


class SearchQrelsDataset(BaseModel):
    """Human-gold dataset contract; it contains IDs and judgments, not documents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["search-qrels-dataset-v1"] = (
        SEARCH_QRELS_DATASET_SCHEMA_VERSION
    )
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    dataset_version: str = Field(min_length=1, max_length=120)
    corpus: SearchCorpusSnapshotEvidence
    adjudication: SearchAdjudicationEvidence
    queries: tuple[SearchEvalQuery, ...] = Field(
        min_length=1,
        max_length=MAX_SEARCH_EVAL_QUERIES,
    )
    source_truth_review: Literal["not_performed"] = "not_performed"
    relevance_scope: Literal["query_document_relevance_only"] = (
        "query_document_relevance_only"
    )
    threshold_approval_state: Literal["not_approved"] = "not_approved"
    quality_claim: Literal["not_established"] = "not_established"
    release_decision: Literal["not_computable"] = "not_computable"

    @model_validator(mode="after")
    def queries_bind_exact_dataset_evidence(self) -> "SearchQrelsDataset":
        query_ids = [query.query_id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query IDs must be unique")
        for query in self.queries:
            provenance = query.provenance
            if query.judgment_tier != "human_gold" or query.review_state != "adjudicated":
                raise ValueError("qrels dataset queries must be adjudicated human_gold")
            if provenance.source_kind != "adjudicated_annotation":
                raise ValueError("qrels provenance must be adjudicated_annotation")
            if provenance.dataset_id != self.dataset_id:
                raise ValueError("query dataset_id does not match dataset")
            if provenance.dataset_version != self.dataset_version:
                raise ValueError("query dataset_version does not match dataset")
            if provenance.corpus_snapshot_id != self.corpus.corpus_snapshot_id:
                raise ValueError("query corpus snapshot does not match dataset")
            if not query.qrels:
                raise ValueError("every adjudicated query must contain qrels")
            if any(not qrel.rationale for qrel in query.qrels):
                raise ValueError("every adjudicated qrel requires a rationale")
        return self


@dataclass(frozen=True)
class LoadedSearchQrelsDataset:
    dataset: SearchQrelsDataset
    artifact_sha256: str
    artifact_bytes: int


def load_search_qrels_dataset(
    path: Path,
    *,
    expected_sha256: str,
) -> LoadedSearchQrelsDataset:
    """Load one bounded local JSON artifact without following links."""

    if _SHA256_RE.fullmatch(expected_sha256) is None:
        raise SearchQrelsDatasetError("expected SHA-256 must be lowercase hexadecimal")
    requested = Path(path)
    if not requested.is_absolute():
        raise SearchQrelsDatasetError("qrels path must be absolute")
    candidate = Path(os.path.normpath(os.path.abspath(os.fspath(requested))))
    if candidate == FORBIDDEN_RELEASE_ROOT or FORBIDDEN_RELEASE_ROOT in candidate.parents:
        raise SearchQrelsDatasetError("qrels path cannot use a production release")
    probe = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        probe = probe / part
        if probe.is_symlink():
            raise SearchQrelsDatasetError("qrels path cannot contain symlinks")
    try:
        stat_before = candidate.stat()
    except OSError as exc:
        raise SearchQrelsDatasetError("qrels artifact is unavailable") from exc
    if not candidate.is_file() or stat_before.st_nlink != 1:
        raise SearchQrelsDatasetError("qrels artifact must be a single-link file")
    if stat_before.st_size <= 0 or stat_before.st_size > MAX_SEARCH_QRELS_DATASET_BYTES:
        raise SearchQrelsDatasetError("qrels artifact size is outside the bounded range")
    try:
        raw = candidate.read_bytes()
        stat_after = candidate.stat()
    except OSError as exc:
        raise SearchQrelsDatasetError("qrels artifact could not be read") from exc
    if (
        stat_before.st_dev,
        stat_before.st_ino,
        stat_before.st_size,
        stat_before.st_mtime_ns,
    ) != (
        stat_after.st_dev,
        stat_after.st_ino,
        stat_after.st_size,
        stat_after.st_mtime_ns,
    ):
        raise SearchQrelsDatasetError("qrels artifact changed while being read")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise SearchQrelsDatasetError("qrels artifact SHA-256 mismatch")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                SearchQrelsDatasetError(f"non-finite JSON number: {value}")
            ),
        )
        dataset = SearchQrelsDataset.model_validate(payload)
    except SearchQrelsDatasetError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SearchQrelsDatasetError("qrels artifact failed strict validation") from exc
    return LoadedSearchQrelsDataset(
        dataset=dataset,
        artifact_sha256=digest,
        artifact_bytes=len(raw),
    )


class SearchCorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["search-corpus-manifest-v1"] = "search-corpus-manifest-v1"
    corpus_snapshot_id: str = Field(min_length=1, max_length=200)
    corpus_sha256: str
    document_count: int = Field(gt=0, le=100_000_000, strict=True)
    cutoff: str = Field(min_length=1, max_length=80)
    document_id_namespace: str = Field(min_length=1, max_length=120)

    @field_validator("corpus_sha256")
    @classmethod
    def corpus_hash_is_canonical(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("corpus SHA-256 is invalid")
        return value


class SearchAdjudicationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    document_id: str = Field(min_length=1, max_length=240)
    relevance_grade: int = Field(ge=0, le=3, strict=True)
    rationale_sha256: str

    @field_validator("rationale_sha256")
    @classmethod
    def rationale_hash_is_canonical(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("rationale SHA-256 is invalid")
        return value


class SearchAdjudicationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["search-adjudication-evidence-v1"] = (
        "search-adjudication-evidence-v1"
    )
    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_version: str = Field(min_length=1, max_length=120)
    corpus_snapshot_id: str = Field(min_length=1, max_length=200)
    annotation_guide_id: str = Field(min_length=1, max_length=120)
    annotation_guide_version: str = Field(min_length=1, max_length=120)
    annotation_guide_sha256: str
    reviewer_ids: tuple[str, ...] = Field(min_length=2, max_length=20)
    agreement_method: Literal[
        "cohen_kappa", "fleiss_kappa", "krippendorff_alpha", "percent_agreement"
    ]
    agreement_value: float = Field(ge=-1, le=1, allow_inf_nan=False)
    completed_at: datetime
    decisions: tuple[SearchAdjudicationDecision, ...] = Field(
        min_length=1,
        max_length=2_000_000,
    )

    @field_validator("annotation_guide_sha256")
    @classmethod
    def guide_hash_is_canonical(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("annotation guide SHA-256 is invalid")
        return value

    @field_validator("completed_at")
    @classmethod
    def completed_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("completed_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def reviewers_and_decisions_are_unique(self) -> "SearchAdjudicationArtifact":
        if len(self.reviewer_ids) != len(set(self.reviewer_ids)) or any(
            _REVIEWER_ID_RE.fullmatch(value) is None for value in self.reviewer_ids
        ):
            raise ValueError("reviewer IDs are invalid or duplicated")
        keys = [(item.query_id, item.document_id) for item in self.decisions]
        if len(keys) != len(set(keys)):
            raise ValueError("adjudication decisions must be unique")
        return self


@dataclass(frozen=True)
class LoadedSearchQrelsBundle:
    dataset: SearchQrelsDataset
    dataset_sha256: str
    corpus_manifest_sha256: str
    annotation_guide_sha256: str
    adjudication_artifact_sha256: str
    verified_evidence_bytes: int
    evidence_bodies_retained: bool = False


def _relative_evidence_locator(value: str, field: str) -> PurePosixPath:
    if not isinstance(value, str) or value != value.strip() or "\\" in value:
        raise SearchQrelsDatasetError(f"{field} must be a normalized relative path")
    locator = PurePosixPath(value)
    if locator.is_absolute() or not locator.parts or "." in locator.parts or ".." in locator.parts:
        raise SearchQrelsDatasetError(f"{field} must be a normalized relative path")
    if any(part in {"releases", "current", "previous", "rejected"} for part in locator.parts):
        raise SearchQrelsDatasetError(f"{field} crosses the release boundary")
    return locator


def _read_bundle_evidence(
    root: Path,
    locator: str,
    *,
    expected_sha256: str,
    maximum_bytes: int,
    field: str,
) -> bytes:
    normalized_root = Path(os.path.abspath(os.path.normpath(root)))
    if normalized_root == FORBIDDEN_RELEASE_ROOT or FORBIDDEN_RELEASE_ROOT in normalized_root.parents:
        raise SearchQrelsDatasetError(f"{field} cannot use a production release")
    relative = _relative_evidence_locator(locator, field)
    candidate = (normalized_root / relative).resolve(strict=False)
    try:
        candidate.relative_to(normalized_root)
    except ValueError as exc:
        raise SearchQrelsDatasetError(f"{field} escapes the qrels bundle") from exc
    probe = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        probe /= part
        if probe.is_symlink():
            raise SearchQrelsDatasetError(f"{field} cannot contain symlinks")
    try:
        before = candidate.stat()
        if not candidate.is_file() or before.st_nlink != 1:
            raise SearchQrelsDatasetError(f"{field} must be a single-link file")
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            raise SearchQrelsDatasetError(f"{field} exceeds its byte boundary")
        body = candidate.read_bytes()
        after = candidate.stat()
    except SearchQrelsDatasetError:
        raise
    except OSError as exc:
        raise SearchQrelsDatasetError(f"{field} is unavailable") from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise SearchQrelsDatasetError(f"{field} changed while being read")
    if len(body) != before.st_size or hashlib.sha256(body).hexdigest() != expected_sha256:
        raise SearchQrelsDatasetError(f"{field} SHA-256 mismatch")
    return body


def _strict_json_artifact(raw: bytes, model: type[BaseModel], field: str) -> BaseModel:
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                SearchQrelsDatasetError(f"non-finite JSON number: {value}")
            ),
        )
        return model.model_validate(payload)
    except SearchQrelsDatasetError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SearchQrelsDatasetError(f"{field} failed strict validation") from exc


def load_search_qrels_bundle(
    path: Path,
    *,
    expected_sha256: str,
    evaluated_at: datetime,
) -> LoadedSearchQrelsBundle:
    """Verify qrels plus corpus, guide, and adjudication evidence artifacts."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise SearchQrelsDatasetError("evaluated_at must include a timezone")
    loaded = load_search_qrels_dataset(path, expected_sha256=expected_sha256)
    root = path.parent.resolve(strict=True)
    dataset = loaded.dataset
    corpus_raw = _read_bundle_evidence(
        root,
        dataset.corpus.manifest_locator,
        expected_sha256=dataset.corpus.manifest_sha256,
        maximum_bytes=4 * 1024 * 1024,
        field="corpus manifest",
    )
    corpus = _strict_json_artifact(corpus_raw, SearchCorpusManifest, "corpus manifest")
    assert isinstance(corpus, SearchCorpusManifest)
    if (
        corpus.corpus_snapshot_id != dataset.corpus.corpus_snapshot_id
        or corpus.corpus_sha256 != dataset.corpus.corpus_sha256
        or corpus.document_count != dataset.corpus.document_count
        or corpus.cutoff != dataset.corpus.cutoff
        or corpus.document_id_namespace != dataset.corpus.document_id_namespace
    ):
        raise SearchQrelsDatasetError("corpus manifest does not match qrels dataset")

    guide_raw = _read_bundle_evidence(
        root,
        dataset.adjudication.annotation_guide_locator,
        expected_sha256=dataset.adjudication.annotation_guide_sha256,
        maximum_bytes=2 * 1024 * 1024,
        field="annotation guide",
    )
    try:
        guide_text = guide_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SearchQrelsDatasetError("annotation guide must be UTF-8") from exc
    if not guide_text.strip():
        raise SearchQrelsDatasetError("annotation guide must not be empty")

    adjudication_raw = _read_bundle_evidence(
        root,
        dataset.adjudication.adjudication_artifact_locator,
        expected_sha256=dataset.adjudication.adjudication_artifact_sha256,
        maximum_bytes=16 * 1024 * 1024,
        field="adjudication artifact",
    )
    adjudication = _strict_json_artifact(
        adjudication_raw,
        SearchAdjudicationArtifact,
        "adjudication artifact",
    )
    assert isinstance(adjudication, SearchAdjudicationArtifact)
    evidence = dataset.adjudication
    if (
        adjudication.dataset_id != dataset.dataset_id
        or adjudication.dataset_version != dataset.dataset_version
        or adjudication.corpus_snapshot_id != dataset.corpus.corpus_snapshot_id
        or adjudication.annotation_guide_id != evidence.annotation_guide_id
        or adjudication.annotation_guide_version != evidence.annotation_guide_version
        or adjudication.annotation_guide_sha256 != evidence.annotation_guide_sha256
        or adjudication.reviewer_ids != evidence.reviewer_ids
        or adjudication.agreement_method != evidence.agreement_method
        or adjudication.agreement_value != evidence.agreement_value
    ):
        raise SearchQrelsDatasetError("adjudication artifact does not match qrels evidence")
    if adjudication.completed_at > evaluated_at.astimezone(timezone.utc):
        raise SearchQrelsDatasetError("adjudication completion is in the future")
    expected_decisions = {
        (query.query_id, qrel.document_id): (
            qrel.relevance_grade,
            hashlib.sha256(str(qrel.rationale).encode("utf-8")).hexdigest(),
        )
        for query in dataset.queries
        for qrel in query.qrels
    }
    observed_decisions = {
        (decision.query_id, decision.document_id): (
            decision.relevance_grade,
            decision.rationale_sha256,
        )
        for decision in adjudication.decisions
    }
    if observed_decisions != expected_decisions:
        raise SearchQrelsDatasetError("adjudication decisions do not exactly bind qrels")
    if any(
        query.provenance.source_locator != evidence.adjudication_artifact_locator
        or query.provenance.reviewer_evidence
        != f"sha256:{evidence.adjudication_artifact_sha256}"
        for query in dataset.queries
    ):
        raise SearchQrelsDatasetError("query provenance does not bind adjudication artifact")
    total = len(corpus_raw) + len(guide_raw) + len(adjudication_raw)
    if total > MAX_SEARCH_QRELS_DATASET_BYTES:
        raise SearchQrelsDatasetError("qrels evidence bundle exceeds total byte boundary")
    return LoadedSearchQrelsBundle(
        dataset=dataset,
        dataset_sha256=loaded.artifact_sha256,
        corpus_manifest_sha256=dataset.corpus.manifest_sha256,
        annotation_guide_sha256=evidence.annotation_guide_sha256,
        adjudication_artifact_sha256=evidence.adjudication_artifact_sha256,
        verified_evidence_bytes=total,
    )


__all__ = (
    "LoadedSearchQrelsBundle",
    "LoadedSearchQrelsDataset",
    "MAX_SEARCH_QRELS_DATASET_BYTES",
    "SEARCH_QRELS_DATASET_SCHEMA_VERSION",
    "SearchAdjudicationArtifact",
    "SearchAdjudicationDecision",
    "SearchAdjudicationEvidence",
    "SearchCorpusManifest",
    "SearchCorpusSnapshotEvidence",
    "SearchQrelsDataset",
    "SearchQrelsDatasetError",
    "load_search_qrels_bundle",
    "load_search_qrels_dataset",
)
