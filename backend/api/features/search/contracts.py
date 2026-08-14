"""Application contracts for dashboard and clustered search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from api.models.schemas import SearchRequest


class SearchContractError(ValueError):
    """A caller supplied a search request that cannot be executed."""


class SearchModeError(SearchContractError):
    """The requested dashboard search mode is unsupported."""


class SearchQueryRequired(SearchContractError):
    """Clustered search requires a non-empty semantic query."""


class SearchSyntaxUnsupported(SearchContractError):
    """The query uses syntax that this search engine does not implement."""

    def __init__(
        self,
        features: tuple[str, ...],
        *,
        reason: str | None = None,
        position: int | None = None,
        query_field: str | None = None,
    ):
        self.features = features
        self.reason = reason
        self.position = position
        self.query_field = query_field
        super().__init__(reason or ("unsupported search syntax: " + ", ".join(features)))


class SearchTimeFilterError(SearchContractError):
    """The requested time field or range cannot be applied as stated."""


class SearchFilterUnsupported(SearchContractError):
    """The selected search surface cannot apply one or more filters."""

    def __init__(self, search_type: str, fields: tuple[str, ...]):
        self.search_type = search_type
        self.fields = fields
        super().__init__(
            f"search_type={search_type} 尚不支持以下筛选，请移除后重试："
            + ", ".join(fields)
        )


class SearchDependencyUnavailable(RuntimeError):
    """A required vector-search dependency is unavailable."""


class DashboardSearchProvider(Protocol):
    def __call__(
        self,
        params: SearchRequest,
        *,
        user: dict[str, Any] | None,
        app_db: Any,
        start_ts: float,
    ) -> Any: ...


@dataclass(frozen=True)
class DashboardSearchDependencies:
    provider: DashboardSearchProvider
    clock: Callable[[], float]


@dataclass(frozen=True)
class DashboardSearchProfile:
    mode: str
    search_type: str
    parse_ms: float
    execute_ms: float
    total_ms: float


@dataclass(frozen=True)
class DashboardSearchExecution:
    result: Any
    profile: DashboardSearchProfile


@dataclass(frozen=True)
class ClusterSearchSettings:
    centroid_top_k: int = 8
    news_per_cluster: int = 5

    def __post_init__(self) -> None:
        if self.centroid_top_k < 1 or self.news_per_cluster < 1:
            raise ValueError("cluster search limits must be positive")


@dataclass(frozen=True)
class ClusterSearchDependencies:
    build_query: Callable[[SearchRequest], str]
    encode_query: Callable[[str], Any]
    get_store: Callable[[], Any]
    resolve_language: Callable[[Any, str | None], int | None]
    fetch_rows: Callable[[Any, list[int]], list[Any]]
    passes_filters: Callable[[Any, SearchRequest, int | None], bool]
    rows_to_items: Callable[[Any, dict[str, Any] | None, list[Any], str | None], list[Any]]
    fallback_enabled: Callable[[], bool]
    execute_exact: Callable[[Any, SearchRequest, dict[str, Any] | None, float], Any]
    clock: Callable[[], float]


__all__ = (
    "ClusterSearchDependencies",
    "ClusterSearchSettings",
    "DashboardSearchDependencies",
    "DashboardSearchExecution",
    "DashboardSearchProfile",
    "DashboardSearchProvider",
    "SearchContractError",
    "SearchDependencyUnavailable",
    "SearchFilterUnsupported",
    "SearchModeError",
    "SearchQueryRequired",
    "SearchSyntaxUnsupported",
    "SearchTimeFilterError",
)
