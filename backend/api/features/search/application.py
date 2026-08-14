"""Transport-independent orchestration for dashboard search use cases."""

from __future__ import annotations

from typing import Any

from api.features.search.contracts import (
    ClusterSearchDependencies,
    ClusterSearchSettings,
    DashboardSearchDependencies,
    DashboardSearchExecution,
    DashboardSearchProfile,
    SearchDependencyUnavailable,
    SearchModeError,
    SearchQueryRequired,
)
from api.features.search.query_contract import (
    normalize_and_validate_time_semantics,
    validate_clustered_vector_query,
    validate_supported_filters,
    validate_supported_query,
)
from api.models.schemas import ClusteredSearchResponse, ClusterGroup, SearchRequest

_SEARCH_MODES = frozenset({"exact", "fuzzy", "cluster", "event_coref"})
_SEARCH_TYPES = frozenset({"news", "l1", "l2", "l3"})


def execute_dashboard_search(
    params: SearchRequest,
    *,
    query_mode: str | None,
    user: dict[str, Any] | None,
    db: Any,
    dependencies: DashboardSearchDependencies,
) -> DashboardSearchExecution:
    """Normalize a request and delegate it to the configured search provider."""

    started_at = dependencies.clock()
    mode = _normalize_mode(query_mode, params.mode)
    params.mode = mode
    search_type = str(params.search_type or "news").strip().lower()
    if search_type not in _SEARCH_TYPES:
        search_type = "news"
        params.search_type = search_type
    validate_supported_query(params)
    normalize_and_validate_time_semantics(params, search_type)
    validate_supported_filters(params, search_type)
    parsed_at = dependencies.clock()
    result = dependencies.provider(
        params,
        user=user,
        app_db=db,
        start_ts=started_at,
    )
    completed_at = dependencies.clock()
    return DashboardSearchExecution(
        result=result,
        profile=DashboardSearchProfile(
            mode=mode,
            search_type=search_type,
            parse_ms=(parsed_at - started_at) * 1000,
            execute_ms=(completed_at - parsed_at) * 1000,
            total_ms=(completed_at - started_at) * 1000,
        ),
    )


def execute_clustered_search(
    params: SearchRequest,
    *,
    user: dict[str, Any] | None,
    db: Any,
    settings: ClusterSearchSettings,
    dependencies: ClusterSearchDependencies,
) -> ClusteredSearchResponse:
    """Build cluster groups while keeping vector adapters outside the feature."""

    started_at = dependencies.clock()
    validate_supported_query(params)
    validate_clustered_vector_query(params)
    normalize_and_validate_time_semantics(params, "news")
    validate_supported_filters(params, "news")
    query = dependencies.build_query(params)
    if not query.strip():
        raise SearchQueryRequired("clustered search requires a non-empty query")

    try:
        vector = dependencies.encode_query(query)
        store = dependencies.get_store()
        centroid_hits = store.search_nearest_centroid(
            vector,
            top_k=settings.centroid_top_k,
        )
    except Exception as exc:
        raise SearchDependencyUnavailable(
            f"Milvus centroid search or BGE-M3 unavailable: {exc!s}"
        ) from exc

    groups: list[ClusterGroup] = []
    language_id = dependencies.resolve_language(
        db,
        params.language,
    )
    for centroid in centroid_hits:
        raw_cluster_id = getattr(centroid, "cluster_id", None)
        if raw_cluster_id is None:
            continue
        cluster_id = int(raw_cluster_id)
        if cluster_id < 0:
            continue
        try:
            inner_hits = store.search_similar_news(
                vector,
                top_k=settings.news_per_cluster,
                cluster_id_filter=cluster_id,
            )
        except Exception:
            inner_hits = []
        news_ids = [
            int(hit.news_id)
            for hit in inner_hits
            if getattr(hit, "news_id", None) is not None
        ]
        if not news_ids:
            continue
        rows = dependencies.fetch_rows(db, news_ids)
        kept = [
            row
            for row in rows
            if dependencies.passes_filters(row, params, language_id)
        ]
        items = dependencies.rows_to_items(
            db,
            user,
            kept,
            params.favorite_scope_topic,
        )
        if items:
            groups.append(
                ClusterGroup(
                    cluster_id=cluster_id,
                    score=float(centroid.score),
                    items=items,
                )
            )

    fallback_applied = False
    if not groups and dependencies.fallback_enabled():
        fallback = dependencies.execute_exact(db, params, user, started_at)
        fallback_items = getattr(fallback, "data", None)
        if fallback_items is None and isinstance(fallback, dict):
            fallback_items = fallback.get("data")
        if fallback_items:
            groups = [ClusterGroup(cluster_id=-1, score=0.0, items=fallback_items)]
            fallback_applied = True

    return ClusteredSearchResponse(
        query=query,
        clusters=groups,
        query_time_ms=(dependencies.clock() - started_at) * 1000,
        effective_strategy="exact_fallback" if fallback_applied else "clustered_vector",
        fallback_applied=fallback_applied,
    )


def _normalize_mode(query_mode: str | None, body_mode: str | None) -> str:
    raw = query_mode if query_mode is not None and query_mode.strip() else body_mode
    mode = str(raw or "exact").strip().lower()
    if mode not in _SEARCH_MODES:
        raise SearchModeError(
            "mode must be exact, fuzzy, cluster, or event_coref"
        )
    return mode


__all__ = ("execute_clustered_search", "execute_dashboard_search")
