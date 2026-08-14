#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from discover_historical_urls import classify_precise_url_window, classify_url_window, is_blocked_article_url, load_window, parse_date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune non-article URLs from a discovered URL queue.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stats", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--require-date-signal", action="store_true")
    parser.add_argument("--max-urls-per-site", type=int, default=0)
    return parser.parse_args()


def should_keep_discovered_row(
    row: dict[str, object],
    start_dt=None,
    end_dt=None,
    require_date_signal: bool = False,
) -> tuple[bool, str]:
    site_id = str(row.get("site_id") or "")
    url = str(row.get("url") or "")
    if not url:
        return False, "missing_url"
    if is_blocked_article_url(site_id, url):
        return False, "blocked_article_url"

    if start_dt is not None and end_dt is not None:
        lastmod = parse_date(str(row.get("lastmod") or ""))
        if require_date_signal:
            window_reason = classify_precise_url_window(url, lastmod, start_dt, end_dt)
        else:
            window_reason = classify_url_window(url, lastmod, start_dt, end_dt)
        if window_reason != "keep":
            return False, window_reason

    return True, "keep"


def main() -> None:
    args = parse_args()
    if bool(args.start_date) != bool(args.end_date):
        raise SystemExit("--start-date and --end-date must be provided together")
    start_dt = end_dt = None
    if args.start_date and args.end_date:
        start_dt, end_dt = load_window(args.start_date, args.end_date)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    removed = 0
    by_site_removed: Counter[str] = Counter()
    by_site_kept: Counter[str] = Counter()
    by_reason_removed: Counter[str] = Counter()
    site_kept_counts: Counter[str] = Counter()

    with args.input.open("r", encoding="utf-8") as src, args.output.open("w", encoding="utf-8") as dst:
        for idx, line in enumerate(src, start=1):
            if args.limit and idx > args.limit:
                break
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                removed += 1
                by_site_removed["__invalid_json__"] += 1
                by_reason_removed["invalid_json"] += 1
                continue
            site_id = str(row.get("site_id") or "")
            keep, reason = should_keep_discovered_row(row, start_dt, end_dt, args.require_date_signal)
            if not keep:
                removed += 1
                by_site_removed[site_id or "__missing_site__"] += 1
                by_reason_removed[reason] += 1
                continue
            site_key = site_id or "__missing_site__"
            if args.max_urls_per_site and site_kept_counts[site_key] >= args.max_urls_per_site:
                removed += 1
                by_site_removed[site_key] += 1
                by_reason_removed["site_cap"] += 1
                continue
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            kept += 1
            by_site_kept[site_key] += 1
            site_kept_counts[site_key] += 1

    payload = {
        "input": str(args.input),
        "output": str(args.output),
        "kept": kept,
        "removed": removed,
        "removed_pct": round((removed / max(1, kept + removed)) * 100, 2),
        "window_filter_enabled": start_dt is not None and end_dt is not None,
        "require_date_signal": bool(args.require_date_signal),
        "max_urls_per_site": int(args.max_urls_per_site),
        "top_removed_reasons": by_reason_removed.most_common(20),
        "top_removed_sites": by_site_removed.most_common(20),
        "top_kept_sites": by_site_kept.most_common(20),
    }
    if args.stats:
        args.stats.parent.mkdir(parents=True, exist_ok=True)
        args.stats.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
