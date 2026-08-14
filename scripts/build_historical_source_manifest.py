#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

from historical_http import curl_fetch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "source_curation" / "seed_whitelist_priority_v4.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "source_curation" / "historical_source_manifest_v1.csv"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "HISTORICAL_SOURCE_MANIFEST_V1_REPORT.md"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GlobeMindHistoricalProbe/1.0; "
        "+https://example.invalid/globemind)"
    ),
    "Accept": "text/plain,text/xml,application/xml,text/html;q=0.9,*/*;q=0.8",
}

COMMON_SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/news-sitemap.xml",
    "/sitemap_news.xml",
    "/sitemap-news.xml",
    "/post-sitemap.xml",
    "/page-sitemap.xml",
)
COMMON_FEED_PATHS = (
    "/feed",
    "/rss",
    "/rss.xml",
    "/feeds/posts/default",
)
ARCHIVE_HINT_KEYWORDS = (
    "archive",
    "archives",
    "newsroom",
    "press",
    "press-releases",
    "press-releases-and-statements",
    "media",
    "latest-news",
    "allnews",
)


@dataclass
class HttpProbe:
    url: str
    status_code: int | None
    content_type: str
    final_url: str
    body: str
    ok: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe whitelist sites and build a historical acquisition manifest."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows


def unique_preserve(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def build_origin(seed_url: str, declared_domain: str) -> tuple[str, str]:
    parsed = urlparse(seed_url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or declared_domain
    if not netloc:
        raise ValueError(f"Unable to determine netloc for {seed_url}")
    domain = declared_domain or netloc
    return f"{scheme}://{netloc}", domain


def fetch_text(url: str, timeout: float) -> HttpProbe:
    resp = curl_fetch(
        url=url,
        timeout=timeout,
        user_agent=HTTP_HEADERS["User-Agent"],
        accept=HTTP_HEADERS["Accept"],
    )
    return HttpProbe(
        url=url,
        status_code=resp.status_code,
        content_type=resp.content_type,
        final_url=resp.final_url,
        body=resp.body_text[:200000],
        ok=resp.ok,
    )


def parse_robots_sitemaps(robots_text: str) -> list[str]:
    lines = robots_text.splitlines()
    sitemaps: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() == "sitemap":
            sitemaps.append(value.strip())
    return sitemaps


def looks_like_xml(probe: HttpProbe) -> bool:
    content_type = probe.content_type.lower()
    body_head = probe.body[:2000].lower()
    return (
        "xml" in content_type
        or probe.final_url.endswith(".xml")
        or probe.final_url.endswith(".xml.gz")
        or "<urlset" in body_head
        or "<sitemapindex" in body_head
    )


def looks_like_feed(probe: HttpProbe) -> bool:
    content_type = probe.content_type.lower()
    body_head = probe.body[:2000].lower()
    return (
        "rss" in content_type
        or "atom" in content_type
        or "<rss" in body_head
        or "<feed" in body_head
    )


def classify_strategy(accessible_sitemaps: list[str], accessible_feeds: list[str], seed_url: str) -> str:
    if accessible_sitemaps:
        return "direct_sitemap"
    if accessible_feeds:
        return "feed_plus_archive"
    if any(keyword in seed_url.lower() for keyword in ARCHIVE_HINT_KEYWORDS):
        return "section_archive_plus_wayback"
    return "gdelt_or_wayback_plus_direct_fetch"


def classify_coverage(strategy: str, accessible_sitemaps: list[str], seed_url: str) -> str:
    if strategy == "direct_sitemap":
        if any("post-sitemap" in url or "news-sitemap" in url or "sitemap_index" in url for url in accessible_sitemaps):
            return "high"
        return "medium"
    if strategy == "feed_plus_archive":
        return "medium"
    if any(keyword in seed_url.lower() for keyword in ARCHIVE_HINT_KEYWORDS):
        return "medium"
    return "low"


def probe_site(row: dict[str, str], timeout: float) -> dict[str, str]:
    seed_url = row["url"].strip()
    origin, domain = build_origin(seed_url, row.get("domain", "").strip())
    robots_url = urljoin(origin + "/", "robots.txt")

    robots_probe = fetch_text(robots_url, timeout)
    robot_sitemaps = parse_robots_sitemaps(robots_probe.body) if robots_probe.ok else []

    sitemap_candidates = unique_preserve(
        robot_sitemaps + [urljoin(origin + "/", path.lstrip("/")) for path in COMMON_SITEMAP_PATHS]
    )
    feed_candidates = unique_preserve(
        [urljoin(origin + "/", path.lstrip("/")) for path in COMMON_FEED_PATHS]
    )

    accessible_sitemaps: list[str] = []
    for candidate in sitemap_candidates[:12]:
        probe = fetch_text(candidate, timeout)
        if probe.ok and looks_like_xml(probe):
            accessible_sitemaps.append(probe.final_url)
    accessible_sitemaps = unique_preserve(accessible_sitemaps)

    accessible_feeds: list[str] = []
    for candidate in feed_candidates[:8]:
        probe = fetch_text(candidate, timeout)
        if probe.ok and looks_like_feed(probe):
            accessible_feeds.append(probe.final_url)
    accessible_feeds = unique_preserve(accessible_feeds)

    strategy = classify_strategy(accessible_sitemaps, accessible_feeds, seed_url)
    coverage = classify_coverage(strategy, accessible_sitemaps, seed_url)

    result = dict(row)
    result.update(
        {
            "seed_origin": origin,
            "robots_url": robots_url,
            "robots_status": "" if robots_probe.status_code is None else str(robots_probe.status_code),
            "robots_has_sitemap": "1" if robot_sitemaps else "0",
            "candidate_sitemap_count": str(len(sitemap_candidates)),
            "accessible_sitemap_count": str(len(accessible_sitemaps)),
            "accessible_sitemaps": json.dumps(accessible_sitemaps, ensure_ascii=False),
            "accessible_feed_count": str(len(accessible_feeds)),
            "accessible_feeds": json.dumps(accessible_feeds, ensure_ascii=False),
            "historical_strategy": strategy,
            "expected_coverage": coverage,
            "wayback_fallback": "1",
            "seed_has_archive_hint": "1"
            if any(keyword in seed_url.lower() for keyword in ARCHIVE_HINT_KEYWORDS)
            else "0",
        }
    )
    return result


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, input_path: Path, output_path: Path, rows: list[dict[str, str]]) -> None:
    by_strategy = Counter(row["historical_strategy"] for row in rows)
    by_coverage = Counter(row["expected_coverage"] for row in rows)
    by_layer = Counter(row.get("layer", "") for row in rows)
    strategy_layer: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        strategy_layer[row["historical_strategy"]][row.get("layer", "")] += 1

    lines = [
        "# Historical Source Manifest V1",
        "",
        f"- Input: [{input_path.name}]({input_path})",
        f"- Output: [{output_path.name}]({output_path})",
        f"- Total sites probed: `{len(rows)}`",
        "",
        "## Strategy Counts",
        "",
    ]
    for key, value in sorted(by_strategy.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Coverage Expectation", ""])
    for key, value in sorted(by_coverage.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Layer Counts", ""])
    for key, value in sorted(by_layer.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Strategy By Layer", ""])
    for strategy in sorted(strategy_layer):
        pieces = ", ".join(
            f"{layer or 'unknown'}={count}" for layer, count in sorted(strategy_layer[strategy].items())
        )
        lines.append(f"- `{strategy}`: {pieces}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    rows = read_rows(args.input)
    if args.limit > 0:
        rows = rows[: args.limit]

    results: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(probe_site, row, args.timeout): row["site_id"]
            for row in rows
        }
        for future in as_completed(future_map):
            results.append(future.result())

    results.sort(key=lambda row: row["site_id"])
    write_csv(args.output, results)
    write_report(args.report, args.input, args.output, results)
    print(f"wrote {len(results)} rows to {args.output}")
    print(f"wrote report to {args.report}")


if __name__ == "__main__":
    main()
