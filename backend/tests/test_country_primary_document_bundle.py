from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.features.authoritative_data import (
    CountryDocumentClaimCitation,
    CountryDocumentClaimRecord,
    CountryDocumentGovernanceEvidence,
    CountryDocumentIdentity,
    CountryDocumentSectionAnchor,
    CountryDocumentTemporalEvidence,
    CountryDocumentTextEvidence,
    CountryDocumentVersionEvidence,
    CountryPilotDocumentRequirement,
    CountryPrimaryDocumentBundle,
    CountryPrimaryDocumentBundleError,
    CountryPrimaryDocumentClaimPlan,
    CountryPrimaryDocumentPilotPlan,
    CountryPrimaryDocumentRecord,
    evaluate_country_primary_document_claims,
    evaluate_country_primary_document_readiness,
    load_country_primary_document_bundle,
    load_country_primary_document_claim_plan,
    load_country_primary_document_pilot_plan,
)

NOW = datetime(2026, 8, 10, 6, 15, tzinfo=timezone.utc)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bundle(root: Path) -> CountryPrimaryDocumentBundle:
    content = "Article 1. Public power is bounded.\nArticle 2. Review is required.\n".encode()
    license_body = b"Official reuse terms reviewed by the country-data team.\n"
    (root / "documents").mkdir(exist_ok=True)
    (root / "licenses").mkdir(exist_ok=True)
    (root / "documents" / "constitution.txt").write_bytes(content)
    (root / "licenses" / "terms.txt").write_bytes(license_body)
    first_end = content.index(b"\n")
    content_sha = _sha(content)
    document_id = f"urn:globemind:country-document:aa:{content_sha}"
    return CountryPrimaryDocumentBundle(
        bundle_id="pilot-country-primary-documents",
        bundle_version="2026.08.10-v1",
        pilot_country_codes=("AA",),
        documents=(
            CountryPrimaryDocumentRecord(
                document_id=document_id,
                identity=CountryDocumentIdentity(
                    country_code="AA",
                    issuing_authority="Constitutional Assembly",
                    official_identifier="CONST-001",
                    document_kind="constitution",
                    original_title="Constitutional Text",
                ),
                text=CountryDocumentTextEvidence(
                    original_language="en",
                    official_locator="https://official.example/constitution.txt",
                    content_locator="documents/constitution.txt",
                    content_sha256=content_sha,
                    section_anchors=(
                        CountryDocumentSectionAnchor(
                            anchor_id="article-1",
                            label="Article 1",
                            byte_start=0,
                            byte_end=first_end,
                            content_sha256=_sha(content[:first_end]),
                        ),
                        CountryDocumentSectionAnchor(
                            anchor_id="article-2",
                            label="Article 2",
                            byte_start=first_end + 1,
                            byte_end=len(content) - 1,
                            content_sha256=_sha(content[first_end + 1 : -1]),
                        ),
                    ),
                ),
                temporal=CountryDocumentTemporalEvidence(
                    issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    effective_from=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    effective_until=None,
                    status_as_of=datetime(2026, 8, 8, tzinfo=timezone.utc),
                ),
                version=CountryDocumentVersionEvidence(
                    version_identifier="original-2026"
                ),
                governance=CountryDocumentGovernanceEvidence(
                    retrieved_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
                    source_cutoff=datetime(2026, 8, 9, tzinfo=timezone.utc),
                    license_state="verified",
                    license_artifact_locator="licenses/terms.txt",
                    license_artifact_sha256=_sha(license_body),
                    owner_identifier="owner:country001",
                    reviewer_identifier="reviewer:legal001",
                    reviewed_at=datetime(2026, 8, 9, 1, tzinfo=timezone.utc),
                    review_expires_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
                ),
            ),
        ),
    )


def _write_manifest(root: Path, bundle: CountryPrimaryDocumentBundle) -> tuple[Path, str]:
    manifest = root / "manifest.json"
    raw = bundle.model_dump_json().encode()
    manifest.write_bytes(raw)
    return manifest, _sha(raw)


def _write_pilot_plan(
    root: Path,
    *,
    country_code: str = "AA",
    required_kinds: tuple[str, ...] = ("constitution",),
    expires_at: datetime = datetime(2026, 9, 10, tzinfo=timezone.utc),
) -> tuple[Path, str]:
    plan = CountryPrimaryDocumentPilotPlan(
        plan_id="country-primary-pilot-plan",
        plan_version="2026.08.10-v1",
        requirements=(
            CountryPilotDocumentRequirement(
                country_code=country_code,
                required_document_kinds=required_kinds,
                minimum_documents_per_kind=1,
            ),
        ),
        owner_identifier="owner:country001",
        reviewer_identifier="reviewer:pilot001",
        approved_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        expires_at=expires_at,
    )
    path = root / "pilot-plan.json"
    raw = plan.model_dump_json().encode()
    path.write_bytes(raw)
    return path, _sha(raw)


def _write_claim_plan(
    root: Path,
    *,
    bundle: CountryPrimaryDocumentBundle,
    bundle_manifest_sha256: str,
    disposition: str = "supported_for_draft",
    valid_at: datetime = datetime(2026, 8, 8, tzinfo=timezone.utc),
    anchor_id: str = "article-1",
) -> tuple[Path, str]:
    statement_sha = "d" * 64
    document_id = bundle.documents[0].document_id
    citations = [
        CountryDocumentClaimCitation(
            document_id=document_id,
            anchor_id=anchor_id,
            citation_role="supporting" if disposition != "not_supported" else "opposing",
            interpretation_scope="direct_text",
        )
    ]
    if disposition == "unresolved":
        citations.append(
            CountryDocumentClaimCitation(
                document_id=document_id,
                anchor_id="article-2",
                citation_role="opposing",
                interpretation_scope="direct_text",
            )
        )
    plan = CountryPrimaryDocumentClaimPlan(
        plan_id="country-claim-plan",
        plan_version="2026.08.10-v1",
        bundle_id=bundle.bundle_id,
        bundle_version=bundle.bundle_version,
        bundle_manifest_sha256=bundle_manifest_sha256,
        claims=(
            CountryDocumentClaimRecord(
                claim_id=f"urn:globemind:country-claim:aa:{statement_sha}",
                country_code="AA",
                statement_sha256=statement_sha,
                valid_at=valid_at,
                disposition=disposition,
                citations=tuple(citations),
            ),
        ),
        owner_identifier="owner:country001",
        reviewer_identifier="reviewer:claim001",
        reviewed_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        review_expires_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )
    path = root / "claim-plan.json"
    raw = plan.model_dump_json().encode()
    path.write_bytes(raw)
    return path, _sha(raw)


def test_bundle_verifies_content_anchors_license_and_review_without_retaining_bodies(
    tmp_path: Path,
) -> None:
    manifest, digest = _write_manifest(tmp_path, _bundle(tmp_path))

    loaded = load_country_primary_document_bundle(
        manifest,
        expected_sha256=digest,
        evaluated_at=NOW,
    )

    assert loaded.bundle.pilot_country_codes == ("AA",)
    assert loaded.verified_artifact_count == 2
    assert loaded.verified_artifact_bytes > 0
    assert loaded.document_bodies_retained is False
    assert not hasattr(loaded, "document_bodies")
    assert loaded.bundle.documents[0].publication_state == (
        "intake_verified_not_published"
    )


def test_bundle_rejects_content_anchor_and_license_drift(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    manifest, digest = _write_manifest(tmp_path, bundle)
    (tmp_path / "documents" / "constitution.txt").write_text(
        "Tampered official text.\n",
        encoding="utf-8",
    )
    with pytest.raises(CountryPrimaryDocumentBundleError, match="content SHA"):
        load_country_primary_document_bundle(
            manifest,
            expected_sha256=digest,
            evaluated_at=NOW,
        )

    bundle = _bundle(tmp_path)
    payload = bundle.model_dump(mode="json")
    payload["documents"][0]["text"]["section_anchors"][0]["content_sha256"] = "f" * 64
    drifted = CountryPrimaryDocumentBundle.model_validate(payload)
    manifest, digest = _write_manifest(tmp_path, drifted)
    with pytest.raises(CountryPrimaryDocumentBundleError, match="anchor SHA"):
        load_country_primary_document_bundle(
            manifest,
            expected_sha256=digest,
            evaluated_at=NOW,
        )

    bundle = _bundle(tmp_path)
    (tmp_path / "licenses" / "terms.txt").write_bytes(b"different terms")
    manifest, digest = _write_manifest(tmp_path, bundle)
    with pytest.raises(CountryPrimaryDocumentBundleError, match="license artifact SHA"):
        load_country_primary_document_bundle(
            manifest,
            expected_sha256=digest,
            evaluated_at=NOW,
        )


def test_bundle_rejects_expired_review_and_open_relationships(tmp_path: Path) -> None:
    payload = _bundle(tmp_path).model_dump(mode="json")
    payload["documents"][0]["governance"]["review_expires_at"] = (
        "2026-08-10T06:00:00Z"
    )
    expired = CountryPrimaryDocumentBundle.model_validate(payload)
    manifest, digest = _write_manifest(tmp_path, expired)
    with pytest.raises(CountryPrimaryDocumentBundleError, match="review is expired"):
        load_country_primary_document_bundle(
            manifest,
            expected_sha256=digest,
            evaluated_at=NOW,
        )

    payload = _bundle(tmp_path).model_dump(mode="json")
    payload["documents"][0]["version"]["amends"] = [
        "urn:globemind:country-document:aa:" + "f" * 64
    ]
    with pytest.raises(ValidationError, match="outside the bundle"):
        CountryPrimaryDocumentBundle.model_validate(payload)


def test_bundle_rejects_unsafe_locator_links_and_owner_self_review(tmp_path: Path) -> None:
    payload = _bundle(tmp_path).model_dump(mode="json")
    payload["documents"][0]["text"]["official_locator"] = (
        "https://user:secret@official.example/document?token=secret"
    )
    with pytest.raises(ValidationError, match="credential-free HTTPS"):
        CountryPrimaryDocumentBundle.model_validate(payload)

    payload = _bundle(tmp_path).model_dump(mode="json")
    payload["documents"][0]["governance"]["reviewer_identifier"] = (
        payload["documents"][0]["governance"]["owner_identifier"]
    )
    with pytest.raises(ValidationError, match="distinct"):
        CountryPrimaryDocumentBundle.model_validate(payload)

    bundle = _bundle(tmp_path)
    manifest, digest = _write_manifest(tmp_path, bundle)
    content = tmp_path / "documents" / "constitution.txt"
    hardlink = tmp_path / "documents" / "hardlink.txt"
    hardlink.hardlink_to(content)
    with pytest.raises(CountryPrimaryDocumentBundleError, match="single-link"):
        load_country_primary_document_bundle(
            manifest,
            expected_sha256=digest,
            evaluated_at=NOW,
        )


def test_bundle_requires_absolute_manifest_and_exact_manifest_hash(tmp_path: Path) -> None:
    manifest, digest = _write_manifest(tmp_path, _bundle(tmp_path))
    with pytest.raises(CountryPrimaryDocumentBundleError, match="absolute"):
        load_country_primary_document_bundle(
            Path("manifest.json"),
            expected_sha256=digest,
            evaluated_at=NOW,
        )
    with pytest.raises(CountryPrimaryDocumentBundleError, match="manifest SHA"):
        load_country_primary_document_bundle(
            manifest,
            expected_sha256="f" * 64,
            evaluated_at=NOW,
        )


def test_approved_pilot_plan_receipt_binds_bundle_without_publishing_facts(
    tmp_path: Path,
) -> None:
    manifest, manifest_sha = _write_manifest(tmp_path, _bundle(tmp_path))
    bundle = load_country_primary_document_bundle(
        manifest,
        expected_sha256=manifest_sha,
        evaluated_at=NOW,
    )
    plan_path, plan_sha = _write_pilot_plan(tmp_path)
    plan = load_country_primary_document_pilot_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )

    receipt = evaluate_country_primary_document_readiness(
        plan,
        bundle,
        evaluated_at=NOW,
    )

    assert receipt.plan_sha256 == plan_sha
    assert receipt.bundle_manifest_sha256 == manifest_sha
    assert receipt.intake_coverage_state == "requirements_met_not_published"
    assert receipt.countries[0].verified_license_counts == {"constitution": 1}
    assert receipt.facts_published is False
    assert receipt.public_catalog_mutated is False
    assert receipt.publication_decision == "not_computable"
    assert receipt.candidate_acceptance == "not_performed"


def test_pilot_readiness_reports_missing_kinds_and_rejects_scope_or_expiry(
    tmp_path: Path,
) -> None:
    manifest, manifest_sha = _write_manifest(tmp_path, _bundle(tmp_path))
    bundle = load_country_primary_document_bundle(
        manifest,
        expected_sha256=manifest_sha,
        evaluated_at=NOW,
    )
    plan_path, plan_sha = _write_pilot_plan(
        tmp_path,
        required_kinds=("constitution", "statute"),
    )
    plan = load_country_primary_document_pilot_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )
    receipt = evaluate_country_primary_document_readiness(
        plan,
        bundle,
        evaluated_at=NOW,
    )
    assert receipt.intake_coverage_state == "requirements_not_met"
    assert receipt.countries[0].missing_document_kinds == ("statute",)

    plan_path, plan_sha = _write_pilot_plan(tmp_path, country_code="BB")
    mismatched = load_country_primary_document_pilot_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )
    with pytest.raises(CountryPrimaryDocumentBundleError, match="country scope"):
        evaluate_country_primary_document_readiness(
            mismatched,
            bundle,
            evaluated_at=NOW,
        )

    plan_path, plan_sha = _write_pilot_plan(
        tmp_path,
        expires_at=datetime(2026, 8, 10, 6, tzinfo=timezone.utc),
    )
    with pytest.raises(CountryPrimaryDocumentBundleError, match="approval is expired"):
        load_country_primary_document_pilot_plan(
            plan_path,
            expected_sha256=plan_sha,
            evaluated_at=NOW,
        )


def test_country_claim_plan_binds_verified_anchors_without_publishing_claim_text(
    tmp_path: Path,
) -> None:
    raw_bundle = _bundle(tmp_path)
    manifest, manifest_sha = _write_manifest(tmp_path, raw_bundle)
    bundle = load_country_primary_document_bundle(
        manifest,
        expected_sha256=manifest_sha,
        evaluated_at=NOW,
    )
    plan_path, plan_sha = _write_claim_plan(
        tmp_path,
        bundle=raw_bundle,
        bundle_manifest_sha256=manifest_sha,
    )
    plan = load_country_primary_document_claim_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )

    receipt = evaluate_country_primary_document_claims(
        plan,
        bundle,
        evaluated_at=NOW,
    )

    assert receipt.plan_sha256 == plan_sha
    assert receipt.bundle_manifest_sha256 == manifest_sha
    assert receipt.claims[0].readiness_state == (
        "citation_structure_ready_not_semantically_verified"
    )
    assert receipt.claims[0].supporting_citation_count == 1
    assert receipt.semantic_entailment == "not_verified_by_loader"
    assert receipt.source_truth == "not_verified_by_loader"
    assert receipt.facts_published is False
    assert receipt.public_catalog_mutated is False
    assert not hasattr(receipt.claims[0], "statement")


def test_country_claim_plan_preserves_conflict_license_and_temporal_blockers(
    tmp_path: Path,
) -> None:
    raw_bundle = _bundle(tmp_path)
    manifest, manifest_sha = _write_manifest(tmp_path, raw_bundle)
    bundle = load_country_primary_document_bundle(
        manifest,
        expected_sha256=manifest_sha,
        evaluated_at=NOW,
    )
    plan_path, plan_sha = _write_claim_plan(
        tmp_path,
        bundle=raw_bundle,
        bundle_manifest_sha256=manifest_sha,
        disposition="unresolved",
    )
    plan = load_country_primary_document_claim_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )
    receipt = evaluate_country_primary_document_claims(
        plan,
        bundle,
        evaluated_at=NOW,
    )
    assert receipt.claims[0].readiness_state == "unresolved_conflict"
    assert receipt.claims[0].supporting_citation_count == 1
    assert receipt.claims[0].opposing_citation_count == 1

    payload = raw_bundle.model_dump(mode="json")
    payload["documents"][0]["governance"]["license_state"] = "restricted"
    restricted_bundle = CountryPrimaryDocumentBundle.model_validate(payload)
    manifest, manifest_sha = _write_manifest(tmp_path, restricted_bundle)
    loaded_restricted = load_country_primary_document_bundle(
        manifest,
        expected_sha256=manifest_sha,
        evaluated_at=NOW,
    )
    plan_path, plan_sha = _write_claim_plan(
        tmp_path,
        bundle=restricted_bundle,
        bundle_manifest_sha256=manifest_sha,
    )
    restricted_plan = load_country_primary_document_claim_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )
    assert evaluate_country_primary_document_claims(
        restricted_plan,
        loaded_restricted,
        evaluated_at=NOW,
    ).claims[0].readiness_state == "license_blocked"

    plan_path, plan_sha = _write_claim_plan(
        tmp_path,
        bundle=restricted_bundle,
        bundle_manifest_sha256=manifest_sha,
        valid_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    future_plan = load_country_primary_document_claim_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )
    with pytest.raises(CountryPrimaryDocumentBundleError, match="temporal evidence"):
        evaluate_country_primary_document_claims(
            future_plan,
            loaded_restricted,
            evaluated_at=NOW,
        )

    plan_path, plan_sha = _write_claim_plan(
        tmp_path,
        bundle=restricted_bundle,
        bundle_manifest_sha256=manifest_sha,
        anchor_id="missing-anchor",
    )
    missing_anchor_plan = load_country_primary_document_claim_plan(
        plan_path,
        expected_sha256=plan_sha,
        evaluated_at=NOW,
    )
    with pytest.raises(CountryPrimaryDocumentBundleError, match="anchor"):
        evaluate_country_primary_document_claims(
            missing_anchor_plan,
            loaded_restricted,
            evaluated_at=NOW,
        )
