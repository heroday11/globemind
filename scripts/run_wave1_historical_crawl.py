#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WAVE1_PATH = PROJECT_ROOT / "data" / "source_curation" / "historical_wave1_targets.csv"
DISCOVERY_SCRIPT = PROJECT_ROOT / "scripts" / "discover_historical_urls.py"
EXTRACT_SCRIPT = PROJECT_ROOT / "scripts" / "extract_historical_articles.py"
MANIFEST_PATH = PROJECT_ROOT / "data" / "source_curation" / "historical_source_manifest_v1_fast.csv"
PYTHON_BIN = PROJECT_ROOT / ".env_torch" / "bin" / "python"
OUTPUT_ROOT = PROJECT_ROOT / "data" / "historical_news" / "wave1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Wave 1 historical crawl site by site.")
    parser.add_argument("--start-date", default="2023-06-21")
    parser.add_argument("--end-date", default="2026-06-21")
    parser.add_argument("--site-id", action="append", default=[])
    parser.add_argument("--site-limit", type=int, default=0)
    parser.add_argument("--discovery-workers", type=int, default=2)
    parser.add_argument("--extract-workers", type=int, default=8)
    parser.add_argument("--parallel-sites", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-sitemaps-per-site", type=int, default=80)
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_wave1_rows() -> list[dict[str, str]]:
    with WAVE1_PATH.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def run_cmd(cmd: list[str], dry_run: bool) -> None:
    print("CMD:", " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)


def run_site(row: dict[str, str], args: argparse.Namespace) -> None:
    site_id = row["site_id"]
    site_dir = OUTPUT_ROOT / site_id
    site_dir.mkdir(parents=True, exist_ok=True)

    discovery_out = site_dir / f"{site_id}_urls_{args.start_date}_{args.end_date}.jsonl"
    discovery_report = site_dir / f"{site_id}_discovery_{args.start_date}_{args.end_date}.md"
    extract_out = site_dir / f"{site_id}_articles_{args.start_date}_{args.end_date}.jsonl"
    extract_err = site_dir / f"{site_id}_articles_{args.start_date}_{args.end_date}_errors.jsonl"

    discovery_cmd = [
        "python3",
        str(DISCOVERY_SCRIPT),
        "--input",
        str(MANIFEST_PATH),
        "--output",
        str(discovery_out),
        "--report",
        str(discovery_report),
        "--workers",
        str(args.discovery_workers),
        "--timeout",
        str(args.timeout),
        "--max-sitemaps-per-site",
        str(args.max_sitemaps_per_site),
        "--site-id",
        site_id,
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
    ]
    run_cmd(discovery_cmd, args.dry_run)

    if args.skip_extract:
        return

    extract_cmd = [
        str(PYTHON_BIN),
        str(EXTRACT_SCRIPT),
        "--input",
        str(discovery_out),
        "--output",
        str(extract_out),
        "--errors",
        str(extract_err),
        "--workers",
        str(args.extract_workers),
        "--timeout",
        str(args.timeout),
    ]
    run_cmd(extract_cmd, args.dry_run)


def main() -> None:
    args = parse_args()
    rows = read_wave1_rows()
    if args.site_id:
        keep = set(args.site_id)
        rows = [row for row in rows if row["site_id"] in keep]
    if args.site_limit > 0:
        rows = rows[: args.site_limit]

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    if args.parallel_sites <= 1:
        for row in rows:
            run_site(row, args)
        return

    with ThreadPoolExecutor(max_workers=args.parallel_sites) as executor:
        futures = [executor.submit(run_site, row, args) for row in rows]
        for future in as_completed(futures):
            future.result()


if __name__ == "__main__":
    main()
