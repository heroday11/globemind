from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.db import get_db
from api.features import search as search_feature
from api.features.search import V11SearchContractError
from api.routes import search as search_route

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MISSING_RELATIONS = ("macro_event" + "_coref", "micro_story" + "_coref")


@dataclass
class _Result:
    scalar_value: Any = None
    rows: list[dict[str, Any]] = field(default_factory=list)

    def scalar(self) -> Any:
        return self.scalar_value

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class _Session:
    def __init__(self, *results: _Result):
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(statement), dict(parameters or {})))
        if not self.results:
            raise AssertionError(f"unexpected SQL: {statement}")
        return self.results.pop(0)


def _cluster_row(identifier: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": f"Title {identifier}",
        "article_count": 12,
        "children_count": 3,
        "initiator": "A",
        "target": "B",
        "start_date": "2026-07-01",
        "end_date": "2026-07-10",
        "event_type": "diplomacy",
        "dominant_trigger": None,
    }


@pytest.mark.parametrize(
    ("level", "relation"),
    [
        ("macro", "event_l3_macro_events"),
        ("micro", "event_l2_chains"),
        ("cluster", "event_coref_clusters"),
    ],
)
def test_v11_search_projects_current_hierarchy(level: str, relation: str) -> None:
    session = _Session(_Result(scalar_value=1), _Result(rows=[_cluster_row("node-1")]))
    request = search_feature.V11ClusterSearchRequest(
        keyword="supply chain",
        level=level,
        page=1,
        page_size=20,
    )

    response = search_feature.search_v11_clusters(session, request)

    assert response.model_dump() == {
        "items": [
            {
                "id": "node-1",
                "title": "Title node-1",
                "level": level,
                "article_count": 12,
                "children_count": 3,
                "children": [],
                "initiator": "A",
                "target": "B",
                "start_date": "2026-07-01",
                "end_date": "2026-07-10",
                "event_type": "diplomacy",
                "dominant_trigger": None,
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 20,
        "total_pages": 1,
        "has_next": False,
        "has_prev": False,
    }
    assert all(relation in sql for sql, _bind in session.calls)
    assert all("supply chain" not in sql for sql, _bind in session.calls)
    assert all(not any(name in sql for name in MISSING_RELATIONS) for sql, _ in session.calls)


def test_v11_search_falls_back_to_articles_with_real_pagination() -> None:
    session = _Session(
        _Result(scalar_value=0),
        _Result(scalar_value=2),
        _Result(rows=[_cluster_row("macro-2")]),
    )
    request = search_feature.V11ClusterSearchRequest(
        keyword="chip%_",
        level="macro",
        page=2,
        page_size=1,
    )

    response = search_feature.search_v11_clusters(session, request)

    assert response.total == 2
    assert response.total_pages == 2
    assert response.has_prev is True
    assert response.has_next is False
    fallback_sql, fallback_bind = session.calls[1]
    assert "event_l3_macro_members" in fallback_sql
    assert "event_l2_chain_segments" in fallback_sql
    assert "event_coref_members" in fallback_sql
    assert "public.news" in fallback_sql
    assert "article.abstract" not in fallback_sql
    assert "article.body" in fallback_sql
    assert fallback_bind == {"keyword": "%chip!%!_%"}
    assert session.calls[2][1]["limit"] == 1
    assert session.calls[2][1]["offset"] == 1


def test_empty_v11_search_does_not_touch_the_database() -> None:
    session = _Session()
    response = search_feature.search_v11_clusters(
        session,
        search_feature.V11ClusterSearchRequest(keyword="   ", page=3, page_size=5),
    )

    assert response.items == []
    assert response.page == 3
    assert response.page_size == 5
    assert session.calls == []


@pytest.mark.parametrize(
    ("parent_level", "count_relation", "expected_child_level", "row"),
    [
        ("macro", "event_l3_macro_members", "micro", _cluster_row("l2-a")),
        ("l3", "event_l3_macro_members", "l2", _cluster_row("l2-a")),
        ("micro", "event_l2_chain_segments", "cluster", _cluster_row("l1-a")),
        ("l2", "event_l2_chain_segments", "l1", _cluster_row("l1-a")),
        (
            "cluster",
            "event_coref_members",
            "news",
            {
                "id": 42,
                "title": "Article",
                "abstract": "Summary",
                "pub_time": "2026-07-11 01:02:03",
                "request_url": "https://example.test/article",
                "language_id": "CN",
            },
        ),
        (
            "l1",
            "event_coref_members",
            "news",
            {
                "id": 42,
                "title": "Article",
                "abstract": "Summary",
                "pub_time": "2026-07-11 01:02:03",
                "request_url": "https://example.test/article",
                "language_id": "CN",
            },
        ),
    ],
)
def test_v11_children_preserve_legacy_and_current_level_labels(
    parent_level: str,
    count_relation: str,
    expected_child_level: str,
    row: dict[str, Any],
) -> None:
    session = _Session(_Result(scalar_value=1), _Result(rows=[row]))

    response = search_feature.expand_v11_cluster_children(
        session,
        "node_123",
        parent_level,
        page=1,
        page_size=10,
    )

    assert response["parent_level"] == parent_level
    assert response["child_level"] == expected_child_level
    assert response["items"][0]["level"] == expected_child_level
    assert response["total"] == 1
    assert response["total_pages"] == 1
    assert count_relation in session.calls[0][0]
    assert session.calls[0][1] == {"item_id": "node_123"}
    assert session.calls[1][1] == {
        "item_id": "node_123",
        "limit": 10,
        "offset": 0,
    }
    assert all("node_123" not in sql for sql, _bind in session.calls)
    assert all(not any(name in sql for name in MISSING_RELATIONS) for sql, _ in session.calls)


@pytest.mark.parametrize(
    "item_id",
    ["", "with space", "../escape", "node/child", "x" * 257, "invalid-node" + "\x00"],
)
def test_v11_children_reject_invalid_ids_without_querying(item_id: str) -> None:
    session = _Session()

    with pytest.raises(V11SearchContractError, match="item_id"):
        search_feature.expand_v11_cluster_children(session, item_id, "cluster")

    assert session.calls == []


def test_v11_children_reject_unknown_level_without_querying() -> None:
    session = _Session()

    with pytest.raises(V11SearchContractError, match="level must be"):
        search_feature.expand_v11_cluster_children(session, "node-1", "root")

    assert session.calls == []


def test_unknown_but_valid_v11_id_returns_an_empty_page() -> None:
    session = _Session(_Result(scalar_value=0))

    response = search_feature.expand_v11_cluster_children(
        session,
        "missing-node",
        "cluster",
    )

    assert response["items"] == []
    assert response["total"] == 0
    assert response["total_pages"] == 0
    assert len(session.calls) == 1


def test_search_route_depends_on_the_feature_public_api() -> None:
    assert search_route._search_v11_clusters is search_feature.search_v11_clusters
    assert (
        search_route._expand_v11_cluster_children
        is search_feature.expand_v11_cluster_children
    )
    post_route = next(
        route
        for route in search_route.router.routes
        if route.path == "/api/dashboard/search/v11-clusters"
    )
    assert post_route.response_model is search_feature.V11ClusterSearchResponse


def _http_client(session: _Session) -> TestClient:
    app = FastAPI()
    app.include_router(search_route.router)

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_v11_search_http_contract_preserves_path_fields_and_status() -> None:
    session = _Session(_Result(scalar_value=1), _Result(rows=[_cluster_row("l3-a")]))

    with _http_client(session) as client:
        response = client.post(
            "/api/dashboard/search/v11-clusters",
            json={"keyword": "diplomacy", "level": "macro", "page": 1, "page_size": 10},
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "l3-a",
                "title": "Title l3-a",
                "level": "macro",
                "article_count": 12,
                "children_count": 3,
                "children": [],
                "initiator": "A",
                "target": "B",
                "start_date": "2026-07-01",
                "end_date": "2026-07-10",
                "event_type": "diplomacy",
                "dominant_trigger": None,
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 10,
        "total_pages": 1,
        "has_next": False,
        "has_prev": False,
    }


def test_v11_children_http_contract_uses_current_level_labels() -> None:
    session = _Session(_Result(scalar_value=1), _Result(rows=[_cluster_row("l1-a")]))

    with _http_client(session) as client:
        response = client.get(
            "/api/dashboard/search/v11-clusters/l2-a/children",
            params={"level": "l2", "page": 1, "page_size": 10},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["parent_level"] == "l2"
    assert payload["child_level"] == "l1"
    assert payload["items"][0]["level"] == "l1"
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 10
    assert payload["total_pages"] == 1
    assert payload["has_next"] is False
    assert payload["has_prev"] is False


def test_v11_http_contract_rejects_invalid_inputs_without_querying() -> None:
    session = _Session()

    with _http_client(session) as client:
        invalid_level = client.get(
            "/api/dashboard/search/v11-clusters/node-1/children",
            params={"level": "root"},
        )
        invalid_item = client.get(
            "/api/dashboard/search/v11-clusters/with%20space/children",
            params={"level": "l1"},
        )
        invalid_search_level = client.post(
            "/api/dashboard/search/v11-clusters",
            json={"keyword": "x", "level": "l3"},
        )

    assert invalid_level.status_code == 422
    assert invalid_level.json() == {
        "detail": "level must be one of l3, l2, l1, macro, micro, or cluster"
    }
    assert invalid_item.status_code == 422
    assert invalid_item.json() == {"detail": "item_id has an invalid format"}
    assert invalid_search_level.status_code == 422
    assert session.calls == []


def test_v11_search_runtime_source_has_no_missing_relation_reference() -> None:
    source_paths = (
        PROJECT_ROOT / "backend/api/features/search/v11.py",
        PROJECT_ROOT / "backend/api/routes/search.py",
        PROJECT_ROOT / "backend/api/services/search_service.py",
    )

    for source_path in source_paths:
        source = source_path.read_text(encoding="utf-8")
        assert not any(name in source for name in MISSING_RELATIONS), source_path


def test_v11_search_feature_does_not_depend_on_routes_or_services() -> None:
    feature_root = PROJECT_ROOT / "backend/api/features/search"
    for source_path in feature_root.glob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        assert "api.routes" not in source, source_path
        assert "api.services" not in source, source_path
