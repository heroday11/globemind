#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg2

from db_runtime_config import require_database_password


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = PROJECT_ROOT / "data" / "source_curation" / "media_source_profile.csv"

REQUIRED_COLUMNS = {
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
}

NON_EMPTY_COLUMNS = {
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
}

OWNERSHIP_TYPES = {
    "government",
    "intergovernmental",
    "nonprofit",
    "party_affiliated",
    "private",
    "public",
    "state",
    "unknown",
    "wire_service",
}

GEO_ALIGNMENTS = {
    "china",
    "global_south",
    "middle_east",
    "mixed",
    "neutral",
    "russia",
    "unknown",
    "western",
}

POLITICAL_LEANINGS = {
    "center",
    "center_left",
    "center_right",
    "left",
    "right",
    "state_aligned",
    "unknown",
}

CREDIBILITY_TIERS = {"high", "medium", "low", "unknown"}
CONFIDENCE_LEVELS = {"high", "medium", "low"}
REVIEW_STATUSES = {"seeded", "needs_review", "reviewed", "locked"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate media_source_profile.csv.")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
    parser.add_argument("--check-db-domains", action="store_true")
    return parser.parse_args()


def load_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return rows, reader.fieldnames or []


def current_db_domains(args: argparse.Namespace) -> set[str]:
    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=15,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT lower(btrim(domain))
                FROM public.media_source
                WHERE domain IS NOT NULL AND btrim(domain) <> ''
                """
            )
            return {str(row[0]) for row in cur.fetchall()}
    finally:
        conn.close()


def require_enum(
    errors: list[str],
    row_num: int,
    row: dict[str, str],
    field: str,
    allowed: set[str],
) -> None:
    value = (row.get(field) or "").strip()
    if value not in allowed:
        errors.append(f"row {row_num}: invalid {field}={value!r}")


def main() -> None:
    args = parse_args()
    rows, columns = load_rows(args.profile)
    errors: list[str] = []
    warnings: list[str] = []

    missing_cols = sorted(REQUIRED_COLUMNS - set(columns))
    if missing_cols:
        errors.append(f"missing columns: {', '.join(missing_cols)}")

    domains: list[str] = []
    for idx, row in enumerate(rows, start=2):
        domain = (row.get("domain") or "").strip().lower()
        if not domain:
            errors.append(f"row {idx}: missing domain")
            continue
        domains.append(domain)

        for field in NON_EMPTY_COLUMNS:
            if not (row.get(field) or "").strip():
                errors.append(f"row {idx}: missing {field}")

        require_enum(errors, idx, row, "ownership_type", OWNERSHIP_TYPES)
        require_enum(errors, idx, row, "geo_alignment", GEO_ALIGNMENTS)
        require_enum(errors, idx, row, "political_leaning", POLITICAL_LEANINGS)
        require_enum(errors, idx, row, "credibility_tier", CREDIBILITY_TIERS)
        require_enum(errors, idx, row, "label_confidence", CONFIDENCE_LEVELS)
        require_enum(errors, idx, row, "review_status", REVIEW_STATUSES)

        source_name = (row.get("source_name") or "").strip()
        if re.search(r"\b(Com|Net|Org|Co|Tv|Int|Gov)\b$", source_name):
            warnings.append(f"row {idx}: source_name={source_name!r} looks domain-derived")

        political = (row.get("political_leaning") or "").strip()
        evidence_url = (row.get("evidence_url") or "").strip()
        confidence = (row.get("label_confidence") or "").strip()
        review_status = (row.get("review_status") or "").strip()
        article_count_raw = (row.get("article_count_snapshot") or "").strip()
        try:
            article_count = int(article_count_raw)
            if article_count < 0:
                errors.append(f"row {idx}: article_count_snapshot must be >= 0")
        except ValueError:
            errors.append(f"row {idx}: invalid article_count_snapshot={article_count_raw!r}")

        if evidence_url and not evidence_url.startswith(("http://", "https://")):
            errors.append(f"row {idx}: evidence_url must start with http:// or https://")

        if political != "unknown":
            if not evidence_url:
                errors.append(f"row {idx}: political_leaning={political!r} requires evidence_url")
            if confidence == "low":
                warnings.append(f"row {idx}: political_leaning={political!r} has low confidence")
            if review_status not in {"reviewed", "locked"}:
                warnings.append(f"row {idx}: political_leaning={political!r} is not reviewed/locked")

    duplicate_domains = [domain for domain, count in Counter(domains).items() if count > 1]
    if duplicate_domains:
        errors.append(f"duplicate domains: {', '.join(sorted(duplicate_domains))}")

    if args.check_db_domains:
        db_domains = current_db_domains(args)
        profile_domains = set(domains)
        missing_from_profile = sorted(db_domains - profile_domains)
        extra_in_profile = sorted(profile_domains - db_domains)
        if missing_from_profile:
            errors.append(f"DB domains missing from profile: {', '.join(missing_from_profile[:30])}")
            if len(missing_from_profile) > 30:
                errors.append(f"... plus {len(missing_from_profile) - 30} more missing DB domains")
        if extra_in_profile:
            warnings.append(f"profile domains not currently in DB: {len(extra_in_profile)}")

    print(f"rows={len(rows)} domains={len(set(domains))}")
    if warnings:
        print("warnings:")
        for item in warnings:
            print(f"  - {item}")
    if errors:
        print("errors:")
        for item in errors:
            print(f"  - {item}")
        raise SystemExit(1)
    print("validation OK")


if __name__ == "__main__":
    main()
