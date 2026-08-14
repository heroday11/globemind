from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from news_ingest_quality import assess_news_row  # noqa: E402


NOW = datetime(2026, 7, 8, 2, 0, tzinfo=timezone.utc)


def test_accepts_normal_article():
    result = assess_news_row(
        {
            "title": "Government announces new energy security measures",
            "body": "Officials said the package will expand grid investment and support regional suppliers. "
            * 4,
            "url": "https://example.com/news/2026/07/08/energy-security-measures",
            "published_at": "2026-07-08T01:00:00+00:00",
        },
        now=NOW,
    )

    assert result.is_good
    assert result.reasons == ()


def test_rejects_page_like_title_and_url():
    result = assess_news_row(
        {
            "title": "Editorial Standards",
            "body": "This page explains the standards used by the newsroom and does not report a news event. " * 3,
            "url": "https://example.com/about/editorial-standards",
            "published_at": "2026-07-08T01:00:00+00:00",
        },
        now=NOW,
    )

    assert not result.is_good
    assert "page_like_title" in result.reasons
    assert "page_like_url" in result.reasons


def test_rejects_short_body():
    result = assess_news_row(
        {
            "title": "Markets open higher after central bank statement",
            "body": "Short update.",
            "url": "https://example.com/2026/07/08/markets-open-higher",
            "published_at": "2026-07-08T01:00:00+00:00",
        },
        now=NOW,
    )

    assert not result.is_good
    assert "body_too_short" in result.reasons


def test_rejects_future_date_beyond_grace():
    result = assess_news_row(
        {
            "title": "Election commission publishes campaign finance data",
            "body": "The commission published a detailed release with figures from all major parties. " * 4,
            "url": "https://example.com/news/2026/07/10/campaign-finance-data",
            "published_at": "2026-07-10T00:00:00+00:00",
        },
        now=NOW,
    )

    assert not result.is_good
    assert "published_future_too_far" in result.reasons


def test_rejects_non_http_and_malformed_urls_without_crashing():
    base = {
        "title": "Election commission publishes campaign finance data",
        "body": "The commission published a detailed release with attributed figures. "
        * 4,
        "published_at": "2026-07-08T01:00:00+00:00",
    }

    for value in (
        "javascript:alert(1)",
        "https://example.com:bad/news",
        "http://[",
        "https://user:secret@example.com/news",
        "http://localhost/private",
        "http://127.0.0.1/private",
    ):
        result = assess_news_row({**base, "url": value}, now=NOW)
        assert not result.is_good
        assert "invalid_url" in result.reasons
