from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.features.search import (
    SearchAdjudicationEvidence,
    SearchAdjudicationArtifact,
    SearchAdjudicationDecision,
    SearchCorpusManifest,
    SearchCorpusSnapshotEvidence,
    SearchEvalProvenance,
    SearchEvalQuery,
    SearchQrel,
    SearchQrelsDataset,
    SearchQrelsDatasetError,
    load_search_qrels_bundle,
    load_search_qrels_dataset,
)


NOW = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64


def _dataset() -> SearchQrelsDataset:
    dataset_id = "globemind-search-human-gold"
    dataset_version = "2026.08.10-v1"
    corpus_snapshot_id = "isolated-corpus-snapshot-20260810"
    return SearchQrelsDataset(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        corpus=SearchCorpusSnapshotEvidence(
            corpus_snapshot_id=corpus_snapshot_id,
            corpus_sha256=SHA_A,
            document_count=100,
            cutoff="2026-08-09T00:00:00Z",
            manifest_locator="evidence/search/corpus-manifest.json",
            manifest_sha256=SHA_B,
            document_id_namespace="news",
        ),
        adjudication=SearchAdjudicationEvidence(
            annotation_guide_id="search-relevance-guide",
            annotation_guide_version="1.0.0",
            annotation_guide_locator="evidence/search/annotation-guide.md",
            annotation_guide_sha256=SHA_A,
            reviewer_ids=("reviewer:alpha001", "reviewer:beta0002"),
            adjudication_artifact_locator="evidence/search/adjudication.json",
            adjudication_artifact_sha256=SHA_B,
            agreement_method="krippendorff_alpha",
            agreement_value=0.75,
        ),
        queries=(
            SearchEvalQuery(
                query_id="expert-001",
                query="Red Sea shipping disruptions",
                language="en",
                country="YE",
                topic="maritime security",
                intent="find reporting relevant to shipping disruption research",
                qrels=[
                    SearchQrel(
                        document_id="news:42",
                        relevance_grade=3,
                        rationale="Directly addresses the adjudicated research intent.",
                    )
                ],
                provenance=SearchEvalProvenance(
                    dataset_id=dataset_id,
                    dataset_version=dataset_version,
                    corpus_snapshot_id=corpus_snapshot_id,
                    source_kind="adjudicated_annotation",
                    source_locator="evidence/search/adjudication.json",
                    created_at=NOW,
                    reviewer_evidence="sha256:" + SHA_B,
                ),
                judgment_tier="human_gold",
                review_state="adjudicated",
            ),
        ),
    )


def _write_dataset(path: Path, dataset: SearchQrelsDataset) -> str:
    raw = dataset.model_dump_json().encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _write_qrels_bundle(root: Path) -> tuple[Path, str, SearchQrelsDataset]:
    evidence_root = root / "evidence" / "search"
    evidence_root.mkdir(parents=True)
    guide = b"# Search relevance guide v1\nIndependent reviewers grade 0-3.\n"
    guide_path = evidence_root / "annotation-guide.md"
    guide_path.write_bytes(guide)
    guide_sha = hashlib.sha256(guide).hexdigest()

    corpus = SearchCorpusManifest(
        corpus_snapshot_id="isolated-corpus-snapshot-20260810",
        corpus_sha256=SHA_A,
        document_count=100,
        cutoff="2026-08-09T00:00:00Z",
        document_id_namespace="news",
    )
    corpus_path = evidence_root / "corpus-manifest.json"
    corpus_raw = corpus.model_dump_json().encode()
    corpus_path.write_bytes(corpus_raw)
    corpus_manifest_sha = hashlib.sha256(corpus_raw).hexdigest()

    rationale = "Directly addresses the adjudicated research intent."
    adjudication = SearchAdjudicationArtifact(
        dataset_id="globemind-search-human-gold",
        dataset_version="2026.08.10-v1",
        corpus_snapshot_id="isolated-corpus-snapshot-20260810",
        annotation_guide_id="search-relevance-guide",
        annotation_guide_version="1.0.0",
        annotation_guide_sha256=guide_sha,
        reviewer_ids=("reviewer:alpha001", "reviewer:beta0002"),
        agreement_method="krippendorff_alpha",
        agreement_value=0.75,
        completed_at=datetime(2026, 8, 10, 5, tzinfo=timezone.utc),
        decisions=(
            SearchAdjudicationDecision(
                query_id="expert-001",
                document_id="news:42",
                relevance_grade=3,
                rationale_sha256=hashlib.sha256(rationale.encode()).hexdigest(),
            ),
        ),
    )
    adjudication_path = evidence_root / "adjudication.json"
    adjudication_raw = adjudication.model_dump_json().encode()
    adjudication_path.write_bytes(adjudication_raw)
    adjudication_sha = hashlib.sha256(adjudication_raw).hexdigest()

    payload = _dataset().model_dump(mode="json")
    payload["corpus"]["manifest_sha256"] = corpus_manifest_sha
    payload["adjudication"]["annotation_guide_sha256"] = guide_sha
    payload["adjudication"]["adjudication_artifact_sha256"] = adjudication_sha
    payload["queries"][0]["provenance"]["reviewer_evidence"] = (
        "sha256:" + adjudication_sha
    )
    dataset = SearchQrelsDataset.model_validate(payload)
    dataset_path = root / "qrels.json"
    digest = _write_dataset(dataset_path, dataset)
    return dataset_path, digest, dataset


def test_human_gold_qrels_dataset_binds_corpus_and_adjudication() -> None:
    dataset = _dataset()

    assert dataset.quality_claim == "not_established"
    assert dataset.release_decision == "not_computable"
    assert dataset.source_truth_review == "not_performed"
    assert dataset.queries[0].judgment_tier == "human_gold"
    assert dataset.corpus.corpus_snapshot_id == (
        dataset.queries[0].provenance.corpus_snapshot_id
    )


def test_qrels_dataset_rejects_silver_drift_and_missing_rationale() -> None:
    payload = _dataset().model_dump(mode="json")
    payload["queries"][0]["judgment_tier"] = "silver"
    with pytest.raises(ValidationError, match="adjudicated human_gold"):
        SearchQrelsDataset.model_validate(payload)

    payload = _dataset().model_dump(mode="json")
    payload["queries"][0]["qrels"][0]["rationale"] = None
    with pytest.raises(ValidationError, match="requires a rationale"):
        SearchQrelsDataset.model_validate(payload)

    payload = _dataset().model_dump(mode="json")
    payload["queries"][0]["provenance"]["corpus_snapshot_id"] = "other"
    with pytest.raises(ValidationError, match="corpus snapshot"):
        SearchQrelsDataset.model_validate(payload)


def test_qrels_loader_requires_exact_hash_and_single_link(tmp_path: Path) -> None:
    artifact = tmp_path / "qrels.json"
    digest = _write_dataset(artifact, _dataset())

    loaded = load_search_qrels_dataset(artifact, expected_sha256=digest)
    assert loaded.artifact_sha256 == digest
    assert loaded.dataset.dataset_id == "globemind-search-human-gold"

    with pytest.raises(SearchQrelsDatasetError, match="absolute"):
        load_search_qrels_dataset(Path("relative-qrels.json"), expected_sha256=digest)

    with pytest.raises(SearchQrelsDatasetError, match="SHA-256 mismatch"):
        load_search_qrels_dataset(artifact, expected_sha256="f" * 64)

    hardlink = tmp_path / "qrels-hardlink.json"
    hardlink.hardlink_to(artifact)
    with pytest.raises(SearchQrelsDatasetError, match="single-link"):
        load_search_qrels_dataset(artifact, expected_sha256=digest)


def test_qrels_loader_rejects_duplicate_keys_and_non_finite(tmp_path: Path) -> None:
    for name, raw, pattern in (
        ("duplicate.json", b'{"schema_version":"a","schema_version":"b"}', "duplicate"),
        ("nan.json", b'{"value":NaN}', "non-finite"),
    ):
        artifact = tmp_path / name
        artifact.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        with pytest.raises(SearchQrelsDatasetError, match=pattern):
            load_search_qrels_dataset(artifact, expected_sha256=digest)


def test_qrels_bundle_verifies_corpus_guide_and_exact_adjudication(tmp_path: Path) -> None:
    path, digest, _dataset_value = _write_qrels_bundle(tmp_path)

    loaded = load_search_qrels_bundle(
        path,
        expected_sha256=digest,
        evaluated_at=NOW,
    )

    assert loaded.dataset_sha256 == digest
    assert loaded.verified_evidence_bytes > 0
    assert loaded.evidence_bodies_retained is False
    assert loaded.dataset.queries[0].review_state == "adjudicated"


def test_qrels_bundle_rejects_evidence_drift_and_adjudication_mismatch(
    tmp_path: Path,
) -> None:
    path, digest, _dataset_value = _write_qrels_bundle(tmp_path)
    (tmp_path / "evidence/search/annotation-guide.md").write_text(
        "tampered guide",
        encoding="utf-8",
    )
    with pytest.raises(SearchQrelsDatasetError, match="annotation guide SHA"):
        load_search_qrels_bundle(path, expected_sha256=digest, evaluated_at=NOW)

    other = tmp_path / "other"
    other.mkdir()
    path, digest, _dataset_value = _write_qrels_bundle(other)
    adjudication_path = other / "evidence/search/adjudication.json"
    payload = json.loads(adjudication_path.read_text(encoding="utf-8"))
    payload["decisions"][0]["relevance_grade"] = 1
    raw = json.dumps(payload, separators=(",", ":")).encode()
    adjudication_path.write_bytes(raw)
    dataset_payload = json.loads(path.read_text(encoding="utf-8"))
    dataset_payload["adjudication"]["adjudication_artifact_sha256"] = hashlib.sha256(raw).hexdigest()
    dataset_payload["queries"][0]["provenance"]["reviewer_evidence"] = (
        "sha256:" + hashlib.sha256(raw).hexdigest()
    )
    path.write_text(json.dumps(dataset_payload, separators=(",", ":")), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(SearchQrelsDatasetError, match="exactly bind qrels"):
        load_search_qrels_bundle(path, expected_sha256=digest, evaluated_at=NOW)
