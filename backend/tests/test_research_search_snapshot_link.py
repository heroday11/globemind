from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.features.research_workflow import (
    MemberChangeRequest,
    ProjectCreateRequest,
    ResearchWorkflowService,
    SavedSearchCreateRequest,
    SearchSnapshotReferenceRejected,
    WorkspaceResearchRepository,
    verify_search_snapshot_reference,
)
from api.features.search import SearchSnapshotLedger, build_query_receipt
from api.models.schemas import NewsItem, SearchRequest, SearchResponse
from api.routes import research_workflow as research_routes
from api.services import news_search_v2
from api.services.auth import get_current_user_required

_QUERY = '(China OR Japan) AND NOT "trade war"'
_CAPTURED_AT = datetime(2026, 8, 9, 7, tzinfo=timezone.utc)


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.value:032x}"


class _StaticReader:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = snapshot

    def get(self, actor_id: int, snapshot_id: str) -> dict:
        return self.snapshot


def _identity(user_id: int = 1, username: str = "alice") -> dict[str, object]:
    return {"user_id": user_id, "username": username, "role": "user"}


def _service(root: Path, *, reader=None) -> ResearchWorkflowService:
    return ResearchWorkflowService(
        WorkspaceResearchRepository(root),
        clock=lambda: "2026-08-09T10:00:00Z",
        id_factory=_Ids(),
        search_snapshot_reader=reader,
    )


def _capture(ledger: SearchSnapshotLedger, *, actor_id: int = 1) -> dict:
    params = SearchRequest(keyword=_QUERY, page=1, page_size=2)
    response = SearchResponse(
        data=[
            NewsItem(
                id=9,
                title="China and Japan trade result",
                body="this body must never enter research saved-search metadata",
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
        captured_at=_CAPTURED_AT,
    )


def _reference(capture: dict) -> dict[str, object]:
    receipt = capture["receipt"]
    return {
        "search_snapshot_id": capture["snapshot_id"],
        "query_receipt_sha256": receipt["receipt_sha256"],
        "normalized_contract_sha256": receipt["normalized_contract_sha256"],
        "ordered_returned_ids_sha256": receipt["ordered_returned_ids_sha256"],
    }


def _body(project: dict, capture: dict, **overrides) -> dict[str, object]:
    return {
        "expected_version": project["version"],
        "reason": "link one explicit search capture",
        "name": "China or Japan without trade war",
        "query": _QUERY,
        "filters": {"time_field": "published_at"},
        **_reference(capture),
        **overrides,
    }


def _app(service: ResearchWorkflowService, identity: dict) -> FastAPI:
    app = FastAPI()
    app.include_router(research_routes.router)
    app.dependency_overrides[get_current_user_required] = lambda: identity
    app.dependency_overrides[research_routes.get_research_workflow_service] = (
        lambda: service
    )
    return app


def test_missing_snapshot_is_honestly_unavailable_and_reads_never_create_ledger(
    tmp_path: Path,
) -> None:
    snapshot_root = tmp_path / "search-snapshots"
    reader = SearchSnapshotLedger(snapshot_root)
    service = _service(tmp_path / "workspace", reader=reader)
    alice = _identity()

    assert not snapshot_root.exists()
    project = service.create_project(
        ProjectCreateRequest(title="No snapshot project", reason="create the project"),
        alice,
    )
    project = service.add_saved_search(
        project["id"],
        SavedSearchCreateRequest(
            expected_version=project["version"],
            reason="save a declared query without a captured execution",
            name="Declared query",
            query="China",
            filters={},
        ),
        alice,
    )
    saved = project["saved_searches"][0]
    assert saved["snapshot_status"] == "unavailable"
    assert saved["snapshot_reason"] == "SEARCH_SNAPSHOT_NOT_PROVIDED"
    assert saved["search_snapshot_id"] is None
    assert saved["query_receipt_sha256"] is None
    assert not snapshot_root.exists()

    with pytest.raises(SearchSnapshotReferenceRejected, match="SEARCH_SNAPSHOT_NOT_FOUND"):
        verify_search_snapshot_reference(
            reader,
            actor_id=1,
            snapshot_id="search-snap-20260809T010203000000Z-0000000000000000",
            query_receipt_sha256="a" * 64,
            normalized_contract_sha256="b" * 64,
            ordered_returned_ids_sha256="c" * 64,
            declared_query="China",
        )
    assert not snapshot_root.exists()


def test_verified_snapshot_metadata_is_persisted_for_project_acl_without_bodies(
    tmp_path: Path,
) -> None:
    ledger = SearchSnapshotLedger(tmp_path / "search-snapshots")
    capture = _capture(ledger)
    ledger_files = {
        path.relative_to(ledger.root): path.read_bytes()
        for path in ledger.root.rglob("*")
        if path.is_file()
    }
    service = _service(tmp_path / "workspace", reader=ledger)
    alice = _identity()
    project = service.create_project(
        ProjectCreateRequest(title="Linked snapshot", reason="create linked project"),
        alice,
    )
    project = service.add_saved_search(
        project["id"],
        SavedSearchCreateRequest.model_validate(_body(project, capture)),
        alice,
    )
    saved = project["saved_searches"][0]

    assert saved["snapshot_status"] == "verified"
    assert saved["snapshot_reason"] == "SEARCH_SNAPSHOT_REFERENCE_VERIFIED"
    assert saved["search_snapshot_id"] == capture["snapshot_id"]
    assert saved["query_receipt_sha256"] == capture["receipt_sha256"]
    assert saved["result_cutoff"] == "2026-08-08T08:00:00Z"
    assert saved["returned_result_count"] == 1
    assert saved["result_total"] == 1
    serialized = json.dumps(saved, ensure_ascii=False)
    assert "this body must never enter research" not in serialized
    assert "ordered_returned_ids" not in saved
    assert {
        path.relative_to(ledger.root): path.read_bytes()
        for path in ledger.root.rglob("*")
        if path.is_file()
    } == ledger_files

    project = service.set_member(
        project["id"],
        "carol",
        MemberChangeRequest(
            expected_version=project["version"],
            role="reader",
            reason="share verified metadata through project ACL",
        ),
        alice,
    )
    # A fresh service has no ledger reader. The member reads only already
    # persisted verification metadata under the original project ACL.
    shared = ResearchWorkflowService(
        WorkspaceResearchRepository(tmp_path / "workspace")
    ).get_project(project["id"], _identity(2, "carol"))
    assert shared["saved_searches"][0]["query_receipt_sha256"] == saved[
        "query_receipt_sha256"
    ]


def test_reference_validator_rechecks_record_integrity_and_declared_query(
    tmp_path: Path,
) -> None:
    capture = _capture(SearchSnapshotLedger(tmp_path / "search-snapshots"))
    reference = _reference(capture)

    altered = dict(capture)
    altered["integrity_sha256"] = "0" * 64
    with pytest.raises(
        SearchSnapshotReferenceRejected,
        match="SEARCH_SNAPSHOT_INTEGRITY_HASH_MISMATCH",
    ):
        verify_search_snapshot_reference(
            _StaticReader(altered),
            actor_id=1,
            snapshot_id=str(reference["search_snapshot_id"]),
            query_receipt_sha256=str(reference["query_receipt_sha256"]),
            normalized_contract_sha256=str(
                reference["normalized_contract_sha256"]
            ),
            ordered_returned_ids_sha256=str(
                reference["ordered_returned_ids_sha256"]
            ),
            declared_query=_QUERY,
        )

    with pytest.raises(
        SearchSnapshotReferenceRejected,
        match="SEARCH_SNAPSHOT_QUERY_TEXT_MISMATCH",
    ):
        verify_search_snapshot_reference(
            _StaticReader(capture),
            actor_id=1,
            snapshot_id=str(reference["search_snapshot_id"]),
            query_receipt_sha256=str(reference["query_receipt_sha256"]),
            normalized_contract_sha256=str(
                reference["normalized_contract_sha256"]
            ),
            ordered_returned_ids_sha256=str(
                reference["ordered_returned_ids_sha256"]
            ),
            declared_query="China",
        )


def test_partial_snapshot_group_is_422_without_project_or_ledger_mutation(
    tmp_path: Path,
) -> None:
    ledger = SearchSnapshotLedger(tmp_path / "search-snapshots")
    capture = _capture(ledger)
    service = _service(tmp_path / "workspace", reader=ledger)
    alice = _identity()
    project = service.create_project(
        ProjectCreateRequest(title="Partial link", reason="create partial test"),
        alice,
    )
    before = {
        path.relative_to(ledger.root): path.read_bytes()
        for path in ledger.root.rglob("*")
        if path.is_file()
    }
    response = TestClient(_app(service, alice)).post(
        f"/api/research/projects/{project['id']}/saved-searches",
        json={
            "expected_version": project["version"],
            "reason": "submit an incomplete reference group",
            "name": "Incomplete link",
            "query": _QUERY,
            "filters": {},
            "search_snapshot_id": capture["snapshot_id"],
            "query_receipt_sha256": capture["receipt_sha256"],
        },
    )

    assert response.status_code == 422
    assert service.get_project(project["id"], alice)["version"] == project["version"]
    assert {
        path.relative_to(ledger.root): path.read_bytes()
        for path in ledger.root.rglob("*")
        if path.is_file()
    } == before


def test_cross_user_and_wrong_receipt_references_return_409(tmp_path: Path) -> None:
    ledger = SearchSnapshotLedger(tmp_path / "search-snapshots")
    capture = _capture(ledger, actor_id=1)

    cross_service = _service(tmp_path / "cross-workspace", reader=ledger)
    alice_two = _identity(2, "alice")
    cross_project = cross_service.create_project(
        ProjectCreateRequest(title="Cross user", reason="create cross-user test"),
        alice_two,
    )
    cross = TestClient(_app(cross_service, alice_two)).post(
        f"/api/research/projects/{cross_project['id']}/saved-searches",
        json=_body(cross_project, capture),
    )
    assert cross.status_code == 409
    assert cross.json()["detail"] == {
        "code": "SEARCH_SNAPSHOT_REFERENCE_REJECTED",
        "reason_code": "SEARCH_SNAPSHOT_NOT_FOUND",
    }

    own_service = _service(tmp_path / "own-workspace", reader=ledger)
    alice = _identity()
    own_project = own_service.create_project(
        ProjectCreateRequest(title="Wrong receipt", reason="create hash test"),
        alice,
    )
    wrong = TestClient(_app(own_service, alice)).post(
        f"/api/research/projects/{own_project['id']}/saved-searches",
        json=_body(own_project, capture, query_receipt_sha256="f" * 64),
    )
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["reason_code"] == (
        "SEARCH_QUERY_RECEIPT_HASH_MISMATCH"
    )
    assert own_service.get_project(own_project["id"], alice)["version"] == 1


def test_tampered_or_unavailable_ledger_returns_503_without_fallback(
    tmp_path: Path,
) -> None:
    ledger = SearchSnapshotLedger(tmp_path / "search-snapshots")
    capture = _capture(ledger)
    alice = _identity()

    unavailable_service = _service(tmp_path / "unavailable-workspace", reader=None)
    unavailable_project = unavailable_service.create_project(
        ProjectCreateRequest(title="Unavailable ledger", reason="create unavailable test"),
        alice,
    )
    unavailable = TestClient(_app(unavailable_service, alice)).post(
        f"/api/research/projects/{unavailable_project['id']}/saved-searches",
        json=_body(unavailable_project, capture),
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == {
        "code": "SEARCH_SNAPSHOT_VERIFICATION_UNAVAILABLE",
        "reason_code": "SEARCH_SNAPSHOT_LEDGER_READER_UNAVAILABLE",
        "fallback": "none",
    }

    record_path = (
        ledger.root
        / "users"
        / "1"
        / "records"
        / f"{capture['snapshot_id']}.json"
    )
    tampered = json.loads(record_path.read_text(encoding="utf-8"))
    tampered["body_persistence"] = "allowed"
    record_path.write_text(json.dumps(tampered), encoding="utf-8")
    tampered_service = _service(tmp_path / "tampered-workspace", reader=ledger)
    tampered_project = tampered_service.create_project(
        ProjectCreateRequest(title="Tampered ledger", reason="create tamper test"),
        alice,
    )
    response = TestClient(_app(tampered_service, alice)).post(
        f"/api/research/projects/{tampered_project['id']}/saved-searches",
        json=_body(tampered_project, capture),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "SEARCH_SNAPSHOT_VERIFICATION_UNAVAILABLE",
        "reason_code": "SEARCH_SNAPSHOT_LEDGER_UNAVAILABLE",
        "fallback": "none",
    }
    assert tampered_service.get_project(tampered_project["id"], alice)["version"] == 1


def test_snapshot_link_requires_canonical_user_id(tmp_path: Path) -> None:
    ledger = SearchSnapshotLedger(tmp_path / "search-snapshots")
    capture = _capture(ledger)
    service = _service(tmp_path / "workspace", reader=ledger)
    alice = _identity()
    project = service.create_project(
        ProjectCreateRequest(title="Canonical identity", reason="create identity test"),
        alice,
    )
    legacy_identity = {"id": 1, "username": "alice", "role": "user"}
    response = TestClient(_app(service, legacy_identity)).post(
        f"/api/research/projects/{project['id']}/saved-searches",
        json=_body(project, capture),
    )

    assert response.status_code == 403
    assert service.get_project(project["id"], alice)["version"] == 1


def test_route_service_constructor_uses_search_facade_reader_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "route-search-snapshots"
    monkeypatch.setenv("SEARCH_SNAPSHOT_ROOT", str(root))
    monkeypatch.setenv("GLOBEMIND_WORKSPACE_ROOT", str(tmp_path / "workspace"))

    service = research_routes.get_research_workflow_service()

    assert isinstance(service.search_snapshot_reader, SearchSnapshotLedger)
    assert service.search_snapshot_reader.root == root
    assert not root.exists()

    alice = _identity()
    project = service.create_project(
        ProjectCreateRequest(title="Read-only route", reason="create GET fixture"),
        alice,
    )
    app = FastAPI()
    app.include_router(research_routes.router)
    app.dependency_overrides[get_current_user_required] = lambda: alice

    response = TestClient(app).get(f"/api/research/projects/{project['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == project["id"]
    assert not root.exists()
