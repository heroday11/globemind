#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import psycopg2

from db_runtime_config import require_database_password


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "source_curation" / "media_source_review_queue.csv"

OUTPUT_COLUMNS = [
    "review_rank",
    "domain",
    "source_name",
    "country",
    "region",
    "source_type",
    "ownership_type",
    "geo_alignment",
    "political_leaning",
    "credibility_tier",
    "label_confidence",
    "review_status",
    "article_count_snapshot",
    "evidence_url",
    "evidence_note",
    "missing_fields",
    "review_priority",
    "proposed_ownership_type",
    "proposed_geo_alignment",
    "proposed_political_leaning",
    "proposed_credibility_tier",
    "proposed_label_confidence",
    "proposed_review_status",
    "evidence_url_1",
    "evidence_url_2",
    "review_note",
    "reviewer",
    "reviewed_at",
]

REVIEW_FIELDS = [
    "ownership_type",
    "geo_alignment",
    "political_leaning",
    "credibility_tier",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export high-impact media sources that still need source-profile review."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--include-reviewed", action="store_true")
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
    return parser.parse_args()


def connect(args: argparse.Namespace) -> Any:
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=15,
    )


def missing_fields(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in REVIEW_FIELDS:
        if str(row.get(field) or "").strip() == "unknown":
            missing.append(field)
    if str(row.get("review_status") or "").strip() not in {"reviewed", "locked"}:
        missing.append("review_status")
    return missing


def review_priority(row: dict[str, Any], missing: list[str]) -> str:
    article_count = int(row.get("article_count_snapshot") or 0)
    source_type = str(row.get("source_type") or "")
    if "political_leaning" in missing or "credibility_tier" in missing:
        if article_count >= 20_000:
            return "P0"
        if article_count >= 5_000:
            return "P1"
        return "P2"
    if source_type in {"state_media", "foreign_ministry", "executive_government"}:
        return "P1"
    return "P2"


def fetch_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    review_filter = ""
    if not args.include_reviewed:
        review_filter = """
        WHERE review_status NOT IN ('reviewed', 'locked')
           OR ownership_type = 'unknown'
           OR geo_alignment = 'unknown'
           OR political_leaning = 'unknown'
           OR credibility_tier = 'unknown'
        """

    conn = connect(args)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT domain, source_name, country, region, source_type,
                       ownership_type, geo_alignment, political_leaning,
                       credibility_tier, label_confidence, review_status,
                       article_count_snapshot, evidence_url, evidence_note
                FROM public.media_source_profile
                {review_filter}
                ORDER BY article_count_snapshot DESC NULLS LAST, domain
                LIMIT %s
                """,
                (args.limit,),
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for rank, row in enumerate(rows, start=1):
            missing = missing_fields(row)
            output = {column: "" for column in OUTPUT_COLUMNS}
            output.update({key: row.get(key, "") for key in row})
            output["review_rank"] = rank
            output["missing_fields"] = ",".join(missing)
            output["review_priority"] = review_priority(row, missing)
            writer.writerow(output)


def main() -> None:
    args = parse_args()
    rows = fetch_rows(args)
    write_csv(args.output, rows)
    p0 = 0
    for row in rows:
        if review_priority(row, missing_fields(row)) == "P0":
            p0 += 1
    print(f"wrote {len(rows)} review rows to {args.output}")
    print(f"P0 rows: {p0}")


if __name__ == "__main__":
    main()
