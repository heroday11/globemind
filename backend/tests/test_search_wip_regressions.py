from __future__ import annotations

import inspect
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from api.features.search import SearchFilterUnsupported, v11
from api.features.search.query_contract import validate_supported_filters
from api.models.schemas import SearchRequest, V11ClusterSearchRequest
from api.services import news_search_v2, search_service


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, *_args: Any, **_kwargs: Any) -> _Rows:
        return _Rows(self._rows)


class _Result(_Rows):
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        scalar_value: Any = None,
    ) -> None:
        super().__init__(rows or [])
        self._scalar_value = scalar_value

    def scalar(self) -> Any:
        return self._scalar_value


class _SequentialConnection:
    def __init__(self, *results: _Result):
        self._results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self) -> "_SequentialConnection":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(
        self,
        statement: Any,
        parameters: dict[str, Any] | None = None,
    ) -> _Result:
        self.calls.append((str(statement), dict(parameters or {})))
        if not self._results:
            raise AssertionError(f"unexpected SQL: {statement}")
        return self._results.pop(0)


class _Engine:
    def __init__(self, connection: _SequentialConnection):
        self._connection = connection

    def connect(self) -> _SequentialConnection:
        return self._connection


def _l3_row() -> dict[str, Any]:
    return {
        "id": "macro-1",
        "title": "Regional security developments",
        "summary": "A bounded fixture summary.",
        "family_group": "military_conflict",
        "macro_key": "classification.macro_key",
        "pair_key": "classification.pair_key",
        "article_count": 4,
        "story_count": 2,
        "start_date": None,
        "end_date": None,
        "quality_score": 0.75,
        "total_count": 1,
    }


def test_l3_title_match_does_not_guess_initiator_from_family_group(monkeypatch) -> None:
    monkeypatch.setattr(
        news_search_v2,
        "_search_connection",
        lambda: _Connection([_l3_row()]),
    )

    response = news_search_v2._macro_events_from_title_matches(
        SearchRequest(keyword="Taiwan", mode="fuzzy", search_type="l3"),
        page_size=10,
        offset=0,
        start_ts=0.0,
    )

    assert response is not None
    assert response.macro_event_items is not None
    assert response.macro_event_items[0].initiator is None
    assert response.macro_event_items[0].target is None


def test_l3_regular_search_does_not_guess_initiator_from_family_group(monkeypatch) -> None:
    monkeypatch.setattr(
        news_search_v2,
        "_search_connection",
        lambda: _Connection([_l3_row()]),
    )
    monkeypatch.setattr(
        news_search_v2,
        "_macro_ids_with_clean_articles",
        lambda *_args: {"macro-1"},
    )
    monkeypatch.setattr(news_search_v2, "_should_expand_l1_aliases", lambda *_args: False)

    response = news_search_v2._search_l3(
        SearchRequest(keyword="regional", search_type="l3"),
        start_ts=0.0,
    )

    assert response.macro_event_items is not None
    assert response.macro_event_items[0].initiator is None
    assert response.macro_event_items[0].target is None


def test_l2_search_does_not_guess_actors_from_classification_fields(monkeypatch) -> None:
    row = {
        **_l3_row(),
        "initiator": None,
        "target": None,
        "family_group": "classification.family_group",
        "event_family": "diplomacy",
        "event_action": "classification.event_action",
        "pair_key": "classification.pair_key",
        "chain_quality": "usable",
    }
    monkeypatch.setattr(
        news_search_v2,
        "_search_connection",
        lambda: _Connection([row]),
    )
    monkeypatch.setattr(
        news_search_v2,
        "_chain_ids_with_clean_articles",
        lambda *_args: {"macro-1"},
    )

    response = news_search_v2._search_l2(
        SearchRequest(keyword="regional", search_type="l2"),
        start_ts=0.0,
    )

    assert response.macro_event_items is not None
    assert response.macro_event_items[0].initiator is None
    assert response.macro_event_items[0].target is None


@pytest.mark.parametrize(
    ("level", "event_type", "classification_fields"),
    [
        (
            "macro",
            "classification.family_group",
            {
                "family_group": "classification.family_group",
                "macro_key": "classification.macro_key",
            },
        ),
        (
            "micro",
            "classification.event_family",
            {
                "family_group": "classification.family_group",
                "event_family": "classification.event_family",
                "event_action": "classification.event_action",
                "pair_key": "classification.pair_key",
            },
        ),
    ],
)
def test_v11_l3_l2_search_keeps_classification_out_of_actor_fields(
    level: str,
    event_type: str,
    classification_fields: dict[str, str],
) -> None:
    row = {
        "id": f"{level}-1",
        "title": "Bounded hierarchy fixture",
        "article_count": 3,
        "children_count": 2,
        "initiator": None,
        "target": None,
        "start_date": None,
        "end_date": None,
        "event_type": event_type,
        "dominant_trigger": None,
        **classification_fields,
    }
    db = _SequentialConnection(
        _Result(scalar_value=1),
        _Result([row]),
    )

    response = v11.search_v11_clusters(
        db,
        V11ClusterSearchRequest(keyword="regional", level=level),
    )

    assert len(response.items) == 1
    assert response.items[0].initiator is None
    assert response.items[0].target is None
    assert response.items[0].event_type == event_type
    projection_sql = db.calls[1][0]
    if level == "macro":
        assert "NULL::text AS initiator" in projection_sql
        assert "NULL::text AS target" in projection_sql
    else:
        assert "parent.initiator" in projection_sql
        assert "parent.target" in projection_sql


@pytest.mark.parametrize(
    ("parent_level", "expected_child_level", "event_type", "classification_fields"),
    [
        (
            "l3",
            "l2",
            "classification.role",
            {
                "family_group": "classification.family_group",
                "pair_key": "classification.pair_key",
                "role": "classification.role",
                "lane": "classification.lane",
            },
        ),
        (
            "l2",
            "l1",
            "classification.event_family",
            {
                "event_family": "classification.event_family",
                "event_action": "classification.event_action",
                "story_angle": "classification.story_angle",
            },
        ),
    ],
)
def test_v11_children_keep_classification_out_of_actor_fields(
    parent_level: str,
    expected_child_level: str,
    event_type: str,
    classification_fields: dict[str, str],
) -> None:
    row = {
        "id": f"{expected_child_level}-1",
        "title": "Bounded child fixture",
        "article_count": 3,
        "children_count": 2,
        "initiator": None,
        "target": None,
        "start_date": None,
        "end_date": None,
        "event_type": event_type,
        "dominant_trigger": None,
        **classification_fields,
    }
    db = _SequentialConnection(
        _Result(scalar_value=1),
        _Result([row]),
    )

    response = v11.expand_v11_cluster_children(
        db,
        "parent-1",
        parent_level,
    )

    assert response["child_level"] == expected_child_level
    assert response["items"][0]["initiator"] is None
    assert response["items"][0]["target"] is None
    assert response["items"][0]["event_type"] == event_type


def test_legacy_l3_children_keep_member_classification_out_of_l2_actors(
    monkeypatch,
) -> None:
    row = {
        "id": "l2-1",
        "title": "Bounded L2 child",
        "article_count": 4,
        "children_count": 2,
        "initiator": None,
        "target": None,
        "family_group": "classification.family_group",
        "pair_key": "classification.pair_key",
        "role": "classification.role",
        "lane": "classification.lane",
        "start_date": None,
        "end_date": None,
    }
    connection = _SequentialConnection(
        _Result(scalar_value=1),
        _Result([row]),
    )
    monkeypatch.setattr(news_search_v2, "NEWS_ENGINE", _Engine(connection))
    monkeypatch.setattr(
        news_search_v2,
        "_chain_ids_with_clean_articles",
        lambda *_args: {"l2-1"},
    )

    response = news_search_v2.expand_cluster_children_v2(
        "l3-1",
        "l3",
        page=1,
        page_size=20,
    )

    assert response["items"][0]["initiator"] is None
    assert response["items"][0]["target"] is None
    assert response["items"][0]["event_type"] == "classification.role"


@pytest.mark.parametrize("parent_level", ["macro", "l2"])
def test_legacy_l2_children_keep_segment_classification_out_of_l1_actors(
    monkeypatch,
    parent_level: str,
) -> None:
    row = {
        "id": "l1-1",
        "title": "Bounded L1 child",
        "article_count": 4,
        "initiator": None,
        "target": None,
        "family_group": "classification.family_group",
        "pair_key": "classification.pair_key",
        "event_type": None,
        "event_family": "classification.event_family",
        "event_action": "classification.event_action",
        "story_angle": "classification.story_angle",
        "start_date": None,
        "end_date": None,
    }
    connection = _SequentialConnection(
        _Result(scalar_value=1),
        _Result([row]),
    )
    monkeypatch.setattr(news_search_v2, "NEWS_ENGINE", _Engine(connection))
    monkeypatch.setattr(
        news_search_v2,
        "_cluster_ids_with_clean_articles",
        lambda *_args: {"l1-1"},
    )

    response = news_search_v2.expand_cluster_children_v2(
        "l2-1",
        parent_level,
        page=1,
        page_size=20,
    )

    assert response["items"][0]["initiator"] == ""
    assert response["items"][0]["target"] == ""
    assert response["items"][0]["event_type"] == "classification.event_family"


def test_v11_hierarchy_sql_keeps_actor_semantics_and_one_parent_predicate() -> None:
    assert "parent.family_group AS initiator" not in v11._SEARCH_SPECS["macro"].select_sql
    assert "parent.macro_key AS target" not in v11._SEARCH_SPECS["macro"].select_sql
    assert "parent.event_family AS initiator" not in v11._SEARCH_SPECS["micro"].select_sql
    assert "parent.event_action AS target" not in v11._SEARCH_SPECS["micro"].select_sql
    assert "member.family_group) AS initiator" not in v11._L3_CHILDREN_SQL
    assert "member.pair_key) AS target" not in v11._L3_CHILDREN_SQL
    assert "segment.event_family) AS initiator" not in v11._L2_CHILDREN_SQL
    assert "segment.story_angle) AS target" not in v11._L2_CHILDREN_SQL
    assert v11._L2_CHILDREN_SQL.count("WHERE segment.chain_id = :item_id") == 1


def test_legacy_l3_children_do_not_guess_l2_actor_fields() -> None:
    source = inspect.getsource(news_search_v2.expand_cluster_children_v2)

    assert "COALESCE(ch.initiator, mm.family_group" not in source
    assert "COALESCE(ch.target, mm.pair_key" not in source


def test_news_country_filter_fails_closed_until_a_country_dimension_is_supported() -> None:
    with pytest.raises(SearchFilterUnsupported) as captured:
        validate_supported_filters(
            SearchRequest(keyword="chip", country="US"),
            "news",
        )

    assert captured.value.fields == ("country",)


def test_search_schema_does_not_advertise_the_unsupported_country_filter() -> None:
    examples = SearchRequest.model_json_schema()["examples"]

    assert examples
    assert all("country" not in example for example in examples)
    assert examples[0]["language"] == "en"


def test_news_country_filter_never_falls_back_to_the_language_predicate() -> None:
    clauses: list[str] = []
    bind: dict[str, Any] = {}

    news_search_v2._add_news_filters(
        clauses,
        bind,
        SearchRequest(keyword="chip", country="US"),
    )

    assert "language" not in bind
    assert all("n.language" not in clause for clause in clauses)

    explicit_language_clauses: list[str] = []
    explicit_language_bind: dict[str, Any] = {}
    news_search_v2._add_news_filters(
        explicit_language_clauses,
        explicit_language_bind,
        SearchRequest(keyword="chip", language="en"),
    )
    assert explicit_language_bind["language"] == "en"
    assert any("n.language" in clause for clause in explicit_language_clauses)


def test_news_country_filter_is_never_reported_as_an_applied_language_filter() -> None:
    assert news_search_v2._applied_filter_explain(
        SearchRequest(keyword="chip", country="US")
    ) == []


def test_legacy_search_adapters_do_not_reinterpret_country_as_language() -> None:
    source = inspect.getsource(search_service)

    assert "params.language or params.country" not in source


def test_legacy_news_adapter_does_not_relabel_language_as_location(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        search_service,
        "get_user_favorite_sets_for_scope",
        lambda *_args, **_kwargs: (set(), set()),
    )
    row = SimpleNamespace(
        id=31,
        title="Explicit language fixture",
        abstract="Bounded summary.",
        body="Bounded body.",
        request_url="https://example.test/news/31",
        pub_time=None,
        language_id="en",
    )

    item = search_service._rows_to_news_items(None, None, [row])[0]

    assert item.language_id == "en"
    assert item.location is None


def test_news_response_preserves_explicit_regions_without_guessing_location() -> None:
    row = {
        "id": 17,
        "title": "Bounded fixture title",
        "body": "Bounded fixture body.",
        "request_url": "https://example.test/news/17",
        "language_id": "en",
        "source_name": "Example source",
        "source_country": "US",
        "source_region": "North America",
        "news_region": "Americas",
        "cluster_article_count": None,
    }

    unknown_location = news_search_v2._news_items_from_rows([row])[0].model_dump()
    explicit_location = news_search_v2._news_items_from_rows(
        [{**row, "id": 18, "location": "Explicit legacy location"}]
    )[0].model_dump()

    assert unknown_location["language_id"] == "en"
    assert unknown_location["source_country"] == "US"
    assert unknown_location["source_region"] == "North America"
    assert unknown_location["news_region"] == "Americas"
    assert unknown_location["location"] is None
    assert explicit_location["location"] == "Explicit legacy location"


def test_news_response_separates_four_time_semantics_without_legacy_fallback() -> None:
    published_at = datetime(2026, 8, 9, 12, 30)
    row = {
        "id": 23,
        "title": "Bounded time fixture",
        "body": "Bounded fixture body.",
        "request_url": "https://example.test/news/23",
        "pub_time": published_at,
        "language_id": "en",
        "source_name": "Example source",
        "cluster_article_count": None,
    }

    payload = news_search_v2._news_items_from_rows([row])[0].model_dump()

    assert payload["time_semantics"] == {
        "schema_version": "search-result-time-semantics-v1",
        "published_at": published_at,
        "event_time_start": None,
        "event_time_end": None,
        "collected_at": None,
        "updated_at": None,
        "legacy_pub_time_status": "legacy_alias_of_published_at_value_unverified",
        "legacy_created_at_status": "legacy_unverified_not_used",
    }
    assert payload["pub_time"] == published_at
    assert payload["created_at"] is None


def test_legacy_news_adapter_emits_the_same_explicit_time_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        search_service,
        "get_user_favorite_sets_for_scope",
        lambda *_args, **_kwargs: (set(), set()),
    )
    published_at = datetime(2026, 8, 9, 12, 30)
    row = SimpleNamespace(
        id=24,
        title="Legacy adapter time fixture",
        abstract="Bounded summary.",
        body="Bounded body.",
        request_url="https://example.test/news/24",
        pub_time=published_at,
        language_id="en",
    )

    payload = search_service._rows_to_news_items(None, None, [row])[0].model_dump()

    assert payload["time_semantics"]["published_at"] == published_at
    assert payload["time_semantics"]["event_time_start"] is None
    assert payload["time_semantics"]["event_time_end"] is None
    assert payload["time_semantics"]["collected_at"] is None
    assert payload["time_semantics"]["updated_at"] is None

    with pytest.raises(ValueError, match="pub_time"):
        news_search_v2.NewsItem(
            id=25,
            title="Contradictory time fixture",
            pub_time=datetime(2026, 8, 9, 12, 30),
            time_semantics=news_search_v2.NewsResultTimeSemantics(
                published_at=datetime(2026, 8, 9, 12, 31)
            ),
        )

    with pytest.raises(ValueError, match="event_time"):
        news_search_v2.NewsResultTimeSemantics(
            event_time_start=datetime(2026, 8, 10, 0, 0),
            event_time_end=datetime(2026, 8, 9, 0, 0),
        )


def test_legacy_publication_alias_cannot_supply_a_missing_canonical_value() -> None:
    legacy_value = datetime(2026, 8, 9, 12, 30)

    with pytest.raises(ValueError, match="pub_time"):
        news_search_v2.NewsItem(
            id=26,
            title="Missing canonical publication time",
            pub_time=legacy_value,
            time_semantics=news_search_v2.NewsResultTimeSemantics(
                published_at=None
            ),
        )

    with pytest.raises(ValueError, match="pub_time"):
        news_search_v2.ClusterTreeNews(
            id=27,
            title="Missing canonical cluster publication time",
            pub_time=legacy_value,
            time_semantics=news_search_v2.NewsResultTimeSemantics(
                published_at=None
            ),
        )


def test_news_analysis_metadata_keeps_language_and_location_separate() -> None:
    item = news_search_v2.NewsItem(
        id=19,
        title="Metadata fixture",
        language_id="en",
        source_country="US",
        source_region="North America",
        news_region="Americas",
        location="Explicit legacy location",
    )

    metadata = {
        entry["key"]: entry
        for entry in news_search_v2._news_analysis_metadata(item)
    }

    assert metadata["language"] == {
        "key": "language",
        "label": "语言代码",
        "value": "en",
    }
    assert metadata["location"]["label"] == "位置（记录值，未核验）"
    assert metadata["location"]["value"] == "Explicit legacy location"
    assert metadata["source_country"]["label"] == "来源国（未权威核验）"
    assert metadata["source_country"]["value"] == "US"
