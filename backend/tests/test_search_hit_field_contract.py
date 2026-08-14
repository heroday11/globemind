from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.features.search import (
    SEARCH_HIT_SCHEMA_VERSION,
    build_search_hit_disclosure,
)
from api.models.schemas import (
    NewsItem,
    NewsResultTimeSemantics,
    SearchRequest,
    SearchHitDisclosure,
)


def _news(**overrides: object) -> NewsItem:
    payload: dict[str, object] = {
        "id": 7,
        "title": "😀 China <script>alert(1)</script>",
        "abstract": "China and 中国 are display text.",
        "time_semantics": NewsResultTimeSemantics(),
    }
    payload.update(overrides)
    return NewsItem(**payload)


def test_literal_display_spans_are_bounded_code_point_offsets_without_html() -> None:
    disclosure = build_search_hit_disclosure(
        title="😀 China <script>alert(1)</script>",
        abstract="China and 中国 are display text.",
        positive_literal_terms=["china", "<script>", "China"],
        effective_search_fields=["news.title", "news.body"],
    )

    assert disclosure.schema_version == SEARCH_HIT_SCHEMA_VERSION
    assert disclosure.status == "available"
    assert disclosure.offset_encoding == "unicode_code_points"
    assert disclosure.coverage == "positive_literal_terms_in_returned_display_only"
    assert disclosure.alias_span_state == "not_available"
    assert disclosure.relevance_score_state == "not_available"
    assert disclosure.reason_code == "DISPLAY_LITERAL_MATCHES_FOUND"
    assert len(disclosure.spans) == 3
    assert [(span.field, span.start, span.end) for span in disclosure.spans] == [
        ("title", 2, 7),
        ("title", 8, 16),
        ("abstract", 0, 5),
    ]
    dumped = disclosure.model_dump()
    assert "<script>" not in str(dumped)
    assert "china" not in str(dumped).lower()

    item = _news(search_hit=dumped)
    assert item.search_hit.status == "available"


def test_missing_display_span_is_not_reported_as_no_document_match() -> None:
    disclosure = build_search_hit_disclosure(
        title="Unrelated returned title",
        abstract="The matching body segment is outside this snippet.",
        positive_literal_terms=["needle"],
        effective_search_fields=["news.body"],
    )

    assert disclosure.status == "no_display_span"
    assert disclosure.spans == []
    assert disclosure.reason_code == "NO_LITERAL_SPAN_IN_RETURNED_DISPLAY_TEXT"
    assert disclosure.document_match_state == "not_asserted"


@pytest.mark.parametrize(
    "search_hit",
    [
        {
            "schema_version": SEARCH_HIT_SCHEMA_VERSION,
            "status": "available",
            "offset_encoding": "unicode_code_points",
            "coverage": "positive_literal_terms_in_returned_display_only",
            "effective_search_fields": ["news.title"],
            "alias_span_state": "not_available",
            "relevance_score_state": "not_available",
            "document_match_state": "not_asserted",
            "reason_code": "DISPLAY_LITERAL_MATCHES_FOUND",
            "spans": [{"field": "title", "start": 0, "end": 999}],
        },
        {
            "schema_version": SEARCH_HIT_SCHEMA_VERSION,
            "status": "available",
            "offset_encoding": "unicode_code_points",
            "coverage": "positive_literal_terms_in_returned_display_only",
            "effective_search_fields": ["news.title"],
            "alias_span_state": "not_available",
            "relevance_score_state": "not_available",
            "document_match_state": "not_asserted",
            "reason_code": "DISPLAY_LITERAL_MATCHES_FOUND",
            "spans": [
                {"field": "title", "start": 0, "end": 5},
                {"field": "title", "start": 4, "end": 6},
            ],
        },
        {
            "schema_version": SEARCH_HIT_SCHEMA_VERSION,
            "status": "available",
            "offset_encoding": "unicode_code_points",
            "coverage": "positive_literal_terms_in_returned_display_only",
            "effective_search_fields": ["news.title"],
            "alias_span_state": "not_available",
            "relevance_score_state": "not_available",
            "document_match_state": "not_asserted",
            "reason_code": "DISPLAY_LITERAL_MATCHES_FOUND",
            "spans": [{"field": "title", "start": True, "end": 5.0}],
        },
    ],
)
def test_news_item_rejects_out_of_bounds_and_overlapping_spans(
    search_hit: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="search[ _]hit"):
        _news(search_hit=search_hit)


def test_non_search_news_item_defaults_to_explicit_unavailable() -> None:
    item = _news()

    assert isinstance(item.search_hit, SearchHitDisclosure)
    assert item.search_hit.status == "unavailable"
    assert item.search_hit.reason_code == "NOT_A_SEARCH_RESPONSE"
    assert item.search_hit.spans == []


def test_dashboard_search_attaches_only_positive_literal_display_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import news_search_v2

    monkeypatch.setattr(
        news_search_v2,
        "_news_search_rows",
        lambda _params, _page, _size: (
            [
                {
                    "id": 9,
                    "title": "China policy, not spoiler",
                    "body": "Policy context mentions China and spoiler.",
                    "pub_time": None,
                }
            ],
            1,
        ),
    )
    response = news_search_v2.search_dashboard_v2(
        SearchRequest(
            keyword="China AND policy NOT spoiler",
            hit_location="标题",
            search_type="news",
        ),
        user=None,
        app_db=None,
    )

    assert response.data[0].search_hit.status == "available"
    assert response.data[0].search_hit.effective_search_fields == ["news.title"]
    slices = [
        response.data[0].title[span.start : span.end].casefold()
        for span in response.data[0].search_hit.spans
    ]
    assert slices == ["china", "policy"]
    assert "spoiler" not in slices
