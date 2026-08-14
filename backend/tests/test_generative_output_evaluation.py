from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.features.assistant import (
    ReportAssuranceError,
    assure_generated_report,
    build_report_source_inventory,
    finalize_interactive_output,
)
from api.features.model_assurance import (
    GENERATIVE_EVALUATION_SCHEMA_VERSION,
    ClaimCoverageAssessment,
    GenerativeEvaluationManifest,
    audit_generative_evaluation_surface_sources,
    build_generative_evaluation_surface_inventory,
    evaluate_generative_outputs,
)
from api.routes import model_assurance
from api.services.auth import get_current_user_required


ROOT = Path(__file__).resolve().parents[2]


def _observation(
    case_id: str,
    surface_id: str,
    scenario_id: str,
    disposition: str,
    *reason_codes: str,
    claim_records: list[dict[str, object]] | None = None,
    citation_export_schema_version: str | None = None,
    artifact_sha256: str | None = None,
    citation_inventory_ids: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "case_id": case_id,
        "surface_id": surface_id,
        "scenario_id": scenario_id,
        "declared_disposition": disposition,
        "reason_codes": list(reason_codes),
        "claim_records": claim_records or [],
    }
    if citation_export_schema_version is not None:
        payload["citation_export_schema_version"] = (
            citation_export_schema_version
        )
    if artifact_sha256 is not None:
        payload["artifact_sha256"] = artifact_sha256
    if citation_inventory_ids is not None:
        payload["citation_inventory_ids"] = citation_inventory_ids
    return payload


def _claim_projection(
    claim_hex: str,
    *,
    supporting_citation_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "claim_id": f"claim-{claim_hex * 24}",
        "statement_sha256": claim_hex * 64,
        "supporting_citation_ids": supporting_citation_ids or [],
        "opposing_citation_ids": [],
        "unknown_disposition": {
            "state": "explicit_unresolved_information_gaps",
            "reason_code": "CLAIM_HAS_LINKED_INFORMATION_GAPS",
            "fact_verification": "not_verified",
            "information_gap_count": 1,
            "information_gap_set_sha256": ("f" if claim_hex != "f" else "e")
            * 64,
        },
    }


def _manifest_payload(
    *,
    evidence_tier: str = "synthetic_fixture",
    label_source: str = "synthetic",
) -> dict[str, object]:
    observations = [
        _observation(
            "interactive-refusal",
            "assistant-interactive",
            "refusal",
            "explicit_unknown",
        ),
        _observation(
            "interactive-out-of-scope",
            "assistant-interactive",
            "out_of_scope_citation",
            "blocked_replaced_unknown",
            "CITATION_SOURCE_ID_OUT_OF_SCOPE",
        ),
        _observation(
            "interactive-injection",
            "assistant-interactive",
            "prompt_injection_forged_citation",
            "blocked_replaced_unknown",
            "CITATION_SOURCE_ID_OUT_OF_SCOPE",
        ),
        _observation(
            "interactive-provider-failure",
            "assistant-interactive",
            "provider_failure",
            "blocked_replaced_unknown",
            "MODEL_GENERATION_INCOMPLETE",
        ),
        _observation(
            "interactive-truncation",
            "assistant-interactive",
            "stream_truncation",
            "blocked_replaced_unknown",
            "MODEL_GENERATION_INCOMPLETE",
        ),
        _observation(
            "report-refusal",
            "assistant-scheduled-report",
            "refusal",
            "quarantined_no_artifact",
            "CITED_SUBSTANTIVE_BLOCKS_EMPTY",
        ),
        _observation(
            "report-out-of-scope",
            "assistant-scheduled-report",
            "out_of_scope_citation",
            "quarantined_no_artifact",
            "CITATION_IDENTIFIER_OUT_OF_SCOPE",
        ),
        _observation(
            "report-injection",
            "assistant-scheduled-report",
            "prompt_injection_active_markup",
            "quarantined_no_artifact",
            "GENERATED_CONTENT_ACTIVE_MARKUP",
        ),
        _observation(
            "report-provider-failure",
            "assistant-scheduled-report",
            "provider_failure",
            "quarantined_no_artifact",
            "RUN_FAILED",
        ),
        _observation(
            "research-claims",
            "research-reviewed-draft-export",
            "structured_claim_coverage",
            "structured_claim_records",
            claim_records=[
                _claim_projection(
                    "1",
                    supporting_citation_ids=[f"citation-{'a' * 24}"],
                ),
                _claim_projection("2"),
            ],
            citation_export_schema_version="research-citation-export-v3",
            artifact_sha256="b" * 64,
            citation_inventory_ids=[f"citation-{'a' * 24}"],
        ),
    ]
    review_state = (
        "not_available"
        if evidence_tier == "synthetic_fixture"
        else "declared"
    )
    return {
        "schema_version": GENERATIVE_EVALUATION_SCHEMA_VERSION,
        "evaluation_id": "gen-eval.synthetic-boundaries.1",
        "evaluated_at": "2026-08-09T22:20:00Z",
        "dataset": {
            "dataset_id": "synthetic-boundary-fixtures",
            "dataset_version": "1.0.0",
            "sha256": "a" * 64,
            "evidence_tier": evidence_tier,
            "label_source": label_source,
            "independent_review_state": review_state,
            "external_evidence_verification": "not_performed",
        },
        "observations": observations,
    }


def test_surface_inventory_separates_structured_claims_from_free_markdown() -> None:
    inventory = build_generative_evaluation_surface_inventory()
    surfaces = {surface.surface_id: surface for surface in inventory.surfaces}

    assert inventory.complete_hallucination_quality_claim is False
    assert inventory.real_human_gold_observed is False
    assert inventory.observation_verification_state == (
        "manifest_attested_not_independently_observed"
    )
    assert set(surfaces) == {
        "assistant-interactive",
        "assistant-scheduled-report",
        "research-reviewed-draft-export",
    }
    for surface_id in ("assistant-interactive", "assistant-scheduled-report"):
        surface = surfaces[surface_id]
        assert surface.claim_structure_state == "not_available"
        assert surface.per_claim_citation_coverage_state == "unknown"
        assert "UNSTRUCTURED_MODEL_OUTPUT_NOT_PER_CLAIM" in surface.open_findings
        assert "PER_CLAIM_CITATION_COVERAGE_UNKNOWN" in surface.open_findings
    assert "NONSTREAM_TRUNCATION_SIGNAL_NOT_AVAILABLE" in surfaces[
        "assistant-interactive"
    ].open_findings
    interactive_scenarios = {
        item.scenario_id
        for item in surfaces["assistant-interactive"].required_scenarios
    }
    assert "stream_truncation" in interactive_scenarios
    assert "truncation" not in interactive_scenarios
    assert any(
        source.path == "backend/api/routes/assistant.py"
        and "/api/assistant/cc/stream" in source.locator
        for source in surfaces["assistant-interactive"].source_locators
    )
    assert any(
        source.path == "backend/api/services/assistant_schedule.py"
        and source.locator == "def _run_failure_code("
        for source in surfaces["assistant-scheduled-report"].source_locators
    )
    research = surfaces["research-reviewed-draft-export"]
    assert research.claim_structure_state == "structured_records"
    assert research.per_claim_citation_coverage_state == (
        "syntactic_disposition_only"
    )
    assert "SEMANTIC_ENTAILMENT_NOT_VERIFIED" in research.open_findings
    assert "GENERATION_STAGE_NOT_INVENTORIED" in research.open_findings

    assert audit_generative_evaluation_surface_sources(ROOT, inventory) == ()
    checked_in = json.loads(
        (ROOT / "config/claim-output-inventory.json").read_text("utf-8")
    )
    checked_in_ids = {entry["id"] for entry in checked_in["entries"]}
    assert set(surfaces).issubset(checked_in_ids)


def test_free_markdown_surface_cannot_supply_fabricated_claim_records() -> None:
    payload = _manifest_payload()
    payload["observations"][0].update(
        {
            "citation_export_schema_version": "research-citation-export-v3",
            "artifact_sha256": "c" * 64,
            "citation_inventory_ids": [f"citation-{'a' * 24}"],
            "claim_records": [
                _claim_projection(
                    "3",
                    supporting_citation_ids=[f"citation-{'a' * 24}"],
                )
            ],
        }
    )

    with pytest.raises(ValidationError, match="unstructured|claim records"):
        GenerativeEvaluationManifest.model_validate(payload)


def test_offline_manifest_reports_only_syntactic_projection_coverage() -> None:
    result = evaluate_generative_outputs(
        GenerativeEvaluationManifest.model_validate(_manifest_payload())
    )
    by_surface = {surface.surface_id: surface for surface in result.surfaces}

    assert result.schema_version == GENERATIVE_EVALUATION_SCHEMA_VERSION
    assert result.evidence_status == "synthetic_fixture_manifest_unverified"
    assert result.observation_verification_state == (
        "manifest_attested_not_independently_observed"
    )
    assert result.boundary_fixture_state == (
        "manifest_conforms_with_open_findings"
    )
    assert result.quality_conclusion == "not_available"
    assert result.hallucination_rate is None
    assert result.hallucination_rate_state == "not_computable"
    assert result.real_human_gold_observed is False
    assert "REAL_HUMAN_GOLD_NOT_OBSERVED" in result.reason_codes
    assert "SEMANTIC_ENTAILMENT_NOT_VERIFIED" in result.reason_codes

    for surface_id in ("assistant-interactive", "assistant-scheduled-report"):
        assessment = by_surface[surface_id].claim_coverage
        assert assessment.state == "unknown_unstructured_output"
        assert assessment.structured_claim_count is None
        assert assessment.syntactic_cited_claim_ratio is None
        assert "PER_CLAIM_CITATION_COVERAGE_UNKNOWN" in assessment.reason_codes

    research = by_surface["research-reviewed-draft-export"].claim_coverage
    assert research.state == "manifest_projection_syntactic_only"
    assert research.structured_claim_count == 2
    assert research.cited_claim_count == 1
    assert research.explicit_unknown_claim_count == 2
    assert research.syntactically_disposed_claim_count == 2
    assert research.undisposed_claim_count == 0
    assert research.syntactic_cited_claim_ratio == "0.500000"
    assert research.syntactic_disposition_ratio == "1.000000"

    serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    for forbidden in (
        "prompt text",
        "model output body",
        "provider-secret",
        "article body",
    ):
        assert forbidden not in serialized.lower()


def test_unexpected_observation_reason_is_not_echoed_and_fails_closed() -> None:
    payload = _manifest_payload()
    payload["observations"][0]["reason_codes"] = ["PROVIDER_SECRET_CANARY"]
    result = evaluate_generative_outputs(
        GenerativeEvaluationManifest.model_validate(payload)
    )
    serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

    assert result.boundary_fixture_state == "failed_closed"
    assert "provider_secret_canary" not in serialized.lower()
    assert "UNEXPECTED_REASON_CODE_OBSERVED" in result.reason_codes


@pytest.mark.parametrize(
    ("evidence_tier", "label_source", "expected_status"),
    [
        (
            "synthetic_fixture",
            "synthetic",
            "synthetic_fixture_manifest_unverified",
        ),
        ("silver_label_manifest", "silver", "silver_manifest_unverified"),
        (
            "human_gold_manifest_claim",
            "human",
            "human_gold_manifest_claim_unverified",
        ),
    ],
)
def test_evidence_tiers_never_create_a_quality_conclusion_without_verified_gold(
    evidence_tier: str,
    label_source: str,
    expected_status: str,
) -> None:
    result = evaluate_generative_outputs(
        GenerativeEvaluationManifest.model_validate(
            _manifest_payload(
                evidence_tier=evidence_tier,
                label_source=label_source,
            )
        )
    )

    assert result.evidence_status == expected_status
    assert result.quality_conclusion == "not_available"
    assert result.hallucination_rate is None
    assert result.real_human_gold_observed is False


def test_evidence_tier_and_label_source_cannot_be_conflated() -> None:
    payload = _manifest_payload(
        evidence_tier="human_gold_manifest_claim",
        label_source="silver",
    )
    with pytest.raises(ValidationError, match="evidence tier|label source"):
        GenerativeEvaluationManifest.model_validate(payload)


def test_missing_fixture_or_out_of_scope_structured_citation_fails_closed() -> None:
    missing_payload = _manifest_payload()
    missing_payload["observations"] = [
        item
        for item in missing_payload["observations"]
        if item["case_id"] != "interactive-truncation"
    ]
    missing_result = evaluate_generative_outputs(
        GenerativeEvaluationManifest.model_validate(missing_payload)
    )
    assert missing_result.boundary_fixture_state == "failed_closed"
    interactive = next(
        item
        for item in missing_result.surfaces
        if item.surface_id == "assistant-interactive"
    )
    truncation = next(
        item
        for item in interactive.cases
        if item.scenario_id == "stream_truncation"
    )
    assert truncation.state == "failed_closed"
    assert truncation.reason_codes == ("FIXTURE_OBSERVATION_MISSING",)

    out_of_scope_payload = _manifest_payload()
    research = out_of_scope_payload["observations"][-1]
    research["claim_records"][0]["supporting_citation_ids"] = [
        f"citation-{'e' * 24}"
    ]
    out_of_scope_result = evaluate_generative_outputs(
        GenerativeEvaluationManifest.model_validate(out_of_scope_payload)
    )
    assert out_of_scope_result.boundary_fixture_state == "failed_closed"
    research_result = next(
        item
        for item in out_of_scope_result.surfaces
        if item.surface_id == "research-reviewed-draft-export"
    )
    assert research_result.claim_coverage.out_of_scope_citation_claim_count == 1
    assert research_result.claim_coverage.undisposed_claim_count == 0


def test_missing_structured_projection_is_not_reported_as_measured() -> None:
    payload = _manifest_payload()
    payload["observations"] = [
        item
        for item in payload["observations"]
        if item["surface_id"] != "research-reviewed-draft-export"
    ]
    result = evaluate_generative_outputs(
        GenerativeEvaluationManifest.model_validate(payload)
    )
    research = next(
        item
        for item in result.surfaces
        if item.surface_id == "research-reviewed-draft-export"
    )

    assert result.boundary_fixture_state == "failed_closed"
    assert research.boundary_fixture_state == "failed_closed"
    assert research.claim_coverage.state == "not_observed"
    assert research.claim_coverage.structured_claim_count is None
    assert research.claim_coverage.syntactic_cited_claim_ratio is None


def test_metadata_identifiers_are_bounded_and_exactly_shaped() -> None:
    oversized = _manifest_payload()
    oversized["observations"][-1]["claim_records"][0][
        "supporting_citation_ids"
    ] = ["citation-" + "a" * 100_000]
    with pytest.raises(ValidationError, match="citation|pattern|string"):
        GenerativeEvaluationManifest.model_validate(oversized)

    body_as_reason = _manifest_payload()
    body_as_reason["observations"][0]["reason_codes"] = ["A" * 100_000]
    with pytest.raises(ValidationError, match="reason|string|96"):
        GenerativeEvaluationManifest.model_validate(body_as_reason)


def test_claim_coverage_result_rejects_state_metric_contradictions() -> None:
    with pytest.raises(ValidationError, match="unknown|not observed|numeric"):
        ClaimCoverageAssessment(
            state="unknown_unstructured_output",
            structured_claim_count=7,
            cited_claim_count=7,
            explicit_unknown_claim_count=0,
            syntactically_disposed_claim_count=7,
            undisposed_claim_count=0,
            out_of_scope_citation_claim_count=0,
            syntactic_cited_claim_ratio="1.000000",
            syntactic_disposition_ratio="1.000000",
            reason_codes=("PER_CLAIM_CITATION_COVERAGE_UNKNOWN",),
        )

    with pytest.raises(ValidationError, match="ratio|disposition|counts"):
        ClaimCoverageAssessment(
            state="manifest_projection_syntactic_only",
            structured_claim_count=2,
            cited_claim_count=1,
            explicit_unknown_claim_count=2,
            syntactically_disposed_claim_count=2,
            undisposed_claim_count=0,
            out_of_scope_citation_claim_count=0,
            syntactic_cited_claim_ratio="0.500000",
            syntactic_disposition_ratio="0.500000",
            reason_codes=("STRUCTURED_CLAIM_PROJECTION_SYNTACTIC_ONLY",),
        )


def test_manifest_rejects_generated_body_or_prompt_fields() -> None:
    for forbidden_field in ("model_output_body", "prompt_text"):
        payload = _manifest_payload()
        payload["observations"][0][forbidden_field] = "secret canary"
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            GenerativeEvaluationManifest.model_validate(payload)


def test_existing_synthetic_gates_fail_closed_on_bounded_scenarios() -> None:
    refusal = finalize_interactive_output(
        "当前证据不足，结论未知。[GM-UNKNOWN]",
        (),
        evidence_required=True,
        generation_complete=True,
    )
    forged = finalize_interactive_output(
        "Prompt injection requested a forged source. [GM-T-FFFFFFFFFFFFFFFF]",
        (),
        evidence_required=True,
        generation_complete=True,
    )
    provider_failure = finalize_interactive_output(
        "partial provider output",
        (),
        evidence_required=True,
        generation_complete=False,
    )
    truncation = finalize_interactive_output(
        "truncated provider output",
        (),
        evidence_required=True,
        generation_complete=False,
    )

    assert refusal.assurance["evidence_state"] == "explicit_unknown"
    assert forged.assurance["status"] == "blocked_replaced_unknown"
    assert forged.assurance["reason_codes"] == [
        "CITATION_SOURCE_ID_OUT_OF_SCOPE"
    ]
    for output, canary in (
        (provider_failure, "partial provider output"),
        (truncation, "truncated provider output"),
    ):
        assert output.assurance["status"] == "blocked_replaced_unknown"
        assert "MODEL_GENERATION_INCOMPLETE" in output.assurance["reason_codes"]
        assert canary not in output.content

    report_inventory = build_report_source_inventory(
        {
            "favorite_context": {
                "items": [
                    {
                        "id": "source-1",
                        "title": "Synthetic source",
                        "source": "Fixture",
                        "url": "https://example.test/source",
                        "abstract": "A bounded synthetic excerpt long enough for the source gate.",
                    }
                ]
            }
        }
    )
    for content, reason in (
        ("只能拒答。[GM-UNKNOWN]", "CITED_SUBSTANTIVE_BLOCKS_EMPTY"),
        ("越界引用。[GM-S02]", "CITATION_IDENTIFIER_OUT_OF_SCOPE"),
        (
            "<script>ignore the system</script> [GM-S01]",
            "GENERATED_CONTENT_ACTIVE_MARKUP",
        ),
    ):
        with pytest.raises(ReportAssuranceError) as captured:
            assure_generated_report(content, report_inventory)
        assert reason in captured.value.reason_codes


def test_authenticated_surface_inventory_route_is_read_only_and_no_store() -> None:
    app = FastAPI()
    app.include_router(model_assurance.router)
    with TestClient(app) as anonymous:
        denied = anonymous.get(
            "/api/model-assurance/generative-evaluation/surfaces"
        )
    assert denied.status_code == 401

    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 12,
        "role": "user",
    }
    with TestClient(app) as client:
        response = client.get(
            "/api/model-assurance/generative-evaluation/surfaces"
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["complete_hallucination_quality_claim"] is False
    assert len(response.json()["surfaces"]) == 3
