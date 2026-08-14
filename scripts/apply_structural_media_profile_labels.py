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

STATE_SOURCE_TYPES = {"state_media"}
OFFICIAL_SOURCE_TYPES = {
    "executive_government",
    "foreign_ministry",
    "foreign_service",
    "international_organization",
    "international_security_org",
    "supranational_executive",
}
PUBLIC_SOURCE_TYPES = {"public_broadcaster"}
WIRE_SOURCE_TYPES = {"wire_service"}

WESTERN_COUNTRIES = {
    "Australia",
    "Canada",
    "European Union",
    "France",
    "Germany",
    "Italy",
    "Japan",
    "New Zealand",
    "South Korea",
    "Spain",
    "Taiwan",
    "United Kingdom",
    "United States",
}
GLOBAL_SOUTH_COUNTRIES = {
    "Chile",
    "Mexico",
    "Nigeria",
    "International",  # only applied to selected org overrides below.
}
MIDDLE_EAST_COUNTRIES = {"Iran", "Palestine", "Qatar", "Saudi Arabia", "United Arab Emirates"}

GEO_OVERRIDES = {
    "ecowas.int": "global_south",
    "nato.int": "western",
    "who.int": "neutral",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply conservative structural media-profile labels and export the profile CSV."
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


def geo_for(domain: str, country: str) -> str:
    if domain in GEO_OVERRIDES:
        return GEO_OVERRIDES[domain]
    if country == "Russia":
        return "russia"
    if country in MIDDLE_EAST_COUNTRIES:
        return "middle_east"
    if country in WESTERN_COUNTRIES:
        return "western"
    if country in GLOBAL_SOUTH_COUNTRIES:
        return "global_south"
    return "unknown"


def build_update(row: dict[str, Any]) -> dict[str, Any] | None:
    source_type = str(row["source_type"] or "")
    domain = str(row["domain"] or "")
    country = str(row["country"] or "")
    review_status = str(row["review_status"] or "")
    if review_status == "locked":
        return None

    update: dict[str, Any] = {
        "domain": domain,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if source_type in STATE_SOURCE_TYPES:
        update.update(
            {
                "geo_alignment": geo_for(domain, country),
                "political_leaning": "state_aligned",
                "credibility_tier": "medium",
                "label_confidence": "medium",
                "review_status": "reviewed",
                "note": "structural_review_v1: state_media treated as state_aligned; credibility remains medium pending external factuality rating",
            }
        )
    elif source_type in OFFICIAL_SOURCE_TYPES:
        update.update(
            {
                "geo_alignment": geo_for(domain, country),
                "political_leaning": "state_aligned",
                "credibility_tier": "medium",
                "label_confidence": "medium",
                "review_status": "reviewed",
                "note": "structural_review_v1: official/intergovernmental source treated as institutional state_aligned; credibility remains medium pending external factuality rating",
            }
        )
    elif source_type in PUBLIC_SOURCE_TYPES:
        update.update(
            {
                "geo_alignment": geo_for(domain, country),
                "political_leaning": "unknown",
                "credibility_tier": "high",
                "label_confidence": "medium",
                "review_status": "needs_review",
                "note": "structural_review_v1: public broadcaster credibility seed; political leaning requires external rating",
            }
        )
    elif source_type in WIRE_SOURCE_TYPES:
        update.update(
            {
                "geo_alignment": geo_for(domain, country),
                "political_leaning": "unknown",
                "credibility_tier": "high",
                "label_confidence": "medium",
                "review_status": "needs_review",
                "note": "structural_review_v1: wire service credibility seed; political leaning requires external rating",
            }
        )
    else:
        return None

    return update


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
                            political_leaning = %(political_leaning)s,
                            credibility_tier = %(credibility_tier)s,
                            label_confidence = %(label_confidence)s,
                            evidence_note = CASE
                                WHEN COALESCE(evidence_note, '') = '' THEN %(note)s
                                WHEN evidence_note LIKE '%%structural_review_v1%%' THEN evidence_note
                                ELSE evidence_note || '; ' || %(note)s
                            END,
                            review_status = %(review_status)s,
                            updated_at = %(updated_at)s::timestamptz
                        WHERE domain = %(domain)s
                          AND review_status <> 'locked'
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
    updates = [update for row in rows if (update := build_update(row))]
    print(f"profile rows: {len(rows)}")
    print(f"structural updates: {len(updates)}")
    by_status: dict[str, int] = {}
    for update in updates:
        by_status[update["review_status"]] = by_status.get(update["review_status"], 0) + 1
    print(f"update review_status counts: {by_status}")
    if args.write_db:
        apply_updates(args, updates)
        print(f"updated DB rows: {len(updates)}")
    else:
        print("dry-run only; pass --write-db to apply")
        for update in updates[:20]:
            print(
                update["domain"],
                update["geo_alignment"],
                update["political_leaning"],
                update["credibility_tier"],
                update["review_status"],
            )

    if args.export_csv:
        exported_rows = fetch_profile_rows(args)
        export_csv(args.output, exported_rows)
        print(f"exported {len(exported_rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
