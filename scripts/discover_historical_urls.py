#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from historical_http import curl_fetch
from news_date_cleaning import date_from_url


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "source_curation" / "historical_source_manifest_v1.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "historical_news" / "discovered_urls_3y.jsonl"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "HISTORICAL_URL_DISCOVERY_REPORT.md"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GlobeMindHistoricalDiscovery/1.0; "
        "+https://example.invalid/globemind)"
    ),
    "Accept": "application/xml,text/xml,text/plain,text/html;q=0.9,*/*;q=0.8",
}
SKIP_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".mp4",
    ".mp3",
    ".pdf",
    ".zip",
    ".gz",
)
YEAR_PATH_SEGMENT_RE = re.compile(r"/(?P<year>18\d{2}|19\d{2}|20\d{2})(?:/|$)")
SITEMAP_YEAR_RE = re.compile(r"(?:^|[/_-])(?P<year>18\d{2}|19\d{2}|20\d{2})(?=$|[/_\-.])")
GENERIC_URL_DENY_PATTERNS = (
    re.compile(r"/feed/?$", re.IGNORECASE),
    re.compile(r"/feeds?/", re.IGNORECASE),
    re.compile(r"/(?:tag|tags|topic|topics|label|labels|classification|serial|author|authors)(?:/|$)", re.IGNORECASE),
    re.compile(r"/(?:sitemap|site-map)(?:/|$)", re.IGNORECASE),
    re.compile(r"/(?:about|about-us|contact|privacy|terms|advertis(?:e|ing)?|careers?|jobs?)(?:/|$)", re.IGNORECASE),
    re.compile(r"/(?:category|section|sections)/[^/]+/?$", re.IGNORECASE),
    re.compile(r"/page/\d+/?$", re.IGNORECASE),
)
SITE_SITEMAP_DENY_PATTERNS = {
    "cbsnews_com": (
        re.compile(r"/xml-sitemap/video-", re.IGNORECASE),
        re.compile(r"/xml-sitemap/pictures-", re.IGNORECASE),
    ),
    "freemalaysiatoday_com": (
        re.compile(r"/feeds-sitemap\.xml$", re.IGNORECASE),
    ),
}
SITE_URL_DENY_PATTERNS = {
    "cbsnews_com": (
        re.compile(r"/video/", re.IGNORECASE),
    ),
    "freemalaysiatoday_com": (
        re.compile(r"/feed/?$", re.IGNORECASE),
        re.compile(r"/category/[^/]+/feed/?$", re.IGNORECASE),
        re.compile(r"^https?://(?:www\.)?freemalaysiatoday\.com/(?:about|accelerator|berita)/?$", re.IGNORECASE),
    ),
    "prothomalo_com": (
        re.compile(r"^https?://1971\.prothomalo\.com/", re.IGNORECASE),
        re.compile(r"^https?://(?:nagorik|trust)\.prothomalo\.com/", re.IGNORECASE),
        re.compile(r"^https?://www\.prothomalo\.com/\.well-known/", re.IGNORECASE),
        re.compile(r"^https?://www\.prothomalo\.com/app-ads\.txt$", re.IGNORECASE),
        re.compile(r"^https?://www\.prothomalo\.com/(?:anniversary|bangladesh|video)/?$", re.IGNORECASE),
    ),
    "asean_news": (
        re.compile(r"/(?:classification|serial|tag)(?:/|$)", re.IGNORECASE),
        re.compile(r"/other-asean-jobs/?$", re.IGNORECASE),
        re.compile(r"/sitemap/?$", re.IGNORECASE),
    ),
    "cbsnews_com": (
        re.compile(r"^https?://(?:www\.)?cbsnews\.com/48-hours/(?:about-us|episode-schedule)/?$", re.IGNORECASE),
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover historical article URLs from sitemap feeds.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-sitemaps-per-site", type=int, default=80)
    parser.add_argument("--max-urls-per-site", type=int, default=0)
    parser.add_argument("--site-id", action="append", default=[])
    parser.add_argument("--start-date", default="2023-06-21")
    parser.add_argument("--end-date", default="2026-06-21")
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = text.replace(" ", "T")
    for candidate in (text, text[:19], text[:10]):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


def load_window(start_date_text: str, end_date_text: str) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(date.fromisoformat(start_date_text), datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(date.fromisoformat(end_date_text), datetime.max.time(), tzinfo=timezone.utc)
    return start_dt, end_dt


def json_list(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed if item]
    return []


def get_xml_bytes(url: str, timeout: float) -> bytes | None:
    resp = curl_fetch(
        url=url,
        timeout=timeout,
        user_agent=HTTP_HEADERS["User-Agent"],
        accept=HTTP_HEADERS["Accept"],
    )
    if not resp.ok:
        return None
    raw = resp.body_bytes
    try:
        if url.endswith(".gz") or "gzip" in resp.content_type.lower():
            raw = gzip.decompress(raw)
    except OSError:
        return None
    return raw


def localname(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def candidate_year(url: str, *, include_sitemap_filename: bool = False) -> int | None:
    url_dt = date_from_url(url)
    if url_dt:
        return url_dt.year

    path = urlparse(url or "").path
    patterns = [YEAR_PATH_SEGMENT_RE]
    if include_sitemap_filename:
        patterns.append(SITEMAP_YEAR_RE)
    for pattern in patterns:
        match = pattern.search(path)
        if not match:
            continue
        try:
            return int(match.group("year"))
        except ValueError:
            return None
    return None


def classify_url_window(
    url: str,
    lastmod: datetime | None,
    start_dt: datetime,
    end_dt: datetime,
) -> str:
    """Return keep or the reason a URL is outside the requested crawl window."""
    url_dt = date_from_url(url)
    if url_dt:
        if url_dt < start_dt:
            return "url_date_before_window"
        if url_dt > end_dt:
            return "url_date_after_window"
        return "keep"

    year_hint = candidate_year(url)
    if year_hint is not None:
        if year_hint < start_dt.year:
            return "url_year_before_window"
        if year_hint > end_dt.year:
            return "url_year_after_window"

    if lastmod:
        if lastmod < start_dt:
            return "lastmod_before_window"
        if lastmod > end_dt:
            return "lastmod_after_window"
    return "keep"


def is_url_in_crawl_window(
    url: str,
    lastmod: datetime | None,
    start_dt: datetime,
    end_dt: datetime,
) -> bool:
    return classify_url_window(url, lastmod, start_dt, end_dt) == "keep"


def classify_precise_url_window(
    url: str,
    lastmod: datetime | None,
    start_dt: datetime,
    end_dt: datetime,
) -> str:
    """Strict daily mode: require an exact URL date or a sitemap lastmod in-window."""
    url_dt = date_from_url(url)
    if url_dt:
        if url_dt < start_dt:
            return "url_date_before_window"
        if url_dt > end_dt:
            return "url_date_after_window"
        return "keep"

    if lastmod:
        if lastmod < start_dt:
            return "lastmod_before_window"
        if lastmod > end_dt:
            return "lastmod_after_window"
        return "keep"

    return "missing_precise_window_signal"


def sitemap_window_reason(
    sitemap_url: str,
    lastmod: datetime | None,
    start_dt: datetime,
    end_dt: datetime,
) -> str:
    sitemap_dt = date_from_url(sitemap_url)
    if sitemap_dt:
        if sitemap_dt < start_dt:
            return "sitemap_url_date_before_window"
        if sitemap_dt > end_dt:
            return "sitemap_url_date_after_window"
        return "keep"

    year_hint = candidate_year(sitemap_url, include_sitemap_filename=True)
    if year_hint is not None:
        if year_hint < start_dt.year:
            return "sitemap_url_year_before_window"
        if year_hint > end_dt.year:
            return "sitemap_url_year_after_window"

    if lastmod:
        if lastmod < start_dt:
            return "sitemap_lastmod_before_window"
    return "keep"


def looks_like_article_url(url: str, domain: str) -> bool:
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        return False
    if domain and domain not in parsed.netloc:
        return False
    lowered = parsed.path.lower()
    if lowered.endswith(SKIP_EXTENSIONS):
        return False
    if lowered in ("", "/"):
        return False
    return True


def is_blocked_sitemap(site_id: str, sitemap_url: str) -> bool:
    for pattern in SITE_SITEMAP_DENY_PATTERNS.get(site_id, ()):
        if pattern.search(sitemap_url):
            return True
    return False


def is_blocked_article_url(site_id: str, article_url: str) -> bool:
    for pattern in GENERIC_URL_DENY_PATTERNS:
        if pattern.search(article_url):
            return True
    for pattern in SITE_URL_DENY_PATTERNS.get(site_id, ()):
        if pattern.search(article_url):
            return True
    return False


def write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def discover_site(
    row: dict[str, str],
    timeout: float,
    start_dt: datetime,
    end_dt: datetime,
    max_sitemaps_per_site: int,
    max_urls_per_site: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    site_id = row["site_id"]
    domain = row.get("domain", "").strip()
    origin = row.get("seed_origin", "").strip()
    discovered: list[dict[str, object]] = []
    stats: Counter[str] = Counter()
    seen_urls: set[str] = set()

    sitemap_queue = json_list(row.get("accessible_sitemaps", ""))
    if not sitemap_queue and origin:
        sitemap_queue = [urljoin(origin + "/", "sitemap.xml")]
    sitemap_queue = sitemap_queue[:max_sitemaps_per_site]
    visited_sitemaps: set[str] = set()

    while sitemap_queue and len(visited_sitemaps) < max_sitemaps_per_site:
        sitemap_url = sitemap_queue.pop(0)
        if not sitemap_url or sitemap_url in visited_sitemaps:
            continue
        if is_blocked_sitemap(site_id, sitemap_url):
            stats["sitemap_filtered"] += 1
            continue
        visited_sitemaps.add(sitemap_url)
        payload = get_xml_bytes(sitemap_url, timeout)
        if not payload:
            stats["sitemap_fetch_failed"] += 1
            continue
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            stats["sitemap_parse_failed"] += 1
            continue

        tag = localname(root.tag)
        if tag == "sitemapindex":
            stats["sitemapindex"] += 1
            for node in root:
                if localname(node.tag) != "sitemap":
                    continue
                loc = ""
                lastmod = None
                for child in node:
                    child_name = localname(child.tag)
                    if child_name == "loc":
                        loc = (child.text or "").strip()
                    elif child_name == "lastmod":
                        lastmod = parse_date(child.text or "")
                if not loc or loc in visited_sitemaps:
                    continue
                window_reason = sitemap_window_reason(loc, lastmod, start_dt, end_dt)
                if window_reason != "keep":
                    stats[window_reason] += 1
                    continue
                sitemap_queue.append(loc)
        elif tag == "urlset":
            stats["urlset"] += 1
            for node in root:
                if localname(node.tag) != "url":
                    continue
                loc = ""
                lastmod_text = ""
                for child in node:
                    child_name = localname(child.tag)
                    if child_name == "loc":
                        loc = (child.text or "").strip()
                    elif child_name == "lastmod":
                        lastmod_text = (child.text or "").strip()
                if not loc or loc in seen_urls:
                    continue
                if not looks_like_article_url(loc, domain):
                    continue
                if is_blocked_article_url(site_id, loc):
                    stats["url_filtered"] += 1
                    continue

                lastmod = parse_date(lastmod_text)
                window_reason = classify_url_window(loc, lastmod, start_dt, end_dt)
                if window_reason != "keep":
                    stats[window_reason] += 1
                    continue

                seen_urls.add(loc)
                discovered.append(
                    {
                        "site_id": site_id,
                        "domain": domain,
                        "source_url": row.get("url", ""),
                        "discovery_method": "sitemap",
                        "sitemap_url": sitemap_url,
                        "url": loc,
                        "lastmod": lastmod.isoformat() if lastmod else "",
                        "layer": row.get("layer", ""),
                        "priority_tier": row.get("priority_tier", ""),
                        "historical_strategy": row.get("historical_strategy", ""),
                    }
                )
                stats["url_discovered"] += 1
                if max_urls_per_site and len(discovered) >= max_urls_per_site:
                    break
            if max_urls_per_site and len(discovered) >= max_urls_per_site:
                break
        else:
            stats["unknown_root"] += 1

    summary = {
        "site_id": site_id,
        "domain": domain,
        "historical_strategy": row.get("historical_strategy", ""),
        "sitemaps_visited": len(visited_sitemaps),
        "urls_discovered": len(discovered),
        "stats": dict(stats),
    }
    return discovered, summary


def write_report(path: Path, output_path: Path, site_summaries: list[dict[str, object]], total_urls: int) -> None:
    by_strategy = Counter(str(row.get("historical_strategy", "")) for row in site_summaries)
    by_size = Counter()
    for row in site_summaries:
        count = int(row.get("urls_discovered", 0))
        if count == 0:
            by_size["0"] += 1
        elif count < 100:
            by_size["1-99"] += 1
        elif count < 1000:
            by_size["100-999"] += 1
        else:
            by_size["1000+"] += 1

    lines = [
        "# Historical URL Discovery Report",
        "",
        f"- Output: [{output_path.name}]({output_path})",
        f"- Total sites processed: `{len(site_summaries)}`",
        f"- Total URLs discovered: `{total_urls}`",
        "",
        "## Sites By Strategy",
        "",
    ]
    for key, value in sorted(by_strategy.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Sites By Discovery Yield", ""])
    for key, value in sorted(by_size.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Top Sites By URL Count", ""])
    for row in sorted(site_summaries, key=lambda item: int(item.get("urls_discovered", 0)), reverse=True)[:20]:
        lines.append(f"- `{row['site_id']}`: `{row['urls_discovered']}`")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    start_dt, end_dt = load_window(args.start_date, args.end_date)
    rows = read_rows(args.input)
    if args.site_id:
        keep = set(args.site_id)
        rows = [row for row in rows if row["site_id"] in keep]

    all_discovered: list[dict[str, object]] = []
    site_summaries: list[dict[str, object]] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(
                discover_site,
                row,
                args.timeout,
                start_dt,
                end_dt,
                args.max_sitemaps_per_site,
                args.max_urls_per_site,
            ): row["site_id"]
            for row in rows
        }
        for future in as_completed(future_map):
            discovered, summary = future.result()
            all_discovered.extend(discovered)
            site_summaries.append(summary)

    all_discovered.sort(key=lambda row: (str(row["site_id"]), str(row["url"])))
    site_summaries.sort(key=lambda row: str(row["site_id"]))
    total_urls = write_jsonl(args.output, all_discovered)
    write_report(args.report, args.output, site_summaries, total_urls)
    print(f"wrote {total_urls} url rows to {args.output}")
    print(f"wrote report to {args.report}")


if __name__ == "__main__":
    main()
