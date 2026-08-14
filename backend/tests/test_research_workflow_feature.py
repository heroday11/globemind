from __future__ import annotations

import copy
import csv
import hashlib
import html
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.features.evidence import EvidenceSnapshotLedger, SNAPSHOT_PARSER_VERSION
from api.features.search import SearchSnapshotLedger, build_query_receipt
from api.features.research_workflow import (
    ARTIFACT_SCHEMA_VERSION,
    AlternativeHypothesisCreateRequest,
    EvidenceCreateRequest,
    EvidenceSnapshotReferenceRejected,
    EvidenceSnapshotVerificationUnavailable,
    ExportManifestCreateRequest,
    HumanDecisionCreateRequest,
    InformationGapCreateRequest,
    JudgmentCreateRequest,
    MemberChangeRequest,
    ProjectCreateRequest,
    QuestionCreateRequest,
    ResearchAccessDenied,
    ResearchArtifactError,
    ResearchContractConflict,
    ResearchRepositoryCapacityExceeded,
    ResearchRepositoryUnavailable,
    ResearchVersionConflict,
    ResearchWorkflowNotReady,
    ResearchWorkflowService,
    ReviewCreateRequest,
    SavedSearchCreateRequest,
    WorkspaceResearchRepository,
    build_research_export_artifact,
)
from api.features.research_workflow import artifacts as artifact_renderer
from api.features.research_workflow import repository as research_repository
from api.features.research_workflow.repository import STORE_DIRECTORY
from api.models.schemas import NewsItem, SearchRequest, SearchResponse
from api.routes import research_workflow as research_routes
from api.services import news_search_v2
from api.services.assistant_user_defaults import SAFE_USERNAME_RE
from api.services.auth import get_current_user_required


def _identity(username: str) -> dict[str, object]:
    return {"user_id": 1, "username": username, "role": "user"}


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.value:032x}"


def _service(
    root: Path, *, evidence_snapshot_reader=None, search_snapshot_reader=None
) -> ResearchWorkflowService:
    return ResearchWorkflowService(
        WorkspaceResearchRepository(root),
        clock=lambda: "2026-08-09T10:00:00Z",
        id_factory=_Ids(),
        evidence_snapshot_reader=evidence_snapshot_reader,
        search_snapshot_reader=search_snapshot_reader,
    )


def _canonical_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _capture_search_snapshot(
    ledger: SearchSnapshotLedger,
    *,
    query: str,
    actor_id: int = 1,
) -> dict[str, object]:
    params = SearchRequest(keyword=query, page=1, page_size=2)
    response = SearchResponse(
        data=[
            NewsItem(
                id=101,
                title="Country A gas dependency",
                pub_time=datetime(2026, 8, 8, 8, tzinfo=timezone.utc),
                time_semantics={
                    "published_at": datetime(
                        2026,
                        8,
                        8,
                        8,
                        tzinfo=timezone.utc,
                    )
                },
            )
        ],
        total=1,
        page=1,
        page_size=2,
        total_pages=1,
        has_next=False,
        has_prev=False,
        query_time_ms=1,
    )
    explain = news_search_v2._build_query_explain(params, total=1)
    receipt = build_query_receipt(params, response, explain)
    return ledger.capture(
        actor_id=actor_id,
        receipt=receipt,
        expected_previous_snapshot_id=None,
        captured_at=datetime(2026, 8, 9, 7, tzinfo=timezone.utc),
    )


def test_constructor_first_list_and_status_are_read_only(tmp_path: Path) -> None:
    workspace = tmp_path / "not-created-by-reads"
    repository = WorkspaceResearchRepository(workspace)
    service = ResearchWorkflowService(repository)

    assert not workspace.exists()
    assert repository.availability() == (True, None)
    assert not workspace.exists()
    assert repository.list_projects() == []
    assert service.list_projects(_identity("alice")).projects == []
    status = research_routes.get_storage_status(
        _user=_identity("alice"),
        service=service,
    )
    assert status["status"] == "available"
    assert status["durability"] == "atomic-json-fsync"
    assert not workspace.exists()


def _complete_workflow(service: ResearchWorkflowService) -> dict[str, object]:
    alice = _identity("alice")
    bob = _identity("bob")
    project = service.create_project(
        ProjectCreateRequest(
            title="Country A energy-security study",
            description="A bounded pilot research task.",
            scope_countries=["aa"],
            reason="create the approved research brief",
        ),
        alice,
    )
    project = service.set_member(
        project["id"],
        "bob",
        MemberChangeRequest(
            expected_version=project["version"],
            role="reviewer",
            reason="assign independent peer reviewer",
        ),
        alice,
    )
    project = service.set_member(
        project["id"],
        "carol",
        MemberChangeRequest(
            expected_version=project["version"],
            role="reader",
            reason="grant read-only stakeholder access",
        ),
        alice,
    )
    project = service.add_question(
        project["id"],
        QuestionCreateRequest(
            expected_version=project["version"],
            question="How exposed is Country A to an interruption in imported gas?",
            reason="record the primary research question",
        ),
        alice,
    )
    project = service.add_saved_search(
        project["id"],
        SavedSearchCreateRequest(
            expected_version=project["version"],
            name="Country A gas dependency",
            query='"Country A" AND (gas OR LNG)',
            filters={"time_field": "event_time", "language": ["en", "aa"]},
            reason="freeze the reusable query contract",
        ),
        alice,
    )
    for relation, source_id, summary in (
        ("support", "article-101", "Imports supply most observed consumption."),
        ("opposing", "dataset-202", "Storage can cover part of a short disruption."),
        ("background", "law-303", "Emergency allocation rules define priority users."),
    ):
        project = service.add_evidence(
            project["id"],
            EvidenceCreateRequest(
                expected_version=project["version"],
                relation=relation,
                summary=summary,
                source_id=source_id,
                source_title=f"Source {source_id}",
                source_url=f"https://example.test/{source_id}",
                original_anchor="paragraph-2",
                reason=f"add {relation} evidence",
            ),
            alice,
        )
    project = service.add_information_gap(
        project["id"],
        InformationGapCreateRequest(
            expected_version=project["version"],
            description="Current commercial storage fill is not independently verified.",
            impact="The duration estimate could change materially.",
            resolution_plan="Request the latest regulator storage bulletin.",
            reason="make the unresolved storage input explicit",
        ),
        alice,
    )
    project = service.add_alternative_hypothesis(
        project["id"],
        AlternativeHypothesisCreateRequest(
            expected_version=project["version"],
            statement="Demand curtailment may avoid a critical shortage.",
            discriminating_evidence="Daily industrial demand after curtailment orders.",
            reason="record a plausible alternative explanation",
        ),
        alice,
    )
    support_id = next(
        row["id"] for row in project["evidence_items"] if row["relation"] == "support"
    )
    opposing_id = next(
        row["id"] for row in project["evidence_items"] if row["relation"] == "opposing"
    )
    project = service.add_judgment(
        project["id"],
        JudgmentCreateRequest(
            expected_version=project["version"],
            statement="A prolonged interruption would create a material supply risk.",
            supporting_evidence_ids=[support_id],
            opposing_evidence_ids=[opposing_id],
            information_gap_ids=[project["information_gaps"][0]["id"]],
            alternative_hypothesis_ids=[project["alternative_hypotheses"][0]["id"]],
            uncertainty="Storage and demand-response data remain incomplete.",
            reason="form a bounded judgment from recorded evidence",
        ),
        alice,
    )
    judgment_id = project["judgments"][0]["id"]
    project = service.add_human_decision(
        project["id"],
        HumanDecisionCreateRequest(
            expected_version=project["version"],
            judgment_id=judgment_id,
            decision="modify",
            rationale="Narrow the duration claim because storage data are incomplete.",
            modified_statement="A prolonged interruption could create material supply risk.",
            reason="apply accountable human calibration",
        ),
        alice,
    )
    decision_id = project["human_decisions"][0]["id"]
    project = service.add_review(
        project["id"],
        ReviewCreateRequest(
            expected_version=project["version"],
            review_type="peer_review",
            target_type="decision",
            target_id=decision_id,
            outcome="approved",
            comment="The evidence links and uncertainty statement are adequate for a draft.",
            reason="complete independent peer review",
        ),
        bob,
    )
    project = service.add_review(
        project["id"],
        ReviewCreateRequest(
            expected_version=project["version"],
            review_type="approval",
            target_type="decision",
            target_id=decision_id,
            outcome="approved",
            comment="Approved for a versioned draft export, not operational use.",
            reason="approve the human-reviewed draft",
        ),
        alice,
    )
    return service.create_export_manifest(
        project["id"],
        ExportManifestCreateRequest(
            expected_version=project["version"],
            report_title="Country A gas exposure — reviewed draft",
            cutoff_at="2026-08-09T09:00:00+00:00",
            cutoff_basis="Only sources captured before the stated cutoff are in scope.",
            method="Structured source comparison with explicit counterevidence and gaps.",
            models=[
                {
                    "name": "manual-workflow",
                    "version": "1.0",
                    "use": "No generative model was used for the judgment.",
                }
            ],
            uncertainty="This is a draft assessment and not a live risk alert.",
            reason="create the first reviewed export manifest",
        ),
        alice,
    )


def test_end_to_end_workflow_is_durable_versioned_and_auditable(tmp_path: Path) -> None:
    service = _service(tmp_path / "workspace")
    project = _complete_workflow(service)

    assert project["version"] == 15
    assert len(project["change_history"]) == 15
    assert len(project["audit_events"]) == 15
    assert project["storage"]["audit_immutability"] == "unavailable"
    for version, change in enumerate(project["change_history"], start=1):
        assert change["version"] == version
        assert change["previous_version"] == (version - 1 or None)
        assert change["actor"]
        assert change["timestamp"].endswith("Z")
        assert change["reason"]

    manifest = project["export_manifests"][0]
    assert manifest["export_version"] == 1
    assert manifest["project_version"] == project["version"]
    assert manifest["previous_project_version"] == project["version"] - 1
    assert len(manifest["sources"]) == 3
    assert len(manifest["opposing_evidence"]) == 1
    assert len(manifest["gaps"]) == 1
    assert manifest["cutoff"]["at"] == "2026-08-09T09:00:00Z"
    assert manifest["method"]
    assert manifest["model"]["status"] == "declared"
    assert manifest["uncertainty"]
    assert manifest["assurance"] == {
        "workflow_gate": "passed",
        "publication_status": "reviewed_draft",
        "researcher_acceptance": "unavailable",
        "source_verification": "researcher_declared_not_server_verified",
    }
    integrity_payload = dict(manifest)
    integrity = integrity_payload.pop("integrity_sha256")
    assert integrity == _canonical_hash(integrity_payload)

    # A fresh repository instance proves state was not held only in memory.
    reloaded = ResearchWorkflowService(
        WorkspaceResearchRepository(tmp_path / "workspace")
    ).get_project(project["id"], _identity("alice"))
    assert reloaded == project
    state_file = (
        tmp_path
        / "workspace"
        / ".research-workflow-v1+store"
        / project["id"]
        / "state.json"
    )
    assert state_file.is_file()
    assert os.stat(state_file).st_mode & 0o777 == 0o600

    tampered = json.loads(state_file.read_text(encoding="utf-8"))
    tampered["export_manifests"][0]["method"] = "silently replaced method"
    state_file.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ResearchRepositoryUnavailable):
        ResearchWorkflowService(
            WorkspaceResearchRepository(tmp_path / "workspace")
        ).get_project(project["id"], _identity("alice"))


def test_project_state_and_history_hash_chains_reject_non_manifest_tampering(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    service = _service(workspace)
    project = service.create_project(
        ProjectCreateRequest(title="Sealed project", reason="create sealed state"),
        _identity("alice"),
    )
    project = service.add_question(
        project["id"],
        QuestionCreateRequest(
            expected_version=project["version"],
            question="Can an otherwise valid project body be silently replaced?",
            reason="extend the sealed state",
        ),
        _identity("alice"),
    )

    previous_change_sha256 = None
    previous_event_sha256 = None
    for change, event in zip(
        project["change_history"], project["audit_events"], strict=True
    ):
        assert change["previous_change_sha256"] == previous_change_sha256
        change_payload = dict(change)
        change_sha256 = change_payload.pop("change_sha256")
        assert change_sha256 == _canonical_hash(change_payload)
        previous_change_sha256 = change_sha256

        assert event["previous_event_sha256"] == previous_event_sha256
        event_payload = dict(event)
        event_sha256 = event_payload.pop("event_sha256")
        assert event_sha256 == _canonical_hash(event_payload)
        previous_event_sha256 = event_sha256

    state_payload = dict(project)
    state_sha256 = state_payload.pop("state_integrity_sha256")
    assert state_sha256 == _canonical_hash(state_payload)

    state_file = (
        workspace / STORE_DIRECTORY / project["id"] / "state.json"
    )
    valid_state = json.loads(state_file.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(valid_state)
    tampered["title"] = "silently replaced but schema-valid title"
    state_file.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ResearchRepositoryUnavailable):
        service.get_project(project["id"], _identity("alice"))

    regressed = copy.deepcopy(valid_state)
    regressed_at = "2026-08-09T09:59:59Z"
    regressed["updated_at"] = regressed_at
    latest_change = regressed["change_history"][-1]
    latest_change["timestamp"] = regressed_at
    latest_change.pop("change_sha256")
    latest_change["change_sha256"] = _canonical_hash(latest_change)
    latest_event = regressed["audit_events"][-1]
    latest_event["timestamp"] = regressed_at
    latest_event.pop("event_sha256")
    latest_event["event_sha256"] = _canonical_hash(latest_event)
    regressed.pop("state_integrity_sha256")
    regressed["state_integrity_sha256"] = _canonical_hash(regressed)
    state_file.write_text(json.dumps(regressed), encoding="utf-8")
    with pytest.raises(ResearchRepositoryUnavailable):
        service.get_project(project["id"], _identity("alice"))


def test_mutation_rejects_clock_regression_without_advancing_state(
    tmp_path: Path,
) -> None:
    timestamps = iter(
        [
            "2026-08-09T10:00:00Z",
            "2026-08-09T09:59:59Z",
        ]
    )
    service = ResearchWorkflowService(
        WorkspaceResearchRepository(tmp_path / "workspace"),
        clock=lambda: next(timestamps),
        id_factory=_Ids(),
    )
    project = service.create_project(
        ProjectCreateRequest(title="Monotonic history", reason="create initial state"),
        _identity("alice"),
    )
    with pytest.raises(ResearchContractConflict, match="clock regressed"):
        service.add_question(
            project["id"],
            QuestionCreateRequest(
                expected_version=project["version"],
                question="Can a regressed clock enter the audit chain?",
                reason="reject non-monotonic history",
            ),
            _identity("alice"),
        )
    assert service.get_project(project["id"], _identity("alice")) == project


def test_export_artifacts_are_deterministic_acl_scoped_and_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    search_snapshot_root = tmp_path / "search-snapshots"
    evidence_snapshot_root = tmp_path / "evidence-snapshots"
    monkeypatch.setenv("GLOBEMIND_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("SEARCH_SNAPSHOT_ROOT", str(search_snapshot_root))
    monkeypatch.setenv("EVIDENCE_SNAPSHOT_ROOT", str(evidence_snapshot_root))
    service = research_routes.get_research_workflow_service()
    project = _complete_workflow(service)
    manifest = project["export_manifests"][0]
    assert ARTIFACT_SCHEMA_VERSION == "research-export-artifact-v3"
    assert manifest["schema_version"] == "research-export-manifest-v2"
    assert manifest["project_scope"] == {
        "title": "Country A energy-security study",
        "description": "A bounded pilot research task.",
        "countries": ["AA"],
        "capture_status": "captured_in_manifest",
    }
    assert all("summary" in source and "note" in source for source in manifest["sources"])

    state_file = (
        workspace
        / ".research-workflow-v1+store"
        / project["id"]
        / "state.json"
    )
    state_before = state_file.read_bytes()
    tree_before = sorted(
        path.relative_to(workspace).as_posix() for path in workspace.rglob("*")
    )
    app = FastAPI()
    app.include_router(research_routes.router)
    app.dependency_overrides[get_current_user_required] = lambda: _identity("carol")
    client = TestClient(app)
    artifact_url = f"/api/research/projects/{project['id']}/exports/1/artifact"

    json_response = client.get(artifact_url, params={"format": "json"})
    repeated = client.get(artifact_url, params={"format": "json"})

    assert json_response.status_code == 200
    assert repeated.content == json_response.content
    assert json_response.headers["content-type"].startswith("application/json")
    assert json_response.headers["cache-control"] == "private, no-store"
    assert json_response.headers["vary"] == "Authorization"
    assert json_response.headers["x-research-artifact-schema"] == (
        ARTIFACT_SCHEMA_VERSION
    )
    response_hash = hashlib.sha256(json_response.content).hexdigest()
    assert json_response.headers["x-research-artifact-sha256"] == response_hash
    assert json_response.headers["etag"] == f'"sha256-{response_hash}"'
    assert re.fullmatch(
        rf'attachment; filename="research-reviewed-draft-{project["id"]}'
        r'-v1-fields-[0-9a-f]{12}\.json"',
        json_response.headers["content-disposition"],
    )
    assert json_response.headers["x-research-publication-status"] == "reviewed_draft"
    assert json_response.headers["x-researcher-acceptance"] == "unavailable"
    assert json_response.headers["x-research-field-selection-schema"] == (
        "research-export-field-selection-v1"
    )
    assert json_response.headers["x-research-source-license-status"] == "unknown"
    payload = json_response.json()
    report = payload["report"]
    assert payload["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert payload["artifact_format"] == "json"
    assert payload["source_policy"] == "persisted_export_manifest_only"
    assert payload["report_content_sha256"] == _canonical_hash(report)
    assert payload["manifest_integrity_sha256"] == manifest["integrity_sha256"]
    assert report["report_title"] == manifest["report_title"]
    selected = report["selected_content"]
    assert selected["project_scope"]["countries"] == ["AA"]
    assert selected["cutoff"] == manifest["cutoff"]
    assert selected["method"]["description"] == manifest["method"]
    assert report["version"]["export_version"] == 1
    assert report["version"]["project_version"] == project["version"]
    assert {
        relation: sum(
            row["relation"] == relation for row in selected["evidence_summaries"]
        )
        for relation in ("background", "opposing", "support")
    } == {
        "background": 1,
        "opposing": 1,
        "support": 1,
    }
    support_reference = next(
        row for row in selected["evidence_summaries"] if row["relation"] == "support"
    )
    assert support_reference["evidence_id"]
    assert support_reference["source_id"] == "article-101"
    assert support_reference["summary"] == "Imports supply most observed consumption."
    support_citation = next(
        row
        for row in report["citation_export"]["citations"]
        if row["evidence_id"] == support_reference["evidence_id"]
    )
    assert support_citation["source"]["original_anchor"] == "paragraph-2"
    assert selected["information_gaps"][0]["description"] == (
        manifest["gaps"][0]["description"]
    )
    assert selected["alternative_hypotheses"][0]["statement"] == (
        manifest["alternative_hypotheses"][0]["statement"]
    )
    assert selected["judgments"][0]["statement"] == manifest["judgments"][0]["statement"]
    assert selected["human_decisions"][0]["decision"] == manifest["decisions"][0]["decision"]
    assert len(selected["review_outcomes"]["peer_reviews"]) == 1
    assert len(selected["review_outcomes"]["approvals"]) == 1
    assert selected["uncertainty"] == manifest["uncertainty"]
    assert report["assurance"]["publication_status"] == "reviewed_draft"
    assert report["assurance"]["researcher_acceptance"] == "unavailable"
    assert report["distribution_boundary"] == {
        "status": "not_for_publication",
        "warning": (
            "REVIEWED DRAFT — RESEARCHER ACCEPTANCE UNAVAILABLE — "
            "NOT FOR PUBLICATION"
        ),
    }
    assert report["rendering_assurance"] == {
        "source_policy": "persisted_export_manifest_only",
        "deterministic": True,
        "new_facts_generated": False,
        "unreferenced_ai_narrative_generated": False,
    }

    markdown_response = client.get(artifact_url, params={"format": "markdown"})
    assert markdown_response.status_code == 200
    assert markdown_response.headers["content-type"].startswith("text/markdown")
    assert markdown_response.headers["content-disposition"].endswith('.md"')
    assert markdown_response.headers["x-research-artifact-sha256"] == (
        hashlib.sha256(markdown_response.content).hexdigest()
    )
    markdown = markdown_response.text
    assert markdown.startswith(
        "# REVIEWED DRAFT — RESEARCHER ACCEPTANCE UNAVAILABLE — NOT FOR PUBLICATION"
    )
    for marker in (
        manifest["report_title"],
        "Project and country scope",
        "Supporting evidence references",
        "Opposing evidence references",
        "Background evidence references",
        "Information gaps",
        "Alternative hypotheses",
        "Judgments",
        "Human decisions",
        "Peer review and approval",
        "reviewed_draft",
        "unavailable",
        manifest["integrity_sha256"],
    ):
        assert marker in markdown

    html_response = client.get(artifact_url, params={"format": "html"})
    repeated_html = client.get(artifact_url, params={"format": "html"})
    assert html_response.status_code == 200
    assert repeated_html.content == html_response.content
    assert html_response.headers["content-type"].startswith("text/html")
    assert html_response.headers["content-disposition"].endswith('.html"')
    assert html_response.headers["content-security-policy"] == (
        "default-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'; sandbox"
    )
    assert html_response.headers["x-research-artifact-format"] == "html"
    html_response_hash = hashlib.sha256(html_response.content).hexdigest()
    assert html_response.headers["x-research-artifact-sha256"] == html_response_hash
    assert html_response.headers["etag"] == f'"sha256-{html_response_hash}"'
    assert html_response.headers["x-research-report-content-sha256"] == (
        payload["report_content_sha256"]
    )
    assert html_response.headers["x-research-manifest-sha256"] == (
        manifest["integrity_sha256"]
    )
    html_document = html_response.text
    assert (
        '<aside role="note">REVIEWED DRAFT — RESEARCHER ACCEPTANCE '
        "UNAVAILABLE — NOT FOR PUBLICATION</aside>"
    ) in html_document
    assert "<script" not in html_document.lower()
    assert "<link" not in html_document.lower()
    assert "<img" not in html_document.lower()
    assert "reviewed_draft" in html_document

    csv_response = client.get(artifact_url, params={"format": "csv"})
    repeated_csv = client.get(artifact_url, params={"format": "csv"})
    assert csv_response.status_code == 200
    assert repeated_csv.content == csv_response.content
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert csv_response.headers["content-disposition"].endswith('.csv"')
    assert csv_response.headers["x-research-artifact-format"] == "csv"
    csv_response_hash = hashlib.sha256(csv_response.content).hexdigest()
    assert csv_response.headers["x-research-artifact-sha256"] == csv_response_hash
    assert csv_response.headers["etag"] == f'"sha256-{csv_response_hash}"'
    assert csv_response.headers["x-research-report-content-sha256"] == (
        payload["report_content_sha256"]
    )
    assert csv_response.headers["x-research-manifest-sha256"] == (
        manifest["integrity_sha256"]
    )
    csv_rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(csv_rows) == 3
    assert {row["relation"] for row in csv_rows} == {
        "support",
        "opposing",
        "background",
    }
    assert all(
        row["manifest_integrity_sha256"] == manifest["integrity_sha256"]
        and row["report_content_sha256"] == payload["report_content_sha256"]
        and row["artifact_kind"] == "deterministic_evidence_reference_inventory"
        and row["new_facts_generated"] == "false"
        and row["unreferenced_ai_narrative_generated"] == "false"
        and row["researcher_acceptance"] == "unavailable"
        and row["distribution_status"] == "not_for_publication"
        and row["license_status"] == "unknown"
        and row["license_redistribution_permission"] == "not_established"
        and row["distribution_warning"]
        == "REVIEWED DRAFT — RESEARCHER ACCEPTANCE UNAVAILABLE — NOT FOR PUBLICATION"
        for row in csv_rows
    )

    assert state_file.read_bytes() == state_before
    assert sorted(
        path.relative_to(workspace).as_posix() for path in workspace.rglob("*")
    ) == tree_before
    assert not search_snapshot_root.exists()
    assert not evidence_snapshot_root.exists()
    assert len(service.get_project(project["id"], _identity("alice"))["audit_events"]) == 15

    denied_app = FastAPI()
    denied_app.include_router(research_routes.router)
    denied_app.dependency_overrides[get_current_user_required] = lambda: _identity(
        "mallory"
    )
    denied = TestClient(denied_app).get(artifact_url, params={"format": "json"})
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "private, no-store"
    unsupported = client.get(artifact_url, params={"format": "pdf"})
    assert unsupported.status_code == 409
    assert unsupported.headers["x-content-type-options"] == "nosniff"
    assert client.get(
        f"/api/research/projects/{project['id']}/exports/2/artifact",
        params={"format": "json"},
    ).status_code == 409


def test_reviewed_draft_citations_bind_claims_and_match_across_all_formats(
    tmp_path: Path,
) -> None:
    project = _complete_workflow(_service(tmp_path / "workspace"))
    manifest = project["export_manifests"][0]

    artifacts = {
        artifact_format: build_research_export_artifact(manifest, artifact_format)
        for artifact_format in ("json", "markdown", "html", "csv")
    }
    repeated_json = build_research_export_artifact(manifest, "json")
    assert artifacts["json"].body == repeated_json.body

    payload = json.loads(artifacts["json"].body)
    citation_export = payload["report"]["citation_export"]
    assert citation_export["schema_version"] == "research-citation-export-v3"
    assert citation_export["style"] == {
        "name": "generic_structured_draft",
        "status": "draft_not_verified",
        "verified_standard": None,
        "not_claimed_standards": ["APA", "Chicago", "GB/T 7714"],
    }
    citations = citation_export["citations"]
    assert len(citations) == 3
    assert [row["footnote_number"] for row in citations] == [1, 2, 3]
    assert len({row["citation_id"] for row in citations}) == len(citations)
    assert all(
        re.fullmatch(r"citation-[0-9a-f]{24}", row["citation_id"])
        and row["locator"]["status"] == "declared_not_verified"
        and row["locator"]["permanence"] == "not_verified"
        and row["locator"]["url"].startswith("https://example.test/")
        for row in citations
    )
    next_export = copy.deepcopy(manifest)
    next_export["manifest_id"] = "f" * 32
    next_export["export_version"] = 2
    next_export.pop("integrity_sha256")
    next_export["integrity_sha256"] = _canonical_hash(next_export)
    next_citations = json.loads(
        build_research_export_artifact(next_export, "json").body
    )["report"]["citation_export"]["citations"]
    assert [row["citation_id"] for row in next_citations] == [
        row["citation_id"] for row in citations
    ]

    binding = citation_export["claim_bindings"][0]
    judgment = manifest["judgments"][0]
    assert binding["judgment_id"] == judgment["id"]
    assert binding["statement_sha256"] == hashlib.sha256(
        judgment["statement"].encode("utf-8")
    ).hexdigest()
    assert binding["unknown_disposition"] == {
        "state": "explicit_unresolved_information_gaps",
        "reason_code": "CLAIM_HAS_LINKED_INFORMATION_GAPS",
        "fact_verification": "not_verified",
        "information_gap_ids": judgment["information_gap_ids"],
    }
    expected_ids = {
        row["evidence_id"]: row["citation_id"] for row in citations
    }
    assert binding["supporting_citation_ids"] == [
        expected_ids[evidence_id]
        for evidence_id in judgment["supporting_evidence_ids"]
    ]
    assert binding["opposing_citation_ids"] == [
        expected_ids[evidence_id]
        for evidence_id in judgment["opposing_evidence_ids"]
    ]

    markdown = artifacts["markdown"].body.decode("utf-8")
    html_document = artifacts["html"].body.decode("utf-8")
    csv_rows = list(
        csv.DictReader(io.StringIO(artifacts["csv"].body.decode("utf-8")))
    )
    csv_by_id = {row["citation_id"]: row for row in csv_rows}
    for citation in citations:
        citation_id = citation["citation_id"]
        footnote = citation["footnote_number"]
        reference_text = citation["reference_text"]
        assert f"[^{footnote}]: {artifact_renderer._markdown_text(reference_text)}" in markdown
        assert f'id="{citation_id}"' in html_document
        assert html.escape(reference_text, quote=True) in html_document
        assert csv_by_id[citation_id]["footnote_number"] == str(footnote)
        assert csv_by_id[citation_id]["reference_text"] == reference_text
        assert csv_by_id[citation_id]["citation_style_status"] == "draft_not_verified"
    assert binding["claim_id"] in markdown
    assert binding["claim_id"] in html_document
    assert "explicit_unresolved_information_gaps" in markdown
    assert "explicit_unresolved_information_gaps" in html_document
    assert {
        row["citation_id"]
        for row in csv_rows
        if binding["claim_id"] in row["bound_claim_ids"]
    } == set(binding["supporting_citation_ids"] + binding["opposing_citation_ids"])
    for row in csv_rows:
        if binding["claim_id"] in row["bound_claim_ids"]:
            dispositions = json.loads(row["bound_claim_unknown_dispositions"])
            assert dispositions == [
                {
                    "claim_id": binding["claim_id"],
                    "fact_verification": "not_verified",
                    "reason_code": "CLAIM_HAS_LINKED_INFORMATION_GAPS",
                    "state": "explicit_unresolved_information_gaps",
                }
            ]


def test_configured_artifacts_use_an_allowlisted_projection_and_unknown_license_boundary(
    tmp_path: Path,
) -> None:
    project = _complete_workflow(_service(tmp_path / "workspace"))
    manifest = copy.deepcopy(project["export_manifests"][0])
    manifest["project_scope"]["description"] = "unselected-scope-canary"
    manifest["method"] = "unselected-method-canary"
    manifest["uncertainty"] = "selected-uncertainty-canary"
    manifest["created_by"] = "sensitive-actor-canary"
    manifest["saved_searches"][0]["query"] = "sensitive-query-canary"
    manifest["saved_searches"][0]["filters"] = {"token": "sensitive-filter-canary"}
    manifest["sources"][0]["note"] = "sensitive-note-canary"
    manifest["decisions"][0]["rationale"] = "sensitive-rationale-canary"
    manifest["reviews"][0]["comment"] = "sensitive-comment-canary"
    manifest.pop("integrity_sha256")
    manifest["integrity_sha256"] = _canonical_hash(manifest)

    selected_fields = ["evidence_summaries", "uncertainty"]
    artifacts = {
        artifact_format: build_research_export_artifact(
            manifest,
            artifact_format,
            export_fields=selected_fields,
        )
        for artifact_format in ("json", "markdown", "html", "csv")
    }
    payload = json.loads(artifacts["json"].body)
    report = payload["report"]

    assert payload["schema_version"] == "research-export-artifact-v3"
    assert report["field_selection"] == {
        "schema_version": "research-export-field-selection-v1",
        "selected_fields": ["uncertainty", "evidence_summaries"],
        "mandatory_fields": [
            "identity",
            "version",
            "claims_and_citations",
            "assurance",
            "distribution_boundary",
            "license_boundary",
            "rendering_assurance",
        ],
        "always_excluded_sensitive_fields": [
            "created_by",
            "source_note",
            "saved_search_query",
            "saved_search_filters",
            "decision_rationale",
            "review_comment",
        ],
    }
    assert set(report["selected_content"]) == {"uncertainty", "evidence_summaries"}
    assert report["selected_content"]["uncertainty"] == "selected-uncertainty-canary"
    assert len(report["selected_content"]["evidence_summaries"]) == 3
    assert report["license_boundary"] == {
        "schema_version": "research-source-license-boundary-v1",
        "status": "unknown",
        "redistribution_permission": "not_established",
        "reason_code": "SOURCE_LICENSE_NOT_CAPTURED_IN_MANIFEST",
        "notice": (
            "Locator availability does not grant reuse permission; verify each "
            "source's terms before redistribution."
        ),
    }
    citations = report["citation_export"]["citations"]
    assert all(
        citation["license"]
        == {
            "status": "unknown",
            "redistribution_permission": "not_established",
            "reason_code": "SOURCE_LICENSE_NOT_CAPTURED_IN_MANIFEST",
        }
        for citation in citations
    )
    assert {
        artifact.report_content_sha256 for artifact in artifacts.values()
    } == {payload["report_content_sha256"]}

    forbidden = (
        "unselected-scope-canary",
        "unselected-method-canary",
        "sensitive-actor-canary",
        "sensitive-query-canary",
        "sensitive-filter-canary",
        "sensitive-note-canary",
        "sensitive-rationale-canary",
        "sensitive-comment-canary",
    )
    for artifact in artifacts.values():
        text = artifact.body.decode("utf-8")
        assert "selected-uncertainty-canary" in text
        assert "not_for_publication" in text or "NOT FOR PUBLICATION" in text
        assert "SOURCE_LICENSE_NOT_CAPTURED_IN_MANIFEST" in text
        assert all(canary not in text for canary in forbidden)

    full_payload = json.loads(build_research_export_artifact(manifest, "json").body)
    full_report = full_payload["report"]
    assert set(full_report["selected_content"]) == {
        "project_scope",
        "cutoff",
        "method",
        "uncertainty",
        "research_questions",
        "saved_search_receipts",
        "evidence_summaries",
        "information_gaps",
        "alternative_hypotheses",
        "judgments",
        "human_decisions",
        "review_outcomes",
    }
    assert "created_by" not in full_report["version"]
    assert "query" not in full_report["selected_content"]["saved_search_receipts"][0]
    assert "filters" not in full_report["selected_content"]["saved_search_receipts"][0]
    assert "note" not in full_report["selected_content"]["evidence_summaries"][0]
    assert "rationale" not in full_report["selected_content"]["human_decisions"][0]
    assert "comment" not in full_report["selected_content"]["review_outcomes"][
        "peer_reviews"
    ][0]
    full_text = json.dumps(full_payload, ensure_ascii=False)
    assert all(canary not in full_text for canary in forbidden[2:])

    markdown = artifacts["markdown"].body.decode("utf-8")
    html_document = artifacts["html"].body.decode("utf-8")
    csv_rows = list(csv.DictReader(io.StringIO(artifacts["csv"].body.decode("utf-8"))))
    for citation in citations:
        assert citation["citation_id"] in markdown
        assert citation["citation_id"] in html_document
        csv_row = next(row for row in csv_rows if row["citation_id"] == citation["citation_id"])
        assert csv_row["reference_text"] == citation["reference_text"]
        assert csv_row["license_status"] == "unknown"
        assert csv_row["license_redistribution_permission"] == "not_established"


@pytest.mark.parametrize("artifact_format", ["pdf", "word", "ppt", "docx"])
def test_artifact_format_and_field_selection_fail_closed(
    tmp_path: Path,
    artifact_format: str,
) -> None:
    manifest = _complete_workflow(_service(tmp_path / "workspace"))[
        "export_manifests"
    ][0]
    with pytest.raises(ResearchArtifactError, match="unsupported research artifact format"):
        build_research_export_artifact(manifest, artifact_format)  # type: ignore[arg-type]
    with pytest.raises(ResearchArtifactError, match="field selection"):
        build_research_export_artifact(
            manifest,
            "json",
            export_fields=["uncertainty", "source_note"],
        )
    with pytest.raises(ResearchArtifactError, match="field selection"):
        build_research_export_artifact(
            manifest,
            "json",
            export_fields=["uncertainty", "uncertainty"],
        )


def test_artifact_http_field_and_error_headers_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("GLOBEMIND_WORKSPACE_ROOT", str(workspace))
    service = research_routes.get_research_workflow_service()
    project = _complete_workflow(service)
    app = FastAPI()
    app.include_router(research_routes.router)
    app.dependency_overrides[get_current_user_required] = lambda: _identity("carol")
    client = TestClient(app)
    artifact_url = f"/api/research/projects/{project['id']}/exports/1/artifact"

    response = client.get(
        artifact_url,
        params=[
            ("format", "json"),
            ("fields", "evidence_summaries"),
            ("fields", "uncertainty"),
        ],
    )
    assert response.status_code == 200
    assert response.headers["x-research-export-fields"] == (
        "uncertainty,evidence_summaries"
    )
    assert response.headers["x-research-field-selection-schema"] == (
        "research-export-field-selection-v1"
    )
    assert response.headers["x-research-source-license-status"] == "unknown"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["vary"] == "Authorization"
    assert re.fullmatch(
        r'attachment; filename="research-reviewed-draft-[A-Za-z0-9_-]+-v1-fields-[0-9a-f]{12}\.json"',
        response.headers["content-disposition"],
    )

    for invalid_format in ("pdf", "word", "ppt"):
        rejected = client.get(
            artifact_url,
            params={"format": invalid_format, "fields": "secret-field-canary"},
        )
        assert rejected.status_code == 409
        assert rejected.headers["cache-control"] == "private, no-store"
        assert rejected.headers["x-content-type-options"] == "nosniff"
        assert rejected.headers["vary"] == "Authorization"
        assert "secret-field-canary" not in rejected.text
        assert invalid_format not in rejected.text

    rejected_field = client.get(
        artifact_url,
        params={"format": "json", "fields": "private_note_canary"},
    )
    assert rejected_field.status_code == 409
    assert "private_note_canary" not in rejected_field.text

    missing_format = client.get(artifact_url)
    assert missing_format.status_code == 409
    assert missing_format.headers["cache-control"] == "private, no-store"
    assert missing_format.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("information_gap_ids", [[], ["missing-gap"]])
def test_citation_export_requires_a_bound_explicit_unknown_disposition(
    tmp_path: Path,
    information_gap_ids: list[str],
) -> None:
    project = _complete_workflow(_service(tmp_path / "workspace"))
    manifest = copy.deepcopy(project["export_manifests"][0])
    manifest["judgments"][0]["information_gap_ids"] = information_gap_ids
    manifest.pop("integrity_sha256")
    manifest["integrity_sha256"] = _canonical_hash(manifest)

    with pytest.raises(
        ResearchArtifactError,
        match="claim unknown disposition is unavailable",
    ):
        build_research_export_artifact(manifest, "json")


def test_citation_export_suppresses_unsafe_locator_and_bounds_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _complete_workflow(_service(tmp_path / "workspace"))
    manifest = copy.deepcopy(project["export_manifests"][0])
    unsafe_locator = "https://example.test/source?access_token=secret-canary"
    manifest["sources"][0]["source_url"] = unsafe_locator
    manifest.pop("integrity_sha256")
    manifest["integrity_sha256"] = _canonical_hash(manifest)

    for artifact_format in ("json", "markdown", "html", "csv"):
        artifact = build_research_export_artifact(manifest, artifact_format)
        assert b"secret-canary" not in artifact.body
    payload = json.loads(build_research_export_artifact(manifest, "json").body)
    citation = next(
        row
        for row in payload["report"]["citation_export"]["citations"]
        if row["evidence_id"] == manifest["sources"][0]["evidence_id"]
    )
    assert citation["locator"] == {
        "status": "unavailable",
        "url": None,
        "permanence": "unavailable",
        "reason_code": "SOURCE_LOCATOR_UNSAFE",
    }

    missing_locator_manifest = copy.deepcopy(project["export_manifests"][0])
    missing_locator_manifest["sources"][0]["source_url"] = None
    missing_locator_manifest.pop("integrity_sha256")
    missing_locator_manifest["integrity_sha256"] = _canonical_hash(
        missing_locator_manifest
    )
    missing_locator = json.loads(
        build_research_export_artifact(missing_locator_manifest, "json").body
    )["report"]["citation_export"]["citations"][0]["locator"]
    assert missing_locator == {
        "status": "unavailable",
        "url": None,
        "permanence": "unavailable",
        "reason_code": "SOURCE_LOCATOR_NOT_PROVIDED",
    }

    monkeypatch.setattr(artifact_renderer, "MAX_CITATION_COUNT", 2)
    with pytest.raises(ResearchArtifactError, match="citation count limit"):
        build_research_export_artifact(project["export_manifests"][0], "json")

    invalid_unicode = copy.deepcopy(project["export_manifests"][0])
    invalid_unicode["sources"][0]["source_title"] = "invalid-\ud800"
    invalid_unicode.pop("integrity_sha256")
    with pytest.raises((UnicodeError, ValueError)):
        _canonical_hash(invalid_unicode)


def test_citation_export_never_publishes_query_or_fragment_values(
    tmp_path: Path,
) -> None:
    project = _complete_workflow(_service(tmp_path / "workspace"))
    manifest = copy.deepcopy(project["export_manifests"][0])
    manifest["sources"][0]["source_url"] = (
        "https://example.test/source?download=secret-canary#reader-state"
    )
    manifest.pop("integrity_sha256")
    manifest["integrity_sha256"] = _canonical_hash(manifest)

    for artifact_format in ("json", "markdown", "html", "csv"):
        artifact = build_research_export_artifact(manifest, artifact_format)
        assert b"secret-canary" not in artifact.body
        assert b"reader-state" not in artifact.body

    citation = json.loads(
        build_research_export_artifact(manifest, "json").body
    )["report"]["citation_export"]["citations"][0]
    assert citation["locator"]["status"] == "unavailable"
    assert citation["locator"]["url"] is None


def test_legacy_manifest_artifact_reports_missing_scope_without_inference(
    tmp_path: Path,
) -> None:
    project = _complete_workflow(_service(tmp_path / "workspace"))
    legacy = copy.deepcopy(project["export_manifests"][0])
    legacy["schema_version"] = "research-export-manifest-v1"
    legacy.pop("project_scope")
    for source in [*legacy["sources"], *legacy["opposing_evidence"]]:
        source.pop("summary", None)
        source.pop("note", None)
    legacy.pop("integrity_sha256")
    legacy["integrity_sha256"] = _canonical_hash(legacy)

    first = build_research_export_artifact(legacy, "json")
    second = build_research_export_artifact(legacy, "json")
    payload = json.loads(first.body)

    assert first.body == second.body
    assert payload["report"]["selected_content"]["project_scope"] == {
        "title": None,
        "description": None,
        "countries": [],
        "capture_status": "unavailable_in_manifest_v1",
    }

    state_file = (
        tmp_path
        / "workspace"
        / ".research-workflow-v1+store"
        / project["id"]
        / "state.json"
    )
    stored = json.loads(state_file.read_text(encoding="utf-8"))
    stored["export_manifests"][0] = legacy
    stored.pop("state_integrity_sha256")
    stored["state_integrity_sha256"] = _canonical_hash(stored)
    state_file.write_text(json.dumps(stored), encoding="utf-8")
    reloaded = ResearchWorkflowService(
        WorkspaceResearchRepository(tmp_path / "workspace")
    )
    persisted = reloaded.get_export_artifact(
        project["id"],
        1,
        "json",
        _identity("alice"),
    )
    assert persisted.body == first.body

    legacy_html = build_research_export_artifact(legacy, "html")
    legacy_csv = build_research_export_artifact(legacy, "csv")
    assert b"unavailable_in_manifest_v1" in legacy_html.body
    legacy_rows = list(csv.DictReader(io.StringIO(legacy_csv.body.decode("utf-8"))))
    assert all(
        row["project_scope_title"] == "unavailable"
        and row["project_scope_capture_status"] == "unavailable_in_manifest_v1"
        and row["summary"] == "unavailable_in_manifest_v1"
        for row in legacy_rows
    )
    assert "note" not in legacy_rows[0]
    assert {
        first.report_content_sha256,
        legacy_html.report_content_sha256,
        legacy_csv.report_content_sha256,
    } == {first.report_content_sha256}


def test_html_and_csv_escape_active_content_and_spreadsheet_formulas(
    tmp_path: Path,
) -> None:
    project = _complete_workflow(_service(tmp_path / "workspace"))
    manifest = copy.deepcopy(project["export_manifests"][0])
    manifest["project_id"] = 'project"\r\nX-Injected: yes'
    manifest["report_title"] = '</title><script src="https://attacker.invalid/x.js">'
    source = manifest["sources"][0]
    source["source_title"] = '=HYPERLINK("https://attacker.invalid","open")'
    source["original_anchor"] = "\t=cmd|' /C calc'!A0"
    source["summary"] = '<img src="https://attacker.invalid/pixel">'
    source["note"] = "  @SUM(1,1)"
    manifest.pop("integrity_sha256")
    manifest["integrity_sha256"] = _canonical_hash(manifest)

    html_artifact = build_research_export_artifact(manifest, "html")
    repeated_html = build_research_export_artifact(manifest, "html")
    csv_artifact = build_research_export_artifact(manifest, "csv")
    repeated_csv = build_research_export_artifact(manifest, "csv")

    assert html_artifact.body == repeated_html.body
    assert csv_artifact.body == repeated_csv.body
    assert html_artifact.report_content_sha256 == csv_artifact.report_content_sha256
    assert html_artifact.manifest_integrity_sha256 == csv_artifact.manifest_integrity_sha256
    assert re.fullmatch(
        r"[A-Za-z0-9._-]+",
        html_artifact.filename,
    )
    assert "\r" not in html_artifact.filename and "\n" not in html_artifact.filename
    document = html_artifact.body.decode("utf-8")
    assert "&lt;/title&gt;&lt;script src=" in document
    assert "&lt;img src=" in document
    assert re.search(r"<(script|img|link|iframe|object|embed)\b", document, re.I) is None
    assert "<style" not in document.lower()

    rows = list(csv.DictReader(io.StringIO(csv_artifact.body.decode("utf-8"))))
    escaped = next(row for row in rows if row["evidence_id"] == source["evidence_id"])
    assert escaped["source_title"].startswith("'=")
    assert escaped["reference_text"].startswith("'=")
    assert escaped["original_anchor"].startswith("'=")
    assert escaped["summary"] == source["summary"]
    assert "note" not in escaped
    assert "@SUM(1,1)" not in csv_artifact.body.decode("utf-8")


def test_html_and_csv_artifact_bounds_fail_closed_without_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _complete_workflow(_service(tmp_path / "workspace"))
    manifest = project["export_manifests"][0]

    monkeypatch.setattr(artifact_renderer, "MAX_CSV_EVIDENCE_ROWS", 2)
    with pytest.raises(ResearchArtifactError, match="evidence row limit"):
        build_research_export_artifact(manifest, "csv")

    monkeypatch.setattr(artifact_renderer, "MAX_CSV_EVIDENCE_ROWS", 10)
    monkeypatch.setattr(artifact_renderer, "MAX_CSV_ARTIFACT_BYTES", 1)
    with pytest.raises(ResearchArtifactError, match="byte limit"):
        build_research_export_artifact(manifest, "csv")

    monkeypatch.setattr(artifact_renderer, "MAX_HTML_ARTIFACT_BYTES", 1)
    with pytest.raises(ResearchArtifactError, match="byte limit"):
        build_research_export_artifact(manifest, "html")


def test_project_rbac_fails_closed_and_audit_stream_excludes_body_text(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "workspace")
    project = _complete_workflow(service)

    reader_view = service.get_project(project["id"], _identity("carol"))
    assert reader_view["id"] == project["id"]
    assert reader_view["audit_events"] == []
    assert reader_view["storage"]["state_integrity_scope"] == (
        "complete-persisted-project-before-acl-redaction"
    )
    assert reader_view["storage"]["response_view"] == "acl-redacted"
    assert service.list_projects(_identity("bob")).projects[0].role == "reviewer"
    assert service.list_projects(_identity("carol")).projects[0].role == "reader"
    with pytest.raises(ResearchAccessDenied):
        service.get_audit_events(project["id"], _identity("carol"))
    with pytest.raises(ResearchAccessDenied):
        service.add_question(
            project["id"],
            QuestionCreateRequest(
                expected_version=1,
                question="This should never be stored.",
                reason="attempt an unauthorized mutation",
            ),
            _identity("mallory"),
        )
    assert service.list_projects(_identity("mallory")).projects == []

    audit = service.get_audit_events(project["id"], _identity("bob"))
    serialized = json.dumps(audit, ensure_ascii=False)
    for sensitive_body in (
        "How exposed is Country A",
        "Imports supply most observed consumption",
        "Narrow the duration claim",
        "complete independent peer review",
    ):
        assert sensitive_body not in serialized
    assert audit["redaction"]["body_fields_included"] == "none"


def test_stale_version_and_incomplete_workflow_are_explicit_conflicts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "workspace")
    alice = _identity("alice")
    project = service.create_project(
        ProjectCreateRequest(title="Pilot project", reason="start the pilot"), alice
    )
    project = service.add_question(
        project["id"],
        QuestionCreateRequest(
            expected_version=project["version"],
            question="What changed and why does it matter?",
            reason="record the first research question",
        ),
        alice,
    )
    with pytest.raises(ResearchVersionConflict) as conflict:
        service.add_question(
            project["id"],
            QuestionCreateRequest(
                expected_version=1,
                question="Could a stale writer overwrite this project?",
                reason="exercise optimistic concurrency",
            ),
            alice,
        )
    assert conflict.value.actual == 2

    with pytest.raises(ResearchWorkflowNotReady) as not_ready:
        service.create_export_manifest(
            project["id"],
            ExportManifestCreateRequest(
                expected_version=project["version"],
                report_title="Premature report",
                cutoff_at="2026-08-09T09:00:00Z",
                cutoff_basis="Test cutoff",
                method="No complete method yet",
                uncertainty="Unknown",
                reason="prove export gates fail closed",
            ),
            alice,
        )
    assert "SAVED_SEARCH_MISSING" in not_ready.value.reason_codes
    assert "OPPOSING_EVIDENCE_MISSING" in not_ready.value.reason_codes
    assert "APPROVED_HUMAN_DECISION_CHAIN_MISSING" in not_ready.value.reason_codes


def test_newer_human_decision_supersedes_an_older_approved_chain(tmp_path: Path) -> None:
    service = _service(tmp_path / "workspace")
    alice = _identity("alice")
    project = _complete_workflow(service)
    project = service.add_human_decision(
        project["id"],
        HumanDecisionCreateRequest(
            expected_version=project["version"],
            judgment_id=project["judgments"][0]["id"],
            decision="reject",
            rationale="New evidence invalidated the previously approved judgment.",
            reason="supersede the prior human decision",
        ),
        alice,
    )
    with pytest.raises(ResearchWorkflowNotReady) as not_ready:
        service.create_export_manifest(
            project["id"],
            ExportManifestCreateRequest(
                expected_version=project["version"],
                report_title="Superseded report",
                cutoff_at="2026-08-09T09:00:00Z",
                cutoff_basis="Test cutoff",
                method="Previously approved method",
                uncertainty="The latest decision rejects the judgment.",
                reason="prove stale approvals cannot authorize another export",
            ),
            alice,
        )
    assert not_ready.value.reason_codes == (
        "APPROVED_HUMAN_DECISION_CHAIN_MISSING",
    )


def test_report_cutoff_cannot_claim_observations_after_export_creation(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "workspace")
    project = _complete_workflow(service)
    before = service.get_project(project["id"], _identity("alice"))

    with pytest.raises(ResearchWorkflowNotReady) as not_ready:
        service.create_export_manifest(
            project["id"],
            ExportManifestCreateRequest(
                expected_version=project["version"],
                report_title="Future-cutoff draft",
                cutoff_at="2026-08-10T00:00:00Z",
                cutoff_basis="A future cutoff cannot be observed at export time.",
                method="Reuse the already reviewed bounded workflow.",
                uncertainty="Future observations remain unavailable.",
                reason="prove future cutoff claims fail closed",
            ),
            _identity("alice"),
        )
    assert not_ready.value.reason_codes == ("REPORT_CUTOFF_AFTER_EXPORT_TIME",)
    assert service.get_project(project["id"], _identity("alice")) == before


def test_evidence_snapshot_reference_is_read_only_and_fails_closed(
    tmp_path: Path,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence-ledger")
    capture = ledger.capture(
        article_id=101,
        title="Country A energy source",
        body="Paragraph one.\nCountry A imported most of its gas supply.",
        source_url="https://example.test/article/101?token=not-persisted",
        actor_id=1,
        reason="create immutable test evidence",
        change_type="initial",
        captured_at=datetime(2026, 8, 8, 8, tzinfo=timezone.utc),
    )
    snapshot = ledger.snapshot(capture["snapshot_id"], include_body=False)
    ledger_files_before = {
        path.relative_to(ledger.root): path.read_bytes()
        for path in ledger.root.rglob("*")
        if path.is_file()
    }
    service = _service(
        tmp_path / "workspace",
        evidence_snapshot_reader=ledger,
    )
    alice = _identity("alice")
    project = service.create_project(
        ProjectCreateRequest(title="Snapshot project", reason="test ledger linkage"),
        alice,
    )
    project = service.add_evidence(
        project["id"],
        EvidenceCreateRequest(
            expected_version=project["version"],
            relation="support",
            summary="The immutable source supports the import dependency statement.",
            source_id="article-101",
            source_url="https://example.test/article/101",
            original_anchor="article-101-paragraph-2",
            article_id=101,
            evidence_snapshot_id=snapshot["snapshot_id"],
            content_sha256=snapshot["content_sha256"],
            captured_at=snapshot["first_captured_at"],
            parser_version=SNAPSHOT_PARSER_VERSION,
            reason="link an existing immutable snapshot",
        ),
        alice,
    )
    evidence = project["evidence_items"][0]
    assert evidence["snapshot_status"] == "verified"
    assert evidence["provenance_status"] == "verified"
    assert evidence["evidence_snapshot_id"] == snapshot["snapshot_id"]
    assert "normalized_body" not in snapshot
    assert {
        path.relative_to(ledger.root): path.read_bytes()
        for path in ledger.root.rglob("*")
        if path.is_file()
    } == ledger_files_before

    with pytest.raises(EvidenceSnapshotReferenceRejected, match="SNAPSHOT_HASH_MISMATCH"):
        service.add_evidence(
            project["id"],
            EvidenceCreateRequest(
                expected_version=project["version"],
                relation="background",
                summary="A mismatched reference must not be persisted.",
                source_id="article-101",
                article_id=101,
                evidence_snapshot_id=snapshot["snapshot_id"],
                content_sha256="b" * 64,
                captured_at=snapshot["first_captured_at"],
                parser_version=SNAPSHOT_PARSER_VERSION,
                reason="prove hash mismatch rejection",
            ),
            alice,
        )
    assert service.get_project(project["id"], alice)["version"] == project["version"]

    unavailable = _service(tmp_path / "other-workspace")
    other = unavailable.create_project(
        ProjectCreateRequest(title="No ledger reader", reason="test unavailable reader"),
        alice,
    )
    with pytest.raises(EvidenceSnapshotVerificationUnavailable):
        unavailable.add_evidence(
            other["id"],
            EvidenceCreateRequest(
                expected_version=other["version"],
                relation="support",
                summary="No fallback may accept this linked evidence.",
                source_id="article-101",
                article_id=101,
                evidence_snapshot_id=snapshot["snapshot_id"],
                content_sha256=snapshot["content_sha256"],
                captured_at=snapshot["first_captured_at"],
                parser_version=SNAPSHOT_PARSER_VERSION,
                reason="prove missing ledger fails closed",
            ),
            alice,
        )


def test_persisted_manifest_comparison_uses_stable_ids_and_project_acl(
    tmp_path: Path,
) -> None:
    search_ledger = SearchSnapshotLedger(tmp_path / "search-snapshots")
    linked_query = '"Country A" AND (gas OR LNG)'
    captured_search = _capture_search_snapshot(
        search_ledger,
        query=linked_query,
    )
    search_files_before = {
        path.relative_to(search_ledger.root): path.read_bytes()
        for path in search_ledger.root.rglob("*")
        if path.is_file()
    }
    service = _service(
        tmp_path / "workspace",
        search_snapshot_reader=search_ledger,
    )
    alice = _identity("alice")
    project = _complete_workflow(service)
    original_audit_count = len(project["audit_events"])
    receipt = captured_search["receipt"]
    project = service.add_saved_search(
        project["id"],
        SavedSearchCreateRequest(
            expected_version=project["version"],
            name="Country A gas dependency — captured run",
            query=linked_query,
            filters={"time_field": "published_at"},
            search_snapshot_id=captured_search["snapshot_id"],
            query_receipt_sha256=receipt["receipt_sha256"],
            normalized_contract_sha256=receipt["normalized_contract_sha256"],
            ordered_returned_ids_sha256=receipt[
                "ordered_returned_ids_sha256"
            ],
            reason="link the explicitly captured search execution receipt",
        ),
        alice,
    )
    assert {
        path.relative_to(search_ledger.root): path.read_bytes()
        for path in search_ledger.root.rglob("*")
        if path.is_file()
    } == search_files_before
    project = service.add_question(
        project["id"],
        QuestionCreateRequest(
            expected_version=project["version"],
            question="What changed after the regulator issued its new bulletin?",
            reason="add a follow-up question for version two",
        ),
        alice,
    )
    project = service.add_evidence(
        project["id"],
        EvidenceCreateRequest(
            expected_version=project["version"],
            relation="background",
            summary="A new regulator bulletin clarifies emergency allocation order.",
            source_id="bulletin-404",
            source_url="https://example.test/bulletin-404",
            original_anchor="section-4",
            reason="add newly available background context",
        ),
        alice,
    )
    project = service.create_export_manifest(
        project["id"],
        ExportManifestCreateRequest(
            expected_version=project["version"],
            report_title="Country A gas exposure — revised draft",
            cutoff_at="2026-08-09T09:30:00Z",
            cutoff_basis="Include the regulator bulletin available by the new cutoff.",
            method="Structured comparison plus regulator-bulletin update review.",
            models=[
                {
                    "name": "manual-workflow",
                    "version": "2.0",
                    "use": "No generative model was used for the judgment.",
                }
            ],
            uncertainty="Storage remains incompletely verified.",
            reason="create the second reviewed export manifest",
        ),
        alice,
    )

    comparison = service.compare_export_manifests(
        project["id"],
        from_export_version=1,
        to_export_version=2,
        user=_identity("carol"),
    )
    categories = {item["id"]: item for item in comparison["categories"]}
    assert list(categories) == [
        "research_questions",
        "saved_searches",
        "support_evidence",
        "opposing_evidence",
        "background_evidence",
        "information_gaps",
        "alternative_hypotheses",
        "judgments",
        "human_decisions",
        "peer_reviews",
        "approvals",
        "method",
        "model",
        "cutoff",
    ]
    assert categories["research_questions"]["added"][0]["id"] == (
        project["research_questions"][-1]["id"]
    )
    saved_search_diff = categories["saved_searches"]["added"][0]["value"]
    assert saved_search_diff["search_snapshot_id"] == captured_search["snapshot_id"]
    assert saved_search_diff["query_receipt_sha256"] == receipt["receipt_sha256"]
    assert saved_search_diff["normalized_contract_sha256"] == receipt[
        "normalized_contract_sha256"
    ]
    assert saved_search_diff["ordered_returned_ids_sha256"] == receipt[
        "ordered_returned_ids_sha256"
    ]
    assert project["export_manifests"][1]["saved_searches"][-1] == saved_search_diff
    assert categories["background_evidence"]["added"][0]["id"] == (
        project["evidence_items"][-1]["id"]
    )
    assert categories["method"]["modified"][0]["id"] == "method"
    assert categories["model"]["modified"][0]["id"] == "model"
    assert categories["cutoff"]["modified"][0]["id"] == "cutoff"
    assert comparison["access"]["content_visibility"] == "project-acl"
    assert comparison["access"]["audit_event_created"] is False
    assert "What changed after" in json.dumps(comparison, ensure_ascii=False)

    app = FastAPI()
    app.include_router(research_routes.router)
    app.dependency_overrides[get_current_user_required] = lambda: _identity("carol")
    app.dependency_overrides[research_routes.get_research_workflow_service] = (
        lambda: service
    )
    response = TestClient(app).get(
        f"/api/research/projects/{project['id']}/export-comparisons",
        params={"from_export_version": 1, "to_export_version": 2},
    )
    assert response.status_code == 200
    assert response.json() == comparison

    with pytest.raises(ResearchAccessDenied):
        service.compare_export_manifests(
            project["id"],
            from_export_version=1,
            to_export_version=2,
            user=_identity("mallory"),
        )
    assert len(service.get_project(project["id"], alice)["audit_events"]) == (
        original_audit_count + 4
    )

    reloaded = ResearchWorkflowService(
        WorkspaceResearchRepository(tmp_path / "workspace")
    ).compare_export_manifests(
        project["id"],
        from_export_version=1,
        to_export_version=2,
        user=_identity("bob"),
    )
    assert reloaded == comparison


def test_corrupt_or_unsafe_storage_never_falls_back_to_memory(tmp_path: Path) -> None:
    assert SAFE_USERNAME_RE.fullmatch(STORE_DIRECTORY) is None
    workspace = tmp_path / "workspace"
    service = _service(workspace)
    project = service.create_project(
        ProjectCreateRequest(title="Durable project", reason="create durable state"),
        _identity("alice"),
    )
    state_path = (
        workspace / ".research-workflow-v1+store" / project["id"] / "state.json"
    )
    state_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ResearchRepositoryUnavailable):
        service.get_project(project["id"], _identity("alice"))

    unsafe_root = tmp_path / "not-a-directory"
    unsafe_root.write_text("occupied", encoding="utf-8")
    unsafe_repository = WorkspaceResearchRepository(unsafe_root)
    assert unsafe_repository.availability() == (False, "WORKSPACE_ROOT_NOT_DIRECTORY")
    with pytest.raises(ResearchRepositoryUnavailable):
        ResearchWorkflowService(unsafe_repository).create_project(
            ProjectCreateRequest(title="No fallback", reason="must fail closed"),
            _identity("alice"),
        )

    bounded = ResearchWorkflowService(
        WorkspaceResearchRepository(tmp_path / "bounded", max_state_bytes=1024)
    )
    with pytest.raises(ResearchRepositoryCapacityExceeded):
        bounded.create_project(
            ProjectCreateRequest(
                title="State ceiling",
                description="x" * 800,
                reason="prove storage remains bounded",
            ),
            _identity("alice"),
        )

    project_limited = ResearchWorkflowService(
        WorkspaceResearchRepository(
            tmp_path / "project-limited", max_projects_per_owner=1
        )
    )
    project_limited.create_project(
        ProjectCreateRequest(title="First project", reason="use the only project slot"),
        _identity("alice"),
    )
    with pytest.raises(ResearchRepositoryCapacityExceeded):
        project_limited.create_project(
            ProjectCreateRequest(title="Second project", reason="prove owner cap"),
            _identity("alice"),
        )


def test_repository_rejects_linked_locks_and_enforces_exact_project_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dangling_workspace = tmp_path / "dangling-workspace"
    dangling_store = dangling_workspace / STORE_DIRECTORY
    dangling_store.mkdir(parents=True)
    (dangling_store / ".locks").symlink_to(tmp_path / "missing-lock-target")
    dangling_repository = WorkspaceResearchRepository(dangling_workspace)
    assert dangling_repository.availability() == (
        False,
        "RESEARCH_STORE_LOCK_ROOT_UNSAFE",
    )

    symlink_workspace = tmp_path / "symlink-workspace"
    store = symlink_workspace / STORE_DIRECTORY
    outside = tmp_path / "outside-locks"
    store.mkdir(parents=True)
    outside.mkdir()
    (store / ".locks").symlink_to(outside, target_is_directory=True)
    linked_repository = WorkspaceResearchRepository(symlink_workspace)
    assert linked_repository.availability() == (False, "RESEARCH_STORE_LOCK_ROOT_UNSAFE")
    with pytest.raises(ResearchRepositoryUnavailable):
        ResearchWorkflowService(linked_repository).create_project(
            ProjectCreateRequest(title="No lock escape", reason="reject linked lock root"),
            _identity("alice"),
        )
    assert list(outside.iterdir()) == []

    hardlink_workspace = tmp_path / "hardlink-workspace"
    hardlink_service = _service(hardlink_workspace)
    hardlink_service.create_project(
        ProjectCreateRequest(title="First", reason="create repository lock"),
        _identity("alice"),
    )
    repository_lock = hardlink_workspace / STORE_DIRECTORY / ".locks" / "repository.lock"
    second_link = tmp_path / "repository-lock-second-link"
    os.link(repository_lock, second_link)
    with pytest.raises(ResearchRepositoryUnavailable):
        hardlink_service.create_project(
            ProjectCreateRequest(title="Second", reason="reject hardlinked lock"),
            _identity("bob"),
        )

    bounded_workspace = tmp_path / "inventory-workspace"
    bounded_service = _service(bounded_workspace)
    bounded_service.create_project(
        ProjectCreateRequest(title="Alice project", reason="first bounded project"),
        _identity("alice"),
    )
    bounded_service.create_project(
        ProjectCreateRequest(title="Bob project", reason="second bounded project"),
        _identity("bob"),
    )
    lock_root = bounded_workspace / STORE_DIRECTORY / ".locks"
    for lock_file in lock_root.iterdir():
        lock_file.unlink()
    lock_root.rmdir()
    monkeypatch.setattr(research_repository, "MAX_REPOSITORY_PROJECT_ENTRIES", 1)
    with pytest.raises(
        ResearchRepositoryUnavailable,
        match="RESEARCH_STORE_INVENTORY_LIMIT_EXCEEDED",
    ):
        bounded_service.repository.list_projects()


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://user:password@example.test/source",
        "https://example.test/source?access_token=secret-canary",
        "https://example.test/source#clientSecret=secret-canary",
        "https://example.test/source\\@attacker.invalid",
        "https://example.test/source\nX-Injected: yes",
    ],
)
def test_evidence_source_urls_reject_credentials_secret_queries_and_ambiguous_syntax(
    unsafe_url: str,
) -> None:
    with pytest.raises(ValueError):
        EvidenceCreateRequest(
            expected_version=1,
            relation="support",
            summary="Bounded source summary",
            source_id="source-1",
            source_url=unsafe_url,
            reason="reject unsafe source locator",
        )


def test_route_requires_authentication_and_returns_structured_version_conflict(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    app.include_router(research_routes.router)
    client = TestClient(app)
    assert client.get("/api/research/projects").status_code == 401

    service = _service(tmp_path / "workspace")
    app.dependency_overrides[get_current_user_required] = lambda: _identity("alice")
    app.dependency_overrides[research_routes.get_research_workflow_service] = (
        lambda: service
    )
    created = client.post(
        "/api/research/projects",
        json={"title": "HTTP project", "reason": "exercise the route contract"},
    )
    assert created.status_code == 201
    project = created.json()

    first = client.post(
        f"/api/research/projects/{project['id']}/questions",
        json={
            "expected_version": 1,
            "question": "Does the route preserve optimistic concurrency?",
            "reason": "create the first versioned question",
        },
    )
    assert first.status_code == 200
    stale = client.post(
        f"/api/research/projects/{project['id']}/questions",
        json={
            "expected_version": 1,
            "question": "Can a stale client overwrite it?",
            "reason": "prove the conflict response",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "PROJECT_VERSION_CONFLICT",
        "expected_version": 1,
        "current_version": 2,
    }

    unavailable_root = tmp_path / "unavailable-root"
    unavailable_root.write_text("not a directory", encoding="utf-8")
    unavailable_service = ResearchWorkflowService(
        WorkspaceResearchRepository(unavailable_root)
    )
    app.dependency_overrides[research_routes.get_research_workflow_service] = (
        lambda: unavailable_service
    )
    storage = client.get("/api/research/storage-status")
    assert storage.status_code == 503
    assert storage.json()["detail"] == {
        "schema_version": "research-storage-status-v1",
        "status": "unavailable",
        "durability": "unavailable",
        "fallback": "none",
        "reason_code": "WORKSPACE_ROOT_NOT_DIRECTORY",
    }


@pytest.mark.parametrize(
    "raw_body",
    [
        b'{"title":"first","title":"second","reason":"duplicate title"}',
        b'{"title":"non-finite","scope_countries":[NaN],"reason":"invalid number"}',
    ],
)
def test_mutation_routes_reject_ambiguous_json_before_any_storage_write(
    tmp_path: Path,
    raw_body: bytes,
) -> None:
    workspace = tmp_path / "zero-write-rejection"
    service = _service(workspace)
    app = FastAPI()
    app.include_router(research_routes.router)
    app.dependency_overrides[get_current_user_required] = lambda: _identity("alice")
    app.dependency_overrides[research_routes.get_research_workflow_service] = (
        lambda: service
    )

    response = TestClient(app).post(
        "/api/research/projects",
        content=raw_body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "RESEARCH_JSON_AMBIGUOUS"
    assert not workspace.exists()
