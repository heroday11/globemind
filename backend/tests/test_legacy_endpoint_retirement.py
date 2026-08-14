from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from api.application import app
from api.core.db import get_db
from api.features import legacy_retirement
from api.routes.opinion_v2 import get_opinion_db

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def retirement_client() -> Iterator[TestClient]:
    previous_overrides = dict(app.dependency_overrides)

    def reject_database_dependency() -> None:
        raise AssertionError("retired endpoint resolved a database dependency")

    app.dependency_overrides[get_db] = reject_database_dependency
    app.dependency_overrides[get_opinion_db] = reject_database_dependency
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


@pytest.mark.parametrize(
    "endpoint",
    sorted(legacy_retirement.RETIRED_OPINION_ENDPOINTS),
)
def test_retired_opinion_endpoint_always_returns_stable_410_contract(
    retirement_client: TestClient,
    endpoint: str,
) -> None:
    response = retirement_client.get(endpoint, params={"ignored": "legacy-value"})

    assert response.status_code == 410
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == legacy_retirement.retired_endpoint_contract(
        endpoint
    ).model_dump()


def test_retired_routes_have_no_query_or_database_dependency() -> None:
    retired_routes = {
        route.path: route
        for route in app.routes
        if route.path in legacy_retirement.RETIRED_OPINION_ENDPOINTS
        and "GET" in getattr(route, "methods", set())
    }

    assert set(retired_routes) == legacy_retirement.RETIRED_OPINION_ENDPOINTS
    for route in retired_routes.values():
        assert route.status_code == 410
        assert route.response_model is legacy_retirement.RetiredEndpointResponse
        assert route.deprecated is True
        assert route.dependant.dependencies == []
        assert route.dependant.query_params == []


def test_retired_registry_rejects_unregistered_endpoint() -> None:
    with pytest.raises(ValueError, match="not registered as retired"):
        legacy_retirement.retired_endpoint_contract("/api/opinion/not-retired")


def test_active_graph_consumers_are_not_registered_as_retired() -> None:
    assert not any(
        endpoint.startswith("/api/graph/")
        for endpoint in legacy_retirement.RETIRED_OPINION_ENDPOINTS
    )
    bridge_source = (PROJECT_ROOT / "backend/cppt/cc_bridge.py").read_text(
        encoding="utf-8"
    )
    for active_path in (
        "/api/graph/macros/search",
        "/api/graph/macro/",
        "/api/graph/micro/",
        "/api/graph/micros/news-batch",
    ):
        assert active_path in bridge_source


def test_legacy_retirement_feature_has_no_route_or_database_dependency() -> None:
    feature_root = PROJECT_ROOT / "backend/api/features/legacy_retirement"
    forbidden = ("api.routes", "api.core.db", "sqlalchemy", "fastapi")

    for source_path in feature_root.glob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden), source_path
