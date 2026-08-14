#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from news_date_cleaning import clean_published_at


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "historical_news" / "wave1_18domains_extract_360_v3_articles.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "historical_news" / "news_table_rows.jsonl"
DEFAULT_SOURCE_MAP = PROJECT_ROOT / "data" / "source_curation" / "historical_wave1_targets.csv"
REGION_CODE_MAP = {
    "africa": "AF",
    "asia": "AS",
    "asia_pacific": "AP",
    "europe": "EU",
    "global": "GL",
    "latin_america": "LA",
    "middle_east": "ME",
    "north_america": "NA",
    "south_asia": "SA",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert extracted crawl rows into public.news-aligned rows.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-map", type=Path, default=DEFAULT_SOURCE_MAP)
    parser.add_argument("--site-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_source_map(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {row["site_id"]: row for row in rows}


def normalize_language(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    primary = text.split("-", 1)[0].split("_", 1)[0].strip().lower()
    return primary or None


def normalize_region_code(value: str) -> str | None:
    text = value.strip().lower()
    if not text:
        return None
    return REGION_CODE_MAP.get(text, text[:8].upper())


def build_news_row(
    row: dict[str, Any],
    source_meta: dict[str, str] | None,
) -> dict[str, Any]:
    source_meta = source_meta or {}
    final_url = (
        str(row.get("response_url") or "").strip()
        or str(row.get("request_url") or "").strip()
        or str(row.get("url") or "").strip()
    )
    url_hash = hashlib.md5(final_url.encode("utf-8")).hexdigest() if final_url else None
    region = normalize_region_code(str(source_meta.get("region") or ""))
    media_source_domain = (
        str(source_meta.get("domain") or "").strip()
        or str(row.get("domain") or "").strip()
        or None
    )

    date_result = clean_published_at(
        {
            **row,
            "url": final_url,
            "response_url": str(row.get("response_url") or "").strip(),
            "request_url": str(row.get("request_url") or "").strip(),
        }
    )

    return {
        "title": str(row.get("title") or "").strip(),
        "body": str(row.get("body") or "").strip() or None,
        "url": final_url or None,
        "url_hash": url_hash,
        "published_at": date_result.isoformat() or None,
        "published_at_source": date_result.source or None,
        "published_at_confidence": date_result.confidence,
        "media_source_domain": media_source_domain,
        "language": normalize_language(str(row.get("language") or "")),
        "region": region,
        "author": str(row.get("author") or "").strip() or None,
    }


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)
    if args.site_id:
        keep = set(args.site_id)
        rows = [row for row in rows if row.get("site_id") in keep]
    if args.limit > 0:
        rows = rows[: args.limit]

    source_map = load_source_map(args.source_map)
    out_rows = [
        build_news_row(
            row=row,
            source_meta=source_map.get(str(row.get("site_id") or "")),
        )
        for row in rows
        if str(row.get("title") or "").strip()
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for row in out_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"wrote {len(out_rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
