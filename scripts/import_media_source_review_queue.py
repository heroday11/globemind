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
DEFAULT_INPUT = PROJECT_ROOT / "data" / "source_curation" / "media_source_review_queue.csv"

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

PROPOSED_FIELDS = {
    "ownership_type": ("proposed_ownership_type", OWNERSHIP_TYPES),
    "geo_alignment": ("proposed_geo_alignment", GEO_ALIGNMENTS),
    "political_leaning": ("proposed_political_leaning", POLITICAL_LEANINGS),
    "credibility_tier": ("proposed_credibility_tier", CREDIBILITY_TIERS),
    "label_confidence": ("proposed_label_confidence", CONFIDENCE_LEVELS),
    "review_status": ("proposed_review_status", REVIEW_STATUSES),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import reviewed media-source profile labels from a review queue CSV."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--write-db", action="store_true", help="Apply updates. Default is dry-run.")
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


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def clean(value: str | None) -> str:
    return (value or "").strip()


def build_update(row: dict[str, str], row_num: int) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    domain = clean(row.get("domain")).lower()
    if not domain:
        return {}, [f"row {row_num}: missing domain"]

    update: dict[str, str] = {"domain": domain}
    has_label_update = False
    for target_field, (source_field, allowed) in PROPOSED_FIELDS.items():
        value = clean(row.get(source_field))
        if not value:
            continue
        if value not in allowed:
            errors.append(f"row {row_num}: invalid {source_field}={value!r}")
            continue
        update[target_field] = value
        has_label_update = True

    evidence_url_1 = clean(row.get("evidence_url_1"))
    evidence_url_2 = clean(row.get("evidence_url_2"))
    existing_evidence_url = clean(row.get("evidence_url"))
    review_note = clean(row.get("review_note"))
    reviewer = clean(row.get("reviewer"))
    reviewed_at = clean(row.get("reviewed_at")) or datetime.now(timezone.utc).isoformat()

    if evidence_url_1 and not evidence_url_1.startswith(("http://", "https://")):
        errors.append(f"row {row_num}: evidence_url_1 must be HTTP(S)")
    if evidence_url_2 and not evidence_url_2.startswith(("http://", "https://")):
        errors.append(f"row {row_num}: evidence_url_2 must be HTTP(S)")

    evidence_url = evidence_url_1 or existing_evidence_url
    political = update.get("political_leaning")
    credibility = update.get("credibility_tier")
    if political and political != "unknown" and not evidence_url:
        errors.append(f"row {row_num}: political_leaning update requires evidence_url_1")
    if credibility and credibility != "unknown" and not evidence_url:
        errors.append(f"row {row_num}: credibility_tier update requires evidence_url_1")

    if not has_label_update and not evidence_url_1 and not evidence_url_2 and not review_note:
        return {}, errors

    if evidence_url_1:
        update["evidence_url"] = evidence_url_1
    note_parts = []
    if review_note:
        note_parts.append(review_note)
    if evidence_url_2:
        note_parts.append(f"secondary_evidence={evidence_url_2}")
    if reviewer:
        note_parts.append(f"reviewer={reviewer}")
    note_parts.append(f"reviewed_at={reviewed_at}")
    update["evidence_note_append"] = "; ".join(note_parts)
    if "review_status" not in update:
        update["review_status"] = "reviewed" if has_label_update else "needs_review"

    needs_confidence_update = any(
        field in update
        for field in (
            "geo_alignment",
            "political_leaning",
            "credibility_tier",
        )
    )
    if "label_confidence" not in update and needs_confidence_update:
        update["label_confidence"] = "medium"
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    return update, errors


def apply_updates(args: argparse.Namespace, updates: list[dict[str, str]]) -> None:
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
                        SET ownership_type = COALESCE(%(ownership_type)s, ownership_type),
                            geo_alignment = COALESCE(%(geo_alignment)s, geo_alignment),
                            political_leaning = COALESCE(%(political_leaning)s, political_leaning),
                            credibility_tier = COALESCE(%(credibility_tier)s, credibility_tier),
                            label_confidence = COALESCE(%(label_confidence)s, label_confidence),
                            evidence_url = COALESCE(%(evidence_url)s, evidence_url),
                            evidence_note = CASE
                                WHEN COALESCE(%(evidence_note_append)s, '') = '' THEN evidence_note
                                WHEN COALESCE(evidence_note, '') = '' THEN %(evidence_note_append)s
                                ELSE evidence_note || '; review_import: ' || %(evidence_note_append)s
                            END,
                            review_status = COALESCE(%(review_status)s, review_status),
                            updated_at = %(updated_at)s::timestamptz
                        WHERE domain = %(domain)s
                        """,
                        {
                            "domain": update["domain"],
                            "ownership_type": update.get("ownership_type"),
                            "geo_alignment": update.get("geo_alignment"),
                            "political_leaning": update.get("political_leaning"),
                            "credibility_tier": update.get("credibility_tier"),
                            "label_confidence": update.get("label_confidence"),
                            "evidence_url": update.get("evidence_url"),
                            "evidence_note_append": update.get("evidence_note_append"),
                            "review_status": update.get("review_status"),
                            "updated_at": update["updated_at"],
                        },
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError(f"domain not updated: {update['domain']}")
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    rows = load_rows(args.input)
    updates: list[dict[str, str]] = []
    errors: list[str] = []
    for idx, row in enumerate(rows, start=2):
        update, row_errors = build_update(row, idx)
        errors.extend(row_errors)
        if update:
            updates.append(update)

    print(f"input rows: {len(rows)}")
    print(f"candidate updates: {len(updates)}")
    if errors:
        print("errors:")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)

    if args.write_db:
        apply_updates(args, updates)
        print(f"updated DB rows: {len(updates)}")
    else:
        print("dry-run only; pass --write-db to apply")
        for update in updates[:10]:
            visible = {key: value for key, value in update.items() if key != "evidence_note_append"}
            print(visible)


if __name__ == "__main__":
    main()
