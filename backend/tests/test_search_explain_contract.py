from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.features.search import (
    ENTITY_ALIAS_CATALOG_VERSION,
    QUERY_LANGUAGE_VERSION,
    QUERY_LIMITS,
    DashboardSearchDependencies,
    SearchFilterUnsupported,
    SearchSyntaxUnsupported,
    SearchTimeFilterError,
    entity_alias_variants,
    execute_dashboard_search,
    parse_supported_query,
    resolve_entity_alias,
)
from api.features.search.entities import ENTITY_ALIAS_CATALOG
from api.features.search.query_contract import (
    detect_unsupported_syntax,
    normalize_and_validate_time_semantics,
    validate_supported_filters,
    validate_supported_query,
)
from api.models.schemas import SearchRequest
from api.routes.search import search_news
from api.services import news_search_v2


def _clock(*values: float):
    remaining = iter(values)
    return lambda: next(remaining)


def test_generic_entity_catalog_has_versioned_stable_ids_and_review_state() -> None:
    china_en = resolve_entity_alias("China")
    china_zh = resolve_entity_alias("中国")

    assert ENTITY_ALIAS_CATALOG_VERSION == "entity-aliases-2026.08.09-v2"
    assert china_en is not None
    assert china_zh is not None
    assert china_en.entity_id == china_zh.entity_id == "urn:globemind:entity:country:CN"
    assert {"China", "中国", "PRC"}.issubset(entity_alias_variants("中国"))
    assert set(entity_alias_variants("China")) == set(entity_alias_variants("中国"))

    entities = ENTITY_ALIAS_CATALOG["entities"]
    entity_ids = [entity["entity_id"] for entity in entities]
    assert len(entities) >= 20
    assert len(entity_ids) == len(set(entity_ids))
    assert {entity["entity_type"] for entity in entities} == {
        "country",
        "person",
        "organization",
        "location",
    }
    assert all(
        entity["entity_id"].startswith(
            f"urn:globemind:entity:{entity['entity_type']}:"
        )
        for entity in entities
    )
    assert all(entity["review_status"] == "review_required" for entity in entities)
    assert ENTITY_ALIAS_CATALOG["accuracy_claim"] == "not_measured"
    assert ENTITY_ALIAS_CATALOG["catalog_review_status"] == "review_required"
    assert ENTITY_ALIAS_CATALOG["human_review_evidence"] is None
    assert resolve_entity_alias("Burma").entity_id == "urn:globemind:entity:country:MM"
    assert resolve_entity_alias("Turkey").entity_id == "urn:globemind:entity:country:TR"
    assert "Burma" not in entity_alias_variants("Myanmar")
    assert entity_alias_variants("Burma")[0] == "Burma"
    assert resolve_entity_alias("习近平").entity_id == "urn:globemind:entity:person:xi-jinping"
    assert resolve_entity_alias("NATO").entity_id == "urn:globemind:entity:organization:nato"
    assert resolve_entity_alias("Taiwan Strait").entity_id == "urn:globemind:entity:location:taiwan-strait"
    assert resolve_entity_alias("南海").matched_alias_status == "context_dependent"
    assert resolve_entity_alias("China").review_status == "review_required"
    assert resolve_entity_alias("Beijing") is None
    assert resolve_entity_alias("American") is None


def test_multiword_entity_alias_is_one_logical_term_in_exact_sql() -> None:
    groups = news_search_v2._title_match_groups(
        "United States semiconductor",
        expand_aliases=False,
    )

    assert groups[0][0] == "United States"
    assert "USA" in groups[0]
    assert groups[1] == ["semiconductor"]
    sql, bind = news_search_v2._title_match_cte_sql(
        groups,
        "entity_term",
        match_all=True,
    )
    assert " OR " in sql
    assert "INTERSECT" in sql
    assert any(value == "%United States%" for value in bind.values())


def test_entity_exclusion_uses_the_same_stable_alias_set() -> None:
    clauses: list[str] = []
    bind: dict[str, Any] = {}
    news_search_v2._add_exclude_clause(
        clauses,
        bind,
        ["COALESCE(n.title, '')"],
        "China",
    )

    assert len(clauses) == 1
    assert clauses[0].startswith("NOT (")
    assert any(value == "%中国%" for value in bind.values())
    assert any("PRC" in value for value in bind.values())


def test_secondary_full_phrase_is_literal_instead_of_becoming_dsl() -> None:
    clauses: list[str] = []
    bind: dict[str, Any] = {}
    news_search_v2._add_text_clause(
        clauses,
        bind,
        ["COALESCE(n.title, '')"],
        '"China OR Japan"',
        "must",
        "and",
    )

    assert len(bind) == 1
    assert next(iter(bind.values())) == "%China OR Japan%"


@pytest.mark.parametrize(
    ("value", "feature"),
    (
        ("China NEAR/5 Japan", "proximity_operator"),
        ("title:China", "field_scope_or_weight"),
        ("China*", "wildcard"),
        ("/China.*/", "regular_expression"),
        ("China && Japan", "symbolic_boolean_operator"),
        ('"China semiconductor', "mixed_or_unbalanced_phrase"),
        ("NOT China", "unbounded_negation"),
    ),
)
def test_unsupported_advanced_query_syntax_is_detected(value: str, feature: str) -> None:
    assert feature in detect_unsupported_syntax(value)


def test_supported_phrase_and_apostrophe_are_not_misclassified() -> None:
    assert detect_unsupported_syntax('"South China Sea"') == ()
    assert detect_unsupported_syntax('"China OR Japan"') == ()
    assert detect_unsupported_syntax('(China OR Japan) AND NOT "trade war"') == ()
    assert detect_unsupported_syntax('"China" semiconductor') == ()
    assert detect_unsupported_syntax("People's Republic of China") == ()


def test_boolean_syntax_in_secondary_controls_is_validated_then_executed() -> None:
    calls = 0

    def provider(*_args: Any, **_kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        return "ok"

    execution = execute_dashboard_search(
        SearchRequest(keyword="China", any_include="Japan OR Korea"),
        query_mode=None,
        user=None,
        db=None,
        dependencies=DashboardSearchDependencies(
            provider=provider,
            clock=_clock(1.0, 1.01, 1.02),
        ),
    )

    assert execution.result == "ok"
    assert calls == 1


@pytest.mark.parametrize(
    ("value", "feature"),
    (
        ("China OR NOT Japan", "unbounded_negation"),
        ("China AND (Japan OR Korea", "unbalanced_parentheses"),
        ("China AND", "missing_operand"),
        ("()", "empty_group"),
        ("China\x00Japan", "control_character"),
        ("(" * 9 + "China" + ")" * 9, "nesting_too_deep"),
        ("x" * (int(QUERY_LIMITS["max_query_chars"]) + 1), "query_too_long"),
    ),
)
def test_malformed_or_unbounded_boolean_queries_fail_closed(
    value: str,
    feature: str,
) -> None:
    with pytest.raises(SearchSyntaxUnsupported) as captured:
        validate_supported_query(SimpleNamespace(keyword=value))
    assert captured.value.features == (feature,)
    assert captured.value.query_field == "keyword"


def test_search_route_returns_structured_422_for_invalid_boolean_query() -> None:
    with pytest.raises(HTTPException) as captured:
        search_news(
            SearchRequest(keyword="China OR NOT Japan"),
            mode=None,
            user=None,
            db=None,
        )

    assert captured.value.status_code == 422
    assert captured.value.detail["code"] == "unsupported_search_syntax"
    assert captured.value.detail["unsupported"] == ["unbounded_negation"]
    assert captured.value.detail["query_language"] == "boolean-v1"
    assert captured.value.detail["query_field"] == "keyword"
    assert captured.value.detail["limits"]["max_ast_nodes"] == 64


def test_conflicting_keyword_and_topic_do_not_silently_choose_one() -> None:
    with pytest.raises(SearchSyntaxUnsupported) as captured:
        validate_supported_query(
            SearchRequest(keyword="China", topic="Japan")
        )
    assert captured.value.features == ("conflicting_primary_query_fields",)
    assert captured.value.query_field == "keyword/topic"


def test_boolean_parser_preserves_precedence_phrases_and_entity_terms() -> None:
    parsed = parse_supported_query(
        '(United States OR China) AND "export control" NOT sanctions'
    )

    assert parsed is not None
    assert parsed.explicit_boolean is True
    assert parsed.nesting_depth == 1
    assert parsed.root.kind == "and"
    assert parsed.root.children[0].kind == "or"
    assert parsed.root.children[0].children[0].value == "United States"
    assert parsed.root.children[1].kind == "phrase"
    assert parsed.root.children[2].kind == "not"
    assert parsed.limits_dict()["observed_terms"] == 4


def test_boolean_sql_is_parameterized_bounded_and_keeps_not_semantics() -> None:
    parsed = parse_supported_query('(China OR Japan) AND NOT "trade war"')
    assert parsed is not None
    clauses: list[str] = []
    bind: dict[str, Any] = {}
    news_search_v2._add_text_clause(
        clauses,
        bind,
        ["COALESCE(n.title, '')"],
        parsed.raw,
        "boolean",
        expand_aliases=False,
    )

    assert len(clauses) == 1
    assert " AND " in clauses[0]
    assert " OR " in clauses[0]
    assert "NOT (" in clauses[0]
    assert "China" not in clauses[0]
    assert any("China" in str(value) for value in bind.values())
    assert "%中国%" in bind.values()
    assert "%trade war%" in bind.values()

    plan = news_search_v2._boolean_title_candidate_plan(
        parsed.raw,
        expand_aliases=False,
    )
    assert plan is not None
    candidate_sql, candidate_bind, predicate = plan
    assert "UNION ALL" in candidate_sql
    assert candidate_bind["per_term"] == news_search_v2.BOOLEAN_TITLE_CANDIDATES_PER_TERM
    assert "NOT (" in predicate


def test_time_field_and_range_semantics_are_strict_and_explicit() -> None:
    news = SearchRequest(keyword="China")
    assert normalize_and_validate_time_semantics(news, "news") == "published_at"
    assert news.time_field == "published_at"
    news_explain = news_search_v2._build_query_explain(news, total=0)
    assert news_explain.time.requested_field == "auto"
    assert news_explain.time.applied_field == "public.news.published_at"

    hierarchy = SearchRequest(keyword="China", search_type="l2", time_field="event_time")
    assert normalize_and_validate_time_semantics(hierarchy, "l2") == "event_time"

    with pytest.raises(SearchTimeFilterError, match="event_time"):
        normalize_and_validate_time_semantics(
            SearchRequest(keyword="China", search_type="l2", time_field="published_at"),
            "l2",
        )
    with pytest.raises(SearchTimeFilterError, match="不得晚于"):
        normalize_and_validate_time_semantics(
            SearchRequest(
                keyword="China",
                start_time="2026-08-10T00:00",
                end_time="2026-08-09T00:00",
            ),
            "news",
        )
    with pytest.raises(SearchTimeFilterError, match="publish_time"):
        normalize_and_validate_time_semantics(
            SearchRequest(keyword="China", publish_time="最近几天"),
            "news",
        )

    for field, value in (
        ("publish_time", "x" * 17),
        ("start_time", "x" * 65),
        ("end_time", "x" * 65),
        ("sort_by", "x" * 33),
        ("sort_order", "x" * 9),
    ):
        with pytest.raises(ValueError):
            SearchRequest.model_validate({"keyword": "China", field: value})


def test_unimplemented_hierarchy_filters_fail_closed_instead_of_being_ignored() -> None:
    params = SearchRequest(
        keyword="China",
        search_type="l2",
        data_source="example.com",
        language="en",
        hit_location="正文",
        sort_by="pub_time",
    )
    with pytest.raises(SearchFilterUnsupported) as captured:
        validate_supported_filters(params, "l2")

    assert captured.value.fields == (
        "data_source",
        "language",
        "hit_location",
        "sort_by",
    )

    with pytest.raises(SearchFilterUnsupported) as similarity:
        validate_supported_filters(
            SearchRequest(keyword="China", search_type="l2", sort_by="similarity"),
            "l2",
        )
    assert similarity.value.fields == ("sort_by",)


def test_news_sort_contract_rejects_unknown_fields_and_orders() -> None:
    validate_supported_filters(
        SearchRequest(keyword="China", sort_by="published_at", sort_order="asc"),
        "news",
    )
    with pytest.raises(SearchFilterUnsupported) as ambiguous_time:
        validate_supported_filters(
            SearchRequest(keyword="China", sort_by="time", sort_order="desc"),
            "news",
        )
    assert ambiguous_time.value.fields == ("sort_by",)

    legacy_sort = SearchRequest(keyword="China", sort_by="pub_time", sort_order="desc")
    validate_supported_filters(legacy_sort, "news")
    assert {
        "field": "published_at",
        "operator": "legacy_alias_sort_desc",
        "value": "pub_time",
    } in news_search_v2._applied_filter_explain(legacy_sort)

    with pytest.raises(SearchFilterUnsupported) as similarity_ascending:
        validate_supported_filters(
            SearchRequest(keyword="China", sort_by="similarity", sort_order="asc"),
            "news",
        )
    assert similarity_ascending.value.fields == ("sort_order",)
    with pytest.raises(SearchFilterUnsupported) as captured:
        validate_supported_filters(
            SearchRequest(keyword="China", sort_by="opaque-score", sort_order="sideways"),
            "news",
        )
    assert captured.value.fields == ("sort_by", "sort_order")


def test_bounded_title_path_honors_time_sort_and_refuses_incomplete_ascending_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return []

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, _bind):
            captured["sql"] = str(statement)
            return _Rows()

    monkeypatch.setattr(news_search_v2, "_search_connection", lambda: _Connection())
    descending = SearchRequest(
        keyword="control",
        sort_by="published_at",
        sort_order="desc",
    )
    assert news_search_v2._news_rows_from_title_matches(descending, 1, 10) == ([], 0)
    assert "0 AS relevance_rank" in captured["sql"]

    ascending = SearchRequest(
        keyword="control",
        sort_by="published_at",
        sort_order="asc",
    )
    assert news_search_v2._news_rows_from_title_matches(ascending, 1, 10) is None


def test_query_explain_reports_actual_aliases_counts_and_manual_relaxation_only() -> None:
    params = SearchRequest(
        keyword="China semiconductor",
        mode="exact",
        search_type="news",
        time_field="published_at",
    )
    explain = news_search_v2._build_query_explain(params, total=0)

    assert explain.normalized_terms == ["China", "semiconductor"]
    assert explain.query_language == QUERY_LANGUAGE_VERSION
    assert explain.query_ast["type"] == "and"
    assert explain.expanded_query_ast["children"][0]["entity_id"] == "urn:globemind:entity:country:CN"
    assert "ANY(" in explain.execution_expression
    assert explain.limits["observed_terms"] == 2
    assert explain.entity_expansions[0].entity_id == "urn:globemind:entity:country:CN"
    assert explain.entity_expansions[0].query_field == "primary_query"
    assert "中国" in explain.entity_expansions[0].expanded_aliases
    assert explain.entity_expansions[0].review_status == "review_required"
    assert explain.entity_expansions[0].expanded_alias_details
    assert explain.effective_mode == "exact"
    assert explain.effective_search_fields == ["news.title"]
    assert "当前未应用时间限制" in explain.time.predicate
    assert explain.automatic_relaxation is False
    assert explain.stages[2].matched_count == 0
    assert explain.stages[2].count_semantics == "api_response_total"
    assert explain.stages[3].status == "not_run"
    assert any(
        "系统本次未自动切换模式" in item
        for item in explain.relaxation_suggestions
    )


def test_hierarchy_explain_uses_event_interval_and_real_fuzzy_expansion() -> None:
    params = SearchRequest(
        keyword="Taiwan",
        mode="fuzzy",
        search_type="l2",
        time_field="event_time",
        start_time="2026-08-01T00:00",
    )
    explain = news_search_v2._build_query_explain(params, total=3)

    assert explain.search_type == "l2"
    assert explain.effective_mode == "fuzzy"
    assert len(explain.expanded_terms) > 1
    assert explain.time.applied_field == "event.start_date/event.end_date"
    assert "区间" in explain.time.predicate
    assert "collected_at（采集时间）" in explain.time.unavailable_fields


def test_all_search_text_controls_share_the_unsupported_syntax_gate() -> None:
    params = SimpleNamespace(
        keyword="China",
        topic=None,
        must_include="safe",
        any_include="safe",
        need_exclude="source:unknown",
    )
    with pytest.raises(SearchSyntaxUnsupported) as captured:
        validate_supported_query(params)
    assert captured.value.features == ("field_scope_or_weight",)


def test_audited_language_codes_have_user_facing_labels() -> None:
    assert {
        code: news_search_v2.LANG_LABELS[code]
        for code in ("bn", "gu", "ta", "th")
    } == {
        "bn": "孟加拉语",
        "gu": "古吉拉特语",
        "ta": "泰米尔语",
        "th": "泰语",
    }
