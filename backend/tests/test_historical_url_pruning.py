from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from discover_historical_urls import (  # noqa: E402
    candidate_year,
    classify_precise_url_window,
    classify_url_window,
    is_url_in_crawl_window,
    load_window,
    parse_date,
    sitemap_window_reason,
)
from prune_discovered_urls_queue import should_keep_discovered_row  # noqa: E402


def wave1_window():
    return load_window("2025-06-21", "2026-06-20")


def test_csmonitor_legacy_url_date_is_rejected_even_with_fresh_lastmod():
    start_dt, end_dt = wave1_window()
    lastmod = parse_date("2026-06-20T12:00:00+00:00")
    url = "https://www.csmonitor.com/1980/0102/010249.html"

    assert classify_url_window(url, lastmod, start_dt, end_dt) == "url_date_before_window"
    assert not is_url_in_crawl_window(url, lastmod, start_dt, end_dt)


def test_archive_year_path_is_rejected_when_full_url_date_is_absent():
    start_dt, end_dt = wave1_window()
    url = "https://www.nato.int/docu/update/1995/9501e.htm"

    assert candidate_year(url) == 1995
    assert classify_url_window(url, None, start_dt, end_dt) == "url_year_before_window"


def test_url_date_inside_window_beats_late_sitemap_lastmod():
    start_dt, end_dt = wave1_window()
    url = "https://example.com/news/2025/07/15/story.html"
    lastmod = parse_date("2026-07-01T00:00:00+00:00")

    assert classify_url_window(url, lastmod, start_dt, end_dt) == "keep"


def test_undated_article_url_is_kept_without_lastmod():
    start_dt, end_dt = wave1_window()
    row = {"site_id": "example_com", "url": "https://example.com/world/current-affairs"}

    assert should_keep_discovered_row(row, start_dt, end_dt) == (True, "keep")


def test_prune_row_rejects_legacy_url_with_window_filter():
    start_dt, end_dt = wave1_window()
    row = {
        "site_id": "csmonitor_com",
        "url": "https://www.csmonitor.com/1980/0102/010249.html",
        "lastmod": "2026-06-20T12:00:00+00:00",
    }

    assert should_keep_discovered_row(row, start_dt, end_dt) == (False, "url_date_before_window")


def test_sitemap_archive_year_is_rejected():
    start_dt, end_dt = wave1_window()

    assert (
        sitemap_window_reason("https://www.nato.int/sitemap-1995.xml", None, start_dt, end_dt)
        == "sitemap_url_year_before_window"
    )


def test_sitemap_lastmod_after_end_does_not_reject_whole_sitemap():
    start_dt, end_dt = wave1_window()
    lastmod = parse_date("2026-07-01T00:00:00+00:00")

    assert sitemap_window_reason("https://example.com/news-sitemap.xml", lastmod, start_dt, end_dt) == "keep"


def test_precise_daily_mode_rejects_undated_url_without_lastmod():
    start_dt, end_dt = wave1_window()

    assert (
        classify_precise_url_window("https://example.com/world/current-affairs", None, start_dt, end_dt)
        == "missing_precise_window_signal"
    )


def test_precise_daily_mode_accepts_in_window_lastmod():
    start_dt, end_dt = wave1_window()
    lastmod = parse_date("2025-07-01T12:00:00+00:00")

    assert classify_precise_url_window("https://example.com/world/current-affairs", lastmod, start_dt, end_dt) == "keep"


def test_prune_row_strict_mode_rejects_undated_url():
    start_dt, end_dt = wave1_window()
    row = {"site_id": "example_com", "url": "https://example.com/world/current-affairs"}

    assert should_keep_discovered_row(row, start_dt, end_dt, require_date_signal=True) == (
        False,
        "missing_precise_window_signal",
    )
