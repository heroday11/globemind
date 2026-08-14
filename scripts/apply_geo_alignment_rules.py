#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2

from db_runtime_config import require_database_password


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "source_curation" / "media_source_profile.csv"

PROFILE_COLUMNS = [
    "domain",
    "site_id",
    "source_name",
    "country",
    "region",
    "region_code",
    "source_type",
    "layer",
    "priority_tier",
    "ownership_type",
    "geo_alignment",
    "political_leaning",
    "credibility_tier",
    "label_confidence",
    "evidence_url",
    "evidence_note",
    "review_status",
    "article_count_snapshot",
    "profile_version",
    "updated_at",
]

WESTERN_COUNTRIES = {
    "Australia",
    "Canada",
    "European Union",
    "France",
    "Germany",
    "Italy",
    "Japan",
    "New Zealand",
    "Portugal",
    "South Korea",
    "Spain",
    "Taiwan",
    "Ukraine",
    "United Kingdom",
    "United States",
}
GLOBAL_SOUTH_COUNTRIES = {
    "Argentina",
    "Bangladesh",
    "Brazil",
    "Cambodia",
    "Chile",
    "Colombia",
    "India",
    "Indonesia",
    "Malaysia",
    "Mexico",
    "Nigeria",
    "Pakistan",
    "Peru",
    "Philippines",
    "Singapore",
    "South Africa",
    "Thailand",
    "Vietnam",
}
MIDDLE_EAST_COUNTRIES = {
    "Iran",
    "Israel",
    "Palestine",
    "Qatar",
    "Saudi Arabia",
    "Turkey",
    "United Arab Emirates",
}

DOMAIN_OVERRIDES = {
    "africanews.com": "global_south",
    "scmp.com": "china",
}

NOTE = (
    "geo_alignment_rule_v1: inferred from curated country/region for source-composition grouping; "
    "not a political-bias label"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill missing geo_alignment values from curated country/region metadata."
    )
    parser.add_argument("--write-db", action="store_true", help="Apply updates. Default is dry-run.")
    parser.add_argument("--export-csv", action="store_true", help="Export media_source_profile.csv from DB after updates.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
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
        connect_timeout=20,
    )


def geo_for(domain: str, country: str, region: str) -> str:
    if domain in DOMAIN_OVERRIDES:
        return DOMAIN_OVERRIDES[domain]
    if country == "Russia":
        return "russia"
    if country == "Hong Kong":
        return "china"
    if country in MIDDLE_EAST_COUNTRIES:
        return "middle_east"
    if country in WESTERN_COUNTRIES:
        return "western"
    if country in GLOBAL_SOUTH_COUNTRIES:
        return "global_south"
    if country == "International":
        if region == "africa":
            return "global_south"
        if region == "global":
            return "neutral"
    return "unknown"


def fetch_profile_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    conn = connect(args)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT domain, site_id, source_name, country, region, region_code,
                       source_type, layer, priority_tier, ownership_type,
                       geo_alignment, political_leaning, credibility_tier,
                       label_confidence, evidence_url, evidence_note, review_status,
                       article_count_snapshot, profile_version, updated_at
                FROM public.media_source_profile
                ORDER BY domain
                """
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def build_updates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("review_status") or "") == "locked":
            continue
        if str(row.get("geo_alignment") or "") != "unknown":
            continue
        domain = str(row.get("domain") or "")
        country = str(row.get("country") or "")
        region = str(row.get("region") or "")
        geo_alignment = geo_for(domain, country, region)
        if geo_alignment == "unknown":
            continue
        updates.append(
            {
                "domain": domain,
                "geo_alignment": geo_alignment,
                "note": NOTE,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return updates


def apply_updates(args: argparse.Namespace, updates: list[dict[str, Any]]) -> None:
    if not updates:
        return
    conn = connect(args)
    try:
        with conn:
            with conn.cursor() as cur:
                for update in updates:
                    cur.execute(
                        """
                        UPDATE public.media_source_profile
                        SET geo_alignment = %(geo_alignment)s,
                            evidence_note = CASE
                                WHEN COALESCE(evidence_note, '') = '' THEN %(note)s
                                WHEN POSITION(%(note)s IN evidence_note) > 0 THEN evidence_note
                                ELSE evidence_note || '; ' || %(note)s
                            END,
                            updated_at = %(updated_at)s::timestamptz
                        WHERE domain = %(domain)s
                          AND review_status <> 'locked'
                          AND geo_alignment = 'unknown'
                        """,
                        update,
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError(f"domain not updated: {update['domain']}")
    finally:
        conn.close()


def export_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PROFILE_COLUMNS)
        writer.writeheader()
        for row in rows:
            out = {column: row.get(column, "") for column in PROFILE_COLUMNS}
            if out.get("updated_at") is not None:
                out["updated_at"] = str(out["updated_at"])
            writer.writerow(out)


def main() -> None:
    args = parse_args()
    rows = fetch_profile_rows(args)
    updates = build_updates(rows)
    counts: dict[str, int] = {}
    for update in updates:
        counts[update["geo_alignment"]] = counts.get(update["geo_alignment"], 0) + 1
    print(f"profile rows: {len(rows)}")
    print(f"geo_alignment updates: {len(updates)}")
    print(f"update geo_alignment counts: {counts}")
    if args.write_db:
        apply_updates(args, updates)
        print(f"updated DB rows: {len(updates)}")
    else:
        print("dry-run only; pass --write-db to apply")

    if args.export_csv:
        rows = fetch_profile_rows(args)
        export_csv(args.output, rows)
        print(f"exported {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
