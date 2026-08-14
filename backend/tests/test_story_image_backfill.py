from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backfill_story_images import editorial_svg, extract_image_urls  # noqa: E402


def test_extract_image_urls_skips_loader_and_spinner_assets():
    html = """
    <img src="/wp-content/themes/site/images/loader.png">
    <img srcset="/spinner.webp 1x, /photos/story-wide.jpg 2x">
    <img src="/photos/story.jpg">
    """

    images = extract_image_urls(html, "https://example.com/news/item")

    assert ("img:src", "https://example.com/photos/story.jpg") in images
    assert ("img:srcset", "https://example.com/photos/story-wide.jpg") in images
    assert all("loader" not in url and "spinner" not in url for _kind, url in images)


def test_editorial_svg_is_cluster_specific_visual_asset():
    svg = editorial_svg(
        {
            "cluster_id": "fast_l1_v2_example",
            "title": "Regional infrastructure development",
            "event_family": "public_development",
        }
    )

    assert svg.startswith("<svg")
    assert "Regional infrastructure development" in svg
    assert "linearGradient" in svg
