from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.features.research_workflow import (
    MAX_SAVED_SEARCH_MONITORING_ITEMS,
    SAVED_SEARCH_MONITORING_SCHEMA_VERSION,
    SavedSearchMonitoringUnavailable,
    build_saved_search_monitoring_status,
)
from api.routes import research_workflow as research_routes
from api.services.auth import get_current_user_required


def _project() -> dict[str, object]:
    return {
        "id": "project-01",
        "saved_searches": [
            {
                "id": "saved-01",
                "name": "sensitive-name-canary",
                "query": "sensitive-query-canary",
                "filters": {"token": "sensitive-filter-canary"},
                "query_sha256": "a" * 64,
                "snapshot_status": "verified",
                "search_snapshot_id": (
                    "search-snap-20260809T010203000000Z-0123456789abcdef"
                ),
            },
            {
                "id": "saved-02",
                "name": "another-name-canary",
                "query": "another-query-canary",
                "filters": {},
                "query_sha256": "b" * 64,
                "snapshot_status": "unavailable",
                "search_snapshot_id": None,
            },
        ],
    }


def test_saved_search_monitoring_status_is_bounded_content_free_and_fail_closed() -> None:
    status = build_saved_search_monitoring_status(_project())

    assert status == {
        "schema_version": SAVED_SEARCH_MONITORING_SCHEMA_VERSION,
        "project_id": "project-01",
        "evidence_scope": "project_saved_search_records_only",
        "read_side_effects": "none",
        "scheduler_state": "not_configured",
        "checkpoint_state": "not_established",
        "delta_semantics_state": "not_established",
        "new_only_state": "not_available",
        "notification_state": "not_configured",
        "items": [
            {
                "saved_search_id": "saved-01",
                "query_contract_sha256": "a" * 64,
                "linked_snapshot_state": "verified",
                "monitor_run_state": "never_run",
                "last_monitor_run_at": None,
                "checkpoint_snapshot_id": None,
                "delta_state": "not_computable",
                "added_result_count": None,
                "new_only_available": False,
                "notification_delivery_state": "not_configured",
                "reason_code": "SCHEDULER_CHECKPOINT_AND_DELTA_NOT_CONFIGURED",
            },
            {
                "saved_search_id": "saved-02",
                "query_contract_sha256": "b" * 64,
                "linked_snapshot_state": "unavailable",
                "monitor_run_state": "never_run",
                "last_monitor_run_at": None,
                "checkpoint_snapshot_id": None,
                "delta_state": "not_computable",
                "added_result_count": None,
                "new_only_available": False,
                "notification_delivery_state": "not_configured",
                "reason_code": "SCHEDULER_CHECKPOINT_AND_DELTA_NOT_CONFIGURED",
            },
        ],
    }
    serialized = str(status)
    assert "sensitive-name-canary" not in serialized
    assert "sensitive-query-canary" not in serialized
    assert "sensitive-filter-canary" not in serialized
    assert "search-snap-" not in serialized


@pytest.mark.parametrize(
    "mutate",
    [
        lambda project: project["saved_searches"].append(
            deepcopy(project["saved_searches"][0])
        ),
        lambda project: project["saved_searches"][0].__setitem__("id", ""),
        lambda project: project["saved_searches"][0].__setitem__(
            "query_sha256", "not-a-sha"
        ),
        lambda project: project["saved_searches"][0].__setitem__(
            "snapshot_status", "complete"
        ),
        lambda project: project.__setitem__("saved_searches", "not-a-list"),
    ],
)
def test_saved_search_monitoring_rejects_ambiguous_or_malformed_records(mutate) -> None:
    project = _project()
    mutate(project)
    with pytest.raises(SavedSearchMonitoringUnavailable):
        build_saved_search_monitoring_status(project)


def test_saved_search_monitoring_rejects_overflow_without_truncation() -> None:
    project = _project()
    project["saved_searches"] = [
        {
            **deepcopy(project["saved_searches"][0]),
            "id": f"saved-{index:04d}",
        }
        for index in range(MAX_SAVED_SEARCH_MONITORING_ITEMS + 1)
    ]
    with pytest.raises(SavedSearchMonitoringUnavailable):
        build_saved_search_monitoring_status(project)


def test_saved_search_monitoring_http_is_authenticated_no_store_and_read_only() -> None:
    class StubService:
        calls = 0

        def get_project(self, project_id, user):
            self.calls += 1
            assert project_id == "project-01"
            assert user == {"user_id": 7, "username": "alice"}
            return _project()

    service = StubService()
    app = FastAPI()
    app.include_router(research_routes.router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 7,
        "username": "alice",
    }
    app.dependency_overrides[research_routes.get_research_workflow_service] = (
        lambda: service
    )

    response = TestClient(app).get(
        "/api/research/projects/project-01/saved-search-monitoring"
    )
    assert response.status_code == 200
    assert response.json()["schema_version"] == SAVED_SEARCH_MONITORING_SCHEMA_VERSION
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["vary"] == "Authorization"
    assert service.calls == 1


def test_saved_search_monitoring_http_failure_is_generic_and_no_store() -> None:
    class InvalidService:
        def get_project(self, _project_id, _user):
            project = _project()
            project["saved_searches"][0]["query_sha256"] = "secret-invalid-canary"
            return project

    app = FastAPI()
    app.include_router(research_routes.router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 7,
        "username": "alice",
    }
    app.dependency_overrides[research_routes.get_research_workflow_service] = (
        InvalidService
    )

    response = TestClient(app).get(
        "/api/research/projects/project-01/saved-search-monitoring"
    )
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "SAVED_SEARCH_MONITORING_UNAVAILABLE",
            "reason_code": "SAVED_SEARCH_MONITORING_RECORD_INVALID",
            "fallback": "none",
        }
    }
    assert "secret-invalid-canary" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["vary"] == "Authorization"
