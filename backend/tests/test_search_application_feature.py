from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from api.features.search import (
    ClusterSearchDependencies,
    ClusterSearchSettings,
    DashboardSearchDependencies,
    SearchDependencyUnavailable,
    SearchModeError,
    SearchQueryRequired,
    SearchSyntaxUnsupported,
    execute_clustered_search,
    execute_dashboard_search,
)
from api.models.schemas import NewsItem, SearchRequest


def _clock(*values: float):
    remaining = iter(values)
    return lambda: next(remaining)


def test_dashboard_application_normalizes_and_profiles_request() -> None:
    observed: dict[str, Any] = {}

    def provider(params: SearchRequest, **kwargs: Any) -> dict[str, Any]:
        observed.update(mode=params.mode, search_type=params.search_type, **kwargs)
        return {"total": 0}

    request = SearchRequest(keyword="chip", mode="exact")
    execution = execute_dashboard_search(
        request,
        query_mode=" FUZZY ",
        user={"id": 4},
        db="session",
        dependencies=DashboardSearchDependencies(
            provider=provider,
            clock=_clock(10.0, 10.002, 10.007),
        ),
    )

    assert execution.result == {"total": 0}
    assert observed == {
        "mode": "fuzzy",
        "search_type": "news",
        "user": {"id": 4},
        "app_db": "session",
        "start_ts": 10.0,
    }
    assert execution.profile.parse_ms == pytest.approx(2.0)
    assert execution.profile.execute_ms == pytest.approx(5.0)
    assert execution.profile.total_ms == pytest.approx(7.0)


def test_dashboard_application_rejects_mode_before_provider() -> None:
    calls = 0

    def provider(*_args: Any, **_kwargs: Any) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(SearchModeError):
        execute_dashboard_search(
            SearchRequest(keyword="chip"),
            query_mode="invalid",
            user=None,
            db=None,
            dependencies=DashboardSearchDependencies(
                provider=provider,
                clock=_clock(1.0),
            ),
        )

    assert calls == 0


@dataclass
class _Store:
    centroids: list[Any]
    inner: list[Any]

    def search_nearest_centroid(self, _vector: Any, *, top_k: int) -> list[Any]:
        assert top_k == 3
        return self.centroids

    def search_similar_news(
        self,
        _vector: Any,
        *,
        top_k: int,
        cluster_id_filter: int,
    ) -> list[Any]:
        assert top_k == 2
        assert cluster_id_filter == 7
        return self.inner


def _cluster_dependencies(store: _Store) -> ClusterSearchDependencies:
    item = NewsItem(id=11, title="Result")
    return ClusterSearchDependencies(
        build_query=lambda _params: "semantic query",
        encode_query=lambda query: [query],
        get_store=lambda: store,
        resolve_language=lambda _db, _language: 2,
        fetch_rows=lambda _db, ids: [{"id": identifier} for identifier in ids],
        passes_filters=lambda row, _params, language_id: row["id"] == 11
        and language_id == 2,
        rows_to_items=lambda _db, _user, rows, _topic: [item] if rows else [],
        fallback_enabled=lambda: False,
        execute_exact=lambda *_args: None,
        clock=_clock(1.0, 1.01),
    )


def test_cluster_application_builds_groups_through_injected_adapters() -> None:
    store = _Store(
        centroids=[SimpleNamespace(cluster_id=7, score=0.87)],
        inner=[SimpleNamespace(news_id=11)],
    )

    response = execute_clustered_search(
        SearchRequest(keyword="chip"),
        user=None,
        db="session",
        settings=ClusterSearchSettings(centroid_top_k=3, news_per_cluster=2),
        dependencies=_cluster_dependencies(store),
    )

    assert response.query == "semantic query"
    assert response.query_time_ms == pytest.approx(10.0)
    assert response.effective_strategy == "clustered_vector"
    assert response.fallback_applied is False
    assert len(response.clusters) == 1
    assert response.clusters[0].cluster_id == 7
    assert response.clusters[0].items[0].id == 11


def test_cluster_application_maps_empty_query_and_dependency_failure() -> None:
    store = _Store(centroids=[], inner=[])
    empty = _cluster_dependencies(store)
    empty = ClusterSearchDependencies(
        **{**empty.__dict__, "build_query": lambda _params: "   "}
    )
    with pytest.raises(SearchQueryRequired):
        execute_clustered_search(
            SearchRequest(),
            user=None,
            db=None,
            settings=ClusterSearchSettings(),
            dependencies=empty,
        )

    unavailable = _cluster_dependencies(store)
    unavailable = ClusterSearchDependencies(
        **{
            **unavailable.__dict__,
            "get_store": lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        }
    )
    with pytest.raises(SearchDependencyUnavailable, match="offline"):
        execute_clustered_search(
            SearchRequest(keyword="chip"),
            user=None,
            db=None,
            settings=ClusterSearchSettings(),
            dependencies=unavailable,
        )


def test_vector_only_cluster_endpoint_rejects_boolean_ast_without_encoding_it() -> None:
    store = _Store(centroids=[], inner=[])
    with pytest.raises(SearchSyntaxUnsupported) as captured:
        execute_clustered_search(
            SearchRequest(keyword="China AND NOT Japan"),
            user=None,
            db=None,
            settings=ClusterSearchSettings(),
            dependencies=_cluster_dependencies(store),
        )

    assert captured.value.features == ("clustered_vector_boolean_ast",)
    assert captured.value.query_field == "keyword"
    assert "mode=cluster" in str(captured.value)


def test_cluster_application_discloses_exact_fallback() -> None:
    store = _Store(centroids=[], inner=[])
    dependencies = _cluster_dependencies(store)
    fallback_item = NewsItem(id=29, title="Exact fallback")
    dependencies = ClusterSearchDependencies(
        **{
            **dependencies.__dict__,
            "fallback_enabled": lambda: True,
            "execute_exact": lambda *_args: {"data": [fallback_item]},
        }
    )

    response = execute_clustered_search(
        SearchRequest(keyword="chip"),
        user=None,
        db=None,
        settings=ClusterSearchSettings(centroid_top_k=3, news_per_cluster=2),
        dependencies=dependencies,
    )

    assert response.fallback_applied is True
    assert response.effective_strategy == "exact_fallback"
    assert response.clusters[0].cluster_id == -1
    assert response.clusters[0].items == [fallback_item]
