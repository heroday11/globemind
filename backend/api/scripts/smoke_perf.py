"""Smoke-test key public pages and APIs.

Usage:
  python backend/api/scripts/smoke_perf.py --base https://globemind.top
  python backend/api/scripts/smoke_perf.py --base http://127.0.0.1:8088 --max-total 2.5
"""
from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from typing import Iterable

import httpx


@dataclass(frozen=True)
class Target:
    name: str
    path: str
    max_total: float


PAGES: tuple[Target, ...] = (
    Target("home", "/", 2.0),
    Target("story_graph", "/data-service/story-graph", 2.0),
    Target("data_search", "/data-service/data-search", 2.0),
    Target("report_center", "/data-service/report-center", 2.0),
    Target("data_assistant", "/data-assistant", 2.0),
    Target("personal_center", "/user-center/personal-center", 2.0),
    Target("about_us", "/about-us", 2.0),
    Target("ground_news_desk", "/data-service/ground-news-desk", 2.0),
    Target("sentiment", "/sentiment-analysis", 2.0),
    Target("financial_terminal", "/financial-terminal", 2.0),
    Target("academic_data", "/academic-data", 2.0),
)

APIS: tuple[Target, ...] = (
    Target("dashboard_news", "/api/dashboard/news?page=1&size=10&sort_order=desc&favorite_scope_topic=", 2.5),
    Target("search_options", "/api/dashboard/search/options", 2.5),
    Target("stats", "/api/dashboard/stats", 2.5),
    Target("story_l2_list", "/api/story-graph/l2-chain/list?page_size=100&min_segments=2", 2.5),
    Target("ground_news_list", "/api/story-graph/ground-news/list?page_size=24&min_articles=2&include_first_detail=true", 2.5),
    Target("opinion_overview", "/api/opinion/overview?days=30&refresh=false", 2.5),
    Target("opinion_trend", "/api/opinion/china-trend?days=90&china_min_score=0.4&sentiment_filter=all&refresh=false", 2.5),
    Target("opinion_dimensions", "/api/opinion/dimensions?days=30&limit=8", 2.5),
    Target("opinion_quality", "/api/opinion/quality", 2.5),
    Target("opinion_top_news", "/api/opinion/top-news?days=30&page_size=10&sentiment_filter=all", 2.5),
    Target("financial_dashboard", "/api/financial/dashboard", 3.5),
)


async def check_one(client: httpx.AsyncClient, target: Target, global_max_total: float | None) -> bool:
    started = time.perf_counter()
    try:
        resp = await client.get(target.path)
        body = resp.content
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        print(f"FAIL {target.name:20s} {elapsed:6.3f}s error={exc}")
        return False

    elapsed = time.perf_counter() - started
    limit = global_max_total if global_max_total is not None else target.max_total
    ok = resp.status_code == 200 and elapsed <= limit
    status = "OK  " if ok else "FAIL"
    print(f"{status} {target.name:20s} {elapsed:6.3f}s status={resp.status_code} bytes={len(body)} limit={limit:.1f}s")
    return ok


async def run(base: str, targets: Iterable[Target], timeout: float, max_total: float | None) -> int:
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
        headers={"Accept-Encoding": "gzip, br"},
        follow_redirects=True,
        trust_env=False,
    ) as client:
        results = []
        for target in targets:
            results.append(await check_one(client, target, max_total))
        return 0 if all(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test GlobeMind public pages and APIs")
    parser.add_argument("--base", default="http://127.0.0.1:8088", help="Base URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout seconds")
    parser.add_argument("--max-total", type=float, default=None, help="Override all per-target total-time limits")
    parser.add_argument("--apis-only", action="store_true", help="Only check API endpoints")
    parser.add_argument("--pages-only", action="store_true", help="Only check page endpoints")
    args = parser.parse_args()

    if args.apis_only and args.pages_only:
        parser.error("--apis-only and --pages-only are mutually exclusive")

    targets: tuple[Target, ...]
    if args.apis_only:
        targets = APIS
    elif args.pages_only:
        targets = PAGES
    else:
        targets = PAGES + APIS

    return asyncio.run(run(args.base, targets, args.timeout, args.max_total))


if __name__ == "__main__":
    raise SystemExit(main())
