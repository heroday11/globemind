from __future__ import annotations

from types import SimpleNamespace

from api.services import news_search_v2


def test_unquoted_exact_title_candidates_require_each_term() -> None:
    groups = news_search_v2._title_match_groups(
        "China semiconductor",
        expand_aliases=False,
    )
    assert groups[0][0] == "China"
    assert "中国" in groups[0]
    assert groups[1] == ["semiconductor"]

    sql, _bind = news_search_v2._title_match_cte_sql(
        groups,
        "research_topic",
        match_all=True,
    )
    assert "INTERSECT" in sql
    assert " OR " in sql


def test_quotes_are_required_for_literal_phrase_matching() -> None:
    assert news_search_v2._title_match_terms(
        '"China semiconductor"',
        expand_aliases=False,
    ) == ["China semiconductor"]
    assert news_search_v2._text_match_mode("“红海 航运”", "exact") == (
        "phrase",
        "红海 航运",
    )
    assert news_search_v2._text_match_mode("红海 航运", "exact") == ("and", "红海 航运")
    assert news_search_v2._text_match_mode("红海 航运", "fuzzy") == ("or", "红海 航运")


def test_time_range_keeps_news_search_on_bounded_title_candidates() -> None:
    params = SimpleNamespace(
        keyword="Taiwan Strait",
        topic="Taiwan Strait",
        publish_time="近三月",
        start_time="",
        end_time="",
        must_include="",
        any_include="",
        need_exclude="",
        data_source="",
        language="",
        country="",
        site="",
    )

    assert news_search_v2._has_advanced_search_filters(params) is True
    assert news_search_v2._has_title_candidate_incompatible_filters(params) is False


def test_secondary_text_filter_uses_full_filter_path() -> None:
    params = SimpleNamespace(
        keyword="Taiwan Strait",
        topic="Taiwan Strait",
        publish_time="近三月",
        must_include="military",
        any_include="",
        need_exclude="",
        data_source="",
        language="",
        country="",
        site="",
    )

    assert news_search_v2._has_title_candidate_incompatible_filters(params) is True


def test_search_deadline_is_shorter_than_browser_budget() -> None:
    assert news_search_v2.SEARCH_DEADLINE_SECONDS == 6.0
    assert news_search_v2.FUZZY_TITLE_CANDIDATES_PER_TERM == 500
    assert "taiwan" in news_search_v2.FUZZY_LITERAL_TITLE_QUERIES
    assert "control" in news_search_v2.FUZZY_LITERAL_TITLE_QUERIES


def test_south_china_sea_uses_direct_l1_cluster_path() -> None:
    assert news_search_v2._should_expand_l1_aliases("South China Sea", "fuzzy") is False
    assert news_search_v2._should_expand_l1_aliases("南海", "fuzzy") is True
    assert news_search_v2._should_expand_l1_aliases("南海", "exact") is False


def test_control_query_prioritizes_technology_and_export_controls() -> None:
    order = news_search_v2._query_specific_news_order("control")
    assert "semiconductor" in order
    assert "export" in order
    assert news_search_v2._query_specific_news_order("Ukraine") == "0"
